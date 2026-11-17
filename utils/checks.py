import discord
from discord import app_commands

from utils.config import ROLE_HOST, ROLE_HOST_PLUS


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

def is_host(interaction: discord.Interaction) -> bool:
    if not interaction.guild or isinstance(interaction.user, discord.User):
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    host_roles = {ROLE_HOST, ROLE_HOST_PLUS} - {0}
    return any(role.id in host_roles for role in interaction.user.roles)

def is_host_or_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_host(interaction):
            return True
        raise app_commands.CheckFailure("Only hosts can use this command.")
    return app_commands.check(predicate)
