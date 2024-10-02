from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, List, Literal, Optional, cast

from discord import (
    CategoryChannel,
    HTTPException,
    Member,
    Message,
    RateLimited,
    Role,
    VoiceChannel,
    VoiceState,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    Range,
    cooldown,
    group,
    has_permissions,
)
from humanfriendly import format_timespan

from tools import CompositeMetaClass, MixinMeta
from tools.client import Context as OriginalContext
from tools.parser import parse

if TYPE_CHECKING:
    from discord.guild import VocalGuildChannel

log = logging.getLogger("greedbot/voice")


class MemberVoice(VoiceState):
    channel: VoiceChannel


class MemberInVoice(Member):
    voice: MemberVoice


class Context(OriginalContext):
    author: MemberInVoice


class VoiceMaster(MixinMeta, metaclass=CompositeMetaClass):
    """
    The VoiceMaster mixin provides members
    with the ability to manage personal voice channels.
    """

    def is_empty(self, channel: "VocalGuildChannel") -> bool:
        """
        Check if a voice channel is empty.

        This doesn't include bots.
        """

        return not bool(list(filter(lambda m: not m.bot, channel.members)))

    async def check_voice_restrictions(self, ctx: Context) -> bool:
        """
        Check the restrictions for a command.

        If the command is a VoiceMaster command,
        and the author is not in a voice channel we'll raise an error.
        """

        if not ctx.command.qualified_name.startswith("voicemaster") or ctx.command in (
            self.voicemaster,
            self.voicemaster_setup,
            self.voicemaster_reset,
            *(
                self.voicemaster_default,
                self.voicemaster_default_category,
                self.voicemaster_default_name,
                self.voicemaster_default_name_remove,
                self.voicemaster_default_status,
                self.voicemaster_default_status_remove,
            ),
        ):
            return True

        elif not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.warn("You aren't in a voice channel!")
            return False

        owner_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT owner_id
                FROM voice.channels
                WHERE channel_id = $1
                """,
                ctx.author.voice.channel.id,
            ),
        )
        if not owner_id:
            await ctx.warn("You aren't in a VoiceMaster channel!")
            return False

        elif ctx.command == self.voicemaster_claim:
            if ctx.author.id == owner_id:
                await ctx.warn("You already own this channel!")
                return False

            elif owner_id in (member.id for member in ctx.author.voice.channel.members):
                await ctx.warn("This channel is still occupied!")
                return False

            return True

        elif ctx.author.id != owner_id:
            await ctx.warn("You don't own this channel!")
            return False

        return True

    async def cog_load(self) -> None:
        """
        Cleanup any leftover voice channels.

        This is useful incase the bot was offline
        when a member left their voice channel.
        """

        self.bot.add_check(self.check_voice_restrictions)

        records = await self.bot.db.fetch(
            """
            SELECT channel_id
            FROM voice.channels
            """,
        )

        scheduled_deletion: List[int | VoiceChannel] = []
        for record in records:
            channel_id = cast(
                int,
                record["channel_id"],
            )
            channel = cast(
                Optional[VoiceChannel],
                self.bot.get_channel(channel_id),
            )
            if not channel or self.is_empty(channel):
                scheduled_deletion.append(channel or channel_id)

        if scheduled_deletion:
            log.info(
                "Scheduled deletion of %s unoccupied voice channel%s.",
                len(scheduled_deletion),
                "s" if len(scheduled_deletion) > 1 else "",
            )

            await self.bot.db.execute(
                """
                DELETE FROM voice.channels
                WHERE channel_id = ANY($1::BIGINT[])
                """,
                [
                    channel.id if not isinstance(channel, int) else channel
                    for channel in scheduled_deletion
                ],
            )
            for channel in scheduled_deletion:
                if not isinstance(channel, int):
                    with suppress(HTTPException):
                        await channel.delete()

        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.bot.remove_check(self.check_voice_restrictions)
        return await super().cog_unload()

    @Cog.listener("on_voice_state_update")
    async def create_voice_channel(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        """
        Create the personal voice channel.
        """

        if (
            member.bot
            or not after.channel
            or before
            and before.channel == after.channel
        ):
            return

        elif not after.channel:
            return

        guild = member.guild
        config = await self.bot.db.fetchrow(
            """
            SELECT *
            FROM voice.config
            WHERE guild_id = $1
            """,
            guild.id,
        )
        if not config or config["channel_id"] != after.channel.id:
            return

        if await self.bot.redis.ratelimited(
            f"voicemaster:{member.id}",
            limit=1,
            timespan=10,
        ):
            await member.move_to(None)
            return

        if config["category_id"] == 0:
            category = None
        else:
            category = cast(
                Optional[CategoryChannel],
                guild.get_channel(config["category_id"]) or after.channel.category,
            )

        bitrate = min(
            cast(
                float,
                config["bitrate"] or guild.bitrate_limit,
            ),
            guild.bitrate_limit,
        )

        if await self.bot.redis.ratelimited(
            f"voicemaster:{guild.id}",
            limit=15,
            timespan=30,
        ):
            return

        log.info("Creating voice channel for %s in %s (%s).", member, guild, guild.id)
        try:
            channel = await guild.create_voice_channel(
                name=parse(
                    config["name"] or f"{member.display_name}'s channel",
                    [guild, member],
                )[:100],
                category=category,
                bitrate=int(bitrate),
                reason=f"Created by {member} ({member.id}) via VoiceMaster",
            )

            await channel.set_permissions(
                member,
                connect=True,
                view_channel=True,
                read_messages=True,
            )
        except HTTPException:
            return

        try:
            await member.move_to(channel)
        except HTTPException:
            with suppress(HTTPException):
                await channel.delete()

        await self.bot.db.execute(
            """
            INSERT INTO voice.channels
            VALUES ($1, $2, $3)
            """,
            guild.id,
            channel.id,
            member.id,
        )
        if config["status"]:
            with suppress(HTTPException):
                await channel.edit(
                    status=parse(config["status"], [guild, member])[:500]
                )

    @Cog.listener("on_voice_state_update")
    async def locked_voice_channel(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        """
        Prevent members from joining locked voice channels.
        """

        if (
            member.bot
            or member.id in self.bot.owner_ids
            or not after.channel
            or before
            and before.channel == after.channel
        ):
            return

        channel = after.channel
        if channel.overwrites_for(member).connect is True:
            return

        elif channel.overwrites_for(channel.guild.default_role).connect is not False:
            return

        temporary = cast(
            bool,
            await self.bot.db.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM voice.channels
                    WHERE channel_id = $1
                    AND owner_id != $2
                )
                """,
                channel.id,
                member.id,
            ),
        )
        if not temporary:
            return

        with suppress(HTTPException):
            if channel.overwrites_for(member).connect is False:
                await member.move_to(None, reason="Voice channel is locked")

            elif channel.overwrites_for(channel.guild.default_role).connect is False:
                await member.move_to(None, reason="Voice channel is locked")

    @Cog.listener("on_voice_state_update")
    async def delete_voice_channel(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        """
        Delete the personal voice channel.
        The channel must be empty excluding bots.
        """

        if not before.channel:
            return

        elif after and before.channel == after.channel:
            return

        elif not self.is_empty(before.channel):
            log.debug(
                "Skipping deletion of %s in %s due to non-empty channel.",
                before.channel,
                member.guild,
            )
            return

        result = await self.bot.db.execute(
            """
            DELETE FROM voice.channels
            WHERE channel_id = $1
            """,
            before.channel.id,
        )
        if result == "DELETE 0":
            return

        with suppress(HTTPException):
            await before.channel.delete()

    @group(
        aliases=["voice", "vc", "vm"],
        invoke_without_command=True,
    )
    async def voicemaster(self, ctx: Context) -> Message:
        """
        The base command for managing personal voice channels.
        """

        return await ctx.send_help(ctx.command)

    @voicemaster.command(name="setup")
    @has_permissions(manage_channels=True)
    @cooldown(1, 30, BucketType.guild)
    async def voicemaster_setup(self, ctx: Context) -> Message:
        """
        Setup the VoiceMaster integration.
        """

        channel_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT channel_id
                FROM voice.config
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            ),
        )
        if channel_id and (channel := ctx.guild.get_channel(channel_id)):
            return await ctx.warn(
                f"VoiceMaster is already setup in {channel.mention}!",
                f"Use `{ctx.clean_prefix}voicemaster reset` to reset it",
            )

        category = await ctx.guild.create_category("Voice Channels")
        channel = await category.create_voice_channel("Join to Create")

        await self.bot.db.execute(
            """
            INSERT INTO voice.config (guild_id, category_id, channel_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id)
            DO UPDATE
            SET
                category_id = $2,
                channel_id = $3
            """,
            ctx.guild.id,
            category.id,
            channel.id,
        )
        return await ctx.approve(
            "Successfully setup the VoiceMaster integration.",
            f"Join {channel.mention} to create a voice channel",
        )

    @voicemaster.command(name="reset")
    @has_permissions(manage_channels=True)
    async def voicemaster_reset(self, ctx: Context) -> Message:
        """
        Reset the VoiceMaster integration.
        """

        channel_ids = cast(
            List[int],
            await self.bot.db.fetchrow(
                """
                DELETE FROM voice.config
                WHERE guild_id = $1
                RETURNING category_id, channel_id
                """,
                ctx.guild.id,
            ),
        )
        if not channel_ids:
            return await ctx.warn("The VoiceMaster integration isn't setup!")

        for channel_id in channel_ids:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                with suppress(HTTPException):
                    await channel.delete()

        return await ctx.approve("Successfully reset the VoiceMaster integration")

    @voicemaster.group(
        name="default",
        aliases=["set"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def voicemaster_default(self, ctx: Context) -> Message:
        """
        Set the default settings for personal voice channels.
        """

        return await ctx.send_help(ctx.command)

    @voicemaster_default.command(name="category")
    @has_permissions(manage_channels=True)
    async def voicemaster_default_category(
        self,
        ctx: Context,
        category: CategoryChannel | Literal["none"],
    ) -> Message:
        """
        Set the category for personal voice channels.
        """

        await self.bot.db.execute(
            """
            UPDATE voice.config
            SET category_id = $2
            WHERE guild_id = $1
            """,
            ctx.guild.id,
            category.id if isinstance(category, CategoryChannel) else 0,
        )
        return await ctx.approve(
            f"Now placing personal voice channels under **{category}**"
            if isinstance(category, CategoryChannel)
            else "No longer placing personal voice channels under a category"
        )

    @voicemaster_default.group(name="name", invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def voicemaster_default_name(
        self,
        ctx: Context,
        *,
        name: Range[str, 1, 100],
    ) -> Message:
        """
        Set the default name for personal voice channels.
        """

        await self.bot.db.execute(
            """
            UPDATE voice.config
            SET name = $2
            WHERE guild_id = $1
            """,
            ctx.guild.id,
            name,
        )
        return await ctx.approve(
            f"Now using `{name}` for channel names",
            f"It will appear as **{parse(name, [ctx.guild, ctx.author])}**",
        )

    @voicemaster_default_name.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        hidden=True,
    )
    @has_permissions(manage_channels=True)
    async def voicemaster_default_name_remove(self, ctx: Context) -> Message:
        """
        Remove the default name for personal voice channels.
        """

        await self.bot.db.execute(
            """
            UPDATE voice.config
            SET name = NULL
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        return await ctx.approve("Reset the **default name** for voice channels")

    @voicemaster_default.group(name="status", invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def voicemaster_default_status(
        self,
        ctx: Context,
        *,
        status: Range[str, 1, 500],
    ) -> Message:
        """
        Set the default status for personal voice channels.
        """

        await self.bot.db.execute(
            """
            UPDATE voice.config
            SET status = $2
            WHERE guild_id = $1
            """,
            ctx.guild.id,
            status,
        )
        return await ctx.approve(
            f"Now using `{status}` for channel statuses",
            f"It will appear as **{parse(status, [ctx.guild, ctx.author])}**",
        )

    @voicemaster_default_status.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        hidden=True,
    )
    @has_permissions(manage_channels=True)
    async def voicemaster_default_status_remove(self, ctx: Context) -> Message:
        """
        Remove the default status for personal voice channels.
        """

        await self.bot.db.execute(
            """
            UPDATE voice.config
            SET status = NULL
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        return await ctx.approve("Reset the **default status** for voice channels")

    @voicemaster.command(name="claim")
    async def voicemaster_claim(self, ctx: Context) -> Message:
        """
        Claim an unoccupied voice channel.
        """

        channel = ctx.author.voice.channel
        if (
            channel.name.endswith("'s channel")
            and ctx.author.display_name not in channel.name
        ):
            self.bot.loop.create_task(
                channel.edit(name=f"{ctx.author.display_name}'s channel")
            )

        await self.bot.db.execute(
            """
            UPDATE voice.channels
            SET owner_id = $2
            WHERE channel_id = $1
            """,
            channel.id,
            ctx.author.id,
        )
        return await ctx.approve(f"You're now the owner of {channel.mention}")

    @voicemaster.command(name="transfer")
    async def voicemaster_transfer(self, ctx: Context, *, member: Member) -> Message:
        """
        Transfer ownership of your voice channel.
        """

        channel = ctx.author.voice.channel

        if member == ctx.author or member.bot:
            return await ctx.warn("You can't transfer ownership to that member!")

        elif member not in channel.members:
            return await ctx.warn("That member isn't in your voice channel!")

        if (
            channel.name.endswith("'s channel")
            and ctx.author.display_name not in channel.name
        ):
            self.bot.loop.create_task(
                channel.edit(name=f"{ctx.author.display_name}'s channel")
            )

        await self.bot.db.execute(
            """
            UPDATE voice.channels
            SET owner_id = $2
            WHERE channel_id = $1
            """,
            channel.id,
            member.id,
        )
        return await ctx.approve(f"Transferred ownership to {member.mention}")

    @voicemaster.command(name="lock")
    async def voicemaster_lock(self, ctx: Context) -> None:
        """
        Prevent members from joining your voice channel.
        """

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            ctx.guild.default_role,
            connect=False,
            reason=f"Locked by {ctx.author} ({ctx.author.id})",
        )
        for member in channel.members:
            await channel.set_permissions(member, connect=True)

        return await ctx.add_check()

    @voicemaster.command(name="unlock")
    async def voicemaster_unlock(self, ctx: Context) -> None:
        """
        Allow members to join your voice channel.
        """

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            ctx.guild.default_role,
            connect=None,
            reason=f"Unlocked by {ctx.author} ({ctx.author.id})",
        )
        return await ctx.add_check()

    @voicemaster.command(name="hide")
    async def voicemaster_hide(self, ctx: Context) -> None:
        """
        Hide your voice channel from the server.
        """

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            ctx.guild.default_role,
            view_channel=False,
            reason=f"Hidden by {ctx.author} ({ctx.author.id})",
        )
        return await ctx.add_check()

    @voicemaster.command(name="reveal")
    async def voicemaster_reveal(self, ctx: Context) -> None:
        """
        Reveal your voice channel to the server.
        """

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            ctx.guild.default_role,
            view_channel=None,
            reason=f"Revealed by {ctx.author} ({ctx.author.id})",
        )
        return await ctx.add_check()

    @voicemaster.command(
        name="allow",
        aliases=["permit"],
    )
    async def voicemaster_allow(
        self,
        ctx: Context,
        *,
        target: Member | Role,
    ) -> Message:
        """
        Allow a member to join your voice channel.
        """

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            target,
            connect=True,
            view_channel=True,
            reason=f"Allowed by {ctx.author} ({ctx.author.id})",
        )
        return await ctx.approve(
            f"Now allowing {target.mention} to join {channel.mention}"
        )

    @voicemaster.command(
        name="reject",
        aliases=[
            "remove",
            "deny",
            "kick",
        ],
    )
    async def voicemaster_reject(
        self,
        ctx: Context,
        *,
        target: Member | Role,
    ) -> Message:
        """
        Reject a member or role from joining your voice channel.
        """

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            target,
            connect=False,
            view_channel=None,
            reason=f"Rejected by {ctx.author} ({ctx.author.id})",
        )

        if isinstance(target, Member) and target in channel.members:
            with suppress(HTTPException):
                await target.move_to(None)

        return await ctx.approve(
            f"No longer allowing {target.mention} to join {channel.mention}"
        )

    @voicemaster.command(
        name="rename",
        aliases=["name"],
    )
    async def voicemaster_rename(
        self,
        ctx: Context,
        *,
        name: Range[str, 1, 100],
    ) -> Optional[Message]:
        """
        Set the name for your voice channel.
        """

        channel = ctx.author.voice.channel

        try:
            await channel.edit(
                name=name,
                reason=f"Renamed by {ctx.author} ({ctx.author.id})",
            )
        except HTTPException:
            return await ctx.warn("The name can't contain vulgarities!")

        except RateLimited as exc:
            return await ctx.warn(
                "Your voice channel is being rate limited!",
                f"Please wait **{format_timespan(int(exc.retry_after))}** before trying again",
            )

        return await ctx.add_check()

    @voicemaster.command(name="limit")
    async def voicemaster_limit(
        self,
        ctx: Context,
        limit: Range[int, 0, 99],
    ) -> Message:
        """
        Set the user limit for your voice channel.
        """

        channel = ctx.author.voice.channel

        await channel.edit(user_limit=limit)
        return await ctx.approve(f"Set the user limit to `{limit}`")
