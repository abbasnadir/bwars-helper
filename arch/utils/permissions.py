import discord

def get_host_perms() -> discord.PermissionOverwrite:
    """Full permissions for Game Hosts."""
    return discord.PermissionOverwrite(
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

def get_archived_host_perms() -> discord.PermissionOverwrite:
    """Neutral permissions for Game Hosts in archived categories."""
    return discord.PermissionOverwrite(
        create_public_threads=True,
        send_messages_in_threads=True,
        send_messages=True
    )

def get_everyone_perms() -> discord.PermissionOverwrite:
    """Permissions for the @everyone role to hide/mute by default."""
    return discord.PermissionOverwrite(
        view_channel=False,
        send_messages=False,
        create_public_threads=False,
        create_private_threads=False,
        send_messages_in_threads=False
    )

def get_spectator_perms() -> discord.PermissionOverwrite:
    """Permissions for spectators (view only)."""
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=False,
        create_public_threads=False,
        create_private_threads=False,
        send_messages_in_threads=False
    )

def get_in_game_perms() -> discord.PermissionOverwrite:
    """Permissions for active players (neutral messaging)."""
    return discord.PermissionOverwrite(
        view_channel=True,
        create_public_threads=False,
        create_private_threads=False
    )
