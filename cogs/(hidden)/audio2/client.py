from typing import Any, Optional
from discord import Embed, Guild, HTTPException, Message
from wavelink import Player, AutoPlayMode
from wavelink import Playable as Track
from cashews import cache
from contextlib import suppress

from main import greedbot
from tools.client import Context
from tools.formatter import shorten


class Client(Player):
    bot: greedbot
    guild: Guild
    message: Optional[Message]
    context: Optional[Context]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bot = self.client  # type: ignore
        self.autoplay = AutoPlayMode.partial
        self.message = None
        self.context = None

    def embed(self, track: Track) -> Embed:
        member = self.guild.get_member(getattr(track.extras, "requester_id") or 0)
        requester = (
            member.display_name
            if member and not track.recommended
            else f"{track.source.title()} DJ"
        )
        embed = Embed(
            description=f"Now playing [{shorten(track.title)}]({track.uri}) by **{shorten(track.author)}**"
        )

        footer = [f"🎶 {requester}"]
        if track.album.name:
            footer.append(f"💿 {track.album.name}")
        embed.set_footer(text=" ".join(footer))
        return embed

    @cache(ttl="30s")
    async def deserialize(self, query: str) -> str:
        response = await self.bot.session.post(
            "https://metadata-filter.vercel.app/api/youtube", params={"track": query}
        )
        with suppress(Exception):
            data = await response.json()
            return data["data"]["track"]

        return query

    async def _destroy(self, **kwargs: Any) -> None:
        if self.message:
            with suppress(HTTPException):
                await self.message.delete()

        if (reason := kwargs.pop("reason", None)) and self.context:
            with suppress(HTTPException):
                await self.context.channel.send(embed=Embed(description=reason))

        return await super()._destroy()
        await self.guild.change_voice_state(channel=None)
