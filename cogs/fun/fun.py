import asyncio
from io import BytesIO
from random import choice
from typing import Any, Dict, List, Optional, cast

from discord import Embed, Member, Message, NotFound, Reaction, File
from discord.ext.commands import BucketType, Cog, command, group, max_concurrency, Range
from discord.ext.commands.context import Context
from pydantic import BaseModel
from typing_extensions import Self
from xxhash import xxh64_hexdigest
from yarl import URL

from main import greedbot
from tools.client import Context
from tools.client.redis import Redis
from tools.formatter import plural

from .views import RPS, TicTacToe

import config


class Blacktea(BaseModel):
    message_id: int
    channel_id: int
    waiting: bool = True
    players: Dict[int, int] = {}
    used_words: List[str] = []

    @staticmethod
    def key(channel_id: int) -> str:
        return xxh64_hexdigest(f"blacktea:{channel_id}")

    @classmethod
    async def get(cls, redis: Redis, channel_id: int) -> Optional[Self]:
        key = cls.key(channel_id)
        data = cast(Optional[Dict[str, Any]], await redis.get(key))
        if not data:
            return

        return cls(**data)

    async def save(self, redis: Redis, **kwargs) -> None:
        key = self.key(self.channel_id)
        await redis.set(key, self.dict(), **kwargs)

    async def delete(self, redis: Redis) -> None:
        key = self.key(self.channel_id)
        await redis.delete(key)


class Fun(Cog):
    def __init__(self, bot: greedbot):
        self.bot = bot
        self.words: List[str] = []

    async def cog_load(self) -> None:
        async with self.bot.session.get(
            "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
        ) as resp:
            buffer = await resp.text()
            self.words = buffer.splitlines()

    async def cog_unload(self) -> None:
        self.words = []

    @Cog.listener()
    async def on_reaction_add(self, reaction: Reaction, member: Member) -> None:
        if member.bot or reaction.emoji != "✅":
            return

        session = await Blacktea.get(self.bot.redis, reaction.message.channel.id)
        if (
            not session
            or not session.waiting
            or session.message_id != reaction.message.id
        ):
            return

        if member.id in session.players:
            return

        session.players[member.id] = 2
        await session.save(self.bot.redis)

        embed = Embed(description=f"**{member}** joined the game")
        await reaction.message.reply(embed=embed, delete_after=3)

    @group(invoke_without_command=True)
    async def blacktea(self, ctx: Context) -> Optional[Message]:
        """
        Start a game of Blacktea.
        """

        session = await Blacktea.get(self.bot.redis, ctx.channel.id)
        if session:
            return await ctx.warn("There is already a game in progress.")

        embed = Embed(
            title="Blacktea",
            description="\n> ".join(
                [
                    "React with `✅` to join the game. The game will start in **30 seconds**",
                    "You'll have **10 seconds** type a word containing the given letters",
                    "The word must be at least **3 letters long** and **not used before**",
                ]
            ),
        )
        message = await ctx.channel.send(embed=embed)

        session = Blacktea(message_id=message.id, channel_id=ctx.channel.id)
        await session.save(self.bot.redis)
        await message.add_reaction("✅")

        await asyncio.sleep(30)
        session = await Blacktea.get(self.bot.redis, ctx.channel.id)
        if not session or len(session.players) < 2:
            await self.bot.redis.delete(Blacktea.key(ctx.channel.id))
            return await ctx.warn("Not enough players to start the game!")

        session.waiting = False
        await session.save(self.bot.redis, ex=600)

        while True:
            for member_id, lives in list(session.players.items()):
                member = ctx.guild.get_member(member_id)
                if not member:
                    if len(session.players) == 1:
                        await session.delete(self.bot.redis)
                        return await ctx.warn("The winner left the server!")

                    continue

                if len(session.players) == 1:
                    await session.delete(self.bot.redis)
                    return await ctx.approve(f"**{member}** has won the game!")

                letters = choice(
                    [
                        segment.upper()
                        for word in self.words
                        if (segment := word[: round(len(word) / 4)])
                        and len(segment) == 3
                    ]
                )
                embed = Embed(description=f"Type a **word** containing `{letters}`")
                prompt = await ctx.channel.send(content=member.mention, embed=embed)

                for index in range(4):
                    try:
                        message: Message = await self.bot.wait_for(
                            "message",
                            check=lambda m: (
                                m.content
                                and m.channel == ctx.channel
                                and m.author == member
                                and m.content.lower() in self.words
                                and letters.lower() in m.content.lower()
                                and m.content.lower() not in session.used_words
                            ),
                            timeout=(7 if index == 0 else 1),
                        )
                    except asyncio.TimeoutError:
                        if index == 3:
                            lives = session.players[member_id] - 1
                            if not lives:
                                del session.players[member_id]
                                embed = Embed(
                                    description=f"**{member}** has been **eliminated**!"
                                )

                            else:
                                session.players[member_id] = lives
                                embed = Embed(
                                    description="\n> ".join(
                                        [
                                            f"You ran out of time, **{member}**!",
                                            f"You have {plural(lives, md='**'):life|lives} remaining",
                                        ]
                                    )
                                )

                            await ctx.channel.send(embed=embed)
                            break

                        elif index != 0:
                            reactions = {
                                1: "3️⃣",
                                2: "2️⃣",
                                3: "1️⃣",
                            }
                            try:
                                await prompt.add_reaction(reactions[index])
                            except NotFound:
                                ...

                        continue
                    else:
                        await message.add_reaction("✅")
                        session.used_words.append(message.content.lower())

                        break

    @command(aliases=["ttt"])
    @max_concurrency(1, BucketType.member)
    async def tictactoe(self, ctx: Context, opponent: Member) -> Message:
        """
        Play Tic Tac Toe with another member.
        """

        if opponent == ctx.author:
            return await ctx.warn("You can't play against **yourself**")

        elif opponent.bot:
            return await ctx.warn("You can't play against **bots**")

        return await TicTacToe(ctx, opponent).start()

    @command()
    @max_concurrency(1, BucketType.member)
    async def rps(self, ctx: Context, opponent: Member) -> Message:
        """
        Play Rock Paper Scissors with another member.
        """

        if opponent == ctx.author:
            return await ctx.warn("You can't play against **yourself**")

        elif opponent.bot:
            return await ctx.warn("You can't play against **bots**")

        return await RPS(ctx, opponent).start()

    @command(aliases=["flipcoin", "cf"])
    async def coinflip(self, ctx: Context) -> Optional[Message]:
        """
        Flip a coin.
        """

        await ctx.neutral("Flipping the coin...")

        await ctx.neutral(
            f"The coin landed on **{choice(['heads', 'tails'])}**",
            patch=ctx.response,
        )

    @command(aliases=["scrap"])
    async def scrapbook(
        self, ctx: Context, *, text: Range[str, 1, 20]
    ) -> Optional[Message]:
        """
        Create scrapbook letters.
        """
        async with ctx.typing():
            async with self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.jeyy.xyz",
                    path="/v2/image/scrapbook",
                ),
                headers={"Authorization": f"Bearer {config.Authorization.JEYY}"},
                params={"text": text},
            ) as response:
                if not response.ok:
                    return await ctx.warn("Failed to generate the image")

                buffer = await response.read()
                image = BytesIO(buffer)

                await ctx.reply(
                    file=File(image, filename="scrapbook.gif"),
                )
