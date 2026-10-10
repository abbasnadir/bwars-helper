import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv

load_dotenv()
ROLE_HOST_PLUS = int(os.getenv('ROLE_HOST_PLUS', 0))
ROLE_HOST = int(os.getenv('ROLE_HOST', 0))
ROLE_JAILED = int(os.getenv('ROLE_JAILED', 0))
ROLE_SPECTATOR = int(os.getenv('ROLE_SPECTATOR', 0))

def has_guild_manage_channels():
    def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        return interaction.user.guild_permissions.manage_channels
    return app_commands.check(predicate)

def has_guild_administrator():
    def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)

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

class GameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='create_game', description='Sets up a category and channels for a discord game.')
    @app_commands.describe(game_id="ID of the game (1-5)")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    @has_guild_manage_channels()
    async def create_game(self, interaction: discord.Interaction, game_id: app_commands.Range[int, 1, 5]):
        await interaction.response.defer()
        
        try:
            category_name = f"The game {game_id}"
            
            # Check if category already exists
            existing_category = discord.utils.get(interaction.guild.categories, name=category_name)
            if existing_category:
                return await interaction.followup.send(f"The category **{category_name}** already exists and another game cannot be created with the same id.", ephemeral=True)
            
            host_perms = discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True,
                manage_roles=True,
                manage_webhooks=True,
                create_instant_invite=True,
                send_messages=True,
                send_messages_in_threads=True,
                create_public_threads=True,
                create_private_threads=True,
                embed_links=True,
                attach_files=True,
                add_reactions=True,
                use_external_emojis=True,
                use_external_stickers=True,
                mention_everyone=True,
                manage_messages=True,
                pin_messages=True,
                bypass_slowmode=True,
                manage_threads=True,
                read_message_history=True,
                send_tts_messages=True,
                send_voice_messages=True,
                create_polls=True,
                use_application_commands=True,
                use_embedded_activities=True,
                use_external_apps=True
            )
            
            # Setup default overwrites
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(
                    view_channel=False,
                    send_messages=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False
                )
            }
            
            # Game Host+
            host_plus_role = interaction.guild.get_role(ROLE_HOST_PLUS)
            if host_plus_role:
                overwrites[host_plus_role] = host_perms
                
            # Game Host
            host_role = interaction.guild.get_role(ROLE_HOST)
            if host_role:
                overwrites[host_role] = host_perms
                
            # Jailed
            jailed_role = interaction.guild.get_role(ROLE_JAILED)
            if jailed_role:
                overwrites[jailed_role] = discord.PermissionOverwrite(view_channel=False)
                
            # Spectators
            spectator_role = interaction.guild.get_role(ROLE_SPECTATOR)
            if spectator_role:
                overwrites[spectator_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False
                )
                
            # Dynamic Game Roles
            in_game_role = discord.utils.get(interaction.guild.roles, name=f"In game {game_id}")
            if in_game_role:
                overwrites[in_game_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    create_public_threads=False,
                    create_private_threads=False
                )
                
            host_game_role = discord.utils.get(interaction.guild.roles, name=f"Host for Game {game_id}")
            if host_game_role:
                overwrites[host_game_role] = host_perms
                
            # Create the category with the overwrites applied
            kwargs = {'name': category_name, 'overwrites': overwrites}
            category = await interaction.guild.create_category(**kwargs)
            
            # Calculate position and move
            anchor_category = None
            for i in range(game_id - 1, 0, -1):
                anchor_category = discord.utils.get(interaction.guild.categories, name=f"The game {i}")
                if anchor_category:
                    break
            
            if not anchor_category:
                anchor_category = discord.utils.find(lambda c: c.name.lower() == "discussion", interaction.guild.categories)
                
            if anchor_category:
                await category.move(after=anchor_category)
            
            # Create text channels inside the category (they will inherit the category's overwrites)
            channel_names = [
                f"news-{game_id}", 
                f"host-status-{game_id}", 
                f"players-{game_id}", 
                f"game-chat-{game_id}", 
                f"voting-phase-{game_id}", 
                f"dead-cards-{game_id}"
            ]
            
            channels_created = []
            for name in channel_names:
                channel = await interaction.guild.create_text_channel(
                    name=name, 
                    category=category
                )
                channels_created.append(channel.mention)
                
            channels_str = ", ".join(channels_created)
            await interaction.followup.send(f"Successfully set up **{category_name}** category with channels: {channels_str}")
            
        except discord.Forbidden as e:
            import traceback
            traceback.print_exc()
            bot_member = interaction.guild.me
            bot_perms = bot_member.guild_permissions
            
            issues = []
            if not bot_perms.manage_channels:
                issues.append("Missing global permission: **Manage Channels**")
            if not bot_perms.manage_roles:
                issues.append("Missing global permission: **Manage Roles**")
                
            roles_to_edit = [
                interaction.guild.get_role(ROLE_HOST_PLUS),
                interaction.guild.get_role(ROLE_HOST),
                interaction.guild.get_role(ROLE_JAILED),
                interaction.guild.get_role(ROLE_SPECTATOR),
                discord.utils.get(interaction.guild.roles, name=f"In game {game_id}"),
                discord.utils.get(interaction.guild.roles, name=f"Host for Game {game_id}")
            ]
            
            for role in roles_to_edit:
                if role and role >= bot_member.top_role:
                    issues.append(f"Role Hierarchy: My role must be higher than **{role.name}**")
                    
            if not bot_perms.administrator:
                missing_overwrite_perms = []
                for perm_name, value in host_perms:
                    if value is True:
                        if not getattr(bot_perms, perm_name, False):
                            readable_name = perm_name.replace('_', ' ').title()
                            missing_overwrite_perms.append(f"**{readable_name}**")
                
                if missing_overwrite_perms:
                    issues.append(f"Missing global permissions to grant to Hosts: {', '.join(missing_overwrite_perms)}")
                    
            if issues:
                issue_str = "\n".join(f"- {issue}" for issue in issues)
                await interaction.followup.send(f"I don't have enough permissions! (Error: `{e}`)\n\n**Here is exactly what I am missing:**\n{issue_str}", ephemeral=True)
            else:
                tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
                if len(tb) > 1000: tb = tb[-1000:]
                await interaction.followup.send(f"I don't have enough permissions! (Error: `{e}`)\n\n*Could not diagnose exactly why. Here is the traceback for debugging:*\n```py\n{tb}\n```", ephemeral=True)

    @app_commands.command(name='delete_game', description='Deletes a game category and all its channels.')
    @app_commands.describe(game_id="The ID of the game to delete (1-5)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    @has_guild_administrator()
    async def delete_game(self, interaction: discord.Interaction, game_id: app_commands.Range[int, 1, 5]):
        # Check for Host for Game {id} role
        host_role_name = f"Host for Game {game_id}"
        has_role = discord.utils.get(interaction.user.roles, name=host_role_name)
        
        if not has_role:
            return await interaction.response.send_message(f"You must have the **{host_role_name}** role to delete this game.", ephemeral=True)
            
        category_name = f"The game {game_id}"
        category = discord.utils.get(interaction.guild.categories, name=category_name)
        
        if not category:
            return await interaction.response.send_message(f"The category **{category_name}** does not exist.", ephemeral=True)
            
        view = ConfirmDeleteView(interaction.user.id, category)
        await interaction.response.send_message(f"Are you sure you want to delete the category **{category.name}** and ALL its channels?", view=view)
        
        await view.wait()
        
        if view.value is None:
            await interaction.edit_original_response(content="Confirmation timed out. Deletion cancelled.", view=None)
        elif view.value is True:
            try:
                # Delete all channels in the category first
                for channel in category.channels:
                    await channel.delete()
                # Delete the category itself
                await category.delete()
                
                try:
                    await interaction.edit_original_response(content=f"Successfully deleted category **{category.name}** and all its channels.", view=None)
                except (discord.NotFound, discord.HTTPException):
                    pass # The channel we ran the command in was just deleted
            except discord.Forbidden:
                try:
                    await interaction.edit_original_response(content="I don't have permissions to delete these channels/category.", view=None)
                except (discord.NotFound, discord.HTTPException):
                    pass

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if hasattr(error, 'retry_after'):
                msg = f"Command on cooldown. Please try again in {error.retry_after:.2f}s."
            else:
                if interaction.command and interaction.command.name == 'delete_game':
                    msg = "You need guild-wide 'Administrator' permission to use this command."
                else:
                    msg = "You need guild-wide 'Manage Channels' permission to use this command."
        else:
            import traceback
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            if len(tb) > 1900:
                tb = tb[:1900] + "..."
            msg = f"An unexpected error occurred:\n```py\n{tb}\n```"
            
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            pass

async def setup(bot):
    await bot.add_cog(GameCog(bot))
