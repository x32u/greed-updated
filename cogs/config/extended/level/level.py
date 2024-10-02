from contextlib import suppress
from datetime import datetime
from random import uniform
from typing import Annotated, List, Optional, cast

from asyncpg import UniqueViolationError
from discord import Embed, HTTPException, Member, Message, Role, TextChannel, Thread
from discord.ext.commands import BucketType, Cog, cooldown, group, has_permissions
from pydantic import BaseModel
from typing_extensions import Self

from tools import CompositeMetaClass, MixinMeta
from tools.client import Context
from tools.conversion import StrictRole
from tools.formatter import plural
from tools.paginator import Paginator
from tools.parser import Script


class LevelConfig(BaseModel):
    guild_id: int
    status: bool = True
    cooldown: int = 60
    max_level: int = 0
    stack_roles: bool = True
    formula_multiplier: int = 1
    xp_multiplier: int = 1
    xp_min: int = 15
    xp_max: int = 40
    effort_status: bool = False
    effort_text: int = 25
    effort_image: int = 3
    effort_boost: int = 10

    @classmethod
    async def fetch(cls, ctx: Context) -> Optional[Self]:
        record = await ctx.bot.db.fetchrow(
            """
            SELECT *
            FROM level.config
            WHERE guild_id = $1
            AND status = TRUE
            """,
            ctx.guild.id,
        )
        if record:
            return cls(**record)


class LevelData(BaseModel):
    guild_id: int
    user_id: int
    xp: int
    level: int
    total_xp: int
    last_message: datetime

    @property
    def next_level(self) -> int:
        return self.level + 1

    def required_xp(self, level: Optional[int] = None, multiplier: int = 1) -> int:
        """
        Calculate the required XP for a given level.
        """

        level = level or self.next_level
        xp = sum((i * 100) + 75 for i in range(level))
        return int(xp * multiplier)

    @classmethod
    async def fetch(cls, ctx: Context) -> Self:
        record = await ctx.bot.db.fetchrow(
            """
            INSERT INTO level.member (guild_id, user_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET last_message = NOW()
            RETURNING *
            """,
            ctx.guild.id,
            ctx.author.id,
        )
        return cls(**record)


class LevelVariables(BaseModel):
    level: int
    xp: int
    total_xp: int

    def __str__(self):
        return str(self.level)

    def _variable(self) -> str:
        return "level"


