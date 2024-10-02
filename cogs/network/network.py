from __future__ import annotations
from asyncio.subprocess import Process

from datetime import datetime, timedelta, timezone
from logging import getLogger
from typing import Dict, List
from asyncio import create_subprocess_exec

from discord.ext.ipc.server import Server
from discord.ext.ipc.objects import ClientPayload
from discord.ext.commands import Cog, Command, Group
from discord.ext.tasks import loop
from discord.utils import utcnow

import config
from main import greedbot

log = getLogger("greedbot/web")


class Network(Cog):
    server: Server
    process: Process

    def __init__(self, bot: greedbot):
        self.bot = bot
        self.server = Server(
            bot,  # type: ignore
            secret_key=config.IPC_KEY,
            logger=log,
        )

    async def cog_load(self) -> None:
        await self.server.start()
        self.process = await create_subprocess_exec("python3.10", "-m", "web")
        self.pubsub_subscriber.start()

    async def cog_unload(self) -> None:
        await self.server.stop()
        self.process.terminate()
        self.pubsub_subscriber.cancel()

    @loop(hours=2)
    async def pubsub_subscriber(self):
        """
        Subscribe to expired pubsub leases.
        """

        records = await self.bot.db.fetch(
            """
            SELECT id
            FROM pubsub
            WHERE expires_at < NOW()
            """
        )

        for record in records:
            await self.pubsub_subscribe(record["id"])

    async def pubsub_subscribe(self, channel_id: str) -> None:
        callback = f"https://{config.BACKEND_HOST}/pubsub/{config.PUBSUB_KEY}"
        topic = "https://www.youtube.com/xml/feeds/videos.xml?channel_id=" + channel_id

        await self.bot.session.post(
            "https://pubsubhubbub.appspot.com/subscribe",
            data={
                "hub.topic": topic,
                "hub.verify": "async",
                "hub.mode": "subscribe",
                "hub.callback": callback,
                "hub.verify_token": config.PUBSUB_KEY,
                "hub.lease_seconds": "432000",
            },
        )

    @Server.route("features")
    async def features(
        self,
        payload: ClientPayload,
    ) -> dict[str, list]:
        features: List[Dict[str, str | List[dict]]] = []
        for name, cog in sorted(self.bot.cogs.items(), key=lambda cog: cog[0].lower()):
            if name in ("Jishaku", "Owner"):
                continue

            commands: List[Dict[str, str | List[str]]] = [
                {
                    "name": command.qualified_name,
                    "aliases": list(command.aliases),
                    "signature": command.signature,
                    "description": command.short_doc,
                }
                for command in cog.walk_commands()
                if not command.hidden
            ]
            if commands:
                features.append(
                    {
                        "name": name,
                        "description": cog.description,
                        "commands": commands,
                    }
                )

        return {"features": features}

    @Server.route("subscribe")
    async def pubsub_receiver(self, payload: ClientPayload) -> dict[str, str]:
        """
        Handle a new subscription.
        """

        channel_id: str = payload.channel_id
        lease: str = payload.lease

        log.info(
            "Received a new subscription for %s.",
            channel_id,
        )

        await self.bot.db.execute(
            """
            INSERT INTO pubsub (id, expires_at)
            VALUES ($1, $2) ON CONFLICT (id)
            DO UPDATE SET expires_at = EXCLUDED.expires_at
            """,
            channel_id,
            utcnow() + timedelta(seconds=int(lease)),
        )
        return {"status": "ok"}

    @Server.route("publish")
    async def pubsub_publisher(self, payload: ClientPayload) -> dict[str, str]:
        """
        Handle a new item for a subscription.
        """

        video_id: str = payload.video_id
        channel_id: str = payload.channel_id
        published: str = payload.published

        if await self.bot.redis.sismember("pubsub", video_id) or datetime.fromisoformat(
            published
        ) < datetime.now(timezone.utc) - timedelta(hours=2):
            return {"text": "old"}

        await self.bot.redis.sadd("pubsub", video_id)
        self.bot.dispatch("pubsub", video_id, channel_id)

        return {"text": "ok"}

    @Server.route("tree")
    async def tree(self, payload: ClientPayload) -> dict[str, str]:
        """
        Command tree endpoint.
        """

        tree = ""
        for cog in self.bot.cogs.values():
            tree += self.build_tree(cog)
            for command in list(cog.get_commands()):
                tree += self.build_tree(command, 1)

        return {"tree": tree}

    def build_tree(self, command: Command | Cog, depth: int = 0) -> str:
        """
        Build a command tree.
        """

        if any(
            forbidden in command.qualified_name.lower()
            for forbidden in ("jishaku", "owner", "network")
        ):
            return ""

        if isinstance(command, Cog):
            return f"{'│    ' * depth}├── {command.qualified_name}\n"

        if command.hidden:
            return ""

        aliases = "|".join(command.aliases)
        if aliases:
            aliases = f"[{aliases}]"

        tree = f"{'│    ' * depth}├── {command.qualified_name}{aliases}: {command.short_doc}\n"
        if isinstance(command, Group):
            for subcommand in command.commands:
                tree += self.build_tree(subcommand, depth + 1)

        return tree
