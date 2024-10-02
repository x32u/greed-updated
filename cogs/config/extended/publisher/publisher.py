from logging import getLogger

from asyncpg import UniqueViolationError
from discord import ChannelType, Embed, HTTPException, Message, TextChannel
from discord.ext.commands import Cog, group, has_permissions

from tools import CompositeMetaClass, MixinMeta
from tools.client import Context
from tools.formatter import plural
from tools.paginator import Paginator

log = getLogger("greedbot/publisher")


class Publisher(MixinMeta, metaclass=CompositeMetaClass):
    """
    Automatically publish announcments.
    """

    @Cog.listener("on_message")
    async def publisher_listener(self, message: Message) -> None:
        """
        Automatically publish an announcment message.
        """

        if not message.guild or message.channel.type != ChannelType.news:
            return

        watched = await self.bot.db.fetch(
            """
            SELECT *
            FROM publisher
            WHERE channel_id = $1
            """,
            message.channel.id,
        )
        if not watched:
            return

        try:
            await message.publish()
        except HTTPException:
            log.warning(
                "Failed to publish message %s in guild %s (%s).",
                message.id,
                message.guild,
                message.guild.id,
            )
            await self.bot.db.execute(
                """
                DELETE FROM publisher
                WHERE channel_id = $1
                """,
                message.channel.id,
            )
        else:
            log.debug(
                "Published message %s in guild %s (%s).",
                message.id,
                message.guild,
                message.guild.id,
            )

    @group(
        aliases=["announcement"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def publisher(self, ctx: Context) -> Message:
        """
        Automatically publish announcement messages.
        """

        return await ctx.send_help(ctx.command)

    @publisher.command(
        name="add",
        aliases=["create", "watch"],
    )
    @has_permissions(manage_channels=True)
    async def publisher_add(self, ctx: Context, *, channel: TextChannel) -> Message:
        """
        Add a channel to be watched.
        """

        if channel.type != ChannelType.news:
            return await ctx.reply("that won't work.... not even a news channel")

        try:
            await self.bot.db.execute(
                """
                INSERT INTO publisher (
                    guild_id,
                    channel_id
                )
                VALUES ($1, $2)
                """,
                ctx.guild.id,
                channel.id,
            )
        except UniqueViolationError:
            return await ctx.warn(f"Already publishing messages in {channel.mention}!")

        return await ctx.approve(
            f"Now automatically publishing messages in {channel.mention}"
        )

    @publisher.command(
        name="remove",
        aliases=[
            "delete",
            "del",
            "rm",
            "unwatch",
        ],
    )
    @has_permissions(manage_channels=True)
    async def publisher_remove(self, ctx: Context, *, channel: TextChannel) -> Message:
        """
        Remove a channel from being watched.
        """

        result = await self.bot.db.execute(
            """
            DELETE FROM publisher
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            ctx.guild.id,
            channel.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"Channel {channel.mention} isn't being watched!")

        return await ctx.approve(f"No longer publishing messages in {channel.mention}")

    @publisher.command(
        name="clear",
        aliases=["clean", "reset"],
    )
    @has_permissions(manage_channels=True)
    async def publisher_clear(self, ctx: Context) -> Message:
        """
        Stop watching all channels.
        """

        await ctx.prompt(
            "Are you sure you want to stop watching all channels?",
        )

        result = await self.bot.db.execute(
            """
            DELETE FROM publisher
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if result == "DELETE 0":
            return await ctx.warn("No channels are being watched!")

        return await ctx.approve(f"No longer watching {plural(result, md='`'):channel}")

    @publisher.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_channels=True)
    async def publisher_list(self, ctx: Context) -> Message:
        """
        View all channels being watched.
        """

        channels = [
            f"{channel.mention} (`{channel.id}`)"
            for record in await self.bot.db.fetch(
                """
                SELECT channel_id
                FROM publisher
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (channel := ctx.guild.get_channel(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No channels are being watched!")

        paginator = Paginator(
            ctx,
            entries=channels,
            embed=Embed(title="Publisher Channels"),
        )
        return await paginator.start()
