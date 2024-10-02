import asyncio
from asyncio import Semaphore
from base64 import b64decode, b64encode
from contextlib import suppress
from json import loads
from logging import getLogger
from typing import Any, Coroutine, Dict, List, Optional

from discord import (
    CategoryChannel,
    ChannelType,
    Color,
    ContentFilter,
    Guild,
    HTTPException,
    Member,
    NotFound,
    Object,
    PermissionOverwrite,
    Permissions,
    Role,
    SystemChannelFlags,
    TextChannel,
    VerificationLevel,
)
from discord.utils import get

from main import greedbot
from tools import capture_time

from .types import BackupData, BooleanArgs

log = getLogger("greedbot/backup")


async def dump(guild: Guild) -> BackupData:
    return {
        "name": guild.name,
        "design": {
            "icon": (
                b64encode(await guild.icon.read()).decode() if guild.icon else None
            ),
            "banner": (
                b64encode(await guild.banner.read()).decode() if guild.banner else None
            ),
            "splash": (
                b64encode(await guild.splash.read()).decode() if guild.splash else None
            ),
            "discovery_splash": (
                b64encode(await guild.discovery_splash.read()).decode()
                if guild.discovery_splash
                else None
            ),
        },
        "afk_channel": guild._afk_channel_id,
        "afk_timeout": guild.afk_timeout,
        "verification_level": guild.verification_level.value,
        "rules_channel": guild._rules_channel_id,
        "community_updates": guild._public_updates_channel_id,
        "system": {
            "channel": guild._system_channel_id,
            "flags": guild._system_channel_flags,
        },
        "categories": [
            {
                "id": category.id,
                "name": category.name,
                "position": category.position,
                "overwrites": {
                    str(target.id): overwrite._values
                    for target, overwrite in category.overwrites.items()
                },
            }
            for category in guild.categories
        ],
        "channels": [
            {
                "id": channel.id,
                "type": channel.type.value,
                "name": channel.name,
                "position": channel.position,
                "topic": getattr(channel, "topic", None),
                "slowmode_delay": getattr(channel, "slowmode_delay", None),
                "nsfw": channel.is_nsfw(),
                "overwrites": {
                    str(target.id): overwrite._values
                    for target, overwrite in channel.overwrites.items()
                },
                "category_id": channel.category_id,
                "bitrate": getattr(channel, "bitrate", None),
                "user_limit": getattr(channel, "user_limit", None),
            }
            for channel in guild.channels
            if channel.type != ChannelType.category
        ],
        "roles": [
            {
                "id": role.id,
                "name": role.name,
                "position": role.position,
                "color": role.color.value,
                "hoist": role.hoist,
                "default": role.is_default(),
                "mentionable": role.mentionable,
                "permissions": role.permissions.value,
                "premium": role.is_premium_subscriber(),
                "members": (
                    [member.id for member in role.members]
                    if not role.is_default()
                    else []
                ),
            }
            for role in guild.roles
        ],
        "bans": {str(entry.user.id): entry.reason async for entry in guild.bans()},
    }


class BackupViewer:
    data: BackupData

    def __init__(self, data: str):
        self.data = loads(data)

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def icon(self) -> Optional[bytes]:
        buffer = self.data["design"]["icon"]
        if buffer:
            return b64decode(buffer)

    def emoji(self, _type: int) -> str:
        if _type == ChannelType.voice.value:
            return "🔊"
        elif _type == ChannelType.stage_voice.value:
            return "🗣️"
        elif _type == ChannelType.news.value:
            return "📢"
        elif _type in (ChannelType.forum.value, ChannelType.media.value):
            return "💬"

        return "📝"

    def channels(self, limit: int = 1000):
        ret = "```"
        for channel in self.data["channels"]:
            if channel["category_id"]:
                continue

            ret += f"\n{self.emoji(channel['type'])}\u200a{channel['name']}"

        ret += "\n"
        for category in self.data["categories"]:
            ret += "\n📁\u200a" + category["name"]
            for channel in self.data["channels"]:
                if channel["category_id"] == category["id"]:
                    ret += f"\n  {self.emoji(channel['type'])}\u200a{channel['name']}"

            ret += "\n"

        return ret[: limit - 10] + "```"

    def roles(self, limit: int = 1000):
        ret = "```"
        for role in reversed(self.data["roles"]):
            if role["default"]:
                continue

            ret += "\n" + role["name"]

        return ret[: limit - 10] + "```"


