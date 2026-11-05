import logging
import re

import discord
from discord.ext import commands
from discord import app_commands

from utils.permissions import get_archived_host_perms, get_everyone_perms, get_host_perms, get_spectator_perms, get_in_game_perms, get_host_perms, get_spectator_perms, get_in_game_perms
from utils.checks import has_guild_manage_channels
from utils.views import CancelView, ConfirmArchiveCategoryView
from utils.config import ROLE_HOST_PLUS, ROLE_HOST, ROLE_JAILED, ROLE_SPECTATOR, ROLE_ARCHIVE
from utils.transcript import build_transcript, is_export_running

log = logging.getLogger(__name__)

SKIP_PREFIXES = ('-', '_')
GAME_CATEGORY_NAME = re.compile(r"the game (\d+)", re.IGNORECASE)
ARCHIVED_CATEGORY_NAME = re.compile(r"archive (\d+)", re.IGNORECASE)

# Archives are slow and heavy, so each server gets one at a time and the bot runs only a few overall.
MAX_ARCHIVE_JOBS = 3

def is_skipped(name: str) -> bool:
    return name.startswith(SKIP_PREFIXES)

def game_id_of(category) -> str | None:
    if category is None:
        return None
    match = GAME_CATEGORY_NAME.fullmatch(category.name)
    return match.group(1) if match else None

class ArchiveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_guilds = set()

    @app_commands.command(name='archive', description='Generates HTML transcripts for a "The game N" category or a channel inside one.')
    @app_commands.describe(
        category="Optional: A whole \"The game N\" category to archive (skips names starting with - or _)",
        fetch_reactors="Whether to fetch reactions for the transcript"
    )
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    @has_guild_manage_channels()
    async def archive(self, interaction: discord.Interaction, category: discord.CategoryChannel = None, fetch_reactors: bool = True):
        channel = interaction.channel
        parent = channel.parent if isinstance(channel, discord.Thread) else channel
        target_category = category or getattr(parent, 'category', None)

        game_id = game_id_of(target_category)
        if game_id is None:
            return await interaction.response.send_message("Only game categories named **The game N**, and the channels inside them, can be archived.", ephemeral=True)

        host_role_name = f"Host for Game {game_id}"
        has_role = discord.utils.get(interaction.user.roles, name=host_role_name)
        if not has_role and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(f"You must have the **{host_role_name}** role to archive this game.", ephemeral=True)

        if category is None and (is_skipped(channel.name) or is_skipped(parent.name)):
            return await interaction.response.send_message("This channel is excluded from archiving because its name starts with `-` or `_`.", ephemeral=True)

        if interaction.guild.id in self.active_guilds:
            return await interaction.response.send_message("An archive is already running in this server. Please wait for it to finish.", ephemeral=True)
        if len(self.active_guilds) >= MAX_ARCHIVE_JOBS:
            return await interaction.response.send_message("The bot is busy with other archives right now. Please try again in a few minutes.", ephemeral=True)

        self.active_guilds.add(interaction.guild.id)
        try:
            if category:
                await self._archive_category(interaction, category, game_id, fetch_reactors)
            else:
                await self._archive_channel(interaction, fetch_reactors)
        finally:
            self.active_guilds.discard(interaction.guild.id)

    async def _archive_channel(self, interaction: discord.Interaction, fetch_reactors: bool):
        view = CancelView(interaction.user.id, is_category=False)
        status = "Generating transcript... This may take a moment depending on the channel size."
        if is_export_running():
            status = "Another transcript is being generated. Yours will start as soon as it finishes."
        await interaction.response.send_message(status, view=view)
        
        msg = await interaction.original_response()
        
        try:
            async with build_transcript(
                interaction.channel,
                bot=self.bot,
                fetch_reactors=fetch_reactors,
                should_stop=lambda: view.cancelled
            ) as transcript:
                if transcript is None:
                    content = "Cancelled before any messages were exported." if view.cancelled else "No messages found to export."
                    await msg.edit(content=content, view=None)
                    return

                notice = self._oversize_notice(interaction.guild, transcript)
                if notice:
                    await msg.edit(content=notice, view=None)
                    return

                await interaction.channel.send(
                    content=f"Here is the transcript ({transcript.message_count} messages):",
                    file=self._transcript_file(interaction.channel, transcript)
                )
            await msg.delete()
            
        except discord.Forbidden:
            await interaction.followup.send("I don't have permissions to read message history in this channel.", ephemeral=True)
        except Exception:
            log.exception("Failed to archive #%s (%s)", interaction.channel.name, interaction.channel.id)
            await msg.edit(content="Something went wrong while generating the transcript. The error has been logged.", view=None)
        finally:
            view.stop()

    def _transcript_file(self, channel_or_thread, transcript) -> discord.File:
        return discord.File(transcript.path, filename=f"transcript-{channel_or_thread.name}.html")

    def _oversize_notice(self, guild: discord.Guild, transcript):
        limit = guild.filesize_limit
        if transcript.size <= limit:
            return None
        mib = 1024 * 1024
        return (
            f"The transcript ({transcript.message_count} messages) is {transcript.size / mib:.1f} MB, "
            f"which is over this server's {limit / mib:.0f} MB upload limit, so it was not uploaded."
        )

    async def _archive_category(self, interaction: discord.Interaction, category: discord.CategoryChannel, game_id: str, fetch_reactors: bool):
        new_category_name = f"Archive {game_id}"
        confirm_view = ConfirmArchiveCategoryView(interaction.user.id)
        
        prompt_msg = f"Are you sure you want to archive the category **{category.name}**?"
        prompt_msg += f"\nIt will be renamed to **{new_category_name}**."
        prompt_msg += "\nThis will generate transcripts and **modify channel permissions** for all un-skipped channels."
            
        await interaction.response.send_message(prompt_msg, view=confirm_view)
        
        await confirm_view.wait()
        if confirm_view.value is not True:
            return
            
        view = CancelView(interaction.user.id, is_category=True)
        msg = await interaction.edit_original_response(content=f"Starting archival of category **{category.name}**...\nArchiving channels and their threads, skipping those starting with `-` or `_`.", view=view)
        
        try:
            archived_count = 0
            skipped_count = 0
            
            channels_to_process = []
            for channel in category.channels:
                if is_skipped(channel.name):
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
                    suffix = f"-{game_id}"
                    if channel.name.endswith(suffix):
                        kwargs['name'] = channel.name[:-len(suffix)]
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
                        
                    if is_skipped(thread.name):
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
                try:
                    await category.edit(name=new_category_name)
                    final_name = new_category_name
                except discord.Forbidden:
                    pass
                await msg.edit(content=f"Archival completed for category **{final_name}**.\nExported {archived_count} channels/threads. Skipped {skipped_count}.", view=None)
        finally:
            view.stop()

    async def _export_channel_or_thread(self, channel_or_thread, view, fetch_reactors):
        try:
            async with build_transcript(
                channel_or_thread,
                bot=self.bot,
                fetch_reactors=fetch_reactors,
                should_stop=lambda: view.cancelled
            ) as transcript:
                if transcript is None:
                    return False

                if isinstance(channel_or_thread, discord.Thread) and channel_or_thread.archived:
                    try:
                        await channel_or_thread.edit(archived=False)
                    except discord.HTTPException:
                        pass

                notice = self._oversize_notice(channel_or_thread.guild, transcript)
                if notice:
                    await channel_or_thread.send(notice)
                    return False

                if view.cancelled:
                    content = f"Archive generated early due to cancellation ({transcript.message_count} messages):"
                else:
                    content = f"Archive generated ({transcript.message_count} messages):"
                await channel_or_thread.send(content=content, file=self._transcript_file(channel_or_thread, transcript))
                return True
        except discord.HTTPException:
            return False
        except Exception:
            log.exception("Failed to archive #%s (%s)", channel_or_thread.name, channel_or_thread.id)
            return False


    @app_commands.command(name='unarchive', description='Reverts an archived category back to active state.')
    @app_commands.describe(category="The archived category to revert")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    @has_guild_manage_channels()
    async def unarchive(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        match = ARCHIVED_CATEGORY_NAME.fullmatch(category.name)
        if not match:
            return await interaction.response.send_message(f"Category **{category.name}** doesn't appear to be a valid archived game category (e.g. 'Archive 1').", ephemeral=True)
            
        game_id = match.group(1)
        host_role_name = f"Host for Game {game_id}"
        has_role = discord.utils.get(interaction.user.roles, name=host_role_name)
        
        if not has_role and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(f"You must have the **{host_role_name}** role to unarchive this game category.", ephemeral=True)

        if interaction.guild.id in self.active_guilds:
            return await interaction.response.send_message("An archive is running in this server. Please wait for it to finish before unarchiving.", ephemeral=True)

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
            if is_skipped(channel.name):
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

async def setup(bot):
    await bot.add_cog(ArchiveCog(bot))
