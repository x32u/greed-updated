from logging import getLogger
from random import choice
from typing import TYPE_CHECKING

from aiohttp import ClientSession as OriginalClientSession, ClientTimeout, TCPConnector
from colorama import Fore
from yarl import URL

from config import Authorization

if TYPE_CHECKING:
    from main import greedbot

log = getLogger("greedbot/lastfm")
BASE_URL = URL.build(
    scheme="https",
    host="ws.audioscrobbler.com",
)


class AsyncClient(OriginalClientSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, connector=TCPConnector(ssl=False))

    async def get(self, *args, **kwargs):
        kwargs["params"] = {
            "api_key": choice(Authorization.LASTFM),
            "autocorrect": 1,
            "format": "json",
            **kwargs.get("params", {}),
        }
        log.debug(
            f"GET {Fore.LIGHTMAGENTA_EX}{kwargs['params']['method']}{Fore.RESET} with {Fore.LIGHTRED_EX}{kwargs['params']['api_key']}{Fore.RESET}."
        )

        response = await super().get(*args, **kwargs)
        if response.status == 429:
            log.warning("Last.fm API rate limit exceeded, changing API key...")
            kwargs["api_key"] = choice(Authorization.LASTFM)
            return await self.get(*args, **kwargs)

        return response


http = AsyncClient(
    headers={
        "User-Agent": "greedbot Last.fm Integration (DISCORD BOT)",
    },
    base_url=BASE_URL.human_repr(),
    timeout=ClientTimeout(total=20),
)


async def setup(bot: "greedbot") -> None:
    from .lastfm import Lastfm

    await bot.add_cog(Lastfm(bot))
