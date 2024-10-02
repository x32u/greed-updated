from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import greedbot


async def setup(bot: "greedbot") -> None:
    from .moderation import Moderation

    await bot.add_cog(Moderation(bot))
