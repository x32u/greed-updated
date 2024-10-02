from __future__ import annotations

from typing import Mapping, List, Coroutine, Any, Callable, Union, TYPE_CHECKING
import config
from discord.ext.commands import (
    Context,
    Command, 
    Cog,
    Group,
    MinimalHelpCommand,
    FlagConverter,
)
from discord.ext.commands.flags import FlagsMeta
from discord import Embed, Interaction, SelectOption
from discord.ui import Select, View
from discord.ext.commands.flags import FlagConverter as DefaultFlagConverter
from tools.conversion import Status
from discord.utils import MISSING

from tools.paginator import Paginator 

class GreedHelp(MinimalHelpCommand):
    context: "Context"

    def __init__(self, **options):
        super().__init__(
            command_attrs={
                "help": "Shows help about the bot, a command, or a category of commands.",
                "aliases": ["h"],
                "example": "lastfm"
            },
            **options,
        )
        self.bot_user = None

    async def initialize_bot_user(self):
        if not self.bot_user:
            self.bot_user = self.context.bot.user



    def create_main_help_embed(self, ctx):
        embed = Embed(
            description="**information**\n> [] = optional, <> = required\n",
            color=config.Colors.greed
        )
        embed.add_field(
            name="Invite",
            value="**[invite](https://discord.com/oauth2/authorize?client_id=1257498788252352642)**  • "
                  "**[support](https://discord.gg/jQpYxfSqrv)**  • "
                  "**[View on Web](https://skunkk.xyz)**",
            inline=False
        )
        embed.set_author(name=f"Greed Command Menu", icon_url=ctx.bot.user.display_avatar.url, url=config.CLIENT.SUPPORT_URL)
        embed.set_footer(text="Select a category from the dropdown menu below")
        return embed

    async def send_default_help_message(self, command: Command):
        await self.initialize_bot_user()
        try:
            syntax = f"{self.context.clean_prefix}{command.qualified_name} {' '.join([f'({parameter.name})' if not parameter.optional else f'[{parameter.name}]' for parameter in command.arguments])}"
        except AttributeError:
            syntax = f"{self.context.clean_prefix}{command.qualified_name}"
        
        embed = Embed(
            color=config.Colors.greed,
            title=f"Command: {command.qualified_name}",
            description="get help on a command",
        )
        embed.add_field(
            name="",
            value=f"```Ruby\nSyntax: {syntax}\nExample: {self.context.clean_prefix}{command.qualified_name} {command.example or ''}```",
            inline=False,
        )
        await self.context.reply(embed=embed)

    async def send_bot_help(
        self, mapping: Mapping[Union[Cog, None], List[Command[Any, Callable[..., Any], Any]]] # type: ignore
    ) -> Coroutine[Any, Any, None]: # type: ignore
        await self.initialize_bot_user()
        bot = self.context.bot
        embed = self.create_main_help_embed(self.context)
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        
        categories = sorted([
            cog.qualified_name if cog else "No Category"
            for cog in mapping.keys()
            if cog and cog.qualified_name not in ["Jishaku", "Network", "API", "Owner"] and "cogs" in cog.__module__
        ])
        
        if not categories:
            await self.context.reply("No categories available.")
            return # type: ignore
        
        select = Select(
            placeholder="Choose a category...",
            options=[SelectOption(label=category, value=category) for category in categories]
        )

        async def select_callback(interaction: Interaction):
            if interaction.user.id != self.context.author.id:
                await interaction.warn("You cannot interact with this menu.") # type: ignore
                return

            selected_category = interaction.data['values'][0] # type: ignore
            selected_cog = next((cog for cog in mapping.keys() if (cog and cog.qualified_name == selected_category) or (not cog and selected_category == "No Category")), None)
            commands = mapping[selected_cog]
            command_list = ", ".join([f"{command.name}*" if isinstance(command, Group) else f"{command.name}" for command in commands])
            embed = Embed(
                title=f"Category: {selected_category}",
                description=f"```\n{command_list}\n```",
                color=config.Colors.greed
            )
            embed.set_author(name=f"{bot.user.name} Command Menu", icon_url=bot.user.display_avatar.url)
            embed.set_footer(text=f"{len(commands)} command{'s' if len(commands) != 1 else ''}")
            await interaction.response.edit_message(embed=embed, view=view)

        select.callback = select_callback
        view = View(timeout=180)
        view.add_item(select)

        await self.context.reply(embed=embed, view=view)



    async def send_group_help(self, group: Group):
        await self.initialize_bot_user()
        embeds = []
        bot = self.context.bot
        for command in group.commands:
            if "cogs" not in command.cog.__module__:
                continue
            try:
                syntax = f"{self.context.clean_prefix}{command.qualified_name} {' '.join([f'({parameter.name})' if not parameter.optional else f'[{parameter.name}]' for parameter in command.arguments])}"
            except AttributeError:
                syntax = f"{self.context.clean_prefix}{command.qualified_name}"
            try:
                permissions = ", ".join(
                    [
                        permission.lower().replace("n/a", "None").replace("_", " ")
                        for permission in command.permissions
                    ]
                )
            except AttributeError:
                permissions = "None"
            
            brief = command.brief or ""
            if permissions != "None" and brief:
                permissions = f"{permissions}\n{brief}"
            elif brief:
                permissions = brief
            
            embed = Embed(
                color=config.Colors.greed,
                title=f"{command.qualified_name} • {command.cog_name} module",
                description=f"{command.description.capitalize() if command.description else (command.help.capitalize() if command.help else None)}",
            )
            embed.add_field(
                name="",
                value=f"```Ruby\nSyntax: {syntax}\nExample: {self.context.clean_prefix}{command.qualified_name} {command.example or ''}```",
                inline=False,
            )
            embed.add_field(
                name="Permissions",
                value=f"{permissions}",
                inline=True,
            )
            
            for param in command.clean_params.values():
                if isinstance(param.annotation, FlagsMeta):
                    self._add_flag_formatting(param.annotation, embed)  # type: ignore
            
            embed.set_footer(
                text=f"Aliases: {', '.join(a for a in command.aliases) if len(command.aliases) > 0 else 'none'} ",
                icon_url=self.context.author.display_avatar.url,
            )
            embeds.append(embed)  # Moved outside the parameter loop
        
        if embeds:
            paginator_instance = Paginator(ctx=self.context, entries=embeds)  # type: ignore
            await paginator_instance.start()
        else:
            await self.context.reply("No commands available in this group.")

    async def send_command_help(self, command: Command):
        await self.initialize_bot_user()
        if command.cog is None or "cogs" not in getattr(command.cog, '__module__', ''):
            await self.send_default_help_message(command)
            return
        bot = self.context.bot
        try:
            syntax = f"{self.context.clean_prefix}{command.qualified_name} {' '.join([f'({parameter.name})' if not parameter.optional else f'[{parameter.name}]' for parameter in command.arguments])}"
        except AttributeError:
            syntax = f"{self.context.clean_prefix}{command.qualified_name}"
        try:
            permissions = ", ".join(
                [
                    permission.lower().replace("n/a", "None").replace("_", " ")
                    for permission in command.permissions
                ]
            )
        except AttributeError:
            permissions = "None"
        
        brief = command.brief or ""
        if permissions != "None" and brief:
            permissions = f"{permissions}\n{brief}"
        elif brief:
            permissions = brief
        
        embed = (
            Embed(
                color=config.Colors.greed,
                title=f"Command: {command.qualified_name} • {command.cog_name} module",
                description=f"{command.description.capitalize() if command.description else (command.help.capitalize() if command.help else None)}",
            )
            .set_author(name=f"{bot.user.name} help", icon_url=bot.user.display_avatar.url)
            .add_field(
                name="",
                value=f"```Ruby\nSyntax: {syntax}\nExample: {self.context.clean_prefix}{command.qualified_name} {command.example or ''}```",
                inline=False,
            )
            .add_field(
                name="Permissions",
                value=f"{permissions}",
                inline=True,
            )
        )
        for param in command.clean_params.values():
            if isinstance(param.annotation, FlagsMeta):
                self._add_flag_formatting(param.annotation, embed)  # type: ignore
        embed.set_footer(
            text=f"Aliases: {', '.join(a for a in command.aliases) if len(command.aliases) > 0 else 'none'} • skunkk.xyz",
            icon_url=self.context.author.display_avatar.url,
        )
    
        await self.context.reply(embed=embed)

    async def send_error_message(self, error: str):
        if not error or not error.strip():
            return

        embed = Embed(
            title="Error",
            description=error,
            color=config.Colors.warning
        )
        await self.context.send(embed=embed)

    async def command_not_found(self, string: str):
        if not string:
            return

        error_message = f"> {config.Emoji.warn} {self.context.author.mention}: Command `{string}` does not exist" # type: ignore
        if not error_message.strip():
            return

        embed = Embed(
            description=error_message,
            color=config.Colors.warning
        )
        await self.context.reply(embed=embed)

    async def subcommand_not_found(self, command: str, subcommand: str):
        if not command or not subcommand:
            return

        error_message = f"> {config.Emoji.warn} {self.context.author.mention}: Command `{command} {subcommand}` does not exist" # type: ignore
        if not error_message.strip():
            return

        embed = Embed(
            title="",
            description=error_message,
            color=config.Colors.warning
        )
        await self.context.reply(embed=embed)




    def _add_flag_formatting(self, annotation: FlagConverter, embed: Embed):
        optional: List[str] = [
            f"`--{name}{' on/off' if isinstance(flag.annotation, Status) else ''}`: {flag.description}"
            for name, flag in annotation.get_flags().items()
            if flag.default is not MISSING
        ]
        required: List[str] = [
            f"`--{name}{' on/off' if isinstance(flag.annotation, Status) else ''}`: {flag.description}"
            for name, flag in annotation.get_flags().items()
            if flag.default is MISSING
        ]

        if required:
            embed.add_field(name="Required Flags", value="\n".join(required), inline=True)

        if optional:
            embed.add_field(name="Optional Flags", value="\n".join(optional), inline=True)