import asyncio
from contextlib import suppress
from logging import getLogger
from typing import Annotated, List, Literal, Optional, cast
from colorama import Fore, Style

import validators
from aiohttp import ClientSession
from discord import Embed, HTTPException, Member, Message
from discord.ext.commands import (
    Cog,
    CommandError,
    command,
    group,
    has_permissions,
    parameter,
)
from discord.utils import as_chunks
from humanfriendly import format_timespan
from humanize import ordinal
from pomice import LoopMode, Playlist, SearchType, Track
from pomice.enums import URLRegex as regex

from cogs.audio import Client, Percentage, Position
from main import greedbot
from tools.client import Context as DefaultContext
from tools.formatter import duration, plural, shorten
from tools.paginator import Paginator

log = getLogger("greedbot/audio")

SOURCE_PATTERNS = (
    regex.SPOTIFY_URL,
    regex.YOUTUBE_URL,
    regex.YOUTUBE_PLAYLIST_URL,
    regex.AM_URL,
    regex.AM_SINGLE_IN_ALBUM_REGEX,
    regex.SOUNDCLOUD_URL,
    regex.SOUNDCLOUD_PLAYLIST_URL,
    regex.SOUNDCLOUD_TRACK_IN_SET_URL,
)


class Context(DefaultContext):
    voice: Client


