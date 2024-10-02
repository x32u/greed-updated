from logging import getLogger

from asyncpg import UniqueViolationError
from discord import Embed, HTTPException, Message
from discord import Thread as ThreadChannel
from discord.ext.commands import Cog, group, has_permissions

from tools import CompositeMetaClass, MixinMeta
from tools.client import Context
from tools.formatter import plural
from tools.paginator import Paginator

log = getLogger("greedbot/watcher")


class Thread(MixinMeta, metaclass=CompositeMetaClass):
    """
    Watch threads to prevent them from being archived.
    """

    @Cog.listener()
    async def on_thread_update(
        self,
        before: ThreadChannel,
        after: ThreadChannel,
    ) -> None:
        """
        Watch threads to prevent them from being archived.
        """

        if not after.archived:
            return

        watched = await self.bot.db.fetch(
            """
            SELECT *
            FROM thread
            WHERE thread_id = $1
            """,
            after.id,
        )
        if not watched:
            return

        try:
            await after.edit(
                archived=False,
                auto_archive_duration=10080,
                reason="Thread is being watched",
            )
        except HTTPException:
            log.warning(
                "Failed to unarchive thread %s (%s) in guild %s (%s).",
                after,
                after.id,
                after.guild,
                after.guild.id,
            )
            await self.bot.db.execute(
                """
                DELETE FROM thread
                WHERE thread_id = $1
                """,
                after.id,
            )
        else:
            log.info(
                "Unarchived thread %s (%s) in guild %s (%s).",
                after,
                after.id,
                after.guild,
                after.guild.id,
            )

    @group(
        aliases=["watcher"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def thread(self, ctx: Context) -> Message:
        """
        Watch threads to prevent them from being archived.
        """

        return await ctx.send_help(ctx.command)

    @thread.command(
        name="add",
        aliases=["create", "watch"],
    )
    @has_permissions(manage_channels=True)
    async def thread_add(self, ctx: Context, *, thread: ThreadChannel) -> Message:
        """
        Add a thread to be watched.
        """

        try:
            await self.bot.db.execute(
                """
                INSERT INTO thread (
                    guild_id,
                    thread_id
                )
                VALUES ($1, $2)
                """,
                ctx.guild.id,
                thread.id,
            )
        except UniqueViolationError:
            return await ctx.warn(f"Already watching thread {thread.mention}!")

        return await ctx.approve(f"Now watching thread {thread.mention} for archival")

    @thread.command(
        name="remove",
        aliases=[
            "delete",
            "del",
            "rm",
            "unwatch",
        ],
    )
    @has_permissions(manage_channels=True)
    async def thread_remove(self, ctx: Context, *, thread: ThreadChannel) -> Message:
        """
        Remove a thread from being watched.
        """

        result = await self.bot.db.execute(
            """
                DELETE FROM thread
                WHERE guild_id = $1
                AND thread_id = $2
                """,
            ctx.guild.id,
            thread.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"Thread {thread.mention} isn't being watched!")

        return await ctx.approve(
            f"No longer watching thread {thread.mention} for archival"
        )

    @thread.command(
        name="clear",
        aliases=["clean", "reset"],
    )
    @has_permissions(manage_channels=True)
    async def thread_clear(self, ctx: Context) -> Message:
        """
        Stop watching all threads.
        """

        await ctx.prompt(
            "Are you sure you want to stop watching all threads?",
        )

        result = await self.bot.db.execute(
            """
            DELETE FROM thread
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if result == "DELETE 0":
            return await ctx.warn("No threads are being watched!")

        return await ctx.approve(f"No longer watching {plural(result, md='`'):thread}")

    @thread.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_channels=True)
    async def thread_list(self, ctx: Context) -> Message:
        """
        View all threads being watched.
        """

        channels = [
            f"{thread.mention} (`{thread.id}`)"
            for record in await self.bot.db.fetch(
                """
                SELECT thread_id
                FROM thread
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (thread := ctx.guild.get_thread(record["thread_id"]))
        ]
        if not channels:
            return await ctx.warn("No threads are being watched!")

        paginator = Paginator(
            ctx,
            entries=channels,
            embed=Embed(
                title="Threads being watched",
            ),
        )
        return await paginator.start()
