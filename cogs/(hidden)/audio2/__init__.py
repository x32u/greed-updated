import re
import asyncio
from contextlib import suppress
from typing import Literal, Optional, cast

from discord import Embed, HTTPException, Message, VoiceChannel
from config import LAVALINK
from main import greedbot
from tools.client import Context as OriginalContext
from wavelink import (
    LavalinkLoadException,
    Node,
    NodeReadyEventPayload,
    Playable as Track,
    Playlist,
    QueueMode,
    Pool,
    TrackEndEventPayload,
    TrackExceptionEventPayload,
    TrackStartEventPayload,
)
from wavelink import TrackSource
from pomice.enums import URLRegex as regex
from discord.ext.commands import group, command, parameter, Range, CommandError, Cog
from tools.paginator import Paginator
from logging import getLogger
from tools.formatter import duration, plural, shorten
from humanize import ordinal
from .client import Client

SOURCE_PATTERNS = (
    regex.SPOTIFY_URL,
    regex.YOUTUBE_URL,
    regex.YOUTUBE_PLAYLIST_URL,
    regex.AM_URL,
    regex.AM_SINGLE_IN_ALBUM_REGEX,
    regex.SOUNDCLOUD_URL,
    regex.SOUNDCLOUD_PLAYLIST_URL,
    regex.SOUNDCLOUD_TRACK_IN_SET_URL,
    re.compile(
        r"https?://(?:www\.)?soundgasm\.net/u/([0-9a-zA-Z_-]+)/([0-9a-zA-Z_-]+)"
    ),
    re.compile(
        r"^https:\/\/(?:www\.)?pornhub\.com\/view_video\.php\?viewkey=[a-zA-Z0-9]+$"
    ),
)

log = getLogger("greedbot.audio")


class Context(OriginalContext):
    voice: Client


