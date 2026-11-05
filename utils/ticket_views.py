import discord
import asyncio
from utils.config import ROLE_HOST_PLUS, ROLE_HOST
from utils.permissions import get_host_perms
from utils.ratelimit import RateLimiter, deny_if_limited

# Buttons and the game dropdown inside ticket channels.
TICKET_ACTION_LIMITER = RateLimiter(rate=3, per=10.0)
# One new ticket per member per minute, and at most 10 per server every 10 minutes to stop raids.
TICKET_CREATE_USER_LIMITER = RateLimiter(rate=1, per=60.0)
TICKET_CREATE_GUILD_LIMITER = RateLimiter(rate=10, per=600.0)

class TicketGameSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Game 1", value="1", description="Ticket for Game 1"),
            discord.SelectOption(label="Game 2", value="2", description="Ticket for Game 2"),
            discord.SelectOption(label="Game 3", value="3", description="Ticket for Game 3"),
            discord.SelectOption(label="Game 4", value="4", description="Ticket for Game 4"),
            discord.SelectOption(label="Game 5", value="5", description="Ticket for Game 5"),
            discord.SelectOption(label="General Support", value="general", description="General inquiries"),
        ]
        super().__init__(placeholder="Select the game this ticket belongs to...", min_values=1, max_values=1, options=options, custom_id="ticket_game_select")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        has_host_perms = False
        if interaction.user.guild_permissions.administrator:
            has_host_perms = True
        else:
            host_plus_role = guild.get_role(ROLE_HOST_PLUS)
            host_role = guild.get_role(ROLE_HOST)
            if (host_plus_role and host_plus_role in interaction.user.roles) or (host_role and host_role in interaction.user.roles):
                has_host_perms = True
                
        if not has_host_perms:
            return await interaction.response.send_message("Only Game Host and Game Host+ can assign tickets to a game.", ephemeral=True)
            
        await interaction.response.send_message("Updating ticket assignment...", ephemeral=True)
        
        channel = interaction.channel
        choice = self.values[0]
        
        # Remove old host permissions
        new_overwrites = channel.overwrites
        for i in range(1, 6):
            host_role = discord.utils.get(guild.roles, name=f"Host for Game {i}")
            if host_role and host_role in new_overwrites:
                del new_overwrites[host_role]
                
        if choice == "general":
            category = discord.utils.get(guild.categories, name="Tickets")
            if not category:
                category = await guild.create_category("Tickets")
            
            try:
                await channel.edit(category=category, overwrites=new_overwrites)
                await interaction.edit_original_response(content="Ticket moved to General Support.")
            except discord.Forbidden:
                await interaction.edit_original_response(content="I don't have permissions to modify this channel.")
            return

        game_id = choice
        category_name = f"The game {game_id}"
        target_category = discord.utils.get(guild.categories, name=category_name)
        
        if not target_category:
            await interaction.edit_original_response(content=f"Category **{category_name}** does not exist. Please create the game first.")
            return
            
        host_role_name = f"Host for Game {game_id}"
        host_role = discord.utils.get(guild.roles, name=host_role_name)
        
        
        if host_role:
            new_overwrites[host_role] = get_host_perms()
            
        try:
            await channel.edit(category=target_category, overwrites=new_overwrites)
            await interaction.edit_original_response(content=f"Ticket assigned and moved to **{category_name}**.")
        except discord.Forbidden:
            await interaction.edit_original_response(content="I don't have permissions to move this ticket.")


class TicketDeleteConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Deleting ticket in 3 seconds...", ephemeral=True)
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete()
        except discord.Forbidden:
            await interaction.edit_original_response(content="I do not have permission to delete this channel.")
        except Exception as e:
            await interaction.edit_original_response(content=f"An error occurred: {e}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Deletion cancelled.", view=None)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketGameSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await deny_if_limited(interaction, TICKET_ACTION_LIMITER, interaction.user.id)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_delete_btn")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        perms = interaction.channel.permissions_for(interaction.user)
        if not perms.manage_channels:
            return await interaction.response.send_message("You do not have permission to delete this ticket.", ephemeral=True)
        await interaction.response.send_message("Are you sure you want to delete this ticket? This action cannot be undone.", view=TicketDeleteConfirmView(), ephemeral=True)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.secondary, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket...", ephemeral=True)
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
                await interaction.edit_original_response(content="Ticket closed. The creator can no longer send messages.")
            except discord.Forbidden:
                await interaction.edit_original_response(content="I don't have permissions to close this ticket.")
        else:
            await interaction.edit_original_response(content="Could not find the ticket creator's permissions to modify.")


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_create_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if await deny_if_limited(interaction, TICKET_CREATE_USER_LIMITER, interaction.user.id):
            return
        if await deny_if_limited(
            interaction,
            TICKET_CREATE_GUILD_LIMITER,
            guild.id,
            message="Too many tickets are being opened right now. Please try again in {retry_after}s.",
        ):
            return

        await interaction.response.send_message("Creating your ticket, please wait...", ephemeral=True)
        
        category = discord.utils.get(guild.categories, name="Tickets")
        if not category:
            try:
                category = await guild.create_category("Tickets")
            except discord.Forbidden:
                return await interaction.edit_original_response(content="I don't have permissions to create categories.")
            except discord.HTTPException:
                return await interaction.edit_original_response(content="I couldn't create the Tickets category. Please contact a host.")
                
        # Base overwrites: private to user
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                add_reactions=True,
                use_external_emojis=True,
                use_external_stickers=True,
                read_message_history=True,
                send_voice_messages=True,
                use_application_commands=True
            )
        }
        
        host_perms = get_host_perms()
        
        host_plus = guild.get_role(ROLE_HOST_PLUS)
        if host_plus:
            overwrites[host_plus] = host_perms
            
        host_reg = guild.get_role(ROLE_HOST)
        if host_reg:
            overwrites[host_reg] = host_perms

        try:
            channel = await guild.create_text_channel(
                name=f"{interaction.user.name}-color",
                category=category,
                overwrites=overwrites
            )
        except discord.Forbidden:
            return await interaction.edit_original_response(content="I don't have permissions to create channels.")
        except discord.HTTPException:
            return await interaction.edit_original_response(content="I couldn't create your ticket (the Tickets category may be full). Please contact a host.")
            
        await interaction.edit_original_response(content=f"Ticket created! Head over to {channel.mention}")
        
        embed = discord.Embed(
            title="Ticket Created",
            description="Welcome to your ticket! Please use the dropdown below to assign this ticket to a specific game, or select 'General Support'. A host will be with you shortly.",
            color=discord.Color.green()
        )
        
        await channel.send(f"{interaction.user.mention}", embed=embed, view=TicketControlView())
