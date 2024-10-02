from logging import getLogger
from sys import getsizeof
from typing import TYPE_CHECKING, List, Optional, Tuple, TypedDict, cast

from discord import (
    Attachment,
    DeletedReferencedMessage,
    Embed,
    File,
    Guild,
    HTTPException,
    Member,
    Message,
    PartialMessage,
    RawReactionActionEvent,
    RawReactionClearEmojiEvent,
    StickerItem,
    TextChannel,
    Thread,
)
from discord.abc import GuildChannel
from discord.ext.commands import Cog, Range, flag, group, has_permissions
from discord.utils import find

from main import greedbot
from tools import quietly_delete
from tools.client import Context, FlagConverter
from tools.conversion import Status
from tools.formatter import plural, shorten
from tools.paginator import Paginator

log = getLogger("greedbot/star")


class StarboardRecord(TypedDict):
    guild_id: int
    channel_id: int
    self_star: bool
    threshold: int
    emoji: str


class StarboardConfig:
    bot: greedbot
    guild_id: int
    channel_id: int
    self_star: bool
    threshold: int
    emoji: str

    def __init__(self, *, bot: greedbot, record: StarboardRecord):
        self.bot = bot
        self.guild_id = record["guild_id"]
        self.channel_id = record["channel_id"]
        self.self_star = record["self_star"]
        self.threshold = record["threshold"]
        self.emoji = record["emoji"]

    @property
    def guild(self) -> Guild:
        return self.bot.get_guild(self.guild_id)  # type: ignore

    @property
    def channel(self) -> Optional[TextChannel]:
        return self.guild and self.guild.get_channel(self.channel_id)  # type: ignore

    async def build_entry(
        self,
        message: Message,
        stars: int,
    ) -> Tuple[
        str,
        Embed,
        List[File],
    ]:
        channel = cast(TextChannel, message.channel)
        author = message.author

        embed = Embed(color=0x2B2D31)
        if message.embeds:
            embed = cast(Embed, message.embeds[0])

        embed.timestamp = message.created_at
        embed.set_author(
            name=author.display_name,
            icon_url=author.display_avatar,
            url=message.jump_url,
        )

        if embed.type in ("image", "gifv"):
            embed.set_image(url=embed.thumbnail.url)
            embed.set_thumbnail(url=None)

        if embed.description and message.system_content:
            embed.description = shorten(
                f"{message.system_content}\n\n{embed.description}", 4096
            )
        else:
            embed.description = shorten(message.system_content, 4096)

        files: List[File] = []
        if message.attachments and message.guild:
            attachment = cast(Attachment, message.attachments[0])

            if attachment.content_type and attachment.content_type.startswith("image"):
                embed.set_image(url=attachment.url)
            else:
                for attachment in message.attachments:
                    file = await attachment.to_file()
                    if not getsizeof(file.fp) > message.guild.filesize_limit:
                        files.append(file)

                    if (
                        sum(getsizeof(file.fp) for file in files)
                        > message.guild.filesize_limit
                    ):
                        files.pop()
                        break

                embed.add_field(
                    name=f"**Attachment{'s' if len(message.attachments) != 1 else ''}**",
                    value="\n".join(
                        f"[{attachment.filename}]({attachment.url})"
                        for attachment in message.attachments
                    ),
                    inline=False,
                )

        elif message.stickers:
            sticker = cast(StickerItem, message.stickers[0])
            embed.set_image(url=sticker.url)

        if (
            (reference := message.reference)
            and (resolved := reference.resolved)
            and not isinstance(resolved, DeletedReferencedMessage)
        ):
            embed.add_field(
                name=f"**Replying to {resolved.author.display_name}**",
                value=(
                    f"[{shorten(resolved.system_content, 950)}]({resolved.jump_url})"
                    if resolved.system_content
                    else f"> [Jump to replied message]({resolved.jump_url})"
                ),
                inline=False,
            )

        embed.add_field(
            name=f"**#{channel}**",
            value=f"[Jump to message]({message.jump_url})",
            inline=False,
        )

        return f"{self.emoji} **#{stars:,}**", embed, files

    async def get_star(self, message: Message) -> Optional[PartialMessage]:
        if not self.channel:
            return

        star_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT star_id
                FROM starboard_entry
                WHERE guild_id = $1
                AND channel_id = $2
                AND message_id = $3
                AND emoji = $4
                """,
                self.guild_id,
                message.channel.id,
                message.id,
                self.emoji,
            ),
        )
        if star_id:
            return self.channel.get_partial_message(star_id)

    async def save_star(
        self,
        stars: int,
        message: Message,
    ) -> Optional[Message]:
        if not self.channel:
            return

        content, embed, files = await self.build_entry(message, stars)

        star_message = await self.get_star(message)
        if star_message:
            try:
                star_message = await star_message.edit(content=content)
            except HTTPException:
                pass
            else:
                return star_message

        star_message = await self.channel.send(
            content=content,
            embed=embed,
            files=files,
        )

        await self.bot.db.execute(
            """
            INSERT INTO starboard_entry (
                guild_id,
                star_id,
                channel_id,
                message_id,
                emoji
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id, channel_id, message_id, emoji)
            DO UPDATE SET star_id = EXCLUDED.star_id
            """,
            self.guild_id,
            star_message.id,
            message.channel.id,
            message.id,
            self.emoji,
        )

        log.debug(
            "Saved entry for %s with %s in %s/%s (%s) to %s (%s).",
            message.id,
            format(plural(stars), "star"),
            message.channel,
            message.guild,
            self.guild.id,
            self.channel,
            self.channel.id,
        )
        return star_message


if TYPE_CHECKING:

    class CompleteStarboardConfig(StarboardConfig):
        guild: Guild
        channel: TextChannel


class Flags(FlagConverter):
    threshold: Range[int, 1, 12] = flag(
        aliases=["limit"],
        description="The threshold before archival.",
        default=3,
    )
    self_star: Status = flag(
        aliases=["self"],
        description="Allow the author to star their own message.",
        default=True,
    )


class Starboard(Cog):
    """
    A starboard to upvote posts obviously.
    """

    def __init__(self, bot: greedbot):
        self.bot: greedbot = bot

    @group(
        aliases=["star", "board", "sb"],
        invoke_without_command=True,
    )
    @has_permissions(manage_guild=True)
    async def starboard(self, ctx: Context) -> Message:
        """
        Archive important messages.
        """

        return await ctx.send_help(ctx.command)

    @starboard.command(
        name="add",
        aliases=["create"],
    )
    @has_permissions(manage_guild=True)
    async def starboard_add(
        self,
        ctx: Context,
        channel: TextChannel,
        emoji: str,
        *,
        flags: Flags,
    ) -> Message:
        """
        Add a new starboard to a channel.
        """

        try:
            await ctx.message.add_reaction(emoji)
        except (HTTPException, TypeError):
            return await ctx.warn(
                f"I'm not capable of using **{emoji}**, try using an emoji from this server!"
            )

        await self.bot.db.execute(
            """
            INSERT INTO starboard VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id, emoji) DO UPDATE
            SET
                channel_id = EXCLUDED.channel_id,
                self_star = EXCLUDED.self_star,
                threshold = EXCLUDED.threshold
            """,
            ctx.guild.id,
            channel.id,
            flags.self_star,
            flags.threshold,
            emoji,
        )
        return await ctx.approve(f"Added a starboard for {emoji} in {channel.mention}")

    @starboard.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    @has_permissions(manage_guild=True)
    async def starboard_remove(
        self,
        ctx: Context,
        channel: TextChannel,
        emoji: str,
    ) -> Message:
        """
        Remove an existing starboard.
        """

        result = await self.bot.db.execute(
            """
            DELETE FROM starboard
            WHERE guild_id = $1
            AND channel_id = $2
            AND emoji = $3
            """,
            ctx.guild.id,
            channel.id,
            emoji,
        )
        if result == "DELETE 0":
            return await ctx.warn(
                f"A starboard for **{emoji}** in {channel.mention} doesn't exist!"
            )

        return await ctx.approve(
            f"Removed the starboard for {emoji} in {channel.mention}"
        )

    @starboard.command(
        name="clear",
        aliases=["clean", "reset"],
    )
    @has_permissions(manage_guild=True)
    async def starboard_clear(self, ctx: Context) -> Message:
        """
        Remove all starboards.
        """

        await ctx.prompt(
            "Are you sure you want to remove all starboards?",
        )

        result = await self.bot.db.execute(
            """
            DELETE FROM starboard
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if result == "DELETE 0":
            return await ctx.warn("No starboards exist for this server!")

        return await ctx.approve(
            f"Successfully  removed {plural(result, md='`'):starboard}"
        )

    @starboard.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_guild=True)
    async def starboard_list(self, ctx: Context) -> Message:
        """
        View all starboards.
        """

        channels = [
            f"{channel.mention} - **{record['emoji']}** (threshold: `{record['threshold']}`, author: `{record['self_star']}`)"
            for record in await self.bot.db.fetch(
                """
                SELECT channel_id, emoji, threshold, self_star
                FROM starboard
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (channel := ctx.guild.get_channel(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No starboards exist for this server!")

        paginator = Paginator(
            ctx,
            entries=channels,
            embed=Embed(
                title="Starboards",
            ),
        )
        return await paginator.start()

    async def get_starboard(
        self,
        guild: Guild,
        emoji: str,
    ) -> Optional[StarboardConfig]:
        record = await self.bot.db.fetchrow(
            """
            SELECT * FROM starboard
            WHERE guild_id = $1
            AND emoji = $2
            """,
            guild.id,
            emoji,
        )
        if record:
            return StarboardConfig(bot=self.bot, record=record)

    async def reaction_action(
        self,
        fmt: str,
        payload: RawReactionActionEvent,
    ):
        guild = payload.guild_id and self.bot.get_guild(payload.guild_id)
        if not guild or guild.me.is_timed_out():
            return

        channel = guild.get_channel_or_thread(payload.channel_id)
        if not isinstance(channel, (TextChannel, Thread)):
            return

        starboard = await self.get_starboard(guild, str(payload.emoji))
        if (
            not starboard
            or not starboard.channel
            or starboard.channel == channel
            or not starboard.channel.permissions_for(guild.me).send_messages
            or not starboard.channel.permissions_for(guild.me).embed_links
        ):
            return

        member = guild.get_member(payload.user_id)
        if not member:
            return

        message = self.bot.get_message(payload.message_id)
        if not message:
            try:
                message = await channel.fetch_message(payload.message_id)
            except HTTPException:
                return

        lock = self.bot.redis.get_lock(f"starboard:{guild.id}")
        async with lock:
            method = getattr(self, f"{fmt}_message")

            await method(
                starboard,
                channel=channel,
                member=member,
                message=message,
            )

    async def star_message(
        self,
        starboard: "CompleteStarboardConfig",
        *,
        channel: TextChannel | Thread,
        member: Member,
        message: Message,
    ):
        if channel.is_nsfw() and not starboard.channel.is_nsfw():
            return

        if message.author.id == member.id and not starboard.self_star:
            return

        reaction = find(
            lambda reaction: str(reaction.emoji) == starboard.emoji,
            message.reactions,
        )
        if not reaction or reaction.count < starboard.threshold:
            return

        await starboard.save_star(
            stars=reaction.count,
            message=message,
        )

    async def unstar_message(
        self,
        starboard: "CompleteStarboardConfig",
        *,
        channel: TextChannel | Thread,
        member: Member,
        message: Message,
    ):
        star_message = await starboard.get_star(message)
        if not star_message:
            return

        reaction = find(
            lambda reaction: str(reaction.emoji) == starboard.emoji,
            message.reactions,
        )
        if not reaction or reaction.count < starboard.threshold:
            await quietly_delete(star_message)

            await self.bot.db.execute(
                """
                DELETE FROM starboard_entry
                WHERE star_id = $1
                """,
                star_message.id,
            )
            return

        await starboard.save_star(
            stars=reaction.count,
            message=message,
        )

    @Cog.listener("on_guild_channel_delete")
    async def starboard_channel_delete(self, channel: GuildChannel):
        await self.bot.db.execute(
            """
            DELETE FROM starboard
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            channel.guild.id,
            channel.id,
        )
        await self.bot.db.execute(
            """
            DELETE FROM starboard_entry
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            channel.guild.id,
            channel.id,
        )

    @Cog.listener("on_raw_reaction_clear")
    async def starboard_reaction_clear(self, payload: RawReactionClearEmojiEvent):
        entries = await self.bot.db.fetch(
            """
            DELETE FROM starboard_entry
            WHERE guild_id = $1
            AND channel_id = $2
            AND message_id = $3
            RETURNING star_id, emoji
            """,
            payload.guild_id,
            payload.channel_id,
            payload.message_id,
        )
        if not entries:
            return

        for entry in entries:
            if not payload.guild_id or not (
                guild := self.bot.get_guild(payload.guild_id)
            ):
                continue

            starboard = await self.get_starboard(guild, entry["emoji"])
            if not starboard or not starboard.channel:
                continue

            star_message = starboard.channel.get_partial_message(entry["star_id"])
            await quietly_delete(star_message)

    @Cog.listener("on_raw_reaction_add")
    async def starboard_reaction_add(self, payload: RawReactionActionEvent):
        await self.reaction_action("star", payload)

    @Cog.listener("on_raw_reaction_remove")
    async def starboard_reaction_remove(self, payload: RawReactionActionEvent):
        await self.reaction_action("unstar", payload)