class Audio(Cog):
    def __init__(self, bot: greedbot):
        self.bot = bot

    async def cog_load(self) -> None:
        if not Pool.nodes:
            nodes = [
                Node(
                    uri=f"http://{LAVALINK.HOST}:{LAVALINK.PORT}",
                    password=LAVALINK.PASSWORD,
                    resume_timeout=180,
                )
            ]
            await Pool.connect(nodes=nodes, client=self.bot, cache_capacity=100)

        return await super().cog_load()

    async def cog_before_invoke(self, ctx: Context) -> None:
        ctx.voice = await self.get_player(ctx)

    async def get_player(self, ctx: Context) -> Client:
        client = ctx.voice_client
        author = ctx.author

        if not author.voice or not author.voice.channel:
            raise CommandError("You're not in a voice channel")

        elif client and client.channel != author.voice.channel:
            raise CommandError("You're not in my voice channel")

        elif not client:
            if ctx.command not in (self.play, self.play_bump):
                raise CommandError("I'm not in a voice channel")

            elif not author.voice.channel.permissions_for(ctx.guild.me).connect:
                raise CommandError(
                    "I'm missing permission to connect to your voice channel"
                )

            client = await author.voice.channel.connect(cls=Client, self_deaf=True)
            client.context = ctx
            await client.set_volume(30)

        return cast(Client, client)

    @Cog.listener()
    async def on_wavelink_node_ready(self, payload: NodeReadyEventPayload) -> None:
        if not payload.resumed:
            return

        node = payload.node
        log.warning(
            f"Node ID {node.identifier} has Successfully  RESUMED session {node.session_id}"
        )
        for client in node.players.values():
            await client._dispatch_voice_update()
            if client.current:
                await client.play(client.current, start=client.position)

    @Cog.listener()
    async def on_wavelink_track_start(self, payload: TrackStartEventPayload) -> None:
        client = cast(Client, payload.player)
        track = payload.track

        if not client:
            return

        if isinstance(client.channel, VoiceChannel):
            title = track.title
            if track.source in ("spotify", "applemusic"):
                title = f"{track.title} by {track.author}"

            with suppress(HTTPException):
                await client.channel.edit(status=title)

        if client.context:
            with suppress(HTTPException):
                embed = client.embed(track)
                client.message = await client.context.channel.send(embed=embed)

    @Cog.listener()
    async def on_wavelink_track_end(self, payload: TrackEndEventPayload):
        client = cast(Client, payload.player)
        track = payload.track

        if not client or not isinstance(client.channel, VoiceChannel):
            return

        if not (client.queue or client.autoplay):
            with suppress(HTTPException):
                await client.channel.edit(status="")

        if client.message:
            with suppress(HTTPException):
                await client.message.delete()

    @Cog.listener()
    async def on_wavelink_track_exception(
        self,
        payload: TrackExceptionEventPayload,
    ) -> None:
        client = cast(Client, payload.player)
        exc_key = f"audio-exc:{client.channel.id}"
        if await self.bot.redis.ratelimited(exc_key, 3, 1):
            return await client._destroy(
                reason="Too many load failures to safely continue"
            )

    @Cog.listener()
    async def on_wavelink_inactive_player(self, client: Client):
        await client._destroy(reason="Leaving the voice channel due to inactivity")

    @command(aliases=("stop", "dc"))
    async def disconnect(self, ctx: Context) -> None:
        """
        Disconnect from the voice channel.
        """

        await ctx.voice.disconnect()
        return await ctx.message.add_reaction("✅")

    @group(aliases=("q",), invoke_without_command=True)
    async def queue(self, ctx: Context) -> Message:
        """
        View all songs in the queue.
        """

        queue = ctx.voice.queue or ctx.voice.auto_queue
        if not ctx.voice.current and not queue:
            return await ctx.warn("No tracks are in the queue")

        embed = Embed(title=f"Queue for {ctx.voice.channel.name}")
        if track := ctx.voice.current:
            embed.description = f"Listening to [{shorten(track.title)}]({track.uri}) by **{track.author}** [`{duration(ctx.voice.position)}/{duration(track.length)}`]"

        if len(queue) > 10:
            embed.set_footer(text=format(plural(len(queue)), "track"))

        paginator = Paginator(
            ctx,
            entries=[
                f"[{shorten(track.title)}]({track.uri}) by **{shorten(track.author)}**"
                for track in queue
            ],
            embed=embed,
        )
        return await paginator.start()

    @queue.command(name="clear", aliases=("clean", "reset"))
    async def queue_clear(self, ctx: Context) -> Optional[Message]:
        """
        Remove all tracks from the queue.
        """

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        queue.clear()
        return await ctx.add_check()

    @queue.command(name="shuffle", aliases=("mix",))
    async def queue_shuffle(self, ctx: Context) -> Optional[Message]:
        """
        Shuffle the queue.
        """

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        queue.shuffle()
        return await ctx.add_check()

    @queue.command(name="remove", aliases=("del", "rm"))
    async def queue_remove(self, ctx: Context, position: int) -> Message:
        """
        Remove a track from the queue.
        """

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        elif not 0 < position <= len(queue):
            return await ctx.warn(
                f"Invalid position - must be between `1` and `{len(queue)}`"
            )

        track = queue[position - 1]
        queue.remove(track)

        return await ctx.approve(
            f"Removed [{shorten(track.title)}]({track.uri}) from the queue"
        )

    @queue.command(name="move", aliases=("mv",))
    async def queue_move(
        self,
        ctx: Context,
        position: int,
        new_position: int,
    ) -> Message:
        """
        Move a track in the queue.
        """

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        elif not 0 < position <= len(queue):
            return await ctx.warn(
                f"Invalid position - must be between `1` and `{len(queue)}`"
            )

        elif not 0 < new_position <= len(queue):
            return await ctx.warn(
                f"Invalid new position - must be between `1` and `{len(queue)}`"
            )

        track = queue[position - 1]
        queue.remove(track)
        queue.put_at(new_position - 1, track)

        return await ctx.approve(
            f"Moved [{shorten(track.title)}]({track.uri}) to `{ordinal(new_position)}` in the queue"
        )

    @group(aliases=("p",), invoke_without_command=True)
    async def play(
        self,
        ctx: Context,
        *,
        query: Optional[str] = parameter(
            description="Search query or audio file attachment.",
        ),
    ) -> Message:
        """
        Add a song to the queue.
        """

        if not query:
            if not ctx.message.attachments:
                return await ctx.send_help(ctx.command)

            if len(ctx.message.attachments) > 1:
                for attachment in ctx.message.attachments[1:]:
                    await self.play(ctx, query=attachment.url)
                    await asyncio.sleep(0.8)

            attachment = ctx.message.attachments[0]
            query = attachment.url

        bump = "bump" in query.lower()
        query = (
            query.replace("bump", "")
            .replace(
                "spotify:track:",
                "https://open.spotify.com/track/",
            )
            .strip()
        )

        if (
            ctx.author.id not in self.bot.owner_ids
            and regex.BASE_URL.match(query)
            and not any(pattern.match(query) for pattern in SOURCE_PATTERNS)
            and not regex.DISCORD_MP3_URL.match(query)
        ):
            return await ctx.reply("no")

        result = None
        with suppress(LavalinkLoadException):
            result = await Track.search(query)

        if not result:
            return await ctx.warn("Couldn't find that song")

        if isinstance(result, Playlist):
            for track in result.tracks:
                track.extras = {"requester_id": ctx.author.id}

            await ctx.voice.queue.put_wait(result)
            message = await ctx.approve(
                f"Added **{plural(result.tracks):track}** to the queue"
            )
        else:
            track = result[0]
            track.extras = {"requester_id": ctx.author.id}
            await ctx.voice.queue.put_wait(
                track
            ) if not bump else ctx.voice.queue.put_at(0, track)
            queued = list(
                filter(
                    lambda t: (t.uri or "") in (a.url for a in ctx.message.attachments)
                    and t != track,
                    ctx.voice.queue,
                )
            )
            message = await ctx.approve(
                f"Added [{shorten(str(track))}]({track.uri}) to the queue"
                + (f" (`+{len(queued)}` more)" if queued else "")
            )

        if not ctx.voice.playing:
            await ctx.voice.play(ctx.voice.queue.get())

        return message

    @play.command(name="bump")
    async def play_bump(self, ctx: Context, *, query: str) -> Message:
        """
        Add a track to the front of the queue.
        """

        return await self.play(ctx, query=f"{query} bump")

    @command(aliases=("next", "sk"))
    async def skip(self, ctx: Context) -> None:
        """
        Skip the current track.
        """

        await ctx.voice.skip(force=True)
        return await ctx.add_check()

    @command()
    async def pause(self, ctx: Context) -> None:
        """
        Pause the current track.
        """

        if not ctx.voice.playing:
            raise CommandError("I'm not playing anything")

        elif ctx.voice.paused:
            raise CommandError("The track is already paused")

        await ctx.voice.pause(True)
        return await ctx.add_check()

    @command()
    async def resume(self, ctx: Context) -> None:
        """
        Resume the current track.
        """

        if not ctx.voice.current:
            raise CommandError("I'm not playing anything")

        elif not ctx.voice.paused:
            raise CommandError("The track is not paused")

        await ctx.voice.pause(False)
        return await ctx.add_check()

    @command(aliases=("mix",))
    async def shuffle(self, ctx: Context) -> Optional[Message]:
        """
        Shuffle the queue.
        """

        return await self.queue_shuffle(ctx)

    @command(aliases=("loop",))
    async def repeat(
        self,
        ctx: Context,
        option: Literal["track", "queue", "off"],
    ) -> None:
        """
        Set the repeat mode.
        """

        if option == "track":
            ctx.voice.queue.mode = QueueMode.loop
            return await ctx.message.add_reaction("🔂")

        elif option == "queue":
            ctx.voice.queue.mode = QueueMode.loop_all
            return await ctx.message.add_reaction("🔁")

        ctx.voice.queue.mode = QueueMode.normal
        return await ctx.add_check()

    @command(aliases=("vol",))
    async def volume(
        self,
        ctx: Context,
        volume: Optional[Range[int, 1, 100]],
    ) -> Message:
        """
        Change the track volume.
        """

        if not volume:
            return await ctx.neutral(f"The volume is currently `{ctx.voice.volume}%`")

        await ctx.voice.set_volume(volume)
        return await ctx.approve(f"Set the volume to `{volume}%`")

    @group(aliases=("filter",))
    async def preset(self, ctx: Context) -> Optional[Message]:
        """
        Set a filter on the audio.
        """

        if not ctx.invoked_subcommand:
            filters = ctx.voice.filters
            if not filters:
                return await ctx.send_help(ctx.command)

            return await ctx.neutral(f"Currently using the `{filters}` filter")

        await ctx.voice.set_filters()

    @preset.command(name="nightcore", aliases=("nc",))
    async def preset_nightcore(self, ctx: Context) -> None:
        """
        Apply the nightcore filter.
        """
        filters = ctx.voice.filters
        filters.timescale.set(pitch=1.2, speed=1.2, rate=1)

        await ctx.voice.set_filters(filters, seek=True)
        return await ctx.add_check()

    @preset.command(name="remove")
    async def preset_remove(self, ctx: Context) -> None:
        """
        Remove the current filter.
        """
        return await ctx.add_check()


async def setup(bot: greedbot) -> None:
    await bot.add_cog(Audio(bot))
