import logging
import math
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from utils.ratelimit import RateLimiter, deny_if_limited

load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
OWNER_ID = os.getenv('OWNER_ID')

log = logging.getLogger(__name__)

# Applies to every slash command, on top of each command's own cooldown.
COMMAND_LIMITER = RateLimiter(rate=5, per=15.0)


class BotCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await deny_if_limited(interaction, COMMAND_LIMITER, interaction.user.id)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"This command is on cooldown. Try again in {math.ceil(error.retry_after)}s."
        elif isinstance(error, app_commands.CheckFailure):
            message = "You don't have permission to use this command."
        else:
            command_name = interaction.command.qualified_name if interaction.command else "unknown"
            log.error("Error in /%s (guild %s, user %s)", command_name, interaction.guild_id, interaction.user.id, exc_info=error)
            message = "Something went wrong while running this command. The error has been logged."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        # Only slash commands and buttons are used, so message events aren't needed.
        intents.messages = False
        intents.message_content = False

        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
            tree_cls=BotCommandTree,
            owner_id=int(OWNER_ID) if OWNER_ID else None,
            # Commands are server-only unless they opt in (e.g. /reload is user-installed).
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
        )

    async def setup_hook(self):
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename != '__init__.py':
                await self.load_extension(f'cogs.{filename[:-3]}')

        try:
            synced = await self.tree.sync()
            log.info("Synced %d slash commands", len(synced))
        except discord.HTTPException:
            log.exception("Failed to sync slash commands")

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user.name, self.user.id)

bot = MyBot()

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN not found in environment variables.")
    else:
        bot.run(TOKEN, root_logger=True)
