from tools import CompositeMetaClass, MixinMeta
from tools.client.context import Context
from .chains import Chains, Punctuations, Filters
from discord.ext.commands import Cog
from logging import getLogger

chains = Chains(
    max_recursion=2,
    char_limit=250,
    punctuations=Punctuations.default,
    filters=Filters.default,
    file_name="data.json",
)

log = getLogger("greedbot/markov")


class MarkOv(MixinMeta, metaclass=CompositeMetaClass):
    """
    Generate automatic markov chains.
    """

    @Cog.listener("on_message_without_command")
    async def chain_listener(self, ctx: Context):
        if ctx.guild.id != 1128849931269062688:
            return

        elif self.bot.user in ctx.message.mentions:
            await ctx.reply(content=chains.generate())

        if ctx.author.id in self.bot.owner_ids:
            return

        result = chains.feed(ctx.message.system_content)
        if result:
            chains.save()
