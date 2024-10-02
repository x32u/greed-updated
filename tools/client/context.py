from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import (
    TYPE_CHECKING,
    Any,
    List,
    Literal,
    Optional,
    Sequence,
    Type,
    cast,
    TypeVar,
)
from aiomisc import PeriodicCallback
import config
import discord
from aiohttp import ClientSession
from cashews import cache
from discord import (
    ButtonStyle,
    Colour,
    File,
    Guild,
    HTTPException,
    Member,
    Message,
    NotFound,
    TextChannel,
    Thread,
    VoiceChannel,
)
from discord.context_managers import Typing as DefaultTyping
from discord.ext.commands import Command
from discord.ext.commands import Context as OriginalContext
from discord.ext.commands import UserInputError
from discord.types.embed import EmbedType
from discord.ui import button
from discord.utils import MISSING, cached_property, get
from pydantic import BaseConfig, BaseModel
from typing_extensions import Self
from xxhash import xxh32_hexdigest


from tools import View, quietly_delete
from tools.client.database import Database, Settings
from tools.client.redis import Redis


if TYPE_CHECKING:
    from main import greedbot
    from types import TracebackType

BE = TypeVar("BE", bound=BaseException)


class ReskinConfig(BaseModel):
    member: Member
    username: Optional[str]
    avatar_url: Optional[str]

    @classmethod
    def key(cls, member: Member) -> str:
        return xxh32_hexdigest(f"reskin.config:{member.id}")

    @classmethod
    async def revalidate(cls, bot: greedbot, member: Member) -> Optional[Self]:
        """
        Revalidate the reskin for a member.
        This will update the cache in redis.
        """

        key = cls.key(member)
        await bot.redis.delete(key)

        record = await bot.db.fetchrow(
            """
            SELECT *
            FROM reskin.config
            WHERE user_id = $1
            """,
            member.id,
        )
        if not record:
            return

        settings = cls(**record, member=member)
        await bot.redis.set(key, settings.dict(exclude={"member"}))
        return settings

    @classmethod
    async def fetch(cls, bot: greedbot, member: Member) -> Optional[Self]:
        """
        Fetch the reskin for a member.
        This will cache the settings in redis.
        """

        key = cls.key(member)
        cached = cast(
            Optional[dict],
            await bot.redis.get(key),
        )
        if cached:
            return cls(**cached, member=member)

        record = await bot.db.fetchrow(
            """
            SELECT *
            FROM reskin.config
            WHERE user_id = $1
            """,
            member.id,
        )
        if not record:
            return

        settings = cls(**record, member=member)
        await bot.redis.set(key, settings.dict(exclude={"member"}))
        return settings

    class Config(BaseConfig):
        arbitrary_types_allowed = True


class Confirmation(View):
    value: Optional[bool]

    def __init__(self, ctx: Context, *, timeout: Optional[int] = 60):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.value = None

    @button(label="Approve", style=ButtonStyle.green)
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = True
        self.stop()

    @button(label="Decline", style=ButtonStyle.danger)
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = False
        self.stop()


class Typing(DefaultTyping):
    ctx: Context

    def __init__(self, ctx: Context):
        super().__init__(ctx.channel)
        self.ctx = ctx

    async def do_typing(self) -> None:
        if self.ctx.settings.reskin:
            return

        return await super().do_typing()


class Loading:
    callback: Optional[PeriodicCallback]
    ctx: Context
    channel: VoiceChannel | TextChannel | Thread

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.channel = ctx.channel
        self.callback = None

    @property
    def redis(self) -> Redis:
        return self.ctx.bot.redis

    @property
    def key(self) -> str:
        return xxh32_hexdigest(f"loader:{self.channel.id}")

    async def locked(self) -> bool:
        if await self.redis.exists(self.key):
            return True

        await self.redis.set(self.key, 1, ex=30)
        return False

    async def task(self) -> None:
        if not self.ctx.response:
            return

        value = self.ctx.response.embeds[0].description  # type: ignore
        if not value:
            return

        value = value.replace("", "")
        if not value.endswith("..."):
            value += "."
        else:
            value = value.rstrip(".")

        await self.ctx.neutral(value, patch=self.ctx.response)

    async def __aenter__(self) -> None:
        if await self.locked():
            return

        self.callback = PeriodicCallback(self.task)
        self.callback.start(10, delay=2)

    async def __aexit__(
        self,
        exc_type: Optional[Type[BE]],
        exc: Optional[BE],
        traceback: Optional[TracebackType],
    ) -> None:
        await self.redis.delete(self.key)
        if self.callback:
            self.callback.stop()


