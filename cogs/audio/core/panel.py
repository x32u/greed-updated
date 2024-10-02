from __future__ import annotations
from contextlib import suppress

from typing import TYPE_CHECKING, Any
from discord.ui.item import Item

import validators
from discord import ButtonStyle, Color, Embed, HTTPException, Interaction
from discord.ui import Button, Modal, TextInput, View, button
from pomice import LoopMode, Playlist, QueueEmpty

from config import EMOJIS
from tools.formatter import plural, shorten

if TYPE_CHECKING:
    from cogs.audio.audio import Context

    from .player import Client


class Queue(Modal, title="Queue"):
    ctx: Context
    client: Client

    track = TextInput(
        label="Track",
        placeholder="Search query..",
    )

    def __init__(self, ctx: Context, client: Client):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.client = client

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user not in self.client.channel.members:
            embed = Embed(
                description=f"You must be in {self.client.channel.mention} to use this!",
                color=Color.dark_embed(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        return interaction.user in self.client.channel.members

    async def on_submit(self, interaction: Interaction) -> None:
        query = self.track.value
        result = await self.client.get_tracks(self.track.value, ctx=self.ctx)
        if not result:
            await interaction.response.send_message(
                embed=Embed(
                    description="No tracks were found!",
                    color=Color.dark_embed(),
                ),
                ephemeral=True,
            )
            return

        if isinstance(result, Playlist):
            for track in result.tracks:
                self.client.insert(track)

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Added {plural(result.track_count, md='**'):track} from [{result.name}]({result.uri or query if validators.url(query) else 'https://google.com'}) to the queue via {interaction.user.mention}",
                    color=Color.dark_embed(),
                ),
                delete_after=7,
            )

        else:
            track = result[0]
            self.client.insert(track)
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Added [**{shorten(track.title)}**]({track.uri}) to the queue via {interaction.user.mention}",
                    color=Color.dark_embed(),
                ),
                delete_after=7,
            )


class Panel(View):
    client: Client

    def __init__(self, ctx: Context):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.client = ctx.voice
        self.repeat.emoji = (
            EMOJIS.AUDIO.REPEAT_TRACK
            if self.client.queue.loop_mode == LoopMode.TRACK
            else EMOJIS.AUDIO.REPEAT
        )
        self.repeat.style = (
            ButtonStyle.primary
            if self.client.queue.loop_mode
            else ButtonStyle.secondary
        )
        self.toggle.emoji = (
            EMOJIS.AUDIO.RESUME if self.client.is_paused else EMOJIS.AUDIO.PAUSE
        )
        # self.previous.disabled = not bool(self.client.queue.history)

    async def on_error(
        self, interaction: Interaction, error: Exception, item: Item[Any]
    ) -> None: ...

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user not in self.client.channel.members:
            embed = Embed(
                description=f"You must be in {self.client.channel.mention} to use this!",
                color=Color.dark_embed(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        if (
            not self.ctx.author.guild_permissions.administrator
            and await self.ctx.db.fetchrow(
                """
                SELECT 1
                FROM commands.ignore
                WHERE guild_id = $1
                AND (
                    target_id = $2
                    OR target_id = $3
                )
                """,
                self.ctx.guild.id,
                self.ctx.author.id,
                self.ctx.channel.id,
            )
        ):
            embed = Embed(
                description="You can't use this feature!",
                color=Color.dark_embed(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        return interaction.user in self.client.channel.members

    @button(custom_id="REPEAT")
    async def repeat(self, interaction: Interaction, button: Button):
        queue = self.client.queue
        if not queue.loop_mode:
            queue.set_loop_mode(LoopMode.QUEUE)
            button.emoji = EMOJIS.AUDIO.REPEAT
            button.style = ButtonStyle.primary

        elif queue.loop_mode == LoopMode.QUEUE:
            queue.set_loop_mode(LoopMode.TRACK)
            button.emoji = EMOJIS.AUDIO.REPEAT_TRACK
            button.style = ButtonStyle.primary

        else:
            queue.disable_loop()
            button.emoji = EMOJIS.AUDIO.REPEAT
            button.style = ButtonStyle.secondary

        await interaction.response.edit_message(view=self)

    @button(custom_id="PREVIOUS", emoji=EMOJIS.AUDIO.PREVIOUS)
    async def previous(self, interaction: Interaction, button: Button):
        if not self.client.queue.history:
            return await interaction.response.send_message(
                embed=Embed(
                    description="There aren't any previous tracks!",
                    color=Color.dark_embed(),
                ),
                ephemeral=True,
                delete_after=7,
            )

        try:
            track = self.client.queue.history.get()
        except QueueEmpty:
            return await interaction.response.send_message(
                embed=Embed(
                    description="There aren't any previous tracks!",
                    color=Color.dark_embed(),
                ),
                ephemeral=True,
                delete_after=7,
            )

        self.client.insert(track, bump=True)
        await self.client.stop()

    @button(custom_id="TOGGLE", emoji=EMOJIS.AUDIO.PAUSE, style=ButtonStyle.primary)
    async def toggle(self, interaction: Interaction, button: Button):
        await self.client.set_pause(not self.client.is_paused)
        button.emoji = (
            EMOJIS.AUDIO.RESUME if self.client.is_paused else EMOJIS.AUDIO.PAUSE
        )

        await interaction.response.edit_message(view=self)
        message = await interaction.followup.send(
            embed=Embed(
                description=f"{interaction.user.mention} {'paused' if self.client.is_paused else 'resumed'} the track",
                color=Color.dark_embed(),
            ),
            wait=True,
        )
        with suppress(HTTPException):
            await message.delete(delay=3)

    @button(custom_id="SKIP", emoji=EMOJIS.AUDIO.SKIP)
    async def skip(self, interaction: Interaction, button: Button):
        await self.client.stop()

    @button(custom_id="QUEUE", emoji=EMOJIS.AUDIO.QUEUE)
    async def queue(self, interaction: Interaction, button: Button):
        await interaction.response.send_modal(Queue(self.ctx, self.client))
