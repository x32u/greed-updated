from json import dumps, loads
from logging import getLogger
from typing import Any, List, Optional, Union

from asyncpg import Connection, Pool
from asyncpg import Record as DefaultRecord
from asyncpg import create_pool

import config

from .settings import Settings

log = getLogger("greedbot/db")


def ENCODER(self: Any) -> str:
    return dumps(self)


def DECODER(self: bytes) -> Any:
    return loads(self)


class Record(DefaultRecord):
    def __getattr__(self: "Record", name: Union[str, Any]) -> Any:
        attr: Any = self[name]
        return attr

    def __setitem__(self, name: Union[str, Any], value: Any) -> None:
        self.__dict__[name] = value

    def to_dict(self: "Record") -> dict[str, Any]:
        return dict(self)


class Database(Pool):
    async def execute(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> str: ...

    async def fetch(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> List[Record]: ...

    async def fetchrow(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> Optional[Record]: ...

    async def fetchval(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> Optional[str | int]: ...


async def init(connection: Connection):
    await connection.set_type_codec(
        "JSONB",
        schema="pg_catalog",
        encoder=ENCODER,
        decoder=DECODER,
    )

    with open("tools/client/database/schema.sql", "r", encoding="UTF-8") as buffer:
        schema = buffer.read()
        await connection.execute(schema)


async def connect() -> Database:
    pool = await create_pool(
        config.DATABASE.DSN,
        record_class=Record,
        init=init,
    )
    if not pool:
        raise RuntimeError("Connection to PostgreSQL server failed!")

    log.debug("Connection to PostgreSQL has been established.")
    return pool  # type: ignore


__all__ = ("Database", "Settings")
