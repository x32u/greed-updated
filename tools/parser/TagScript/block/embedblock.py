import re
from inspect import ismethod
from typing import Optional, Union

from discord import Colour, Embed
from discord.utils import utcnow

from ..exceptions import EmbedParseError
from ..interface import Block
from ..interpreter import Context
from .helpers import helper_split, implicit_bool


def string_to_color(argument: str) -> Colour:
    arg = argument.replace("0x", "").lower()

    if arg[0] == "#":
        arg = arg[1:]
    try:
        value = int(arg, base=16)
        return Colour.default() if not (0 <= value <= 0xFFFFFF) else Colour(value=value)
    except ValueError:
        arg = arg.replace(" ", "_")
        method = getattr(Colour, arg, None)
        if arg.startswith("from_") or method is None or not ismethod(method):
            return Colour.default()
        return method()


def set_color(embed: Embed, attribute: str, value: str):
    value = string_to_color(value)
    setattr(embed, attribute, value)


def set_dynamic_url(embed: Embed, attribute: str, value: str):
    method = getattr(embed, f"set_{attribute}")
    method(url=value)


def add_field(embed: Embed, _: str, payload: str):
    if (data := helper_split(payload, maxsplit=3)) is None:
        raise EmbedParseError("The `add_field` payload was not split by `&&`")
    try:
        name, value, _inline = data
        inline = implicit_bool(_inline)
        if inline is None:
            raise EmbedParseError(
                "The `inline` argument for `add_field` is not a boolean value (`_inline`)"
            )
    except ValueError:
        name, value = helper_split(payload, maxsplit=2)
        inline = False
    embed.add_field(name=name, value=value, inline=inline)


def set_footer(embed: Embed, _: str, payload: str):
    data = helper_split(payload, 2)
    if data is None:
        embed.set_footer(text=payload)
    else:
        text, icon_url = data
        embed.set_footer(text=text, icon_url=icon_url)


class EmbedBlock(Block):
    """
    An embed block will send an embed in the tag response.
    There are two ways to use the embed block, either by using properly
    formatted embed JSON from an embed generator or manually inputting
    the accepted embed attributes.

    **JSON**

    Using JSON to create an embed offers complete embed customization.
    Multiple embed generators are available online to visualize and generate
    embed JSON.

    **Usage:** ``{embed(<json>)}``

    **Payload:** None

    **Parameter:** json

    **Examples:** ::

        {embed({"title":"Hello!", "description":"This is a test embed."})}
        {embed({
            "title":"Here's a random duck!",
            "image":{"url":"https://random-d.uk/api/randomimg"},
            "color":15194415
        })}

    **Manual**

    The following embed attributes can be set manually:

    *   ``title``
    *   ``description``
    *   ``color``
    *   ``url``
    *   ``thumbnail``
    *   ``image``
    *   ``footer``
    *   ``field`` - (See below)

    Adding a field to an embed requires the payload to be split by ``|``, into
    either 2 or 3 parts. The first part is the name of the field, the second is
    the text of the field, and the third optionally specifies whether the field
    should be inline.

    **Usage:** ``{embed(<attribute>):<value>}``

    **Payload:** value

    **Parameter:** attribute

    **Examples:** ::

        {embed(color):#37b2cb}
        {embed(title):Rules}
        {embed(description):Follow these rules to ensure a good experience in our server!}
        {embed(field):Rule 1|Respect everyone you speak to.|false}
        {embed(footer):Thanks for reading!|{guild(icon)}}

    Both methods can be combined to create an embed in a tag.
    The following tagscript uses JSON to create an embed with fields and later
    set the embed title.

    ::

        {embed({{"fields":[{"name":"Field 1","value":"field description","inline":false}]})}
        {embed(title):my embed title}
    """

    ATTRIBUTE_HANDLERS = {
        "description": setattr,
        "title": setattr,
        "color": set_color,
        "colour": set_color,
        "url": setattr,
        "thumbnail": set_dynamic_url,
        "image": set_dynamic_url,
        "field": add_field,
        "footer": set_footer,
    }

    ACCEPTED_NAMES = ("embed", "Embed", *ATTRIBUTE_HANDLERS.keys())

    def get_embed(self, ctx: Context) -> Embed:
        if not ctx.response.actions.get("embeds"):
            ctx.response.actions["embeds"] = [Embed()]

        return ctx.response.actions["embeds"][-1]

    def add_embed(self, ctx: Context):
        if not ctx.response.actions.get("embeds"):
            ctx.response.actions["embeds"] = []

        ctx.response.actions["embeds"].append(Embed())

    @staticmethod
    def value_to_color(value: Optional[Union[int, str]]) -> Colour:
        if value is None or isinstance(value, Colour):
            return value
        if isinstance(value, int):
            return Colour(value)
        elif isinstance(value, str):
            return string_to_color(value)
        else:
            raise EmbedParseError(
                "Received invalid type for color key (expected string or int)"
            )

    @classmethod
    def update_embed(cls, embed: Embed, attribute: str, value: str) -> Embed:
        handler = cls.ATTRIBUTE_HANDLERS[attribute]
        try:
            handler(embed, attribute, value)
        except Exception as error:
            raise EmbedParseError(error) from error
        return embed

    @staticmethod
    def return_error(error: Exception) -> str:
        return f"Embed Parse Error: {error}"

    @staticmethod
    def return_embed(ctx: Context, embed: Embed) -> str:
        try:
            length = len(embed)
        except Exception as error:
            return str(error)
        if length > 6000:
            return f"`MAX EMBED LENGTH REACHED ({length}/6000)`"
        ctx.response.actions["embed"] = embed
        return ""

    async def process(self, ctx: Context) -> Optional[str]:
        string = str(ctx.verb.parsed_string).removeprefix("embed.")
        if match := re.match(r"(?P<name>[^:]+):\s*(?P<value>[^}]+)", string):
            if match["name"] in self.ATTRIBUTE_HANDLERS:
                embed = self.get_embed(ctx)
                self.update_embed(embed, match["name"], match["value"])

        else:
            if ctx.verb.parsed_string == "embed.timestamp":
                embed = self.get_embed(ctx)
                embed.timestamp = utcnow()

            if ctx.verb.parsed_string == "embed":
                self.add_embed(ctx)

        return ""
