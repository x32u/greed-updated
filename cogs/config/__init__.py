from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import greedbot


async def setup(bot: "greedbot") -> None:
    from .config import Config

    await bot.add_cog(Config(bot))
