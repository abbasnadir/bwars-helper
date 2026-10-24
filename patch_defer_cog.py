with open("cogs/ticket.py", "r") as f:
    content = f.read()

old_cmd = """    async def close_ticket(self, interaction: discord.Interaction):
        await interaction.response.defer()"""
new_cmd = """    async def close_ticket(self, interaction: discord.Interaction):
        await interaction.response.send_message("Closing ticket...", ephemeral=True)"""
content = content.replace(old_cmd, new_cmd)

content = content.replace("await interaction.followup.send(\"Ticket closed. The creator can no longer send messages.\")", "await interaction.edit_original_response(content=\"Ticket closed. The creator can no longer send messages.\")")
content = content.replace("await interaction.followup.send(\"I don't have permissions to close this ticket.\", ephemeral=True)", "await interaction.edit_original_response(content=\"I don't have permissions to close this ticket.\")")
content = content.replace("await interaction.followup.send(\"Could not find the ticket creator's permissions to modify.\", ephemeral=True)", "await interaction.edit_original_response(content=\"Could not find the ticket creator's permissions to modify.\")")

with open("cogs/ticket.py", "w") as f:
    f.write(content)
print("Patched cogs/ticket.py")
