with open("utils/ticket_views.py", "r") as f:
    content = f.read()

# Fix TicketGameSelect callback
old_select = """        if not has_host_perms:
            return await interaction.response.send_message("Only Game Host and Game Host+ can assign tickets to a game.", ephemeral=True)
            
        await interaction.response.defer()"""
new_select = """        if not has_host_perms:
            return await interaction.response.send_message("Only Game Host and Game Host+ can assign tickets to a game.", ephemeral=True)
            
        await interaction.response.send_message("Updating ticket assignment...", ephemeral=True)"""
content = content.replace(old_select, new_select)

# Replace followup.send in select
content = content.replace("await interaction.followup.send(\"Ticket moved to General Support.\", ephemeral=True)", "await interaction.edit_original_response(content=\"Ticket moved to General Support.\")")
content = content.replace("await interaction.followup.send(\"I don't have permissions to modify this channel.\", ephemeral=True)", "await interaction.edit_original_response(content=\"I don't have permissions to modify this channel.\")")
content = content.replace("await interaction.followup.send(f\"Category **{category_name}** does not exist. Please create the game first.\", ephemeral=True)", "await interaction.edit_original_response(content=f\"Category **{category_name}** does not exist. Please create the game first.\")")
content = content.replace("await interaction.followup.send(f\"Ticket assigned and moved to **{category_name}**.\", ephemeral=True)", "await interaction.edit_original_response(content=f\"Ticket assigned and moved to **{category_name}**.\")")
content = content.replace("await interaction.followup.send(\"I don't have permissions to move this ticket.\", ephemeral=True)", "await interaction.edit_original_response(content=\"I don't have permissions to move this ticket.\")")

# Fix close_ticket button
old_close = """    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()"""
new_close = """    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket...", ephemeral=True)"""
content = content.replace(old_close, new_close)

# Replace followup in close_ticket
content = content.replace("await interaction.followup.send(\"Ticket closed. The creator can no longer send messages.\")", "await interaction.edit_original_response(content=\"Ticket closed. The creator can no longer send messages.\")")
content = content.replace("await interaction.followup.send(\"I don't have permissions to close this ticket.\", ephemeral=True)", "await interaction.edit_original_response(content=\"I don't have permissions to close this ticket.\")")
content = content.replace("await interaction.followup.send(\"Could not find the ticket creator's permissions to modify.\", ephemeral=True)", "await interaction.edit_original_response(content=\"Could not find the ticket creator's permissions to modify.\")")

# Fix create_ticket button
old_create = """    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)"""
new_create = """    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Creating your ticket, please wait...", ephemeral=True)"""
content = content.replace(old_create, new_create)

# Replace followup in create_ticket
content = content.replace("return await interaction.followup.send(\"I don't have permissions to create categories.\", ephemeral=True)", "return await interaction.edit_original_response(content=\"I don't have permissions to create categories.\")")
content = content.replace("return await interaction.followup.send(\"I don't have permissions to create channels.\", ephemeral=True)", "return await interaction.edit_original_response(content=\"I don't have permissions to create channels.\")")
content = content.replace("await interaction.followup.send(f\"Ticket created! Head over to {channel.mention}\", ephemeral=True)", "await interaction.edit_original_response(content=f\"Ticket created! Head over to {channel.mention}\")")

with open("utils/ticket_views.py", "w") as f:
    f.write(content)
print("Patched ticket_views.py")
