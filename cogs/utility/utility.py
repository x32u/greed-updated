import re
import textwrap
import unicodedata
from datetime import datetime
from hashlib import sha1, sha224, sha256, sha384, sha512
from re import Pattern, compile
from io import BytesIO
from typing import Annotated, Dict, List, Optional, cast
from urllib.parse import quote_plus

from aiohttp import FormData

import dateparser
from bs4 import BeautifulSoup
from dateutil.tz import gettz
from discord import (
    Embed,
    File,
    HTTPException,
    Member,
    Message,
    RawReactionActionEvent,
    TextChannel,
    Thread,
    Attachment,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    clean_content,
    command,
    cooldown,
    group,
    has_permissions,
    max_concurrency,
    parameter,
    is_owner,
)
from discord.utils import format_dt, utcnow
from humanize import ordinal
from shazamio import Serialize as ShazamSerialize
from shazamio import Shazam as ShazamClient
from xxhash import xxh32_hexdigest, xxh64_hexdigest, xxh128_hexdigest
from yarl import URL

import config
from cogs.social.models import YouTubeVideo
from config import Authorization
from main import greedbot
from tools import dominant_color
from tools.client import Context
from tools.conversion import PartialAttachment, Timezone
from tools.formatter import codeblock, human_join, shorten
from tools.paginator import Paginator
from tools.parser import Script
from tools.parser.script import TagScript, engine

from .extended import Extended
from .models.google import Google, GoogleTranslate

LINKVERTISE_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:linkvertise\.com|link-target\.net)/(?P<id>\d+)/(?P<name>[a-zA-Z0-9-]+)(?:\?o=sharing)?"
)


