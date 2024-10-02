from contextlib import suppress
from secrets import token_urlsafe
from typing import Annotated, Dict, List, Literal, Optional, TypedDict, cast, overload
from tools import CompositeMetaClass, MixinMeta
from discord import (
    ActionRow,
    AllowedMentions,
    ButtonStyle,
    CategoryChannel,
    Color,
    Embed,
    Guild,
    HTTPException,
    Interaction,
    Member,
    Message,
    PartialMessage,
    PermissionOverwrite,
    Role,
    TextChannel,
)
from discord.components import Button as ButtonComponent
from discord.ext.commands import group, has_permissions, Cog, flag, check, Range
from tools.client import FlagConverter, Context as OriginalContext
from discord.ui import View, Button
from discord.utils import find
from tools.conversion.discord import StrictRole, TouchableMember
from tools.parser import Script, parse
from tools.formatter import codeblock, vowel
from tools.paginator import Paginator


class Context(OriginalContext):
    channel: TextChannel


class TicketConfig(TypedDict):
    guild_id: int
    channel_id: int
    message_id: int
    staff_ids: list[int]
    blacklisted_ids: list[int]
    channel_name: Optional[str]


class TicketButton(TypedDict):
    identifier: str
    guild_id: int
    template: Optional[str]
    category_id: Optional[int]
    topic: Optional[str]


class TicketChannel(TypedDict):
    identifier: str
    guild_id: int
    channel_id: int
    user_id: int


class ButtonFlags(FlagConverter):
    style: Literal["blurple", "grey", "gray", "green", "red"] = flag(
        default="green",
        aliases=["color"],
    )
    emoji: Optional[str] = flag(
        aliases=["emote"],
    )


def in_ticket():
    async def predicate(ctx: Context):
        if not ctx.guild:
            return False

        record = cast(
            Optional[TicketChannel],
            await ctx.bot.db.fetchrow(
                """
                SELECT *
                FROM ticket.open
                WHERE guild_id = $1
                AND channel_id = $2
                """,
                ctx.guild.id,
                ctx.channel.id,
            ),
        )
        return bool(record)

    return check(predicate)