class Audio(Cog):
    def __init__(self, bot: greedbot):
        self.bot = bot

    async def cog_before_invoke(self, ctx: Context) -> None:
        ctx.voice = await self.get_player(ctx)

    async def get_player(self, ctx: Context) -> Client:
        client = ctx.voice_client
        author = cast(Member, ctx.author)

        if not author.voice or not author.voice.channel:
            raise CommandError("You're not in a voice channel!")

        elif client and client.channel != author.voice.channel:
            raise CommandError("You're not in my voice channel!")

        elif not client:
            if ctx.command != self.play:
                raise CommandError("I'm not in a voice channel!")

            elif not author.voice.channel.permissions_for(ctx.guild.me).connect:
                raise CommandError(
                    "I don't have permission to connect to your voice channel!"
                )

            client = await author.voice.channel.connect(cls=Client, self_deaf=True)
            volume = (
                cast(
                    Optional[int],
                    await self.bot.db.fetchval(
                        """
                        SELECT volume
                        FROM audio.config
                        WHERE guild_id = $1
                        """,
                        ctx.guild.id,
                    ),
                )
                or 60
            )
            await client.set_volume(volume)
            await client.set_context(ctx)  # type: ignore

        return cast(Client, client)

    @Cog.listener()
    async def on_pomice_track_start(self, client: Client, track: Track):
        if not track.requester:
            return

        log.info(
            f" {Fore.RESET}".join(
                [
                    f"Now playing {Fore.LIGHTCYAN_EX}{Style.BRIGHT}{track.title}{Style.NORMAL}",
                    f"in {Fore.LIGHTMAGENTA_EX}{client.channel}",
                    f"@ {Fore.LIGHTYELLOW_EX}{client.guild}{Fore.RESET}.",
                ]
            )
        )
        await self.bot.db.execute(
            """
            INSERT INTO audio.statistics VALUES ($1, $2, 1)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET tracks_played = audio.statistics.tracks_played + 1
            """,
            client.guild.id,
            track.requester.id,
        )

    @Cog.listener()
    async def on_pomice_track_end(self, client: Client, track: Track, _):
        await client.do_next()

    @Cog.listener()
    async def on_pomice_track_stuck(self, client: Client, track: Track, _):
        await client.do_next()

    @Cog.listener()
    async def on_pomice_track_exception(self, client: Client, track: Track, _):
        await client.do_next()

    # @Cog.listener()
    # async def on_wavelink_track_start(
    #     self,
    #     payload: TrackStartEventPayload,
    # ) -> Optional[Message]:
    #     """
    #     Notify the channel what track is now playing.
    #     """

    #     if not payload.player:
    #         return

    #     channel = payload.player.channel
    #     track = payload.original or payload.track
    #     title = await self.sanitize(track)

    #     with suppress(HTTPException, AttributeError):
    #         topic = f"♫ {title}" + (
    #             f" - {track.author}"
    #             if track.author not in track.title and " - " not in track.title
    #             else ""
    #         )
    #         if not topic:
    #             return

    #         elif isinstance(channel, StageChannel) and channel.instance:
    #             await channel.instance.edit(topic=topic)

    #         elif isinstance(channel, VoiceChannel):
    #             await channel.edit(status=topic)

    async def sanitize(self, track: Track) -> str:
        """
        Sanitize the track title.
        """

        title = track.title

        with suppress(Exception):
            async with ClientSession() as session:
                async with session.get(
                    "https://metadata-filter.vercel.app/api/youtube",
                    params={"track": track.title},
                ) as resp:
                    title = (await resp.json())["data"]["track"]

        return title

    @group(
        aliases=["q"],
        invoke_without_command=True,
    )
    async def queue(self, ctx: Context) -> Optional[Message]:
        """
        View the tracks in the queue.
        """

        if not ctx.voice.current and not ctx.voice.queue:
            return await ctx.warn("The queue is empty!")

        embed = Embed(
            title=f"Queue for {ctx.guild}",
            description=(
                f"Listening to [**{shorten(track.title)}**]({track.uri}) [`{duration(ctx.voice.position)}/{duration(track.length)}`]\n"
                + (f"Requested by {track.requester.mention}" if track.requester else "")
                if (track := ctx.voice.current)
                else "Nothing is currently playing"
            ),
        )
        fields: List[dict] = []

        if ctx.voice.queue or ctx.voice.auto_queue:
            offset = 0
            for index, chunk in enumerate(
                as_chunks(list(ctx.voice.queue or ctx.voice.auto_queue), 5)
            ):
                is_left = index % 2 == 0

                fields.append(
                    dict(
                        name="**Next up**" if is_left else "​",
                        value="\n".join(
                            f"{'' if is_left else ''}`{position + 1 + offset}` [**{shorten(track.title)}**]({track.uri})"
                            for position, track in enumerate(chunk)
                        )[:1024],
                        inline=True,
                    )
                )
                offset += len(chunk)

            embed.set_footer(
                text=" • ".join(
                    [
                        f"{plural(len(ctx.voice.queue or ctx.voice.auto_queue)):track}",
                        format_timespan(
                            sum(
                                track.length / 1e3
                                for track in (ctx.voice.queue or ctx.voice.auto_queue)
                            )
                        ),
                    ]
                ),
            )

        paginator = Paginator(
            ctx,
            entries=fields,
            embed=embed,
            per_page=2,
        )
        return await paginator.start()

    @queue.command(
        name="clear",
        aliases=["clean", "reset"],
    )
    async def queue_clear(self, ctx: Context) -> Optional[Message]:
        """
        Remove all tracks from the queue.
        """

        queue = ctx.voice.queue or ctx.voice.auto_queue
        if not queue:
            return await ctx.warn("The queue is empty!")

        queue.clear()
        return await ctx.message.add_reaction("✅")

    @queue.command(
        name="shuffle",
        aliases=["mix"],
    )
    async def queue_shuffle(self, ctx: Context) -> Optional[Message]:
        """
        Shuffle the queue.
        """

        queue = ctx.voice.queue or ctx.voice.auto_queue
        if not queue:
            return await ctx.warn("The queue is empty!")

        queue.shuffle()
        return await ctx.message.add_reaction("✅")

    @queue.command(
        name="remove",
        aliases=["del", "rm"],
    )
    async def queue_remove(self, ctx: Context, position: int) -> Optional[Message]:
        """
        Remove a track from the queue.
        """

        queue = ctx.voice.queue or ctx.voice.auto_queue
        if not queue:
            return await ctx.warn("The queue is empty!")

        elif not 0 < position <= len(queue):
            return await ctx.warn(
                f"Invalid position - must be between `1` and `{len(queue)}`!"
            )

        track = queue[position - 1]
        queue.remove(track)

        return await ctx.approve(
            f"Removed [**{shorten(track.title)}**]({track.uri}) from the queue"
        )

    @queue.command(
        name="move",
        aliases=["mv"],
    )
    async def queue_move(
        self,
        ctx: Context,
        position: int,
        new_position: int,
    ) -> Optional[Message]:
        """
        Move a track in the queue.
        """

        queue = ctx.voice.queue or ctx.voice.auto_queue
        if not queue:
            return await ctx.warn("The queue is empty!")

        elif not 0 < position <= len(queue):
            return await ctx.warn(
                f"Invalid position - must be between `1` and `{len(queue)}`!"
            )

        elif not 0 < new_position <= len(queue):
            return await ctx.warn(
                f"Invalid new position - must be between `1` and `{len(queue)}`!"
            )

        track = queue[position - 1]
        queue.remove(track)
        queue._queue.insert(new_position - 1, track)

        return await ctx.approve(
            f"Moved [**{shorten(track.title)}**]({track.uri}) to `{ordinal(new_position)}` in the queue"
        )

    @group(aliases=["p"], invoke_without_command=True)
    async def play(
        self,
        ctx: Context,
        *,
        query: Optional[str] = parameter(
            description="Search query or audio file attachment.",
        ),
    ) -> Optional[Message]:
        """
        Play a track in the voice channel.
        """

        if not query:
            if not ctx.message.attachments:
                return await ctx.send_help(ctx.command)

            attachment = ctx.message.attachments[0]
            query = attachment.url

        bump = "bump" in query.lower()
        query = query.replace("bump", "").strip()

        if (
            ctx.author.id not in self.bot.owner_ids
            and regex.BASE_URL.match(query)
            and not any(pattern.match(query) for pattern in SOURCE_PATTERNS)
            and not regex.DISCORD_MP3_URL.match(query)
        ):
            return await ctx.reply("no")

        try:
            result = await ctx.voice.get_tracks(
                query,
                ctx=ctx,
            )
        except KeyError:
            result = None

        if not result:
            return await ctx.warn("No tracks were found!")

        if isinstance(result, Playlist):
            for track in result.tracks:
                ctx.voice.insert(track, bump=bump)

            message = await ctx.approve(
                f"Added {plural(result.track_count, md='**'):track} from [{result.name}]({result.uri or query if validators.url(query) else 'https://google.com'}) to the queue"
            )

        else:
            track = result[0]
            ctx.voice.insert(track, bump=bump)
            message = await ctx.approve(
                f"Added [**{shorten(track.title)}**]({track.uri}) to the queue"
            )

        if not ctx.voice.is_playing:
            await ctx.voice.do_next()

        if ctx.settings.play_deletion:
            with suppress(HTTPException):
                await asyncio.sleep(5)
                await ctx.channel.delete_messages([ctx.message, message])

    @play.command(name="bump")
    async def play_bump(self, ctx: Context, *, query: str) -> Optional[Message]:
        """
        Add a track to the front of the queue.
        """

        return await self.play(ctx, query=f"{query} bump")

    @play.command(name="panel", aliases=["interface"])
    @has_permissions(manage_messages=True)
    async def play_panel(self, ctx: Context) -> Message:
        """
        Toggle the now playing button panel.
        """

        await ctx.settings.update(play_panel=not ctx.settings.play_panel)
        return await ctx.approve(
            f"{'Now' if ctx.settings.play_panel else 'No longer'} displaying the button panel"
        )

    @play.command(name="deletion", aliases=["delete"])
    @has_permissions(manage_messages=True)
    async def play_deletion(self, ctx: Context) -> Message:
        """
        Toggle added to queue message deletion.
        """

        await ctx.settings.update(play_deletion=not ctx.settings.play_deletion)
        return await ctx.approve(
            f"{'Now' if ctx.settings.play_deletion else 'No longer'} deleting added to queue messages"
        )

    @command(
        aliases=[
            "fastforward",
            "rewind",
            "ff",
            "rw",
        ],
    )
    async def seek(
        self,
        ctx: Context,
        position: Annotated[
            int,
            Position,
        ],
    ) -> Message:
        """
        Seek to a specific position.
        """

        if not ctx.voice.is_playing or not ctx.voice.current:  # type: ignore
            return await ctx.warn("I'm not playing anything!")

        await ctx.voice.seek(position)
        return await ctx.approve(
            f"Seeked to `{duration(position)}` in [{ctx.voice.current}]({ctx.voice.current.uri})"
        )

    @command(aliases=["vol"])
    async def volume(
        self,
        ctx: Context,
        volume: Annotated[int, Percentage],
    ) -> Message:
        """
        Change the volume.
        """

        await self.bot.db.execute(
            """
            INSERT INTO audio.config (guild_id, volume)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET volume = EXCLUDED.volume
            """,
            ctx.guild.id,
            volume,
        )
        await ctx.voice.set_volume(volume)
        return await ctx.approve(f"Set the volume to `{volume}%`")

    @command()
    async def pause(self, ctx: Context) -> Optional[Message]:
        """
        Pause the current track.
        """

        if not ctx.voice.is_playing:  # type: ignore
            return await ctx.warn("I'm not playing anything!")

        elif ctx.voice.is_paused:  # type: ignore
            return await ctx.warn("The track is already paused!")

        await ctx.voice.set_pause(True)  # type: ignore
        return await ctx.message.add_reaction("✅")

    @command()
    async def resume(self, ctx: Context) -> Optional[Message]:
        """
        Resume the current track.
        """

        if not ctx.voice.current:
            return await ctx.warn("I'm not playing anything!")

        elif not ctx.voice.is_paused:  # type: ignore
            return await ctx.warn("The track is not paused!")

        await ctx.voice.set_pause(False)  # type: ignore
        return await ctx.message.add_reaction("✅")

    @command(aliases=["next", "sk"])
    async def skip(self, ctx: Context) -> None:
        """
        Skip the current track.
        """

        await ctx.voice.stop()
        return await ctx.message.add_reaction("✅")

    @command(aliases=["mix"])
    async def shuffle(self, ctx: Context) -> Optional[Message]:
        """
        Shuffle the queue.
        """

        return await self.queue_shuffle(ctx)

    @command(aliases=["loop"])
    async def repeat(
        self,
        ctx: Context,
        option: Literal["track", "queue", "off"],
    ) -> None:
        """
        Set the repeat mode.
        """

        if option == "track":
            ctx.voice.queue.set_loop_mode(LoopMode.TRACK)  # type: ignore
            await ctx.voice.refresh_panel()  # type: ignore
            return await ctx.message.add_reaction("🔂")

        elif option == "queue":
            ctx.voice.queue.set_loop_mode(LoopMode.QUEUE)  # type: ignore
            await ctx.voice.refresh_panel()  # type: ignore
            return await ctx.message.add_reaction("🔁")

        ctx.voice.queue.disable_loop()
        await ctx.voice.refresh_panel()  # type: ignore
        return await ctx.message.add_reaction("✅")

    @command(aliases=["stop", "dc"])
    async def disconnect(self, ctx: Context) -> None:
        """
        Disconnect from the voice channel.
        """

        await ctx.voice.destroy()  # type: ignore
        return await ctx.message.add_reaction("✅")
