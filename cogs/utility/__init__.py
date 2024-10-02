from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import greedbot


async def setup(bot: "greedbot") -> None:
    from .utility import Utility

    await bot.add_cog(Utility(bot))
