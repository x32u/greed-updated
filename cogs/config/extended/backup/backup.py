from contextlib import suppress
from json import dumps
from logging import getLogger
from secrets import token_urlsafe
from typing import Optional, cast

from discord import Embed, HTTPException, Message
from discord.ext.commands import BucketType, cooldown, group
from discord.utils import format_dt

from cogs.config.extended.security.antinuke import Settings
from tools import CompositeMetaClass, MixinMeta
from tools.client import Context
from tools.paginator import Paginator

from .models import BackupLoader, BackupViewer, dump
from .types import BooleanArgs

log = getLogger("greedbot/backup")


class Backup(MixinMeta, metaclass=CompositeMetaClass):
    """
    Restore previous backups of the server.
    """

    async def cog_load(self) -> None:
        # data = await self.bot.db.fetchrow(
        #     """
        #     SELECT pg_size_pretty(
        #         pg_total_relation_size('backup')
        #     ) AS total_size,
        #     COUNT(*) AS records
        #     FROM backup
        #     """
        # )
        # if data:
        #     log.info(
        #         "Loaded %s which %s using %s.",
        #         format(plural(data["records"]), "backup"),
        #         "are" if data["records"] != 1 else "is",
        #         data["total_size"],
        #     )

        self.bot.add_check(self.check_backup_restrictions)
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.bot.remove_check(self.check_backup_restrictions)
        return await super().cog_unload()

    async def check_backup_restrictions(self, ctx: Context) -> bool:
        """
        Check the restrictions for the backup command.
        """

        if not ctx.command.qualified_name.startswith("backup"):
            return True

        config = await Settings.fetch(self.bot, ctx.guild)
        if not config.is_trusted(ctx.author):
            await ctx.warn(
                "You must be a **trusted administrator** to use this command!"
            )
            return False

        return True

    @group(invoke_without_command=True)
    async def backup(self, ctx: Context) -> Message:
        """
        Backup the server layout and settings.
        """

        return await ctx.send_help(ctx.command)

    @backup.command(name="create", aliases=["make", "take", "new"])
    async def backup_create(self, ctx: Context) -> Message:
        """
        Create a new restore point.
        """

        backup_count = cast(
            int,
            await self.bot.db.fetchval(
                """
                SELECT COUNT(*)
                FROM backup
                WHERE user_id = $1
                """,
                ctx.author.id,
            ),
        )
        if backup_count >= 10:
            return await ctx.warn(
                "You have reached the maximum amount of backups!",
                f"Use `{ctx.prefix}backup remove` to remove a backup",
            )

        async with ctx.typing():
            key = token_urlsafe(12)
            backup = await dump(ctx.guild)

            await self.bot.db.execute(
                """
                INSERT INTO backup (key, guild_id, user_id, data)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (key, guild_id) DO UPDATE
                SET data = EXCLUDED.data
                """,
                key,
                ctx.guild.id,
                ctx.author.id,
                (dumps(backup).encode()).decode(),
            )

        if ctx.author.is_on_mobile():
            with suppress(HTTPException):
                await ctx.author.send(f"{ctx.prefix}backup restore {key}")

        return await ctx.approve(
            f"Successfully created a restore point with key `{key}`",
            f"Use `{ctx.prefix}backup restore {key}` to restore this backup",
        )

    @backup.command(name="view", aliases=["info", "display"])
    async def backup_view(self, ctx: Context, key: str) -> Message:
        """
        View an existing restore point.
        """

        record = await self.bot.db.fetchrow(
            """
            SELECT *
            FROM backup
            WHERE key = $1
            AND user_id = $2
            """,
            key,
            ctx.author.id,
        )
        if not record:
            return await ctx.warn("You don't have a backup with that identifier!")

        backup = BackupViewer(record["data"])
        embed = Embed(
            title=backup.name,
            description=f"{format_dt(record['created_at'])} ({format_dt(record['created_at'], 'R')})",
        )
        embed.add_field(name="**Channels**", value=backup.channels())
        embed.add_field(name="**Roles**", value=backup.roles())

        return await ctx.send(embed=embed)

    @backup.command(name="restore", aliases=["load", "apply", "set"])
    @cooldown(1, 5 * 60, BucketType.guild)
    async def backup_restore(
        self,
        ctx: Context,
        key: str,
        *options,
    ) -> Optional[Message]:
        """
        Load an existing restore point.
        """

        record = await self.bot.db.fetchrow(
            """
            SELECT *
            FROM backup
            WHERE key = $1
            AND user_id = $2
            """,
            key,
            ctx.author.id,
        )
        if not record:
            return await ctx.warn("You don't have a backup with that identifier!")

        # if (
        #     len(ctx.guild.roles) > 3
        #     and (role := ctx.guild.me.top_role)
        #     and ctx.guild.me.top_role.position > len(ctx.guild.roles) - 3
        # ):
        #     return await ctx.warn(
        #         "My role isn't high enough to continue with the backup!",
        #         f"Please place {role.mention} in the top 3 of the hierarchy",
        #     )

        options = BooleanArgs(["channels", "roles", "settings"] + list(options))
        warnings: list[str] = []
        if options.channels:
            warnings.append("Channels will be deleted")
        if options.roles:
            warnings.append("Roles will be deleted")
        if options.bans:
            warnings.append("Bans will be restored")
        if options.settings:
            warnings.append("Server settings will be updated")

        if not warnings:
            return await ctx.warn(
                "You must specify at least one option to restore!",
            )

        await ctx.prompt(
            f"Are you sure you want to load backup `{key}`?"
            f"\n**The following changes will occur**",
            "\n".join(warnings),
        )

        backup = BackupLoader(self.bot, ctx.guild, record["data"])

        await ctx.neutral(f"Preparing to load backup `{key}`..")
        await backup.load(ctx.author, options)

        if ctx.guild.text_channels:
            return await ctx.guild.text_channels[0].send(
                content=ctx.author.mention,
                embed=Embed(
                    title="Backup Loaded",
                    description="Successfully  loaded the backup",
                ),
                delete_after=10,
            )

    @backup.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    async def backup_remove(self, ctx: Context, key: str) -> Message:
        """
        Remove a restore point.
        """

        result = await self.bot.db.execute(
            """
            DELETE FROM backup
            WHERE key = $1
            AND user_id = $2
            """,
            key,
            ctx.author.id,
        )
        if result == "DELETE 0":
            return await ctx.warn("You don't have a backup with that identifier!")

        return await ctx.approve(
            f"Successfully  removed the restore point with key `{key}`"
        )

    @backup.command(
        name="list",
        aliases=["ls"],
    )
    async def backup_list(self, ctx: Context) -> Message:
        """
        View your restore points.
        """

        channels = [
            f"**{backup.name}** (`{record['key']}`) - {format_dt(record['created_at'], 'd')}"
            for record in await self.bot.db.fetch(
                """
                SELECT key, data, created_at
                FROM backup
                WHERE user_id = $1
                """,
                ctx.author.id,
            )
            if (backup := BackupViewer(record["data"]))
        ]
        if not channels:
            return await ctx.warn("You haven't created any restore points!")

        paginator = Paginator(
            ctx,
            entries=channels,
            embed=Embed(title="Restore Points"),
        )
        return await paginator.start()
