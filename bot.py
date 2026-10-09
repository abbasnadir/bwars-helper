import discord
from discord.ext import commands
import chat_exporter
import io
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')

class CancelView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.cancelled = False

    @discord.ui.button(label="Cancel & Export Early", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("You don't have permission to cancel this.", ephemeral=True)
        
        self.cancelled = True
        await interaction.response.defer()

@bot.command(name='transcript', help='Generates an HTML transcript of the current channel. Flags: --no-reactors (-r)')
@commands.has_permissions(manage_messages=True)
async def create_transcript(ctx, *args):
    fetch_reactors = True
    
    for arg in args:
        if arg in ('--no-reactors', '-r', '-nr'):
            fetch_reactors = False
        elif arg.startswith('-') and not arg.startswith('--'):
            if 'r' in arg:
                fetch_reactors = False

    view = CancelView(ctx)
    msg = await ctx.send("Generating transcript... This may take a moment depending on the channel size.", view=view)
    
    messages = []
    try:
        # Fetch history manually so we can interrupt it
        async for message in ctx.channel.history(limit=None):
            if view.cancelled:
                break
            messages.append(message)

        if not messages:
            await msg.edit(content="No messages found to export.", view=None)
            return
            
        if view.cancelled:
            await msg.edit(content=f"Cancelled. Exporting the {len(messages)} messages collected so far...", view=None)

        # Generate the transcript
        transcript = await chat_exporter.raw_export(
            ctx.channel,
            messages=messages,
            tz_info='UTC',
            bot=bot,
            guild=ctx.guild,
            fetch_reactors=fetch_reactors
        )
        
        if transcript is None:
            await msg.edit(content="Failed to generate the transcript.", view=None)
            return

        transcript_file = discord.File(
            io.BytesIO(transcript.encode()),
            filename=f"transcript-{ctx.channel.name}.html"
        )
        
        await ctx.send(content=f"Here is the transcript ({len(messages)} messages):", file=transcript_file)
        await msg.delete()
        
    except Exception as e:
        await msg.edit(content=f"An error occurred: {e}", view=None)

@create_transcript.error
async def create_transcript_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to use this command. (Requires 'Manage Messages' permission)")

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN not found in environment variables.")
    else:
        bot.run(TOKEN)
