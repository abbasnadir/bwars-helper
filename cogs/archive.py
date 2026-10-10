import discord
from discord.ext import commands
from discord import app_commands
import chat_exporter
import io
import os
import re

from utils.permissions import get_archived_host_perms, get_everyone_perms, get_host_perms, get_spectator_perms, get_in_game_perms, get_host_perms, get_spectator_perms, get_in_game_perms
from utils.checks import has_guild_manage_channels
from utils.views import CancelView, ConfirmArchiveCategoryView
from utils.config import ROLE_HOST_PLUS, ROLE_HOST, ROLE_JAILED, ROLE_SPECTATOR, ROLE_ARCHIVE

class ArchiveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_transcripts = set()

    @app_commands.command(name='archive', description='Generates an HTML transcript of the current channel or an entire category.')
    @app_commands.describe(
        category="Optional: An entire category to archive (skips names starting with -)",
        fetch_reactors="Whether to fetch reactions for the transcript"
    )
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    @has_guild_manage_channels()
    async def archive(self, interaction: discord.Interaction, category: discord.CategoryChannel = None, fetch_reactors: bool = True):
        if category:
            await self._archive_category(interaction, category, fetch_reactors)
        else:
            await self._archive_channel(interaction, fetch_reactors)

    async def _archive_channel(self, interaction: discord.Interaction, fetch_reactors: bool):
        if interaction.channel.id in self.active_transcripts:
            return await interaction.response.send_message("A transcript is already being generated in this channel. Please wait or cancel the current one.", ephemeral=True)
            
        self.active_transcripts.add(interaction.channel.id)
        
        view = CancelView(interaction.user.id, is_category=False)
        await interaction.response.send_message("Generating transcript... This may take a moment depending on the channel size.", view=view)
        
        msg = await interaction.original_response()
        
        messages = []
        try:
            async for message in interaction.channel.history(limit=None):
                if view.cancelled:
                    break
                messages.append(message)

            if not messages:
                await msg.edit(content="No messages found to export.", view=None)
                return
                
            if view.cancelled:
                await msg.edit(content=f"Cancelled. Exporting the {len(messages)} messages collected so far...", view=None)
            else:
                await msg.edit(content=f"Generating transcript for {len(messages)} messages...", view=None)

            transcript = await chat_exporter.raw_export(
                interaction.channel,
                messages=messages,
                tz_info='UTC',
                bot=self.bot,
                guild=interaction.guild,
                fetch_reactors=fetch_reactors
            )
            
            if transcript is None:
                await msg.edit(content="Failed to generate the transcript.", view=None)
                return

            transcript_file = discord.File(
                io.BytesIO(transcript.encode()),
                filename=f"transcript-{interaction.channel.name}.html"
            )
            
            await interaction.channel.send(content=f"Here is the transcript ({len(messages)} messages):", file=transcript_file)
            await msg.delete()
            
        except discord.Forbidden:
            await interaction.followup.send("I don't have permissions to read message history in this channel.", ephemeral=True)
        except Exception as e:
            await msg.edit(content=f"An error occurred: {e}", view=None)
        finally:
            self.active_transcripts.discard(interaction.channel.id)

    async def _archive_category(self, interaction: discord.Interaction, category: discord.CategoryChannel, fetch_reactors: bool):
        match = re.match(r"(?i)the game (\d+)", category.name)
        new_category_name = None
        if match:
            game_id = match.group(1)
            new_category_name = f"Archive {game_id}"
            host_role_name = f"Host for Game {game_id}"
            has_role = discord.utils.get(interaction.user.roles, name=host_role_name)
            if not has_role and not interaction.user.guild_permissions.administrator:
                return await interaction.response.send_message(f"You must have the **{host_role_name}** role to archive this game category.", ephemeral=True)
        
        confirm_view = ConfirmArchiveCategoryView(interaction.user.id)
        
        prompt_msg = f"Are you sure you want to archive the category **{category.name}**?"
        if new_category_name:
            prompt_msg += f"\nIt will be renamed to **{new_category_name}**."
        prompt_msg += "\nThis will generate transcripts and **modify channel permissions** for all un-skipped channels."
            
        await interaction.response.send_message(prompt_msg, view=confirm_view)
        
        await confirm_view.wait()
        if confirm_view.value is not True:
            return
            
        view = CancelView(interaction.user.id, is_category=True)
        msg = await interaction.edit_original_response(content=f"Starting archival of category **{category.name}**...\nArchiving channels and their threads, skipping those starting with `-`.", view=view)
        
        archived_count = 0
        skipped_count = 0
        
        channels_to_process = []
        for channel in category.channels:
            if channel.name.startswith('-'):
                skipped_count += 1
                continue
            channels_to_process.append(channel)

        for channel in channels_to_process:
            if view.cancelled:
                break
                
            success = await self._export_channel_or_thread(channel, view, fetch_reactors)
            if view.cancelled:
                break
            if success:
                archived_count += 1
                
            # Apply new permissions
            new_overwrites = {}
            
            new_overwrites[interaction.guild.default_role] = get_everyone_perms()
            
            host_plus = interaction.guild.get_role(ROLE_HOST_PLUS)
            if host_plus:
                new_overwrites[host_plus] = get_archived_host_perms()
                
            host_reg = interaction.guild.get_role(ROLE_HOST)
            if host_reg:
                new_overwrites[host_reg] = get_archived_host_perms()
                
            jailed = interaction.guild.get_role(ROLE_JAILED)
            if jailed:
                new_overwrites[jailed] = discord.PermissionOverwrite(view_channel=False)
                
            archives_role = interaction.guild.get_role(ROLE_ARCHIVE)
            if archives_role:
                new_overwrites[archives_role] = discord.PermissionOverwrite(view_channel=True)
                
            try:
                kwargs = {'overwrites': new_overwrites}
                if 'game_id' in locals() and game_id and channel.name.endswith(f"-{game_id}"):
                    kwargs['name'] = channel.name.replace(f"-{game_id}", "")
                await channel.edit(**kwargs)
            except discord.Forbidden:
                pass
                
            threads_to_process = []
            if hasattr(channel, 'threads'):
                threads_to_process.extend(channel.threads)
            if hasattr(channel, 'archived_threads'):
                try:
                    async for thread in channel.archived_threads(limit=None):
                        threads_to_process.append(thread)
                except discord.Forbidden:
                    pass
                    
            for thread in threads_to_process:
                if view.cancelled:
                    break
                    
                if thread.name.startswith('-'):
                    skipped_count += 1
                    continue
                    
                success = await self._export_channel_or_thread(thread, view, fetch_reactors)
                if view.cancelled:
                    break
                if success:
                    archived_count += 1
                    
            if view.cancelled:
                break

        if view.cancelled:
            await msg.edit(content=f"Archival cancelled. Exported {archived_count} channels/threads. Skipped {skipped_count}.", view=None)
        else:
            final_name = category.name
            if new_category_name:
                try:
                    await category.edit(name=new_category_name)
                    final_name = new_category_name
                except discord.Forbidden:
                    pass
            await msg.edit(content=f"Archival completed for category **{final_name}**.\nExported {archived_count} channels/threads. Skipped {skipped_count}.", view=None)

    async def _export_channel_or_thread(self, channel_or_thread, view, fetch_reactors):
        messages = []
        try:
            async for message in channel_or_thread.history(limit=None):
                if view.cancelled:
                    break
                messages.append(message)
        except discord.Forbidden:
            return False

        if not messages:
            return False

        try:
            transcript = await chat_exporter.raw_export(
                channel_or_thread,
                messages=messages,
                tz_info='UTC',
                bot=self.bot,
                guild=channel_or_thread.guild,
                fetch_reactors=fetch_reactors
            )
        except Exception:
            return False
            
        if transcript:
            try:
                if isinstance(channel_or_thread, discord.Thread) and channel_or_thread.archived:
                    try:
                        await channel_or_thread.edit(archived=False)
                    except discord.HTTPException:
                        pass
                        
                file = discord.File(
                    io.BytesIO(transcript.encode()),
                    filename=f"transcript-{channel_or_thread.name}.html"
                )
                
                if view.cancelled:
                    await channel_or_thread.send(content=f"Archive generated early due to cancellation ({len(messages)} messages):", file=file)
                else:
                    await channel_or_thread.send(content=f"Archive generated ({len(messages)} messages):", file=file)
                return True
            except discord.Forbidden:
                return False
        return False


    @app_commands.command(name='unarchive', description='Reverts an archived category back to active state.')
    @app_commands.describe(category="The archived category to revert")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    @has_guild_manage_channels()
    async def unarchive(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        match = re.match(r"(?i)archive (\d+)", category.name)
        if not match:
            return await interaction.response.send_message(f"Category **{category.name}** doesn't appear to be a valid archived game category (e.g. 'Archive 1').", ephemeral=True)
            
        game_id = match.group(1)
        host_role_name = f"Host for Game {game_id}"
        has_role = discord.utils.get(interaction.user.roles, name=host_role_name)
        
        if not has_role and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(f"You must have the **{host_role_name}** role to unarchive this game category.", ephemeral=True)

        await interaction.response.defer()

        # Generate original overwrites
        host_perms = get_host_perms()
        
        # Base default overwrite
        new_overwrites = {
            interaction.guild.default_role: get_everyone_perms()
        }
        
        host_plus = interaction.guild.get_role(ROLE_HOST_PLUS)
        if host_plus:
            new_overwrites[host_plus] = host_perms
            
        host_reg = interaction.guild.get_role(ROLE_HOST)
        if host_reg:
            new_overwrites[host_reg] = host_perms
            
        jailed = interaction.guild.get_role(ROLE_JAILED)
        if jailed:
            new_overwrites[jailed] = discord.PermissionOverwrite(view_channel=False)
            
        spectator = interaction.guild.get_role(ROLE_SPECTATOR)
        if spectator:
            new_overwrites[spectator] = get_spectator_perms()
            
        in_game_role = discord.utils.get(interaction.guild.roles, name=f"In game {game_id}")
        if in_game_role:
            new_overwrites[in_game_role] = get_in_game_perms()
            
        host_game_role = discord.utils.get(interaction.guild.roles, name=host_role_name)
        if host_game_role:
            new_overwrites[host_game_role] = host_perms
            
        # Revert category name
        new_category_name = f"The game {game_id}"
        try:
            await category.edit(name=new_category_name)
        except discord.Forbidden:
            pass

        channels_restored = 0
        skipped_count = 0
        
        base_channels_to_rename = ["news", "host-status", "players", "game-chat", "voting-phase", "dead-cards"]

        for channel in category.channels:
            if channel.name.startswith('-'):
                skipped_count += 1
                continue
                
            try:
                kwargs = {'overwrites': new_overwrites}
                if channel.name in base_channels_to_rename:
                    kwargs['name'] = f"{channel.name}-{game_id}"
                await channel.edit(**kwargs)
                channels_restored += 1
            except discord.Forbidden:
                pass
                
        await interaction.followup.send(f"Successfully unarchived! Category renamed to **{new_category_name}**. Restored permissions and names for {channels_restored} channels. Skipped {skipped_count}.")

    @archive.error
    async def command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if hasattr(error, 'retry_after'):
                await interaction.response.send_message(f"Command on cooldown. Please try again in {error.retry_after:.2f}s.", ephemeral=True)
            else:
                await interaction.response.send_message("You need guild-wide 'Manage Channels' permission to use this command.", ephemeral=True)
        else:
            import traceback
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            if len(tb) > 1900:
                tb = tb[:1900] + "..."
            msg = f"An unexpected error occurred:\n```py\n{tb}\n```"
            
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ArchiveCog(bot))
