import discord

class ConfirmDeleteView(discord.ui.View):
    def __init__(self, user_id: int, category: discord.CategoryChannel):
        super().__init__(timeout=60.0)
        self.user_id = user_id
        self.category = category
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You cannot interact with this button.", ephemeral=True)
        self.value = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Deleting category and its channels...", view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You cannot interact with this button.", ephemeral=True)
        self.value = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Deletion cancelled.", view=self)
        self.stop()

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

class ConfirmArchiveCategoryView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60.0)
        self.user_id = user_id
        self.value = None

    @discord.ui.button(label="Confirm Archive", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You cannot interact with this button.", ephemeral=True)
        self.value = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Confirmation received. Starting archival...", view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You cannot interact with this button.", ephemeral=True)
        self.value = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Archival cancelled.", view=self)
        self.stop()
