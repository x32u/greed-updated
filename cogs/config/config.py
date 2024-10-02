from __future__ import annotations

import asyncio
from typing import List, Optional, cast

from cashews import cache
from discord import AuditLogEntry, HTTPException, Message, TextChannel
from discord.ext.commands import (
    BucketType,
    Cog,
    Range,
    cooldown,
    group,
    has_permissions,
    parameter,
)

from config import CLIENT
from main import greedbot
from tools import quietly_delete
from tools.client.context import Context, ReskinConfig
from tools.conversion import PartialAttachment
from tools.formatter import codeblock, plural, vowel
from tools.parser import Script

from .extended import Extended


class Config(Extended, Cog):
    def __init__(self, bot: greedbot):
        self.bot = bot

    @Cog.listener("on_audit_log_entry_webhook_delete")
    async def reskin_webhook_delete(self, entry: AuditLogEntry) -> None:
        if not (webhook := entry.target):
            return

        channel = cast(TextChannel, entry.before.channel)
        await self.bot.db.execute(
            """
            DELETE FROM reskin.webhook
            WHERE channel_id = $1
            AND webhook_id = $2
            """,
            channel.id,
            webhook.id,
        )
        await cache.delete_match(f"reskin:webhook:{entry.guild.id}:{channel.id}")

    @group(invoke_without_command=True)
    async def prefix(self, ctx: Context) -> Message:
        """
        View the current server prefixes.
        """

        prefixes = ctx.settings.prefixes or [CLIENT.PREFIX]

        return await ctx.neutral(
            f"The current prefixes are: {', '.join(f'`{prefix}`' for prefix in prefixes)}"
            if len(prefixes) > 1
            else f"The current prefix is `{prefixes[0]}`"
        )

    @prefix.command(name="set")
    @has_permissions(manage_guild=True)
    async def prefix_set(self, ctx: Context, prefix: str) -> Message:
        """
        Set the server prefix.
        """

        if not prefix:
            return await ctx.warn("You must provide a prefix!")

        await ctx.prompt(
            f"Are you sure you want to set the prefix to `{prefix}`?",
            "This will overwrite all existing prefixes.",
        )

        await ctx.settings.update(prefixes=[prefix])
        return await ctx.approve(f"The prefix has been set to `{prefix}`")

    @prefix.command(name="add")
    @has_permissions(manage_guild=True)
    async def prefix_add(self, ctx: Context, prefix: str) -> Message:
        """
        Add a prefix to the server.
        """

        if not prefix:
            return await ctx.warn("You must provide a prefix!")

        elif prefix in ctx.settings.prefixes:
            return await ctx.warn(f"`{prefix}` is already a prefix!")

        await ctx.settings.update(prefixes=[*ctx.settings.prefixes, prefix])
        return await ctx.approve(f"Now accepting `{prefix}` as a prefix")

    @prefix.command(name="remove")
    @has_permissions(manage_guild=True)
    async def prefix_remove(self, ctx: Context, prefix: str) -> Message:
        """
        Remove a prefix from the server.
        """

        if not prefix:
            return await ctx.warn("You must provide a prefix!")

        elif prefix not in ctx.settings.prefixes:
            return await ctx.warn(f"`{prefix}` is not a prefix!")

        await ctx.settings.update(
            prefixes=[p for p in ctx.settings.prefixes if p != prefix]
        )
        return await ctx.approve(f"No longer accepting `{prefix}` as a prefix")

    @prefix.command(name="reset")
    @has_permissions(manage_guild=True)
    async def prefix_reset(self, ctx: Context) -> Message:
        """
        Reset the server prefixes.
        """

        await ctx.settings.update(prefixes=[])
        return await ctx.approve("Reset the server prefixes")

    @group(invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invoke(self, ctx: Context) -> Message:
        """
        Set custom moderation invoke messages.

        Accepts the `moderator` and `reason` variables.
        """

        return await ctx.send_help(ctx.command)

    @invoke.group(name="kick", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invoke_kick(self, ctx: Context, *, script: Script) -> Message:
        """
        Set the kick invoke message.
        """

        await ctx.settings.update(invoke_kick=script.template)
        return await ctx.approve(
            f"Successfully set {vowel(script.format)} **kick** message",
            f"Use `{ctx.clean_prefix}invoke kick remove` to remove it",
        )

    @invoke_kick.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        hidden=True,
    )
    @has_permissions(manage_guild=True)
    async def invoke_kick_remove(self, ctx: Context) -> Message:
        """
        Remove the kick invoke message.
        """

        await ctx.settings.update(invoke_kick=None)
        return await ctx.approve("Removed the **kick** invoke message")

    @invoke.group(name="ban", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invoke_ban(self, ctx: Context, *, script: Script) -> Message:
        """
        Set the ban invoke message.
        """

        await ctx.settings.update(invoke_ban=script.template)
        return await ctx.approve(
            f"Successfully set {vowel(script.format)} **ban** message",
            f"Use `{ctx.clean_prefix}invoke ban remove` to remove it",
        )

    @invoke_ban.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        hidden=True,
    )
    @has_permissions(manage_guild=True)
    async def invoke_ban_remove(self, ctx: Context) -> Message:
        """
        Remove the ban invoke message.
        """

        await ctx.settings.update(invoke_ban=None)
        return await ctx.approve("Removed the **ban** invoke message")

    @invoke.group(name="unban", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invoke_unban(self, ctx: Context, *, script: Script) -> Message:
        """
        Set the unban invoke message.
        """

        await ctx.settings.update(invoke_unban=script.template)
        return await ctx.approve(
            f"Successfully set {vowel(script.format)} **unban** message",
            f"Use `{ctx.clean_prefix}invoke unban remove` to remove it",
        )

    @invoke_unban.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        hidden=True,
    )
    @has_permissions(manage_guild=True)
    async def invoke_unban_remove(self, ctx: Context) -> Message:
        """
        Remove the unban invoke message.
        """

        await ctx.settings.update(invoke_unban=None)
        return await ctx.approve("Removed the **unban** invoke message")

    @invoke.group(name="timeout", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invoke_timeout(self, ctx: Context, *, script: Script) -> Message:
        """
        Set the timeout invoke message.

        Accepts the `duration` and `expires` variables.
        """

        await ctx.settings.update(invoke_timeout=script.template)
        return await ctx.approve(
            f"Successfully set {vowel(script.format)} **timeout** message",
            f"Use `{ctx.clean_prefix}invoke timeout remove` to remove it",
        )

    @invoke_timeout.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        hidden=True,
    )
    @has_permissions(manage_guild=True)
    async def invoke_timeout_remove(self, ctx: Context) -> Message:
        """
        Remove the timeout invoke message.
        """

        await ctx.settings.update(invoke_timeout=None)
        return await ctx.approve("Removed the **timeout** invoke message")

    @invoke.group(name="untimeout", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invoke_untimeout(self, ctx: Context, *, script: Script) -> Message:
        """
        Set the untimeout invoke message.
        """

        await ctx.settings.update(invoke_untimeout=script.template)
        return await ctx.approve(
            f"Successfully set {vowel(script.format)} **untimeout** message",
            f"Use `{ctx.clean_prefix}invoke untimeout remove` to remove it",
        )

    @invoke_untimeout.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        hidden=True,
    )
    @has_permissions(manage_guild=True)
    async def invoke_untimeout_remove(self, ctx: Context) -> Message:
        """
        Remove the untimeout invoke message.
        """

        await ctx.settings.update(invoke_untimeout=None)
        return await ctx.approve("Removed the **untimeout** invoke message")

    @group(invoke_without_command=True)
    async def reskin(self, ctx: Context) -> Message:
        """
        Customize the bot's appearance.
        """

        return await ctx.send_help(ctx.command)

    @reskin.command(
        name="setup",
        aliases=["enable", "on"],
    )
    @has_permissions(administrator=True)
    @cooldown(1, 120, BucketType.guild)
    async def reskin_setup(self, ctx: Context) -> Message:
        """
        Setup the webhooks for the reskin system.
        """

        await ctx.settings.update(reskin=True)

        webhooks: List[tuple[TextChannel, int]] = [
            (channel, record["webhook_id"])
            for record in await self.bot.db.fetch(
                """
                SELECT channel_id, webhook_id
                FROM reskin.webhook
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (
                channel := cast(
                    Optional[TextChannel], ctx.guild.get_channel(record["channel_id"])
                )
            )
        ]

        await ctx.neutral(
            "Setting up the reskin webhooks...",
            "This might take a while to complete!",
        )
        async with ctx.typing():
            FILTERED_NAMES = ("ticket", "log", "audit")
            for channel in ctx.guild.text_channels[:30]:
                if channel in (c for c, _ in webhooks):
                    continue

                elif any(name in channel.name.lower() for name in FILTERED_NAMES):
                    continue

                elif channel.category and any(
                    name in channel.category.name for name in FILTERED_NAMES
                ):
                    continue

                try:
                    webhook = await asyncio.wait_for(
                        channel.create_webhook(name="greedbot reskin"),
                        timeout=15,
                    )
                except asyncio.TimeoutError:
                    await ctx.warn(
                        "Webhook creation timed out while setting up reskin!",
                        "We've likely been rate limited, please try again later",
                    )
                    break
                except HTTPException as exc:
                    await ctx.warn(
                        "Failed to create webhooks while setting up reskin!",
                        codeblock(exc.text),
                    )
                    break

                webhooks.append((channel, webhook.id))

        await self.bot.db.executemany(
            """
            INSERT INTO reskin.webhook (guild_id, channel_id, webhook_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, channel_id) DO UPDATE
            SET webhook_id = EXCLUDED.webhook_id
            """,
            [
                (ctx.guild.id, channel.id, webhook_id)
                for channel, webhook_id in webhooks
            ],
        )
        await cache.delete_match(f"reskin:webhook:{ctx.guild.id}:*")

        if ctx.response:
            await quietly_delete(ctx.response)

        return await ctx.approve(
            f"Successfully setup reskin for {plural(webhooks, md='`'):channel}"
        )

    @reskin.command(
        name="disable",
        aliases=["off"],
    )
    @has_permissions(administrator=True)
    async def reskin_disable(self, ctx: Context) -> Message:
        """
        Disable the reskin system server wide.
        """

        await ctx.settings.update(reskin=False)
        return await ctx.approve("No longer reskinning messages for this server")

    @reskin.command(name="username", aliases=["name"])
    async def reskin_username(
        self,
        ctx: Context,
        *,
        username: Range[str, 1, 32],
    ) -> Message:
        """
        Set your personal reskin username.
        """

        if any(
            forbidden in username.lower()
            for forbidden in ("clyde", "discord", "bleed", "haunt")
        ):
            return await ctx.warn(
                "That username is either reserved or forbidden!",
                "Attempting to bypass this will result in a blacklist",
            )

        await self.bot.db.execute(
            """
            INSERT INTO reskin.config (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username
            """,
            ctx.author.id,
            username,
        )
        await ReskinConfig.revalidate(self.bot, ctx.author)

        return await ctx.approve(f"Your reskin username has been set as **{username}**")

    @reskin.command(name="avatar", aliases=["icon", "av"])
    async def reskin_avatar(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Set your personal reskin avatar.
        """

        if not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")

        await self.bot.db.execute(
            """
            INSERT INTO reskin.config (user_id, avatar_url)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET avatar_url = EXCLUDED.avatar_url
            """,
            ctx.author.id,
            attachment.url,
        )
        await ReskinConfig.revalidate(self.bot, ctx.author)

        return await ctx.approve("Your reskin avatar has been set")

    @reskin.command(name="remove", aliases=["reset"])
    async def reskin_remove(self, ctx: Context) -> Message:
        """
        Remove your personal reskin settings.
        """

        await self.bot.db.execute(
            """
            DELETE FROM reskin.config
            WHERE user_id = $1
            """,
            ctx.author.id,
        )
        await ReskinConfig.revalidate(self.bot, ctx.author)

        return await ctx.approve("Your reskin settings have been removed")