class BackupLoader:
    bot: greedbot
    guild: Guild
    data: BackupData
    options: BooleanArgs
    semaphore: Semaphore
    reason: str
    id_translator: dict[int, int]

    def __init__(self, bot: greedbot, guild: Guild, data: str):
        self.bot = bot
        self.guild = guild
        self.data = loads(data)
        self.options = BooleanArgs([])
        self.semaphore = Semaphore(4)
        self.reason = "Backup loaded by greedbot"
        self.id_translator = {}

    async def get_overwrites(self, data: dict[str, dict[str, Optional[bool]]]):
        overwrites: Dict[Member | Role, PermissionOverwrite] = {}
        for union_id, overwrite in data.items():
            try:
                union = await self.guild.fetch_member(int(union_id))
            except NotFound:
                union = get(self.guild.roles, id=self.id_translator.get(int(union_id)))

            if union:
                overwrites[union] = PermissionOverwrite(**overwrite)

        return overwrites

    async def run_tasks(
        self,
        coros: List[Coroutine[Any, Any, None]],
        wait: bool = False,
    ) -> None:
        async def exec(_coro: Coroutine[Any, Any, None]) -> None:
            try:
                await _coro
            except Exception:
                ...
            finally:
                self.semaphore.release()

        tasks: List[asyncio.Task] = []
        for coro in coros:
            await self.semaphore.acquire()
            tasks.append(self.bot.loop.create_task(exec(coro)))

        if wait and tasks:
            await asyncio.wait(tasks)

    async def prepare(self):
        if self.options.roles:
            log.debug("Deleting roles for %s (%s).", self.guild, self.guild.id)
            for role in self.guild.roles:
                if not role.is_assignable():
                    continue

                with suppress(HTTPException):
                    await role.delete(reason=self.reason)

        if self.options.channels:
            log.debug("Deleting channels for %s (%s).", self.guild, self.guild.id)
            for channel in self.guild.channels:
                with suppress(HTTPException):
                    if channel in (
                        self.guild.public_updates_channel,
                        self.guild.rules_channel,
                    ):
                        await self.guild.edit(community=False)

                    await channel.delete(reason=self.reason)

    async def load_settings(self):
        design: dict[str, bytes] = {
            key: b64decode(value)  # type: ignore
            for key, value in self.data["design"].items()
            if value is not None
        }

        rules_channel: TextChannel = self.guild.get_channel(
            self.id_translator.get(self.data["rules_channel"])  # type: ignore
        )
        updates_channel: TextChannel = self.guild.get_channel(
            self.id_translator.get(self.data["community_updates"])  # type: ignore
        )
        community = bool(rules_channel and updates_channel)

        await self.guild.edit(
            name=self.data["name"],
            icon=design.get("icon"),
            banner=design.get("banner") if "BANNER" in self.guild.features else None,
            splash=design.get("splash"),
            discovery_splash=design.get("discovery_splash"),
            afk_channel=self.guild.get_channel(
                self.id_translator.get(self.data["afk_channel"])  # type: ignore
            ),
            afk_timeout=self.data["afk_timeout"],
            system_channel=self.guild.get_channel(
                self.id_translator.get(self.data["system"]["channel"])  # type: ignore
            ),
            system_channel_flags=SystemChannelFlags._from_value(
                self.data["system"]["flags"]
            ),
            verification_level=VerificationLevel(self.data["verification_level"] or 1),
            community=community,
            explicit_content_filter=ContentFilter.all_members,
            rules_channel=rules_channel,
            public_updates_channel=updates_channel,
            reason=self.reason,
        )

        if "COMMUNITY" in self.guild.features:
            for data in self.data["channels"]:
                if data["type"] not in (
                    ChannelType.news.value,
                    ChannelType.forum.value,
                    ChannelType.media.value,
                ):
                    continue

                channel = self.guild.get_channel(self.id_translator.get(data["id"]))  # type: ignore
                if not channel:
                    continue

                with suppress(HTTPException):
                    await channel.edit(type=ChannelType(data["type"]))  # type: ignore

    async def load_roles(self):
        tasks: List[Coroutine[Any, Any, None]] = []
        members: Dict[Member, List[Role]] = {}

        for data in reversed(self.data["roles"]):
            kwargs = {
                "name": data["name"],
                "hoist": data["hoist"],
                "mentionable": data["mentionable"],
                "color": Color(data["color"]),
                "permissions": Permissions(data["permissions"]),
                "reason": self.reason,
            }
            role: Optional[Role] = None
            if data["default"]:
                kwargs.pop("name")
                role = self.guild.default_role

            elif data["premium"]:
                role = self.guild.premium_subscriber_role

            with suppress(HTTPException):
                if role and role.is_assignable():
                    await role.edit(**kwargs)

                elif not role:
                    try:
                        role = await asyncio.wait_for(
                            self.guild.create_role(**kwargs), 10
                        )
                    except asyncio.TimeoutError:
                        break

                    for member_id in data["members"]:
                        member = self.guild.get_member(member_id)
                        if not member:
                            continue

                        if member not in members:
                            members[member] = []

                        members[member].append(role)

                self.id_translator[data["id"]] = role.id

        tasks.extend(
            member.add_roles(*roles, reason=self.reason)
            for member, roles in members.items()
        )
        await self.run_tasks(tasks)

    async def load_categories(self):
        for data in self.data["categories"]:
            with suppress(HTTPException):
                overwrites = await self.get_overwrites(data["overwrites"])
                category = await self.guild.create_category(
                    name=data["name"],
                    overwrites=overwrites,
                    reason=self.reason,
                )

                self.id_translator[data["id"]] = category.id

    async def load_channels(self):
        await self.load_categories()

        for data in self.data["channels"]:
            try:
                overwrites = await self.get_overwrites(data["overwrites"])
                coro = (
                    self.guild.create_voice_channel
                    if data["type"] == ChannelType.voice.value
                    else self.guild.create_stage_channel
                    if data["type"] == ChannelType.stage_voice.value
                    else self.guild.create_text_channel
                )

                kwargs = {
                    "name": data["name"],
                    "overwrites": overwrites,
                    "position": data["position"],
                    "reason": self.reason,
                }
                if (
                    data["category_id"]
                    and (
                        channel := self.guild.get_channel(
                            self.id_translator.get(data["category_id"])  # type: ignore
                        )
                    )
                    and isinstance(channel, CategoryChannel)
                ):
                    kwargs["category"] = channel

                for key, value in (
                    ("topic", data["topic"]),
                    ("nsfw", data["nsfw"]),
                    ("slowmode_delay", data["slowmode_delay"]),
                    (
                        "bitrate",
                        data["bitrate"]
                        if data["bitrate"]
                        and data["bitrate"] <= self.guild.bitrate_limit
                        else None,
                    ),
                    ("user_limit", data["user_limit"]),
                ):
                    if not value:
                        continue

                    kwargs[key] = value

                channel = await coro(**kwargs)
                self.id_translator[data["id"]] = channel.id
            except HTTPException as exc:
                log.error(exc)

    async def load_bans(self):
        tasks = [
            self.guild.ban(Object(int(user_id)), reason=reason)
            for user_id, reason in self.data["bans"].items()
        ]
        await self.run_tasks(tasks)

    async def load(self, loader: Member, options: BooleanArgs):
        self.options = options
        self.reason = f"Backup loaded by {loader}"

        log.info("Loading backup for %s (%s).", self.guild, self.guild.id)
        with capture_time(
            f"Finished loading backup for {self.guild} ({self.guild.id})",
            log,
        ):
            await self.prepare()
            steps = [
                ("roles", self.load_roles),
                ("channels", self.load_channels),
                ("settings", self.load_settings),
                ("bans", self.load_bans),
            ]
            for option, coro in steps:
                if self.options.get(option):
                    log.debug(
                        "Loading %s for %s (%s).", option, self.guild, self.guild.id
                    )
                    await coro()
