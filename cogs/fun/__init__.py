from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import greedbot


async def setup(bot: "greedbot") -> None:
    from .fun import Fun

    await bot.add_cog(Fun(bot))