class Ticket(MixinMeta, metaclass=CompositeMetaClass):
    """
    Create tickets for users to contact the staff.
    """

    @overload
    async def get_ticket_message(
        self,
        guild: Guild,
        record: TicketConfig,
        partial: Literal[True],
    ) -> PartialMessage: ...

    @overload
    async def get_ticket_message(
        self,
        guild: Guild,
        record: TicketConfig,
        partial: Literal[False] = False,
    ) -> Optional[Message]: ...

    async def get_ticket_message(
        self,
        guild: Guild,
        record: TicketConfig,
        partial: bool = False,
    ) -> Optional[Message | PartialMessage]:
        channel = guild.get_channel_or_thread(record["channel_id"])
        if not channel:
            return

        if partial:
            return channel.get_partial_message(record["message_id"])  # type: ignore

        try:
            return await channel.fetch_message(record["message_id"])  # type: ignore
        except HTTPException:
            return None

    async def create_ticket_transcript(self, channel: TextChannel) -> List[dict]:
        messages: List[dict] = []
        async for message in channel.history(limit=None, oldest_first=True):
            if not message.guild:
                continue

            messages.append(
                {
                    "id": message.id,
                    "guild_id": message.guild.id,
                    "channel_id": message.channel.id,
                    "author": {
                        "id": message.author.id,
                        "avatar": message.author.display_avatar.key,
                        "bot": message.author.bot,
                        "discriminator": message.author.discriminator,
                        "username": message.author.name,
                    },
                    "mentions": [],
                    "content": message.system_content,
                    "timestamp": message.created_at,
                    "edited_timestamp": message.edited_at,
                    "attachments": [],
                    "embeds": [],
                }
            )

        return messages

    @Cog.listener("on_guild_channel_delete")
    async def ticket_channel_delete(self, channel: TextChannel):
        if not isinstance(channel, TextChannel):
            return

        await self.bot.db.execute(
            """
            DELETE FROM ticket.open
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            channel.guild.id,
            channel.id,
        )

    @Cog.listener("on_interaction")
    async def ticket_create(self, interaction: Interaction):
        """
        Listener for the ticket interaction.
        """

        if (
            not interaction.data
            or not interaction.guild
            or not isinstance(interaction.user, Member)
        ):
            return

        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.endswith("ticket_create"):
            return

        guild = interaction.guild
        member = interaction.user
        identifier = custom_id.split(":", 1)[0]

        config = cast(
            Optional[TicketConfig],
            await self.bot.db.fetchrow(
                """
                SELECT *
                FROM ticket.config
                WHERE guild_id = $1
                """,
                guild.id,
            ),
        )
        record = cast(
            Optional[TicketButton],
            await self.bot.db.fetchrow(
                """
                SELECT *
                FROM ticket.button
                WHERE guild_id = $1
                AND identifier = $2
                """,
                guild.id,
                identifier,
            ),
        )
        if not config or not record:
            return await interaction.response.send_message(
                embed=Embed(
                    color=Color.dark_embed(),
                    description=(
                        "This button shouldn't exist anymore!"
                        "\n> Please contact a staff member about this"
                    ),
                ),
                ephemeral=True,
            )

        elif member.id in config["blacklisted_ids"] or [
            role for role in member.roles if role.id in config["blacklisted_ids"]
        ]:
            return await interaction.response.send_message(
                embed=Embed(
                    color=Color.dark_embed(),
                    description="You're not allowed to create tickets!",
                ),
                ephemeral=True,
            )

        ticket = cast(
            Optional[TicketChannel],
            await self.bot.db.fetchrow(
                """
                SELECT *
                FROM ticket.open
                WHERE guild_id = $1
                AND user_id = $2
                AND identifier = $3
                """,
                guild.id,
                member.id,
                identifier,
            ),
        )
        if ticket:
            channel = guild.get_channel(ticket["channel_id"])
            if channel:
                return await interaction.response.send_message(
                    embed=Embed(
                        color=Color.dark_embed(),
                        description=f"You already have an open ticket - {channel.mention}",
                    ),
                    ephemeral=True,
                )

        await interaction.response.defer(ephemeral=True, thinking=True)
        category = guild.get_channel(record["category_id"] or 0)
        if not isinstance(category, CategoryChannel):
            category = None

        overwrites: Dict[Role | Member, PermissionOverwrite] = {
            guild.default_role: PermissionOverwrite(
                view_channel=False,
                read_messages=False,
            ),
        }
        for target in (
            member,
            *[
                role
                for role_id in config["staff_ids"]
                if (role := guild.get_role(role_id))
            ],
        ):
            overwrites[target] = PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                read_message_history=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                mention_everyone=False,
            )

        try:
            channel = await guild.create_text_channel(
                name=parse(
                    config["channel_name"] or f"ticket-{member.name}",
                    [guild, member],
                )[:100],
                category=category,
                topic=parse(record["topic"] or "", [guild, member]),
                overwrites=overwrites,
                reason=f"Ticket opened by {member} ({member.id})",
            )
        except HTTPException as exc:
            return await interaction.followup.send(
                embed=Embed(
                    color=Color.dark_embed(),
                    description=(
                        "Falied to create a ticket channel!"
                        f"\n> {codeblock(exc.text)}"
                    ),
                ),
                ephemeral=True,
            )

        await self.bot.db.execute(
            """
            INSERT INTO ticket.open (
                identifier,
                guild_id,
                channel_id,
                user_id
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (identifier, guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id
            """,
            identifier,
            guild.id,
            channel.id,
            member.id,
        )

        await interaction.followup.send(
            embed=Embed(
                color=Color.dark_embed(),
                description=f"Created a new ticket - {channel.mention}",
            ),
            ephemeral=True,
        )
        if record["template"]:
            script = Script(
                record["template"],
                [guild, member, channel],
            )
            with suppress(HTTPException):
                await script.send(
                    channel,
                    allowed_mentions=AllowedMentions.all(),
                )

    @group(
        aliases=["tickets"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket(self, ctx: Context) -> Message:
        """
        Manage support tickets.
        """

        return await ctx.send_help(ctx.command)

    @ticket.group(
        name="panel",
        aliases=["message", "link"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_panel(self, ctx: Context, message: Message) -> Message:
        """
        Set the ticket panel message.
        """

        if message.guild != ctx.guild:
            return await ctx.warn("The message must be in this server!")

        elif message.author != ctx.guild.me:
            return await ctx.warn("The message must be from me!")

        await self.bot.db.execute(
            """
            INSERT INTO ticket.config (guild_id, channel_id, message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                message_id = EXCLUDED.message_id
            """,
            ctx.guild.id,
            message.channel.id,
            message.id,
        )
        return await ctx.approve(
            f"Successfully  set that [`message`]({message.jump_url}) as a ticket panel.",
            f"Use `{ctx.clean_prefix}ticket button` to attach buttons",
        )

    @ticket_panel.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_panel_remove(self, ctx: Context) -> Message:
        """
        Remove the ticket panel message.
        """

        record = cast(
            Optional[TicketConfig],
            await self.bot.db.fetchrow(
                """
                DELETE FROM ticket.config
                WHERE guild_id = $1
                RETURNING *
                """,
                ctx.guild.id,
            ),
        )
        if not record:
            return await ctx.warn("The ticket panel hasn't been set yet!")

        message = await self.get_ticket_message(ctx.guild, record, partial=True)
        if message:
            with suppress(HTTPException):
                await message.delete()

        return await ctx.approve("Successfully  removed the ticket panel")

    @ticket.group(
        name="button",
        aliases=["option"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_button(self, ctx: Context) -> Message:
        """
        Control the buttons on the panel.
        """

        return await ctx.send_help(ctx.command)

    @ticket_button.command(
        name="add",
        aliases=["button"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_button_add(self, ctx: Context, *, label: str) -> Message:
        """
        Add a button to the ticket panel.

        Accepts the following flags:
        `--color`: `blurple`, `grey`, `green` or `red`.
        `--emoji`: A unicode or custom emoji.
        """

        label, flags = await ButtonFlags().find(ctx, label)
        if not label:
            return await ctx.warn("You must provide a button label!")

        record: TicketConfig = await self.bot.db.fetchrow(
            """
            SELECT *
            FROM ticket.config
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if not record:
            return await ctx.warn(
                "The ticket panel hasn't been set yet!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        message = await self.get_ticket_message(ctx.guild, record)
        if not message:
            return await ctx.warn(
                "The ticket panel no longer exists!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        view = View()
        if message.components and isinstance(message.components[0], ActionRow):
            for child in message.components[0].children:
                if isinstance(child, ButtonComponent):
                    view.add_item(
                        Button(
                            style=child.style,
                            label=child.label,
                            disabled=child.disabled,
                            custom_id=child.custom_id,
                            emoji=child.emoji,
                        ),
                    )

        identifier = token_urlsafe(13)
        view.add_item(
            Button(
                style=getattr(
                    ButtonStyle,
                    flags.style,
                    ButtonStyle.green,
                ),
                label=label,
                emoji=flags.emoji,
                custom_id=f"{identifier}:ticket_create",
            ),
        )

        try:
            await message.edit(view=view)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your **button**!",
                codeblock(exc.text),
            )

        await self.bot.db.execute(
            """
            INSERT INTO ticket.button (identifier, guild_id)
            VALUES ($1, $2)
            ON CONFLICT (identifier, guild_id)
            DO NOTHING
            """,
            identifier,
            ctx.guild.id,
        )
        return await ctx.approve(
            f"Added button **{flags.emoji or ''} {label}** with identifier [`{identifier}`]({message.jump_url}).",
            "Use the identifier above to change what this button does",
        )

    @ticket_button.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_button_remove(self, ctx: Context, identifier: str) -> Message:
        """
        Remove a button from the ticket panel.

        You can use `ticket button list` to view all identifiers.
        """

        record: TicketConfig = await self.bot.db.fetchrow(
            """
            SELECT *
            FROM ticket.config
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if not record:
            return await ctx.warn(
                "The ticket panel hasn't been set yet!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        message = await self.get_ticket_message(ctx.guild, record)
        if not message:
            return await ctx.warn(
                "The ticket panel no longer exists!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        result = await self.bot.db.execute(
            """
            DELETE FROM ticket.button
            WHERE guild_id = $1
            AND identifier = $2
            """,
            ctx.guild.id,
            identifier,
        )
        if result == "DELETE 0":
            return await ctx.warn(
                f"A button with identifier [`{identifier}`]({message.jump_url}) doesn't exist!"
            )

        view = View()
        if message.components and isinstance(message.components[0], ActionRow):
            for child in message.components[0].children:
                if isinstance(child, ButtonComponent):
                    if (
                        child.custom_id
                        and child.custom_id.split(":", 1)[0] == identifier
                    ):
                        continue

                    view.add_item(
                        Button(
                            style=child.style,
                            label=child.label,
                            disabled=child.disabled,
                            custom_id=child.custom_id,
                            emoji=child.emoji,
                        ),
                    )

        try:
            await message.edit(view=view)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your **panel**!",
                codeblock(exc.text),
            )

        return await ctx.approve(
            f"Removed button with identifier [`{identifier}`]({message.jump_url})"
        )

    @ticket_button.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_button_list(self, ctx: Context) -> Message:
        """
        View all button identifiers.
        """

        record: TicketConfig = await self.bot.db.fetchrow(
            """
            SELECT *
            FROM ticket.config
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if not record:
            return await ctx.warn(
                "The ticket panel hasn't been set yet!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        message = await self.get_ticket_message(ctx.guild, record)
        if not message:
            return await ctx.warn(
                "The ticket panel no longer exists!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        elif not message.components or not isinstance(message.components[0], ActionRow):
            return await ctx.warn("No buttons have been added yet!")

        children = message.components[0].children
        buttons = [
            f"**{button.emoji or ''} {button.label}** (`{record['identifier']}`)"
            for record in await self.bot.db.fetch(
                """
                SELECT identifier
                FROM ticket.button
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (
                button := find(
                    lambda b: b.custom_id
                    and b.custom_id.startswith(record["identifier"]),
                    children,
                )
            )
            and isinstance(button, ButtonComponent)
        ]
        if not buttons:
            return await ctx.warn("No buttons have been added yet!")

        paginator = Paginator(
            ctx,
            entries=buttons,
            embed=Embed(title="Ticket Buttons"),
        )
        return await paginator.start()

    @ticket.command(
        name="open",
        aliases=["welcome", "opening"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_welcome(
        self,
        ctx: Context,
        identifier: str,
        *,
        script: Script,
    ) -> Message:
        """
        Set the opening message for a ticket.
        """

        record = cast(
            Optional[TicketButton],
            await self.bot.db.fetchrow(
                """
                SELECT *
                FROM ticket.button
                WHERE guild_id = $1
                AND identifier = $2
                """,
                ctx.guild.id,
                identifier,
            ),
        )
        if not record:
            return await ctx.warn(
                f"A button with identifier `{identifier}` doesn't exist!",
                f"Use `{ctx.clean_prefix}ticket button list` to view all identifiers",
            )

        await self.bot.db.execute(
            """
            UPDATE ticket.button
            SET template = $3
            WHERE guild_id = $1
            AND identifier = $2
            """,
            ctx.guild.id,
            identifier,
            script.template,
        )
        return await ctx.approve(
            f"Now sending {vowel(script.format)} message to tickets from button `{identifier}`"
        )

    @ticket.group(
        name="category",
        aliases=["redirect"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_category(
        self,
        ctx: Context,
        identifier: str,
        *,
        channel: CategoryChannel,
    ) -> Message:
        """
        Set the category for a ticket button.
        """

        record = cast(
            Optional[TicketButton],
            await self.bot.db.fetchrow(
                """
                SELECT *
                FROM ticket.button
                WHERE guild_id = $1
                AND identifier = $2
                """,
                ctx.guild.id,
                identifier,
            ),
        )
        if not record:
            return await ctx.warn(
                f"A button with identifier `{identifier}` doesn't exist!",
                f"Use `{ctx.clean_prefix}ticket button list` to view all identifiers",
            )

        await self.bot.db.execute(
            """
            UPDATE ticket.button
            SET category_id = $3
            WHERE guild_id = $1
            AND identifier = $2
            """,
            ctx.guild.id,
            identifier,
            channel.id,
        )
        return await ctx.approve(
            f"Now redirecting tickets from button `{identifier}` to [`{channel.name}`]({channel.jump_url})"
        )

    @ticket.command(
        name="name",
        aliases=["channel"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_name(
        self,
        ctx: Context,
        *,
        name: Range[str, 1, 100],
    ) -> Message:
        """
        Set the name for new ticket channels.
        """

        result = await self.bot.db.execute(
            """
            UPDATE ticket.config
            SET channel_name = $2
            WHERE guild_id = $1
            """,
            ctx.guild.id,
            name,
        )
        if result.endswith("0"):
            return await ctx.warn(
                "The ticket panel hasn't been set yet!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        return await ctx.approve(
            f"Now using `{name}` for ticket channel names.",
            f"It will appear as **{parse(name, [ctx.guild, ctx.author])}**",
        )

    @ticket_category.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_category_remove(
        self,
        ctx: Context,
        identifier: str,
    ) -> Message:
        """
        Remove the category for a ticket button.
        """

        record = cast(
            Optional[TicketButton],
            await self.bot.db.fetchrow(
                """
                SELECT *
                FROM ticket.button
                WHERE guild_id = $1
                AND identifier = $2
                """,
                ctx.guild.id,
                identifier,
            ),
        )
        if not record:
            return await ctx.warn(
                f"A button with identifier `{identifier}` doesn't exist!",
                f"Use `{ctx.clean_prefix}ticket button list` to view all identifiers",
            )

        await self.bot.db.execute(
            """
            UPDATE ticket.button
            SET category_id = NULL
            WHERE guild_id = $1
            AND identifier = $2
            """,
            ctx.guild.id,
            identifier,
        )
        return await ctx.approve(
            f"No longer redirecting tickets from button `{identifier}`"
        )

    @ticket.group(
        name="staff",
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_staff(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """
        Allow a role to see new tickets.
        """

        result = await self.bot.db.execute(
            """
            UPDATE ticket.config
            SET staff_ids = ARRAY_APPEND(staff_ids, $2::BIGINT)
            WHERE NOT staff_ids @> ARRAY[$2::BIGINT]
            AND guild_id = $1
            """,
            ctx.guild.id,
            role.id,
        )
        if result.endswith("0"):
            return await ctx.warn(f"{role.mention} is already allowed!")

        return await ctx.approve(
            f"Now allowing {role.mention} to see new tickets",
        )

    @ticket_staff.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_staff_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """
        Disallow a role from seeing tickets.
        """

        result = await self.bot.db.execute(
            """
            UPDATE ticket.config
            SET staff_ids = ARRAY_REMOVE(staff_ids, $2::BIGINT)
            WHERE guild_id = $1
            AND staff_ids @> ARRAY[$2::BIGINT]
            """,
            ctx.guild.id,
            role.id,
        )
        if result.endswith("0"):
            return await ctx.warn(f"{role.mention} already isn't allowed!")

        return await ctx.approve(
            f"No longer allowing {role.mention} to see new tickets",
        )

    @ticket_staff.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_staff_list(self, ctx: Context) -> Message:
        """
        View all roles which can see tickets.
        """

        roles = [
            f"{role.mention} (`{role.id}`)"
            for record in await self.bot.db.fetch(
                """
                SELECT UNNEST(staff_ids) AS role_id
                FROM ticket.config
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (role := ctx.guild.get_member(record["role_id"]))
        ]
        if not roles:
            return await ctx.warn("No roles have been allowed yet!")

        paginator = Paginator(
            ctx,
            entries=roles,
            embed=Embed(title="Ticket Staff"),
        )
        return await paginator.start()

    @ticket.group(
        name="ignore",
        aliases=["blacklist"],
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_ignore(
        self,
        ctx: Context,
        *,
        target: Annotated[
            Role,
            StrictRole,
        ]
        | Annotated[Member, TouchableMember],
    ) -> Message:
        """
        Prevent a role or member from creating tickets.
        """

        result = await self.bot.db.execute(
            """
            UPDATE ticket.config
            SET blacklisted_ids = ARRAY_APPEND(blacklisted_ids, $2::BIGINT)
            WHERE NOT blacklisted_ids @> ARRAY[$2::BIGINT]
            AND guild_id = $1
            """,
            ctx.guild.id,
            target.id,
        )
        if result.endswith("0"):
            return await ctx.warn(f"{target.mention} is already blacklisted!")

        return await ctx.approve(
            f"No longer allowing {target.mention} to create tickets"
        )

    @ticket_ignore.command(
        name="remove",
        aliases=[
            "delete",
            "del",
            "rm",
        ],
    )
    @has_permissions(manage_channels=True)
    async def ticket_ignore_remove(
        self,
        ctx: Context,
        *,
        target: Annotated[Role, StrictRole] | Annotated[Member, TouchableMember],
    ) -> Message:
        """
        Allow an entity to make tickets again.
        """

        result = await self.bot.db.execute(
            """
            UPDATE ticket.config
            SET blacklisted_ids = ARRAY_REMOVE(blacklisted_ids, $2::BIGINT)
            WHERE guild_id = $1
            AND blacklisted_ids @> ARRAY[$2::BIGINT]
            """,
            ctx.guild.id,
            target.id,
        )
        if result.endswith("0"):
            return await ctx.warn(f"{target.mention} isn't blacklisted!")

        return await ctx.approve(f"Now allowing {target.mention} to create tickets")

    @ticket_ignore.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_channels=True)
    async def ticket_ignore_list(self, ctx: Context) -> Message:
        """
        View all blacklisted entities.
        """

        targets = [
            f"{target.mention} (`{target.id}`)"
            for record in await self.bot.db.fetch(
                """
                SELECT UNNEST(blacklisted_ids) AS target_id
                FROM ticket.config
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (target := ctx.guild.get_member(record["target_id"]))
            or (target := ctx.guild.get_role(record["target_id"]))
        ]
        if not targets:
            return await ctx.warn("No members have been blacklisted yet!")

        paginator = Paginator(
            ctx,
            entries=targets,
            embed=Embed(title="Ticket Blacklisted"),
        )
        return await paginator.start()

    @ticket.command(
        name="add",
        aliases=["allow"],
    )
    @in_ticket()
    @has_permissions(manage_channels=True)
    async def ticket_add(
        self,
        ctx: Context,
        target: Annotated[Role, StrictRole] | Annotated[Member, TouchableMember],
    ) -> Message:
        """
        Add a role or member to the ticket.
        """

        await ctx.channel.set_permissions(
            target,
            view_channel=True,
            read_messages=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            mention_everyone=False,
            reason=f"Granted access by {ctx.author} ({ctx.author.id}).",
        )
        return await ctx.approve(f"Now allowing {target.mention} to see this ticket")

    @ticket.command(
        name="remove",
        aliases=["hide"],
    )
    @in_ticket()
    @has_permissions(manage_channels=True)
    async def ticket_remove(
        self,
        ctx: Context,
        target: Annotated[Role, StrictRole] | Annotated[Member, TouchableMember],
    ) -> Message:
        """
        Remove a role or member from the ticket.
        """

        await ctx.channel.set_permissions(
            target,
            overwrte=None,
            reason=f"Denied access by {ctx.author} ({ctx.author.id}).",
        )
        return await ctx.approve(
            f"No longer allowing {target.mention} to see this ticket"
        )

    @ticket.command(
        name="close",
        aliases=["end"],
    )
    @in_ticket()
    @has_permissions(manage_channels=True)
    async def ticket_close(self, ctx: Context) -> None:
        """
        Close an open ticket and forward the transcript.
        """

        await self.bot.db.execute(
            """
            DELETE FROM ticket.open
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            ctx.guild.id,
            ctx.channel.id,
        )
        await ctx.channel.delete(reason=f"Closed by {ctx.author} ({ctx.author.id}).")