class Context(OriginalContext):
    bot: "greedbot"
    guild: Guild
    author: Member
    channel: VoiceChannel | TextChannel | Thread
    command: Command[Any, ..., Any]
    settings: Settings
    response: Optional[Message] = None

    @property
    def session(self) -> ClientSession:
        return self.bot.session

    @property
    def db(self) -> Database:
        return self.bot.database

    @cached_property
    def replied_message(self) -> Optional[Message]:
        reference = self.message.reference
        if reference and isinstance(reference.resolved, Message):
            return reference.resolved

        return None

    @property
    def color(self) -> Colour:
        return Colour.from_rgb(config.Colors.greed)

    def typing(self) -> Typing:
        return Typing(self)

    def loading(self, *args: str, **kwargs) -> Loading:
        if args:
            self.bot.loop.create_task(self.neutral(*args))

        return Loading(self)

    async def add_check(self) -> None:
        """
        Adds a ✅ reaction to the message.
        """

        return await self.message.add_reaction("✅")

    async def send(self, *args, **kwargs) -> Message:
        if kwargs.pop("no_reference", False):
            reference = None
        else:
            reference = kwargs.pop("reference", self.message)

        patch = cast(
            Optional[Message],
            kwargs.pop("patch", None),
        )

        embed = cast(
            Optional[Embed],
            kwargs.get("embed"),
        )
        if embed and not embed.color:
            embed.color = self.color

        if args:
            kwargs["content"] = args[0]
            args = ()

        if kwargs.get("content") and len(str(kwargs["content"])) > 2000:
            kwargs["file"] = File(
                BytesIO(str(kwargs["content"]).encode("utf-8")),
                filename="message.txt",
            )
            kwargs["content"] = None

        if file := kwargs.pop("file", None):
            kwargs["files"] = [file]

        if kwargs.get("view") is None:
            kwargs.pop("view", None)

        if self.settings.reskin:
            reskin = await ReskinConfig.fetch(self.bot, self.author)
            if reskin:
                webhook = await self.reskin_webhook()
                if webhook:
                    delete_after: Optional[int] = kwargs.pop("delete_after", None)
                    for item in ("stickers", "reference"):
                        kwargs.pop(item, None)

                    try:
                        if patch:
                            self.response = await webhook.edit_message(
                                message_id=patch.id,
                                **kwargs,
                            )

                        else:
                            kwargs["username"] = reskin.username
                            kwargs["avatar_url"] = reskin.avatar_url
                            kwargs["wait"] = True
                            self.response = await webhook.send(
                                *args,
                                **kwargs,
                            )

                        if delete_after:
                            await self.response.delete(delay=delete_after)

                        return self.response

                    except NotFound:
                        await self.bot.db.execute(
                            """
                            DELETE FROM reskin.webhook
                            WHERE guild_id = $1
                            AND channel_id = $2
                            """,
                            self.guild.id,
                            self.channel.id,
                        )
                        await cache.delete(
                            f"reskin:webhook:{self.guild.id}:{self.channel.id}"
                        )

        if patch:
            self.response = await patch.edit(**kwargs)
        else:
            if reference:
                kwargs["reference"] = reference

            try:
                self.response = await super().send(*args, **kwargs)
            except HTTPException:
                kwargs.pop("reference", None)
                self.response = await super().send(*args, **kwargs)

        return self.response

    async def reply(self, *args, **kwargs) -> Message:
        return await self.send(*args, **kwargs)

    async def neutral(
        self,
        *args: str,
        **kwargs,
    ) -> Message:
        """
        Send a neutral embed.
        """

        embed = Embed(
            description="\n".join(
                ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                for index, arg in enumerate(args)
            ),
            color=kwargs.pop("color", config.Colors.greed),
        )
        return await self.send(embed=embed, **kwargs)

    async def approve(
        self,
        *args: str,
        **kwargs,
    ) -> Message:
        """
        Send a success embed.
        """

        embed = Embed(
            description="✅ "+"\n".join(
                ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                for index, arg in enumerate(args)
            ),
            color=kwargs.pop("color", config.Colors.approve),
        )
        return await self.send(embed=embed, **kwargs)

    async def warn(
        self,
        *args: str,
        **kwargs,
    ) -> Message:
        """
        Send an error embed.
        """

        embed = Embed(
            description="⚠️ "+"\n".join(
                ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                for index, arg in enumerate(args)
            ),
            color=kwargs.pop("color", config.Colors.warning),
        )
        return await self.send(embed=embed, **kwargs)

    async def prompt(
        self,
        *args: str,
        timeout: int = 60,
        delete_after: bool = True,
    ) -> Literal[True]:
        """
        An interactive reaction confirmation dialog.

        Raises UserInputError if the user denies the prompt.
        """

        key = xxh32_hexdigest(f"prompt:{self.author.id}:{self.command.qualified_name}")
        async with self.bot.redis.get_lock(key):
            embed = Embed(
                description="\n".join(
                    ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                    for index, arg in enumerate(args)
                ),
                color=config.Colors.greed,
            )
            view = Confirmation(self, timeout=timeout)

            try:
                message = await self.send(embed=embed, view=view)
            except HTTPException as exc:
                raise UserInputError("Failed to send prompt message!") from exc

            await view.wait()
            if delete_after:
                await quietly_delete(message)

            if view.value is True:
                return True

            raise UserInputError("Confirmation prompt wasn't approved!")

    @cache(ttl="2h", prefix="reskin:webhook", key="{self.guild.id}:{self.channel.id}")
    async def reskin_webhook(self) -> Optional[discord.Webhook]:
        if not isinstance(self.channel, TextChannel):
            return

        webhook_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT webhook_id
                FROM reskin.webhook
                WHERE guild_id = $1
                AND channel_id = $2
                """,
                self.guild.id,
                self.channel.id,
            ),
        )
        if not webhook_id:
            return

        webhooks = await self.channel.webhooks()
        webhook = get(webhooks, id=webhook_id)
        if webhook:
            return webhook

        cache.invalidate(self.reskin_webhook)  # type: ignore
        await self.bot.db.execute(
            """
            DELETE FROM reskin.webhook
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            self.guild.id,
            self.channel.id,
        )





class Embed(discord.Embed):
    def __init__(
        self,
        value: Optional[str] = None,
        *,
        colour: int | Colour | None = None,
        color: int | Colour | None = None,
        title: Any | None = None,
        type: EmbedType = "rich",
        url: Any | None = None,
        description: Any | None = None,
        timestamp: datetime | None = None,
    ):
        description = description or value
        super().__init__(
            colour=colour,
            color=color or config.Colors.greed,
            title=title,
            type=type,
            url=url,
            description=description[:4096] if description else None,
            timestamp=timestamp,
        )


discord.Embed = Embed
