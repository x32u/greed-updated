from __future__ import annotations

import asyncio
from io import BytesIO
from typing import TYPE_CHECKING, Literal, Optional, cast

from discord import File, Message
from discord.ext.commands import (
    BucketType,
    Range,
    command,
    cooldown,
    flag,
    group,
    has_permissions,
    parameter,
)
from PIL import Image
from wand.image import Image as WandImage
from xxhash import xxh32_hexdigest

from tools import (
    CACHE_ROOT,
    CompositeMetaClass,
    MixinMeta,
    executor_function,
    temp_file,
)
from tools.client import Context, FlagConverter
from tools.conversion import PartialAttachment

if TYPE_CHECKING:
    from cogs.utility.utility import Utility


class Flags(FlagConverter):
    title: Optional[str] = flag(
        description="The title of the song.",
        aliases=["t", "name"],
        default=None,
    )
    artist: Optional[str] = flag(
        description="The artist of the song.",
        aliases=["a"],
        default=None,
    )
    album: Optional[str] = flag(
        description="The album of the song.",
        aliases=["al"],
        default=None,
    )


async def convert_to_mp3(
    buffer: bytes,
    flags: Optional[Flags] = None,
) -> bytes:
    """
    Convert a video to an mp3.
    """

    await CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    async with temp_file("mp4") as tmp:
        await tmp.write_bytes(buffer)

        async with temp_file("mp3") as output:
            args = [
                "-i",
                str(tmp),
                "-vn",
                "-acodec",
                "libmp3lame",
                "-y",
                "-loglevel",
                "panic",
            ]
            if flags:
                if flags.title:
                    args.extend(["-metadata", f"title={flags.title}"])
                if flags.artist:
                    args.extend(["-metadata", f"artist={flags.artist}"])
                if flags.album:
                    args.extend(["-metadata", f"album={flags.album}"])

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                *args,
                str(output),
            )
            await proc.communicate()

            return await output.read_bytes()


@executor_function
def rotate_image(buffer: bytes, degrees: int) -> BytesIO:
    """
    Rotate an image by a certain amount of degrees.
    """

    with Image.open(BytesIO(buffer)) as image:
        image = image.convert("RGBA").resize((image.width * 2, image.height * 2))
        image = image.rotate(
            angle=degrees,
            expand=True,
        )

        output = BytesIO()
        image.save(
            output,
            format="PNG",
        )

        output.seek(0)
        image.close()
        return output


@executor_function
def convert_image(buffer: bytes, format: str) -> BytesIO:
    """
    Convert an image to a different format.
    """

    with Image.open(BytesIO(buffer)) as image:
        output = BytesIO()
        image.save(
            output,
            format=format.upper(),
        )

        output.seek(0)
        image.close()
        return output


@executor_function
def compress_image(buffer: bytes, amount: int) -> BytesIO:
    """
    Compress the quality of an image.
    """

    with WandImage(blob=buffer) as image:
        image.coalesce()
        image.optimize_layers()
        image.compression_quality = 101 - amount

        output = image.make_blob(image.format)
        if not output:
            raise ValueError("Failed to create the blob buffer!")

        image.close()
        return BytesIO(output)


class Conversion(MixinMeta, metaclass=CompositeMetaClass):
    """
    File conversion via ffmpeg, pillow & wand.
    """

    @command(aliases=["mp3"])
    @cooldown(1, 5, BucketType.user)
    async def audio(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=PartialAttachment.fallback,
        ),
        *,
        flags: Flags,
    ) -> Message:
        """
        Convert a video to an mp3.
        """

        if not attachment.is_video() and not attachment.is_audio():
            return await ctx.warn("The attachment must be a video!")

        async with ctx.typing():
            name = flags.title or xxh32_hexdigest(attachment.filename)
            buffer = await convert_to_mp3(attachment.buffer, flags)

            return await ctx.reply(
                file=File(
                    BytesIO(buffer),
                    filename=f"{name}.mp3",
                ),
            )

    @group(
        aliases=[
            "images",
            "img",
        ],
        invoke_without_command=True,
    )
    @cooldown(1, 4, BucketType.user)
    async def image(self, ctx: Context, *, query: str) -> Message:
        """
        Modify an image.
        """

        await ctx.send_help(ctx.command)

    @image.command(name="rotate")
    @cooldown(1, 10, BucketType.user)
    @has_permissions(attach_files=True, embed_links=True)
    async def image_rotate(
        self,
        ctx: Context,
        degrees: Range[int, 1, 360] = 90,
        attachment: Optional[PartialAttachment] = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Rotate an image by a certain amount of degrees.
        """

        if not attachment:
            return await ctx.warn("You must provide an image attachment!")

        elif not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")

        async with ctx.typing():
            buffer = await rotate_image(attachment.buffer, degrees)

            return await ctx.reply(
                file=File(
                    buffer,
                    filename=attachment.filename,
                ),
            )

    @image.command(name="compress")
    @cooldown(1, 10, BucketType.user)
    @has_permissions(attach_files=True, embed_links=True)
    async def image_compress(
        self,
        ctx: Context,
        amount: Range[int, 1, 100] = 100,
        attachment: Optional[PartialAttachment] = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Compress the quality of an image.
        """

        if not attachment:
            return await ctx.warn("You must provide an image attachment!")

        elif not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")

        async with ctx.typing():
            buffer = await compress_image(attachment.buffer, amount)

            return await ctx.reply(
                file=File(
                    buffer,
                    filename=attachment.filename,
                ),
            )

    @image.command(
        name="convert",
        aliases=["conversion"],
    )
    @cooldown(1, 10, BucketType.user)
    @has_permissions(attach_files=True, embed_links=True)
    async def image_convert(
        self,
        ctx: Context,
        format: Literal["png", "jpg", "jpeg", "webp"] = "png",
        attachment: Optional[PartialAttachment] = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Convert an image to a different format.
        """

        if not attachment:
            return await ctx.warn("You must provide an image attachment!")

        elif not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")

        async with ctx.typing():
            buffer = await convert_image(attachment.buffer, format)

            return await ctx.reply(
                file=File(
                    buffer,
                    filename=f"{xxh32_hexdigest(attachment.buffer)}.{format}",
                ),
            )
