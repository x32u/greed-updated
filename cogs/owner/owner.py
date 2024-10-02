import asyncio
from contextlib import suppress
import gc
from copy import copy
from importlib import import_module, reload
from io import BytesIO, StringIO
from itertools import chain
from logging import getLogger
from pathlib import Path
from random import uniform
from traceback import format_exception
from typing import Annotated, Dict, List, Optional, TypedDict, cast
from aiomisc import PeriodicCallback
from colorama import Fore, Style

import stackprinter
from cashews import cache
from discord import (
    AuditLogEntry,
    Embed,
    File,
    Guild,
    HTTPException,
    Invite,
    Member,
    Message,
    Permissions,
    TextChannel,
    User,
)
from discord.ext.commands import Cog, command, group, parameter, flag
from discord.ext.tasks import loop
from discord.utils import oauth_url
from humanfriendly import format_timespan
from jishaku.modules import ExtensionConverter
from datetime import datetime

from main import greedbot
from tools.client import Context, FlagConverter
from tools.conversion import PartialAttachment, StrictMember
from tools.formatter import codeblock

log = getLogger("greedbot/owner")


class PaymentFlags(FlagConverter):
    method: str = flag(
        default="CashApp",
        aliases=["platform"],
        description="The payment method used.",
    )
    amount: int = flag(
        default=8,
        aliases=["price"],
        description="The amount paid.",
    )


class PaymentRecord(TypedDict):
    guild_id: int
    customer_id: int
    method: str
    amount: int
    transfers: int
    paid_at: datetime


