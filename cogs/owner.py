import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Discord error codes for interactions that can no longer be answered.
ALREADY_ACKNOWLEDGED = 40060
UNKNOWN_INTERACTION = 10062


class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="reload", description="Reloads the bot's cogs (bot owner only).")
    @app_commands.describe(sync="Also re-sync slash commands with Discord")
    @app_commands.allowed_installs(guilds=False, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def reload(self, interaction: discord.Interaction, sync: bool = False):
        # Acknowledge first: /reload does slow work and Discord drops replies sent too late.
        answerable = True
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.InteractionResponded, discord.HTTPException) as error:
            code = getattr(error, "code", ALREADY_ACKNOWLEDGED if isinstance(error, discord.InteractionResponded) else None)
            if code not in (ALREADY_ACKNOWLEDGED, UNKNOWN_INTERACTION):
                raise
            # 40060: another copy of the bot answered it. 10062: the interaction expired
            # (usually replayed after a gateway reconnect), so there is nothing left to reply to.
            answerable = code == ALREADY_ACKNOWLEDGED
            log.warning(
                "Ignoring stale /reload interaction (code %s). Another bot copy may be running, "
                "or the gateway session replayed an old event.",
                code,
            )

        if not await self.bot.is_owner(interaction.user):
            if answerable:
                await interaction.followup.send("This command is only for the bot owner.", ephemeral=True)
            return

        reloaded_cogs = []
        failed_cogs = []

        for filename in sorted(os.listdir('./cogs')):
            if filename.endswith('.py') and filename != '__init__.py':
                cog_name = f'cogs.{filename[:-3]}'
                try:
                    await self.bot.reload_extension(cog_name)
                    reloaded_cogs.append(cog_name)
                except commands.ExtensionError as e:
                    # discord.py keeps the old version loaded when a reload fails, so the bot stays up.
                    log.exception("Failed to reload %s", cog_name)
                    failed_cogs.append(f"{cog_name} ({type(e).__name__})")

        result_msg = f"✅ Reloaded {len(reloaded_cogs)} cogs."
        if failed_cogs:
            result_msg += f"\n❌ Failed to reload: {', '.join(failed_cogs)}"

        if sync:
            try:
                synced = await self.bot.tree.sync()
                result_msg += f"\n✅ Synced {len(synced)} slash commands globally!"
            except discord.HTTPException as e:
                result_msg += f"\n❌ Failed to sync slash commands: {e}"

        if answerable:
            await interaction.followup.send(result_msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(OwnerCog(bot))