class Level(MixinMeta, metaclass=CompositeMetaClass):
    """
    Award members with roles for being active in the server.
    """

    def required_xp(self, level: int, multiplier: int = 1) -> int:
        """
        Calculate the required XP for a given level.
        """

        xp = sum((i * 100) + 75 for i in range(level))
        return int(xp * multiplier)

    async def grant_level_roles(
        self,
        ctx: Context,
        config: LevelConfig,
        data: LevelData,
    ):
        """
        Grant roles to the member.
        """

        roles: List[tuple[Role, int]] = [
            (role, record["level"])
            for record in await self.bot.db.fetch(
                """
                SELECT level, role_id
                FROM level.role
                WHERE guild_id = $1
                AND level <= $2
                """,
                ctx.guild.id,
                data.level,
            )
            if (role := ctx.guild.get_role(record["role_id"]))
        ]
        if roles:
            level_role = min(roles, key=lambda r: abs(r[1] - data.level))
            if level_role not in ctx.author.roles:
                await ctx.author.add_roles(
                    level_role[0],
                    reason=f"Member reached level {level_role[1]}",
                )

            if not config.stack_roles:
                remove_roles = [
                    role
                    for role in ctx.author.roles
                    if role in roles and role != level_role[0]
                ]
                if remove_roles:
                    await ctx.author.remove_roles(
                        *remove_roles,
                        reason="Stacking roles is disabled",
                    )

    async def send_level_notification(
        self,
        ctx: Context,
        data: LevelData,
    ):
        """
        Send a notification for the member.
        """

        record = await self.bot.db.fetchrow(
            """
            SELECT channel_id, dm, template
            FROM level.notification
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if not record:
            return

        template = record["template"] or (
            "{user.mention} has reached level **{level}**. GG!"
        )
        script = Script(
            template,
            [
                ctx.guild,
                ctx.author,
                LevelVariables(level=data.level, xp=data.xp, total_xp=data.total_xp),
            ],
        )

        channel = (
            ctx.author
            if record["dm"]
            else (ctx.guild.get_channel(record["channel_id"]) or ctx.channel)
        )
        if not isinstance(channel, (Member, TextChannel, Thread)):
            return

        with suppress(HTTPException):
            await script.send(channel)

    @Cog.listener("on_message_without_command")
    async def level_listener(self, ctx: Context):
        """
        Award XP to members for sending messages.
        """

        config = await LevelConfig.fetch(ctx)
        if not config:
            return

        data = await LevelData.fetch(ctx)
        # if await self.bot.redis.ratelimited(
        #     f"level:{ctx.guild.id}:{ctx.author.id}",
        #     1,
        #     config.cooldown,
        # ):
        #     return

        xp = int(uniform(config.xp_min, config.xp_max) * config.xp_multiplier)
        if config.effort_status:
            if len(ctx.message.content) >= config.effort_text:
                xp += config.effort_boost

            if len(ctx.message.attachments) >= config.effort_image:
                xp += config.effort_boost

        data.xp += xp
        data.total_xp += xp
        if data.xp >= data.required_xp(multiplier=config.formula_multiplier):
            data.xp = 0
            data.level += 1

            with suppress(HTTPException):
                await self.grant_level_roles(ctx, config, data)

            with suppress(HTTPException):
                await self.send_level_notification(ctx, data)

        await self.bot.db.execute(
            """
            UPDATE level.member
            SET
                xp = $3,
                level = $4,
                total_xp = $5
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            ctx.author.id,
            data.xp,
            data.level,
            data.total_xp,
        )

    @group(
        aliases=["lvl", "rank"],
        invoke_without_command=True,
    )
    async def level(
        self,
        ctx: Context,
        *,
        member: Optional[Member] = None,
    ) -> Message:
        """
        View your current rank.
        """

        member = member or ctx.author
        record = await self.bot.db.fetchrow(
            """
            SELECT
                member.xp,
                member.level,
                member.total_xp,
                config.formula_multiplier,
                (
                    SELECT COUNT(*)
                    FROM level.member
                    WHERE guild_id = member.guild_id
                    AND level > 0
                ) AS total_members,
                (
                    SELECT COUNT(*) + 1
                    FROM level.member
                    WHERE guild_id = member.guild_id
                    AND total_xp > member.total_xp
                ) AS rank
            FROM level.member AS member
            INNER JOIN level.config AS config
            ON member.guild_id = config.guild_id
            WHERE member.guild_id = $1
            AND member.user_id = $2
            GROUP BY member.xp, member.level, member.total_xp, config.formula_multiplier
            """,
            ctx.guild.id,
            member.id,
        )
        if not record:
            return await ctx.warn("No level data has been recorded yet!")

        embed = Embed()
        embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar,
        )
        embed.add_field(
            name="**Level**",
            value=record["level"],
        )
        embed.add_field(
            name="**Experience**",
            value=f"{record['xp']:,} of {self.required_xp(record['level'] + 1, record['formula_multiplier']):,}",
        )
        embed.add_field(
            name="**Total XP**",
            value=f"{record['total_xp']:,}",
        )
        embed.set_footer(
            text=f"Rank #{record['rank']} out of {plural(record['total_members']):member}"
        )
        return await ctx.send(embed=embed)

    @level.command(
        name="leaderboard",
        aliases=["lb"],
    )
    @cooldown(1, 5, BucketType.guild)
    async def level_leaderboard(self, ctx: Context) -> Message:
        """
        View the server's level leaderboard.
        """

        members = [
            f"**{member}** is **level {record['level']:,}** (`{record['total_xp']:,} XP`)"
            for record in await self.bot.db.fetch(
                """
                SELECT user_id, level, total_xp
                FROM level.member
                WHERE guild_id = $1
                AND level > 0
                ORDER BY total_xp DESC
                LIMIT 100
                """,
                ctx.guild.id,
            )
            if (member := ctx.guild.get_member(record["user_id"]))
        ]
        if not members:
            return await ctx.warn("No level data has been recorded yet!")

        paginator = Paginator(
            ctx,
            entries=members,
            embed=Embed(title="Level Leaderboard"),
        )
        return await paginator.start()

    @level.command(
        name="toggle",
        aliases=["switch"],
    )
    @has_permissions(manage_guild=True)
    async def level_toggle(self, ctx: Context) -> Message:
        """
        Toggle the level system.
        """

        status = cast(
            bool,
            await self.bot.db.fetchval(
                """
                INSERT INTO level.config (guild_id)
                VALUES ($1)
                ON CONFLICT (guild_id)
                DO UPDATE SET status = NOT level.config.status
                RETURNING status
                """,
                ctx.guild.id,
            ),
        )

        return await ctx.approve(
            f"The level system has been {'enabled' if status else 'disabled'}"
        )

    @level.group(
        name="role",
        aliases=["roles"],
        invoke_without_command=True,
    )
    async def level_role(self, ctx: Context) -> Message:
        """
        Award roles to members for reaching a certain level.
        """

        if ctx.invoked_with == "roles":
            return await self.level_role_list(ctx)

        return await ctx.send_help(ctx.command)

    @level_role.command(
        name="stack",
        aliases=["stacking"],
    )
    @has_permissions(manage_guild=True, manage_roles=True)
    async def level_role_stack(self, ctx: Context) -> Message:
        """
        Toggle if members should keep previous level roles.
        """

        stack_roles = cast(
            bool,
            await self.bot.db.fetchval(
                """
                INSERT INTO level.config (guild_id)
                VALUES ($1)
                ON CONFLICT (guild_id)
                DO UPDATE SET stack_roles = NOT level.config.stack_roles
                RETURNING stack_roles
                """,
                ctx.guild.id,
            ),
        )

        return await ctx.approve(
            f"{'Now' if stack_roles else 'No longer'} stacking level roles"
        )

    @level_role.command(
        name="add",
        aliases=["create"],
    )
    @has_permissions(manage_guild=True, manage_roles=True)
    async def level_role_add(
        self,
        ctx: Context,
        level: int,
        *,
        role: Annotated[
            Role,
            StrictRole,
        ],
    ) -> Message:
        """
        Add a role to be granted at a certain level.
        """

        try:
            await self.bot.db.execute(
                """
                INSERT INTO level.role (guild_id, role_id, level)
                VALUES ($1, $2, $3)
                """,
                ctx.guild.id,
                role.id,
                level,
            )
        except UniqueViolationError:
            return await ctx.warn(f"The role {role.mention} is already being granted!")

        return await ctx.approve(f"Now granting {role.mention} at **level {level:,}**")

    @level_role.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    @has_permissions(manage_guild=True, manage_roles=True)
    async def level_role_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole,
        ],
    ) -> Message:
        """
        Remove a role from being granted at a certain level.
        """

        level = await self.bot.db.fetchval(
            """
            DELETE FROM level.role
            WHERE guild_id = $1
            AND role_id = $2
            RETURNING level
            """,
            ctx.guild.id,
            role.id,
        )
        if not level:
            return await ctx.warn(f"The role {role.mention} is not being granted!")

        return await ctx.approve(
            f"No longer granting {role.mention} at **level {level}**"
        )

    @level_role.command(
        name="list",
        aliases=["ls"],
    )
    async def level_role_list(self, ctx: Context) -> Message:
        """
        View the roles being granted at certain levels.
        """

        config = await LevelConfig.fetch(ctx)
        if not config:
            return await ctx.warn("The level system is not enabled in this server!")

        data = await LevelData.fetch(ctx)
        roles: List[tuple[Role, int]] = [
            (role, record["level"])
            for record in await self.bot.db.fetch(
                """
                SELECT level, role_id
                FROM level.role
                WHERE guild_id = $1
                ORDER BY level ASC
                """,
                ctx.guild.id,
            )
            if (role := ctx.guild.get_role(record["role_id"]))
        ]
        if not roles:
            return await ctx.warn("No roles are being granted!")

        embed = Embed(title="Level Roles")
        fields: List[dict] = [
            {
                "name": f"**Level {level:,}**",
                "value": (
                    (
                        "***UNLOCKED***"
                        if level <= data.level
                        else f"Remaining XP: **{data.required_xp(level, config.formula_multiplier) - data.total_xp}**"
                    )
                    + f"\n> {role.mention}"
                ),
                "inline": True,
            }
            for role, level in roles
        ]

        paginator = Paginator(
            ctx,
            entries=fields,
            embed=embed,
            per_page=6,
        )
        return await paginator.start()
