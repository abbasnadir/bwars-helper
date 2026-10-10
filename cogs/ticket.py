import discord
from discord.ext import commands
from discord import app_commands
from utils.ticket_views import TicketPanelView, TicketControlView
from utils.checks import has_guild_manage_channels, has_guild_administrator

class TicketCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Register persistent views so they work after bot restarts
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())

    @app_commands.command(name='setup_tickets', description='Sends the ticket creation panel to the current channel.')
    @app_commands.default_permissions(administrator=True)
    @has_guild_administrator()
    async def setup_tickets(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Support Tickets",
            description="Click the button below to create a new support ticket. You can assign the ticket to a specific game after creating it.",
            color=discord.Color.blue()
        )
        await interaction.channel.send(embed=embed, view=TicketPanelView())
        await interaction.response.send_message("Ticket panel created successfully.", ephemeral=True)


    @app_commands.command(name='close_ticket', description='Closes the ticket by removing send message permissions from the creator.')
    @app_commands.default_permissions(manage_channels=True)
    async def close_ticket(self, interaction: discord.Interaction):
        await interaction.response.defer()
        channel = interaction.channel
        new_overwrites = channel.overwrites
        modified = False
        for target, overwrite in new_overwrites.items():
            if isinstance(target, discord.Member) and not target.bot:
                overwrite.send_messages = False
                overwrite.send_messages_in_threads = False
                overwrite.add_reactions = False
                overwrite.create_public_threads = False
                overwrite.create_private_threads = False
                new_overwrites[target] = overwrite
                modified = True
                
        if modified:
            try:
                await channel.edit(overwrites=new_overwrites)
                await interaction.followup.send("Ticket closed. The creator can no longer send messages.")
            except discord.Forbidden:
                await interaction.followup.send("I don't have permissions to close this ticket.", ephemeral=True)
        else:
            await interaction.followup.send("Could not find the ticket creator's permissions to modify.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TicketCog(bot))
