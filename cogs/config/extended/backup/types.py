from typing import Optional, TypedDict


class CategoryData(TypedDict):
    id: int
    name: str
    position: int
    overwrites: dict[str, dict[str, Optional[bool]]]


class ChannelData(TypedDict):
    id: int
    type: int
    name: str
    position: int
    topic: Optional[str]
    slowmode_delay: Optional[int]
    user_limit: Optional[int]
    bitrate: Optional[int]
    nsfw: bool
    overwrites: dict[str, dict[str, Optional[bool]]]
    category_id: Optional[int]


class RoleData(TypedDict):
    id: int
    name: str
    position: int
    color: int
    hoist: bool
    mentionable: bool
    default: bool
    permissions: int
    premium: bool
    members: list[int]


class DesignData(TypedDict):
    icon: Optional[str]
    banner: Optional[str]
    splash: Optional[str]
    discovery_splash: Optional[str]


class BackupData(TypedDict):
    name: str
    design: DesignData
    afk_channel: Optional[int]
    afk_timeout: int
    verification_level: int
    rules_channel: Optional[int]
    community_updates: Optional[int]
    system: dict[str, Optional[int]]
    categories: list[CategoryData]
    channels: list[ChannelData]
    roles: list[RoleData]
    bans: dict[str, Optional[str]]


class BooleanArgs:
    def __init__(self, args):
        self._args = {}
        self.all = False

        for arg in args:
            arg = arg.lower()

            if arg in ["-", "!*"]:
                self.all = False
                self._args = {}

            if arg in ["+", "*"]:
                self.all = True

            if arg.startswith("!"):
                self._args[arg.strip("!")] = False

            else:
                self._args[arg] = True

    def get(self, item) -> bool:
        return self.all or self._args.get(item, False)

    def __getattr__(self, item) -> bool:
        return self.get(item)
