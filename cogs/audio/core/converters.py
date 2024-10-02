from re import compile
from typing import cast

from discord.ext.commands import CommandError, Converter

from tools.client import Context

from .player import Client

PERCENTAGE = compile(r"(\d+)%")
HH_MM_SS = compile(r"(?P<h>\d{1,2}):(?P<m>\d{1,2}):(?P<s>\d{1,2})")
MM_SS = compile(r"(?P<m>\d{1,2}):(?P<s>\d{1,2})")
OFFSET = compile(r"(?P<s>(?:\-|\+)\d+)\s*s")
HUMAN = compile(r"(?:(?P<m>\d+)\s*m\s*)?(?P<s>\d+)\s*[sm]")


class Percentage(Converter[int]):
    async def convert(self, ctx: Context, argument: str) -> int:
        if argument.isnumeric():
            amount = int(argument)

        elif match := PERCENTAGE.fullmatch(argument):
            amount = int(match.group(1))

        else:
            raise CommandError("Invalid percentage format!")

        return max(1, min(amount, 100))


class Position(Converter[int]):
    async def convert(self, ctx: Context, argument: str) -> int:
        ms: int = 0
        voice = cast(Client, ctx.voice_client)

        if ctx.invoked_with in ("fastforward", "ff") and not argument.startswith("+"):
            argument = "+" + argument

        elif ctx.invoked_with in ("rewind", "rw") and not argument.startswith("-"):
            argument = "-" + argument

        if match := HH_MM_SS.fullmatch(argument):
            ms += (
                int(match.group("h")) * 3600000
                + int(match.group("m")) * 60000
                + int(match.group("s")) * 1000
            )

        elif match := MM_SS.fullmatch(argument):
            ms += int(match.group("m")) * 60000 + int(match.group("s")) * 1000

        elif (match := OFFSET.fullmatch(argument)) and voice.current:
            ms += voice.position + int(match.group("s")) * 1000

        elif match := HUMAN.fullmatch(argument):
            if minutes := match.group("m"):
                ms += int(minutes) * 60000

            elif seconds := match.group("s"):
                ms += int(seconds) * 1000

        else:
            raise CommandError("Invalid position format!")

        if ms < 0:
            raise CommandError("Position cannot be negative!")

        return max(0, min(ms, voice.current.length)) if voice.current else ms
