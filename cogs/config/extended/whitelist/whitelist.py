from contextlib import suppress
from typing import Literal, cast

from discord import HTTPException, Member, Message, User
from discord.ext.commands import Cog, UserInputError, group, has_permissions

from tools import CompositeMetaClass, MixinMeta
from tools.client import Context

# CREATE TABLE IF NOT EXISTS whitelist (
#   guild_id BIGINT NOT NULL,
#   status BOOLEAN NOT NULL DEFAULT FALSE,
#   action TEXT NOT NULL DEFAULT 'kick'
# );


class Whitelist(MixinMeta, metaclass=CompositeMetaClass):
    """
    Restrict access to your server.
    """

    @group(aliases=["wl"], invoke_without_command=True)
    @has_permissions(administrator=True)
    async def whitelist(self, ctx: Context) -> Message:
        """
        Restrict access to your server.
        """

        return await ctx.send_help(ctx.command)

    @whitelist.command(
        name="toggle",
        aliases=["switch"],
    )
    @has_permissions(administrator=True)
    async def whitelist_toggle(self, ctx: Context) -> Message:
        """
        Toggle the whitelist system.
        """

        status = cast(
            bool,
            await self.bot.db.fetchval(
                """
                INSERT INTO whitelist (guild_id, status)
                VALUES ($1, TRUE)
                ON CONFLICT (guild_id)
                DO UPDATE SET status = NOT whitelist.status
                RETURNING status
                """,
                ctx.guild.id,
            ),
        )

        return await ctx.approve(
            f"The **whitelist system** has been **{'enabled' if status else 'disabled'}**"
        )

    @whitelist.command(
        name="action",
        aliases=["punishment"],
    )
    @has_permissions(administrator=True)
    async def whitelist_action(
        self,
        ctx: Context,
        action: Literal["kick", "ban"],
    ) -> Message:
        """
        Set the punishment for users who are not whitelisted.
        """

        await self.bot.db.execute(
            """
            INSERT INTO whitelist (guild_id, action)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET action = EXCLUDED.action
            """,
            ctx.guild.id,
            action,
        )

        return await ctx.approve(f"Whitelist action has been set to **{action}**")

    @whitelist.command(
        name="permit",
        aliases=["allow", "add"],
    )
    @has_permissions(administrator=True)
    async def whitelist_permit(
        self,
        ctx: Context,
        user: Member | User,
    ) -> Message:
        """
        Allow a user to join the server.
        """

        if isinstance(user, Member):
            return await ctx.warn("That user is already in the server!")

        await self.bot.redis.set(
            f"whitelist:{ctx.guild.id}:{user.id}",
            "1",
            ex=7200,
        )
        with suppress(HTTPException):
            await ctx.guild.unban(user, reason="Whitelist permit")

        return await ctx.approve(f"Granted **{user}** access to join the server")

    @whitelist.command(
        name="remove",
        aliases=["reject"],
    )
    @has_permissions(administrator=True)
    async def whitelist_remove(
        self,
        ctx: Context,
        user: Member | User,
    ) -> Message:
        """
        Remove a user's pending permit.
        """

        result = await self.bot.redis.delete(
            f"whitelist:{ctx.guild.id}:{user.id}",
        )
        if not result and isinstance(user, User):
            return await ctx.warn("That user already requires a permit!")

        if isinstance(user, Member):
            try:
                await ctx.prompt(f"Would you like to kick **{user}** from the server?")
            except UserInputError:
                ...
            else:
                with suppress(HTTPException):
                    await user.kick(reason="Whitelist removed")

        return await ctx.approve(f"No longer allowing **{user}** to join the server")

    @Cog.listener("on_member_remove")
    async def whitelist_remove_listener(self, member: Member) -> None:
        """
        Remove a user from the whitelist when they leave.
        """

        if member.bot:
            return

        await self.bot.redis.delete(f"whitelist:{member.guild.id}:{member.id}")

    @Cog.listener("on_member_join")
    async def whitelist_listener(self, member: Member) -> None:
        """
        Check if a user is whitelisted when they join.
        """

        if member.bot:
            return

        if await self.bot.redis.get(f"whitelist:{member.guild.id}:{member.id}"):
            return

        record = await self.bot.db.fetchrow(
            """
            SELECT status, action
            FROM whitelist
            WHERE guild_id = $1
            """,
            member.guild.id,
        )
        if not record or not record["status"]:
            return

        with suppress(HTTPException):
            if record["action"] == "kick":
                await member.kick(reason="Not permitted. (WHITELIST SYSTEM)")

            elif record["action"] == "ban":
                await member.ban(reason="Not permitted. (WHITELIST SYSTEM)")
