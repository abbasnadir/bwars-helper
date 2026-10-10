import discord
from discord.ext import commands
from discord import app_commands
import chat_exporter
import io

def has_guild_manage_channels():
    def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        return interaction.user.guild_permissions.manage_channels
    return app_commands.check(predicate)

class CancelView(discord.ui.View):
    def __init__(self, user_id: int, is_category: bool):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.cancelled = False
        self.is_category = is_category
        
        self.cancel_btn = discord.ui.Button(
            label="Cancel Archiving" if is_category else "Cancel & Export Early", 
            style=discord.ButtonStyle.danger
        )
        self.cancel_btn.callback = self.cancel_button
        self.add_item(self.cancel_btn)

    async def cancel_button(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id and not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("You don't have permission to cancel this.", ephemeral=True)
        
        self.cancelled = True
        self.cancel_btn.disabled = True
        
        content = "Cancelling the archival process... (Stopping after current channel/thread finishes)" if self.is_category else None
        
        if content:
            await interaction.response.edit_message(content=content, view=self)
        else:
            await interaction.response.edit_message(view=self)

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
        view = CancelView(interaction.user.id, is_category=True)
        await interaction.response.send_message(f"Starting archival of category **{category.name}**...\nArchiving channels and their threads, skipping those starting with `-`.", view=view)
        
        msg = await interaction.original_response()
        
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
            await msg.edit(content=f"Archival completed for category **{category.name}**.\nExported {archived_count} channels/threads. Skipped {skipped_count}.", view=None)

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
