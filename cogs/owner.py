import os

import discord
from discord import app_commands
from discord.ext import commands


class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="reload", description="Reloads the bot's cogs (bot owner only).")
    @app_commands.describe(sync="Also re-sync slash commands with Discord")
    @app_commands.allowed_installs(guilds=False, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def reload(self, interaction: discord.Interaction, sync: bool = False):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("This command is only for the bot owner.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        reloaded_cogs = []
        failed_cogs = []

        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename != '__init__.py':
                cog_name = f'cogs.{filename[:-3]}'
                try:
                    await self.bot.reload_extension(cog_name)
                    reloaded_cogs.append(cog_name)
                except Exception as e:
                    failed_cogs.append(f"{cog_name} ({e})")

        result_msg = f"✅ Reloaded {len(reloaded_cogs)} cogs."
        if failed_cogs:
            result_msg += f"\n❌ Failed to reload: {', '.join(failed_cogs)}"

        if sync:
            try:
                synced = await self.bot.tree.sync()
                result_msg += f"\n✅ Synced {len(synced)} slash commands globally!"
            except discord.HTTPException as e:
                result_msg += f"\n❌ Failed to sync slash commands: {e}"

        await interaction.followup.send(result_msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(OwnerCog(bot))
