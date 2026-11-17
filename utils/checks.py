import re

import discord
from discord import app_commands

from utils.config import ROLE_HOST, ROLE_HOST_PLUS

# Per-game roles are made by hand and matched by name, the same way /create_game finds them.
GAME_HOST_RE = re.compile(r"host for game (\d+)", re.IGNORECASE)
GAME_ROLE_RE = re.compile(r"(?:host for game|in game) \d+", re.IGNORECASE)


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

def hosted_games(member: discord.Member) -> set[str]:
    """Game numbers this member hosts, from their "Host for Game N" roles."""
    games = set()
    for role in member.roles:
        match = GAME_HOST_RE.fullmatch(role.name.strip())
        if match:
            games.add(str(int(match.group(1))))
    return games

def is_host_or_game_host():
    """Hosts and admins, or anyone with a "Host for Game N" role. The command must still check the target."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_host(interaction):
            return True
        if isinstance(interaction.user, discord.Member) and hosted_games(interaction.user):
            return True
        raise app_commands.CheckFailure("Only hosts and game hosts can use this command.")
    return app_commands.check(predicate)
