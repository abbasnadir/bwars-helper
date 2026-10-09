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
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.cancelled = False

    @discord.ui.button(label="Cancel & Export Early", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # We also check guild-wide manage_channels here for consistency
        if interaction.user.id != self.user_id and not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("You don't have permission to cancel this.", ephemeral=True)
        
        self.cancelled = True
        await interaction.response.defer()

class ArchiveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_transcripts = set()

    @app_commands.command(name='transcript', description='Generates an HTML transcript of the current channel.')
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    @has_guild_manage_channels()
    async def create_transcript(self, interaction: discord.Interaction, fetch_reactors: bool = True):
        if interaction.channel.id in self.active_transcripts:
            return await interaction.response.send_message("A transcript is already being generated in this channel. Please wait or cancel the current one.", ephemeral=True)
            
        self.active_transcripts.add(interaction.channel.id)
        
        view = CancelView(interaction.user.id)
        await interaction.response.send_message("Generating transcript... This may take a moment depending on the channel size.", view=view)
        
        msg = await interaction.original_response()
        
        messages = []
        try:
            # Fetch history manually so we can interrupt it
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

            # Generate the transcript
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

    @create_transcript.error
    async def create_transcript_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
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
