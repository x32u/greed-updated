from typing import cast, Optional

from discord import Embed, Member, Message, TextChannel
from discord.ext.commands import Cog, command, group
from humanize import ordinal

from cogs.roleplay import BASE_URL
from main import greedbot
from tools.client import Context


ACTIONS = {
    "bite": "bites",
    "cuddle": "cuddles",
    "feed": "feeds",
    "hug": "hugs",
    "kiss": "kisses",
    "pat": "pats",
    "poke": "pokes",
    "punch": "punches",
    "slap": "slaps",
    "smug": "smugs at",
    "tickle": "tickles",
}


class Roleplay(Cog):
    def __init__(self, bot: greedbot):
        self.bot = bot

    async def send(
        self,
        ctx: Context,
        member: Member,
        category: str,
    ) -> Message:
        """
        Requests the API,
        and structures the embed.
        """

        response = await self.bot.session.get(
            BASE_URL.with_path(f"/api/v2/{category}"),
        )
        data = await response.json()
        if not data.get("results"):
            return await ctx.warn("Something went wrong, please try again later!")

        amount = 0
        if member != ctx.author:
            amount = cast(
                int,
                await self.bot.db.fetchval(
                    """
                    INSERT INTO roleplay (user_id, target_id, category)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, target_id, category)
                    DO UPDATE SET amount = roleplay.amount + 1
                    RETURNING amount
                    """,
                    ctx.author.id,
                    member.id,
                    category,
                ),
            )

        embed = Embed(
            description=(
                f"> {ctx.author.mention} **{ACTIONS[category]}** {member.mention if member != ctx.author else 'themselves'}"
                + (
                    f" for the **{ordinal(amount)}** time!"
                    if member != ctx.author and amount
                    else "... kinky"
                )
            ),
        )
        embed.set_image(url=data["results"][0]["url"])

        return await ctx.send(embed=embed)

    @command()
    async def bite(self, ctx: Context, member: Member) -> Message:
        """
        Bite someone.
        """

        return await self.send(ctx, member, "bite")

    @command()
    async def cuddle(self, ctx: Context, member: Member) -> Message:
        """
        Cuddle someone.
        """

        return await self.send(ctx, member, "cuddle")

    @command()
    async def feed(self, ctx: Context, member: Member) -> Message:
        """
        Feed someone.
        """

        return await self.send(ctx, member, "feed")

    @command()
    async def hug(self, ctx: Context, member: Member) -> Message:
        """
        Hug someone.
        """

        return await self.send(ctx, member, "hug")

    @command()
    async def kiss(self, ctx: Context, member: Member) -> Message:
        """
        Kiss someone.
        """

        return await self.send(ctx, member, "kiss")

    @command()
    async def pat(self, ctx: Context, member: Member) -> Message:
        """
        Pat someone.
        """

        return await self.send(ctx, member, "pat")

    @command()
    async def poke(self, ctx: Context, member: Member) -> Message:
        """
        Poke someone.
        """

        return await self.send(ctx, member, "poke")

    @command()
    async def punch(self, ctx: Context, member: Member) -> Message:
        """
        Punch someone.
        """

        return await self.send(ctx, member, "punch")

    @command()
    async def slap(self, ctx: Context, member: Member) -> Message:
        """
        Slap someone.
        """

        return await self.send(ctx, member, "slap")

    @command()
    async def smug(self, ctx: Context, member: Member) -> Message:
        """
        Smug at someone.
        """

        return await self.send(ctx, member, "smug")

    @command()
    async def tickle(self, ctx: Context, member: Member) -> Message:
        """
        Tickle someone.
        """

        return await self.send(ctx, member, "tickle")
