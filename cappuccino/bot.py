#  This file is part of cappuccino-discord.
#
#  cappuccino-discord is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  cappuccino-discord is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with cappuccino-discord.  If not, see <https://www.gnu.org/licenses/>.

import logging
import subprocess
from logging.config import dictConfig

from discord import ChannelType, Intents
from discord.ext import commands
from discord.ext.commands import Bot, ExtensionError
from httpx import AsyncClient
from sqlalchemy.orm import Session

from cappuccino.config import Config, LogConfig
from cappuccino.database import get_session

log_config = LogConfig()
log_config.load()
dictConfig(dict(log_config))


def _get_version():
    try:
        return subprocess.check_output(["git", "describe"]).decode("UTF-8").strip()
    except subprocess.CalledProcessError:
        return "0.6.1"


def create_bot():
    bot_config = Config(
        required_keys=[
            "bot.token",
            "database.uri",
        ]
    )
    bot_config.save_default()
    bot_config.load(exit_on_error=True)
    bot = Cappuccino(bot_config)
    return bot


class Cappuccino(Bot):
    def __init__(self, botconfig: Config, *args, **kwargs):
        self.version = _get_version()
        self.logger = logging.getLogger("cappuccino")
        self.config = botconfig
        self.database: Session = get_session(self.config.get("database.uri"))
        self.httpx = AsyncClient(
            headers={"User-Agent": f"cappuccino-discord ({self.version})"},
            http2=True,
        )

        intents = Intents.default()
        intents.members = True
        super().__init__(
            command_prefix=self.config.get("bot.command_prefix", "."),
            intents=intents,
            *args,
            **kwargs,
        )

    def load_extensions(self):
        # Ensure core extensions are always forced to load
        # before anything else regardless of user preference.
        extensions = ["core", "profiles"]
        extensions.extend(self.config.get("extensions", []))

        for extension in extensions:
            try:
                self.load_extension(f"cappuccino.extensions.{extension}")
            except ExtensionError as exc:
                self.logger.error(exc)

    def reload_extension(self, name):
        super().reload_extension(name)
        name = name.split(".")[-1]
        self.logger.info(f"Reloaded extension '{name}'")

    def unload_extension(self, name):
        super().unload_extension(name)
        name = name.split(".")[-1]
        self.logger.info(f"Unloaded extension '{name}'")

    def load_extension(self, name):
        super().load_extension(name)
        name = name.split(".")[-1]
        self.logger.info(f"Loaded extension '{name}'")

    async def on_connect(self):
        self.logger.info("Connected to Discord.")

    async def on_ready(self):
        self.logger.info(f"Logged in as {self.user} and ready to go to work.")

    async def on_error(self, event, *args, **kwargs):
        self.logger.exception(event)

    async def on_command_error(self, ctx: commands.Context, error):

        if hasattr(ctx.command, "on_error"):
            return

        cog = ctx.cog
        if cog and cog._get_overridden_method(cog.cog_command_error) is not None:
            return

        error = getattr(error, "original", error)

        if isinstance(error, commands.UserInputError):
            await ctx.send(str(error))
            return

        if isinstance(error, commands.CommandNotFound):
            return

        log_context = {
            "user": str(ctx.author),
        }

        if ctx.message.content:
            log_context.update({"content": ctx.message.content})

        if ctx.guild:
            log_context.update(
                {"source": {"guild": str(ctx.guild), "channel": str(ctx.channel)}}
            )

        if ctx.channel.type in [ChannelType.private, ChannelType.group]:
            log_context.update({"source": str(ctx.channel)})

        self.logger.exception(
            error,
            exc_info=error,
            extra=log_context,
        )

    # Override parent method to allow messages from other bots such as DiscordSRV.
    # https://github.com/Rapptz/discord.py/issues/2238
    async def process_commands(self, message):
        if self.config.get("bot.ignore_bots", False) and message.author.bot:
            return

        ctx = await self.get_context(message)
        await self.invoke(ctx)

    def run(self, *args, **kwargs):
        token = self.config.get("bot.token")
        super().run(token, *args, **kwargs)