class Owner(
    Cog,
    command_attrs=dict(hidden=True),
):
    def __init__(self, bot: greedbot):
        self.bot = bot
        self.check_cb = PeriodicCallback(self.check_whitelist)

    async def cog_check(self, ctx: Context) -> bool:
        return ctx.author.id in self.bot.owner_ids

    # async def cog_load(self) -> None:
    #     self.check_cb.start(34, delay=2)

    # async def cog_unload(self) -> None:
    #     self.check_cb.stop()

    async def check_whitelist(self) -> None:
        await self.bot.wait_until_ready()
        records = await self.bot.db.fetch(
            """
            SELECT guild_id
            FROM payment
            """
        )
        if len(records) < len(self.bot.guilds) * 0.5:
            return log.critical(
                f"We only have {len(records)} whitelisted servers out of {len(self.bot.guilds)}!"
            )

        for guild in self.bot.guilds:
            reason: Optional[str] = None
            if guild.id not in (record["guild_id"] for record in records):
                reason = "missing payment"

            elif not guild.me:
                log.warning(
                    f"Guild {Fore.LIGHTYELLOW_EX}{guild}{Fore.RESET} ({Fore.RED}{guild.id}{Fore.RESET}) was not chunked!"
                )
                continue

            if reason:
                await asyncio.sleep(uniform(0.5, 1.0))
                log.warning(
                    f"Leaving {Fore.LIGHTYELLOW_EX}{guild}{Fore.RESET} ({Fore.RED}{guild.id}{Fore.RESET}) {Fore.LIGHTWHITE_EX}{Style.DIM}{reason}{Fore.RESET}{Style.NORMAL}."
                )
                with suppress(HTTPException):
                    await guild.leave()

    @Cog.listener()
    async def on_audit_log_entry_ban(self, entry: AuditLogEntry):
        if (
            not isinstance(entry.target, (Member, User))
            or entry.target.id not in self.bot.owner_ids
        ):
            return

        await entry.guild.unban(entry.target)
        if entry.guild.vanity_url:
            await entry.target.send(entry.guild.vanity_url)

    @command()
    async def shutdown(self, ctx: Context) -> None:
        """
        Shutdown the bot.
        """

        await ctx.add_check()
        await self.bot.close()

    @command(aliases=["trace"])
    async def traceback(self, ctx: Context, error_code: Optional[str]) -> Message:
        if error_code is None:
            if not self.bot.traceback:
                return await ctx.warn("No traceback has been raised!")

            error_code = list(self.bot.traceback.keys())[-1]

        exc = self.bot.traceback.get(error_code)
        if not exc:
            return await ctx.warn("No traceback has been raised with that error code!")

        await ctx.add_check()
        fmt = stackprinter.format(exc)

        if len(fmt) > 1900:
            return await ctx.author.send(
                file=File(
                    StringIO(fmt),  # type: ignore
                    filename="error.py",
                ),
            )

        return await ctx.author.send(f"```py\n{fmt}\n```")

    @group(invoke_without_command=True)
    async def sudo(
        self,
        ctx: Context,
        channel: Optional[TextChannel],
        target: Optional[
            Annotated[
                Member,
                StrictMember,
            ]
        ],
        *,
        command: str,
    ) -> None:
        """
        Run a command as another user.
        """

        message = copy(ctx.message)
        message.channel = channel or ctx.channel
        message.author = target or ctx.guild.owner or ctx.author
        message.content = f"{ctx.prefix or ctx.settings.prefixes[0]}{command}"

        new_ctx = await self.bot.get_context(message, cls=type(ctx))
        return await self.bot.invoke(new_ctx)

    @sudo.command(name="send", aliases=["dm"])
    async def sudo_send(
        self,
        ctx: Context,
        target: Annotated[
            Member,
            StrictMember,
        ]
        | User,
        *,
        content: str,
    ) -> Optional[Message]:
        """
        Send a message to a user.
        """

        try:
            await target.send(content, delete_after=15)
        except HTTPException as exc:
            return await ctx.warn("Failed to send the message!", codeblock(exc.text))

        return await ctx.add_check()

    @sudo.command(name="collect")
    async def sudo_collect(self, ctx: Context) -> None:
        """
        Flush the cache.
        """

        gc.collect()
        cached_keys = [key[0] async for key in cache.get_match("*")]
        for key in cached_keys:
            await cache.delete(key)

        return await ctx.add_check()

    @sudo.command(name="avatar", aliases=["pfp"])
    async def sudo_avatar(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Update the bot's avatar.
        """

        if not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")

        await self.bot.user.edit(avatar=attachment.buffer)
        return await ctx.reply("done")

    @sudo.command(name="reload")
    async def sudo_reload(self, ctx: Context, *, module: str) -> Optional[Message]:
        """
        Reload a dependency.
        """

        try:
            reload(import_module(module))
        except ModuleNotFoundError:
            return await ctx.warn("That module does not exist!")

        return await ctx.add_check()

    @sudo.command(name="emojis", aliases=["emotes"])
    async def sudo_emojis(self, ctx: Context) -> Message:
        """
        Load all necessary emojis.
        """

        path = Path("assets")
        result: Dict[str, List[str]] = {}
        for category in ("badges", "paginator", "audio"):
            result[category] = []
            for file in path.glob(f"{category}/*.jpg"):
                emoji = await ctx.guild.create_custom_emoji(
                    name=file.stem, image=file.read_bytes()
                )
                result[category].append(f'{file.stem.upper()}: str = "{emoji}"')

        return await ctx.reply(
            codeblock(
                "\n".join(
                    f"class {category.upper()}:\n"
                    + "\n".join(f"    {name}" for name in names)
                    for category, names in result.items()
                )
            )
        )

    @sudo.command(name="x")
    async def sudo_x(
        self,
        ctx: Context,
        *,
        guild: Guild,
    ) -> None:
        async with ctx.typing():
            for channel in guild.text_channels:
                result: List[str] = []
                async for message in channel.history(limit=500, oldest_first=True):
                    result.append(
                        f"[{message.created_at:%d/%m/%Y - %H:%M}] {message.author} ({message.author.id}): {message.system_content}"
                    )

                if not result:
                    continue

                await ctx.send(
                    file=File(
                        BytesIO("\n".join(result).encode()),
                        filename=f"{channel.name}.txt",
                    ),
                )

        return await ctx.add_check()

    @command(aliases=["bl"])
    async def blacklist(
        self,
        ctx: Context,
        user: Member | User,
        *,
        information: Optional[str],
    ) -> Message:
        """
        Blacklist a user from using the bot.
        """

        blacklisted = await self.bot.db.execute(
            """
            DELETE FROM blacklist
            WHERE user_id = $1
            """,
            user.id,
        )
        if blacklisted == "DELETE 0":
            await self.bot.db.execute(
                """
                INSERT INTO blacklist (user_id, information)
                VALUES ($1, $2)
                """,
                user.id,
                information,
            )
            for guild in user.mutual_guilds:
                if guild.owner_id == user.id:
                    await guild.leave()

            return await ctx.approve(
                f"No longer allowing **{user}** to use **{self.bot.user}**"
            )

        return await ctx.approve(
            f"Allowing **{user}** to use **{self.bot.user}** again"
        )

    @command(aliases=["rl"])
    async def reload(
        self,
        ctx: Context,
        *extensions: Annotated[str, ExtensionConverter],
    ) -> Message:
        result: List[str] = []

        for extension in chain(*extensions):
            extension = "cogs." + extension.replace("cogs.", "")
            method, icon = (
                (
                    self.bot.reload_extension,
                    "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}",
                )
                if extension in self.bot.extensions
                else (self.bot.load_extension, "\N{INBOX TRAY}")
            )

            try:
                await method(extension)
            except Exception as exc:
                traceback_data = "".join(
                    format_exception(type(exc), exc, exc.__traceback__, 1)
                )

                result.append(
                    f"{icon}\N{WARNING SIGN} `{extension}`\n```py\n{traceback_data}\n```"
                )
            else:
                result.append(f"{icon} `{extension}`")

        return await ctx.reply("\n".join(result))

    @command(aliases=["debug"])
    async def logger(self, ctx: Context, module: str, level: str = "DEBUG") -> None:
        getLogger(f"greedbot/{module}").setLevel(level.upper())
        return await ctx.add_check()

    @group(
        aliases=["guild", "payment"],
        invoke_without_command=True,
    )
    async def server(self, ctx: Context) -> Message:
        """
        Manage server payments.
        """

        return await ctx.send_help(ctx.command)

    @server.command(
        name="add",
        aliases=["allow"],
    )
    async def server_add(
        self,
        ctx: Context,
        guild_id: int | Invite,
        user: Member | User,
        *,
        flags: PaymentFlags,
    ) -> Message:
        """
        Add a server payment.
        """

        guild_name: Optional[str] = None
        if isinstance(guild_id, Invite):
            if not guild_id.guild:
                return await ctx.warn("That invite is invalid!")

            guild_name = getattr(guild_id.guild, "name", None)
            guild_id = guild_id.guild.id

        customer_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                INSERT INTO payment (
                    guild_id,
                    customer_id,
                    method,
                    amount
                ) VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id)
                DO NOTHING
                RETURNING customer_id
                """,
                guild_id,
                user.id,
                flags.method,
                flags.amount,
            ),
        )
        if not customer_id:
            return await ctx.warn("That server has already been whitelisted!")

        notified = False
        invite_url = oauth_url(
            self.bot.user.id,
            permissions=Permissions(permissions=8),
        )

        with suppress(HTTPException):
            await user.send(invite_url)
            notified = True

        return await ctx.approve(
            f"Added a payment of `${flags.amount} USD` via **{flags.method}** for {f'**{guild_name}**' if guild_name else f'`{guild_id}`'}"
            + (" (failed to notify)" if not notified else "")
        )

    @server.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    async def server_remove(
        self,
        ctx: Context,
        guild_id: int | Invite,
    ) -> Message:
        """
        Remove a server payment.
        """

        if isinstance(guild_id, Invite):
            if not guild_id.guild:
                return await ctx.warn("That invite is invalid!")

            guild_id = guild_id.guild.id

        result = await self.bot.db.execute(
            """
            DELETE FROM payment
            WHERE guild_id = $1
            """,
            guild_id,
        )
        if result == "DELETE 0":
            return await ctx.warn("That server has not been whitelisted!")

        guild = self.bot.get_guild(guild_id)
        if guild:
            with suppress(HTTPException):
                await guild.leave()

        return await ctx.approve(
            f"Removed the payment for {f'**{guild.name}**' if guild else f'`{guild_id}`'}"
        )

    @server.command(
        name="migrate",
        aliases=["merge"],
    )
    async def server_migrate(
        self,
        ctx: Context,
        old_user: Member | User,
        new_user: Member | User,
    ) -> Message:
        """
        Migrate server payments from one user to another.
        """

        result = await self.bot.db.execute(
            """
            UPDATE payment
            SET customer_id = $2
            WHERE customer_id = $1
            """,
            old_user.id,
            new_user.id,
        )
        if result.endswith("0"):
            return await ctx.warn(
                f"**{old_user}** does not have any server payments to migrate!"
            )

        return await ctx.approve(
            f"Migrated {result.split()[1]} server payments from **{old_user}** to **{new_user}**"
        )

    @server.command(
        name="transfer",
        aliases=["swap"],
    )
    async def server_transfer(
        self,
        ctx: Context,
        customer: Member | User,
        old_guild_id: int,
        new_guild_id: int,
    ) -> Message:
        """
        Transfer a payment to a different server.
        """

        result = await self.bot.db.execute(
            """
            UPDATE payment
            SET
                guild_id = $3,
                transfers = transfers + 1
            WHERE guild_id = $2
            AND customer_id = $1
            AND NOT method = 'Unknown'
            """,
            customer.id,
            old_guild_id,
            new_guild_id,
        )
        if result.endswith("0"):
            return await ctx.warn(
                f"**{customer}** does not have a payment for that server!"
            )

        old_guild = self.bot.get_guild(old_guild_id)
        if old_guild:
            with suppress(HTTPException):
                await old_guild.leave()

        return await ctx.approve(
            f"Transferred **{customer}**'s payment from `{old_guild_id}` to `{new_guild_id}`"
        )

    @server.command(
        name="view",
        aliases=["check"],
    )
    async def server_view(
        self,
        ctx: Context,
        guild_id: int | Invite,
    ) -> Message:
        """
        View a server payment.
        """

        if isinstance(guild_id, Invite):
            if not guild_id.guild:
                return await ctx.warn("That invite is invalid!")

            guild_id = guild_id.guild.id

        record = cast(
            Optional[PaymentRecord],
            await self.bot.db.fetchrow(
                """
                SELECT *
                FROM payment
                WHERE guild_id = $1
                """,
                guild_id,
            ),
        )
        if not record:
            return await ctx.warn("That server has not been whitelisted!")

        embed = Embed(
            title="Payment Information",
            timestamp=record["paid_at"],
        )
        guild = self.bot.get_guild(guild_id)
        if guild:
            embed.set_author(
                name=guild.name,
                icon_url=guild.icon,
                url=guild.vanity_url,
            )

        customer = await self.bot.get_or_fetch_user(record["customer_id"])
        embed.add_field(
            name="**Customer**",
            value=f"{customer} (`{customer.id}`)",
        )
        embed.add_field(
            name="**Payment Method**",
            value=f"**{record['method']}**\n> `${record['amount']} USD`",
            inline=False,
        )
        embed.set_footer(text="Payment sent")

        return await ctx.send(embed=embed)
