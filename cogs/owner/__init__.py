from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import greedbot


async def setup(bot: "greedbot") -> None:
    from .owner import Owner

    await bot.add_cog(Owner(bot))