class Utility(Extended, Cog):
    def __init__(self, bot: greedbot):
        self.bot = bot
        self.shazamio = ShazamClient()

    @Cog.listener("on_message_without_command")
    async def linkvertise_listener(self, ctx: Context) -> Optional[Message]:
        match = LINKVERTISE_PATTERN.search(ctx.message.content)
        if not match:
            return

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="bypass.pm",
                    path="/bypass2",
                    query={"url": match.group()},
                ),
                proxy=config.WARP,
            )
            data = await response.json()
            if not data.get("success"):
                return await ctx.warn(
                    "Failed to get **Linkvertise** destination!", delete_after=5
                )

            return await ctx.reply(data["destination"])

    @Cog.listener("on_raw_reaction_add")
    async def quote_listener(
        self,
        payload: RawReactionActionEvent,
    ) -> Optional[Message]:
        record = await self.bot.db.fetchrow(
            """
            SELECT channel_id, embeds
            FROM quoter
            WHERE guild_id = $1
            AND emoji = $2
            """,
            payload.guild_id,
            str(payload.emoji),
        )
        if not record:
            return

        guild = payload.guild_id and self.bot.get_guild(payload.guild_id)
        if not guild or guild.me.is_timed_out():
            return

        payload_channel = guild.get_channel_or_thread(payload.channel_id)
        if not isinstance(payload_channel, (TextChannel, Thread)):
            return

        channel = cast(Optional[TextChannel], guild.get_channel(record["channel_id"]))
        if not channel:
            return

        message = self.bot.get_message(payload.message_id)
        if not message:
            try:
                message = await payload_channel.fetch_message(payload.message_id)
            except HTTPException:
                return

        if message.embeds and message.embeds[0].type != "image":
            embed = message.embeds[0]
        else:
            embed = Embed(color=message.author.color)

        embed.description = embed.description or ""
        embed.timestamp = message.created_at
        embed.set_author(
            name=message.author,
            icon_url=message.author.display_avatar,
            url=message.jump_url,
        )

        if message.content:
            embed.description += f"\n{message.content}"

        if message.attachments:
            attachment = message.attachments[0]
            if attachment.content_type and attachment.content_type.startswith("image"):
                embed.set_image(url=attachment.proxy_url)

        files: List[File] = []
        for attachment in message.attachments:
            if (
                not attachment.content_type
                or attachment.content_type.startswith("image")
                or attachment.size > guild.filesize_limit
                or not attachment.filename.endswith(
                    ("mp4", "mp3", "mov", "wav", "ogg", "webm")
                )
            ):
                continue

            file = await attachment.to_file()
            files.append(file)

        embed.set_footer(
            text=f"#{message.channel}/{message.guild or 'Unknown Guild'}",
            icon_url=message.guild.icon if message.guild else None,
        )

        if not record["embeds"] and files:
            return await channel.send(files=files)

        return await channel.send(
            embed=embed,
            files=files,
        )

    @Cog.listener("on_message_without_command")
    async def afk_listener(self, ctx: Context) -> Optional[Message]:
        if left_at := cast(
            Optional[datetime],
            await self.bot.db.fetchval(
                """
                DELETE FROM afk
                WHERE user_id = $1
                RETURNING left_at
                """,
                ctx.author.id,
            ),
        ):
            return await ctx.neutral(
                f"Welcome back, you left {format_dt(left_at, 'R')}",
                reference=ctx.message,
            )

        if len(ctx.message.mentions) == 1:
            user = ctx.message.mentions[0]

            if record := await self.bot.db.fetchrow(
                """
                SELECT status, left_at
                FROM afk
                WHERE user_id = $1
                """,
                user.id,
            ):
                return await ctx.neutral(
                    f"{user.mention} is currently AFK: **{record['status']}** - {format_dt(record['left_at'], 'R')}",
                    reference=ctx.message,
                )

    @command(aliases=["away"])
    async def afk(
        self,
        ctx: Context,
        *,
        status: str = "AFK",
    ) -> Optional[Message]:
        """
        Set an AFK status.
        """

        status = shorten(status, 200)
        if await self.bot.db.execute(
            """
            INSERT INTO afk (user_id, status)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING
            """,
            ctx.author.id,
            status,
        ):
            return await ctx.approve(f"You're now **AFK** with the status **{status}**")

    @command(
        name="translate",
        aliases=[
            "translation",
            "tr",
        ],
    )
    async def translate(
        self,
        ctx: Context,
        destination: Annotated[
            str,
            Optional[GoogleTranslate],
        ] = "en",
        *,
        text: Annotated[
            Optional[str],
            clean_content,
        ] = None,
    ) -> Message:
        """
        Translate text with Google Translate.
        This is an alias for the `google translate` command.
        """

        return await self.google_translate(
            ctx,
            destination=destination,
            text=text,
        )

    @command(
        aliases=[
            "rimg",
            "sauce",
        ]
    )
    async def reverse(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Reverse search an image on Google.
        This is an alias for the `google reverse` command.
        """

        return await self.google_reverse(ctx, attachment=attachment)

    @group(
        aliases=["g", "ddg"],
        invoke_without_command=True,
    )
    @cooldown(3, 60, BucketType.user)
    async def google(self, ctx: Context, *, query: str) -> Message:
        """
        Search a query on Google.
        """

        async with ctx.typing():
            data = await Google.search(
                self.bot.session,
                query,
            )
            if not data.results:
                return await ctx.warn(f"No results found for **{query}**!")

        fields: List[dict] = []
        embed = Embed(
            title=(
                f"{data.header}"
                + (f" - {data.description}" if data.description else "")
                if data.header
                else f"Google Search - {query}"
            ),
        )
        if panel := data.panel:
            if panel.source:
                embed.url = panel.source.url

            embed.description = shorten(panel.description, 200)

            for item in panel.items:
                if not embed.description:
                    embed.description = ""

                embed.description += f"\n> **{item.name}:** `{item.value}`"

        for result in data.results:
            if any(result.title in field["name"] for field in fields):
                continue

            snippet = result.snippet or (".." if not result.tweets else "")
            for highlight in result.highlights:
                snippet = snippet.replace(highlight, f"**{highlight}**")

            fields.append(
                dict(
                    name=f"**{result.title}**",
                    value=(
                        f"**{result.url.split('?', 1)[0]}**\n{shorten(snippet, 200)}"
                        + (
                            "\n"
                            if result.extended_links or result.tweets and snippet
                            else ""
                        )
                        + "\n".join(
                            [
                                f"> [`{extended.title}`]({extended.url}): {textwrap.shorten(extended.snippet or '...', 46, placeholder='..')}"
                                for extended in result.extended_links
                            ]
                        )
                        + "\n".join(
                            [
                                f"> [`{textwrap.shorten(tweet.text, 46, placeholder='..')}`]({tweet.url}) **{tweet.footer}**"
                                for tweet in result.tweets[:3]
                            ]
                        )
                    ),
                    inline=False,
                )
            )

        paginator = Paginator(
            ctx,
            entries=fields,
            embed=embed,
            per_page=3,
        )
        return await paginator.start()

    @google.command(
        name="translate",
        aliases=[
            "translation",
            "tr",
        ],
    )
    async def google_translate(
        self,
        ctx: Context,
        destination: Annotated[
            str,
            Optional[GoogleTranslate],
        ] = "en",
        *,
        text: Annotated[
            Optional[str],
            clean_content,
        ] = None,
    ) -> Message:
        """
        Translate text with Google Translate.
        """

        if not text:
            reply = ctx.replied_message
            if reply and reply.content:
                text = reply.clean_content
            else:
                return await ctx.send_help(ctx.command)

        async with ctx.typing():
            result = await GoogleTranslate.translate(
                self.bot.session,
                text,
                target=destination,
            )

        embed = Embed(title="Google Translate")
        embed.add_field(
            name=f"**{result.source_language} to {result.target_language}**",
            value=result.translated,
            inline=False,
        )

        return await ctx.send(embed=embed)

    @google.command(
        name="youtube",
        aliases=["yt"],
    )
    async def google_youtube(self, ctx: Context, *, query: str) -> Message:
        """
        Search a query on YouTube.
        """

        async with ctx.typing():
            results = await YouTubeVideo.search(self.bot.session, query)
            if not results:
                return await ctx.warn(f"No videos found for **{query}**!")

            paginator = Paginator(
                ctx,
                entries=[result.url for result in results],
            )
            return await paginator.start()

    @google.command(
        name="reverse",
        aliases=["rimg", "sauce"],
    )
    @cooldown(1, 10, BucketType.user)
    async def google_reverse(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Reverse search an image on Google.
        """

        if not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")

        async with ctx.typing():
            classes: Dict[str, str | Pattern[str]] = {
                "description": compile("VwiC3b yXK7lf"),
                "result": "srKDX cvP2Ce",
                "related": "fKDtNb",
            }
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="www.google.com",
                    path="/searchbyimage",
                    query={
                        "safe": "off" if ctx.channel.is_nsfw() else "on",
                        "sbisrc": "tg",
                        "image_url": attachment.url,
                    },
                ),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
                        "Gecko/20100101 Firefox/111.0"
                    )
                },
            )
            content = await response.text()

            data = BeautifulSoup(content, "lxml")
            related = data.find("a", class_=classes["related"])
            results = data.findAll("div", class_=classes["result"])
            if not related or not results:
                return await ctx.warn(
                    f"No results were found for [`{attachment.filename}`]({attachment.url})!"
                )

        embed = Embed(
            title="Reverse Image Search",
            description=f"*`{related.text}`*",
        )
        embed.set_thumbnail(url=attachment.url)
        if stats := data.find("div", id="result-stats"):
            embed.set_footer(text=stats.text)

        for result in results[:3]:
            link = result.a.get("href")
            title = result.find("h3").text
            description = (
                result.find("div", class_=classes["description"])
                .findAll("span")[-1]
                .text
            )

            embed.add_field(
                name=title,
                value=f"[`{shorten(description, 65)}`]({link})",
                inline=False,
            )

        return await ctx.send(embed=embed)

    @command(aliases=["recognize", "find"])
    @max_concurrency(1, wait=True)
    @cooldown(1, 5, BucketType.guild)
    async def shazam(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Recognize a song from an attachment.
        """

        if not attachment.is_video() and not attachment.is_audio():
            return await ctx.warn("The attachment must be a video!")

        async with ctx.typing():
            data = await self.shazamio.recognize_song(attachment.buffer)
            output = ShazamSerialize.full_track(data)

        if not (track := output.track):
            return await ctx.warn(
                f"No tracks were found from [`{attachment.filename}`]({attachment.url})!"
            )

        return await ctx.approve(
            f"Found [**{track.title}**]({track.shazam_url}) "
            f"by [*`{track.subtitle}`*]({URL(f'https://google.com/search?q={track.subtitle}')})"
        )

    @command(aliases=["ai", "ask", "chatgpt"])
    async def gemini(self, ctx: Context, *, question: str) -> Optional[Message]:
        """
        Ask AI a question.
        """

        async with ctx.typing():
            response = await self.bot.session.post(
                URL.build(
                    scheme="https",
                    host="generativelanguage.googleapis.com",
                    path="/v1/models/gemini-pro:generateContent",
                    query={
                        "key": Authorization.GEMINI,
                    },
                ),
                json={"contents": [{"parts": [{"text": question}]}]},
            )

            if not (data := await response.json()):
                return await ctx.warn("No response was found for that question!")

            if not (content := data.get("candidates", [])[0].get("content")) or not (
                parts := content.get("parts")
            ):
                return await ctx.warn("No response was found for that question!")

            await ctx.reply(parts[0]["text"])

    @command(aliases=["w"])
    async def wolfram(
        self,
        ctx: Context,
        *,
        question: str,
    ) -> Message:
        """
        Solve a question with Wolfram Alpha.
        """

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.wolframalpha.com",
                    path="/v1/result",
                    query={
                        "i": question,
                        "appid": Authorization.WOLFRAM,
                    },
                ),
            )
            if not response.ok:
                return await ctx.warn("No solution was found for that question!")

            content = await response.read()

        return await ctx.reply(content.decode("utf-8"))

    @command(aliases=["char"])
    async def charinfo(self, ctx: Context, *, characters: str) -> Message:
        """
        View unicode characters.
        """

        def to_string(char: str):
            digit = f"{ord(char):x}"
            name = unicodedata.name(char, "Unknown")

            return f"[`\\U{digit:>08}`](http://www.fileformat.info/info/unicode/char/{digit}): {name}"

        paginator = Paginator(
            ctx,
            entries=list(map(to_string, characters)),
            embed=Embed(
                title="Character Information",
            ),
            per_page=5,
            counter=False,
        )
        return await paginator.start()

    @group(
        name="hash",
        invoke_without_command=True,
    )
    async def _hash(self, ctx: Context) -> Message:
        """
        Hash a string with a given algorithm.
        """

        hash_methods = human_join(
            [
                f"`{command.name}`"
                for command in sorted(
                    self._hash.commands,
                    key=lambda command: command.name,
                    reverse=True,
                )
            ]
        )

        return await ctx.neutral(
            f"Please specify a valid hash algorithm to use!\n{hash_methods}"
        )

    @_hash.command(name="xxh32")
    async def _hash_xxh32(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the XXH32 algorithm.
        """

        hashed = xxh32_hexdigest(text)

        embed = Embed(
            title="XXH32 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="xxh64")
    async def _hash_xxh64(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the XXH64 algorithm.
        """

        hashed = xxh64_hexdigest(text)

        embed = Embed(
            title="XXH64 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="xxh128")
    async def _hash_xxh128(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the XXH128 algorithm.
        """

        hashed = xxh128_hexdigest(text)

        embed = Embed(
            title="XXH128 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha1")
    async def _hash_sha1(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the SHA1 algorithm.
        """

        hashed = sha1(text.encode()).hexdigest()

        embed = Embed(
            title="SHA1 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha224")
    async def _hash_sha224(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the SHA224 algorithm.
        """

        hashed = sha224(text.encode()).hexdigest()

        embed = Embed(
            title="SHA224 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha256")
    async def _hash_sha256(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the SHA256 algorithm.
        """

        hashed = sha256(text.encode()).hexdigest()

        embed = Embed(
            title="SHA256 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha384")
    async def _hash_sha384(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the SHA384 algorithm.
        """

        hashed = sha384(text.encode()).hexdigest()

        embed = Embed(
            title="SHA384 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha512")
    async def _hash_sha512(self, ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the SHA512 algorithm.
        """

        hashed = sha512(text.encode()).hexdigest()

        embed = Embed(
            title="SHA512 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @group(
        aliases=["bday", "bd"],
        invoke_without_command=True,
    )
    async def birthday(
        self,
        ctx: Context,
        *,
        member: Member = parameter(
            default=lambda ctx: ctx.author,
        ),
    ) -> Message:
        """
        View your birthday.
        """

        birthday = cast(
            Optional[datetime],
            await self.bot.db.fetchval(
                """
                SELECT birthday
                FROM birthdays
                WHERE user_id = $1
                """,
                member.id,
            ),
        )
        if not birthday:
            if member == ctx.author:
                return await ctx.warn(
                    "You haven't set your birthday yet!",
                    f"Use `{ctx.clean_prefix}birthday set <date>` to set it",
                )

            return await ctx.warn(f"**{member}** hasn't set their birthday yet!")

        current = utcnow()
        next_birthday = current.replace(
            year=current.year + 1,
            month=birthday.month,
            day=birthday.day,
        )
        if next_birthday.day == current.day and next_birthday.month == current.month:
            phrase = "**today**, happy birthday! 🎊"
        elif (
            next_birthday.day + 1 == current.day
            and next_birthday.month == current.month
        ):
            phrase = "**tomorrow**, happy early birthday! 🎊"
        else:
            days_until_birthday = (next_birthday - current).days
            if days_until_birthday > 365:
                next_birthday = current.replace(
                    year=current.year,
                    month=birthday.month,
                    day=birthday.day,
                )
                days_until_birthday = (next_birthday - current).days

            phrase = f"**{next_birthday.strftime('%B')} {ordinal(next_birthday.day)}**, that's {format_dt(next_birthday, 'R')}"

        return await ctx.neutral(
            f"Your birthday is {phrase}"
            if member == ctx.author
            else f"**{member}**'s birthday is {phrase}"
        )

    @birthday.command(name="set")
    async def birthday_set(
        self,
        ctx: Context,
        *,
        date: str,
    ) -> Message:
        """
        Set your birthday
        """

        birthday = dateparser.parse(date)
        if not birthday:
            return await ctx.warn(f"Date not found for **{date}**")

        await self.bot.db.execute(
            """
            INSERT INTO birthdays (user_id, birthday)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET birthday = EXCLUDED.birthday
            """,
            ctx.author.id,
            birthday,
        )
        return await ctx.approve(
            f"Your birthday has been set to **{birthday:%B} {ordinal(birthday.strftime('%-d'))}**"
        )

    @group(
        aliases=["time", "tz"],
        invoke_without_command=True,
    )
    async def timezone(
        self,
        ctx: Context,
        *,
        member: Member = parameter(
            default=lambda ctx: ctx.author,
        ),
    ) -> Message:
        """
        View your local time.
        """

        timezone = cast(
            Optional[str],
            await self.bot.db.fetchval(
                """
                SELECT timezone
                FROM timezones
                WHERE user_id = $1
                """,
                member.id,
            ),
        )
        if not timezone:
            if member == ctx.author:
                return await ctx.warn(
                    "You haven't set your timezone yet!",
                    f"Use `{ctx.clean_prefix}timezone set <location>` to set it",
                )

            return await ctx.warn(f"**{member}** hasn't set their timezone yet!")

        timestamp = utcnow().astimezone(gettz(timezone))
        return await ctx.neutral(
            f"It's currently **{timestamp.strftime('%B %d, %I:%M %p')}** "
            + ("for you" if member == ctx.author else f"for {member.mention}")
        )

    @timezone.command(name="set")
    async def timezone_set(
        self,
        ctx: Context,
        *,
        timezone: Annotated[
            str,
            Timezone,
        ],
    ) -> Message:
        """
        Set your local timezone.
        """

        await self.bot.db.execute(
            """
            INSERT INTO timezones (user_id, timezone)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET timezone = EXCLUDED.timezone
            """,
            ctx.author.id,
            timezone,
        )
        return await ctx.approve(f"Your timezone has been set to `{timezone}`")

    @command(aliases=["tagscript"])
    async def script(self, ctx: Context, *, script: str) -> Message:
        author = TagScript.MemberAdapter(ctx.author)
        target = (
            TagScript.MemberAdapter(ctx.message.mentions[0])
            if ctx.message.mentions
            else author
        )
        channel = TagScript.ChannelAdapter(ctx.channel)
        guild = TagScript.GuildAdapter(ctx.guild)
        seed = {
            "author": author,
            "user": author,
            "target": target,
            "channel": channel,
            "guild": guild,
            "server": guild,
        }

        output = await engine.process(script, seed)
        message = await ctx.channel.send(
            content=output.body, embeds=output.actions.get("embeds", [])
        )
        return message

    @command(aliases=["parse", "ce"])
    async def embed(self, ctx: Context, *, script: Script) -> Message:
        """
        Parse a script into an embed.
        """

        try:
            return await script.send(ctx)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your **script**!",
                codeblock(exc.text),
            )

    @command(aliases=["embedcode", "ec"])
    async def copyembed(
        self,
        ctx: Context,
        message: Optional[Message],
    ) -> Message:
        """
        Copy a script from a message.
        """

        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)

        script = Script.from_message(message)
        if not script:
            return await ctx.warn(
                f"That [`message`]({message.jump_url}) doesn't have any content!"
            )

        return await ctx.reply(codeblock(script.template))

    @group(
        aliases=["quoter"],
        invoke_without_command=True,
    )
    async def quote(
        self,
        ctx: Context,
        message: Optional[Message],
    ) -> Message:
        """
        Repost a message.
        """

        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)

        channel = message.channel
        if not channel.permissions_for(ctx.author).view_channel:
            return await ctx.warn("You don't have access to that channel!")

        if message.embeds and message.embeds[0].type != "image":
            embed = message.embeds[0]
        else:
            embed = Embed(color=message.author.color)

        embed.description = embed.description or ""
        embed.timestamp = message.created_at
        embed.set_author(
            name=message.author,
            icon_url=message.author.display_avatar,
            url=message.jump_url,
        )

        if message.content:
            embed.description += f"\n{message.content}"

        if message.attachments:
            attachment = message.attachments[0]
            if attachment.content_type and attachment.content_type.startswith("image"):
                embed.set_image(url=attachment.proxy_url)

        files: List[File] = []
        for attachment in message.attachments:
            if (
                not attachment.content_type
                or attachment.content_type.startswith("image")
                or attachment.size > ctx.guild.filesize_limit
                or not attachment.filename.endswith(
                    ("mp4", "mp3", "mov", "wav", "ogg", "webm")
                )
            ):
                continue

            file = await attachment.to_file()
            files.append(file)

        embed.set_footer(
            text=f"#{message.channel}/{message.guild or 'Unknown Guild'}",
            icon_url=message.guild.icon if message.guild else None,
        )

        return await ctx.send(
            embed=embed,
            files=files,
        )

    @quote.command(name="channel")
    @has_permissions(manage_messages=True)
    async def quote_channel(self, ctx: Context, *, channel: TextChannel) -> Message:
        """
        Set the quote relay channel.
        """

        await self.bot.db.execute(
            """
            INSERT INTO quoter (guild_id, channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET channel_id = EXCLUDED.channel_id
            """,
            ctx.guild.id,
            channel.id,
        )
        return await ctx.approve(f"Now relaying quoted messages to {channel.mention}")

    @quote.command(name="emoji", aliases=["set"])
    @has_permissions(manage_messages=True)
    async def quote_emoji(self, ctx: Context, emoji: str) -> Message:
        """
        Set the quote emoji to detect.
        """

        try:
            await ctx.message.add_reaction(emoji)
        except (HTTPException, TypeError):
            return await ctx.warn(
                f"I'm not able to use **{emoji}**",
                "Try using an emoji from this server",
            )

        await self.bot.db.execute(
            """
            INSERT INTO quoter (guild_id, emoji)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET emoji = EXCLUDED.emoji
            """,
            ctx.guild.id,
            emoji,
        )
        return await ctx.approve(f"Now watching for {emoji} on messages")

    @quote.command(name="embeds", aliases=["embed"])
    @has_permissions(manage_messages=True)
    async def quote_embeds(self, ctx: Context) -> Message:
        """
        Toggle if quoted messages will have an embed.
        """

        status = cast(
            bool,
            await self.bot.db.fetchval(
                """
                INSERT INTO quoter (guild_id)
                VALUES ($1)
                ON CONFLICT (guild_id)
                DO UPDATE SET embeds = NOT quoter.embeds
                RETURNING embeds
                """,
                ctx.guild.id,
            ),
        )

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} displaying **embeds** for relayed messages"
        )

    @command(
        aliases=[
            "dictionary",
            "define",
            "urban",
            "ud",
        ],
    )
    async def urbandictionary(
        self,
        ctx: Context,
        *,
        word: str,
    ) -> Message:
        """
        Define a word with Urban Dictionary.
        """

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.urbandictionary.com",
                    path="/v0/define",
                    query={
                        "term": word,
                    },
                ),
            )
            data = await response.json()
            if not data["list"]:
                return await ctx.warn(f"No definitions found for **{word}**!")

        embeds: List[Embed] = []
        for result in data["list"]:
            embed = Embed(
                url=result["permalink"],
                title=result["word"],
                description=re.sub(
                    r"\[(.*?)\]",
                    lambda m: f"[{m[1]}](https://www.urbandictionary.com/define.php?term={quote_plus(m[1])})",
                    result["definition"],
                )[:4096],
            )

            embed.add_field(
                name="**Example**",
                value=re.sub(
                    r"\[(.*?)\]",
                    lambda m: f"[{m[1]}](https://www.urbandictionary.com/define.php?term={quote_plus(m[1])})",
                    result["example"],
                )[:1024],
            )
            embeds.append(embed)

        paginator = Paginator(
            ctx,
            entries=embeds,
        )
        return await paginator.start()

    @command(aliases=["dom", "hex"])
    async def dominant(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Extract the dominant color from an image.
        """

        if not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")

        color = await dominant_color(attachment.buffer)
        image_url = f"https://place-hold.it/250x250/{str(color).strip('#')}?text=%20"
        return await ctx.neutral(
            f"The dominant color is [**{color}**]({image_url})",
            color=color,
        )

    @command()
    async def upload(
        self,
        ctx: Context,
        attachment: Attachment = parameter(
            default=lambda ctx: ctx.message.attachments[0]
            if ctx.message.attachments
            else None,
        ),
    ) -> Message:
        """
        Upload a file to Kraken Files.
        """

        if not attachment:
            return await ctx.warn("No attachment found!")

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="krakenfiles.com",
                    path="/api/server/available",
                ),
            )
            data = await response.json()

            if not data["data"]:
                return await ctx.warn(
                    "No available servers to upload file. Try again later!"
                )

            buffer = await attachment.read()

            server = data["data"]
            form = FormData(
                {
                    "file": buffer,
                    "filename": attachment.filename,
                    "serverAccessToken": server["serverAccessToken"],
                }
            )
            response = await self.bot.session.post(
                server["url"],
                data=form,
                headers={"X-AUTH-TOKEN": config.Authorization.KRAKEN},
            )
            data = await response.json()
            if not data["data"]:
                return await ctx.warn(
                    "Please try again, the serverAccessToken has already been used!"
                )

            return await ctx.neutral(data["data"]["url"])
        

    @command(aliases=["ss"], hidden=True)
    @is_owner()  # TODO: Eventually make it check through AI for explicit content, owner only for now.
    async def screenshot(
        self, ctx: Context, url: str, full_page: bool = parameter(default=False)
    ) -> Message:
        """
        Capture a screenshot of a webpage.
        """

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        async with ctx.typing():
            async with ctx.bot.browser.borrow_page() as page:
                await page.emulate_media(color_scheme="dark")
                await page.goto(url, wait_until="load")
                screenshot = await page.screenshot(full_page=full_page)
                await page.close()

        return await ctx.send(file=File(BytesIO(screenshot), filename="screenshot.png"))
