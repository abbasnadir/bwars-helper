from discord import app_commands


class NotOwner(app_commands.CheckFailure):
    """Raised when a command is used by anyone other than the bot owner."""


def owner_only():
    """Make a slash command usable only by the application owner."""

    async def predicate(interaction) -> bool:
        if await interaction.client.is_owner(interaction.user):
            return True
        raise NotOwner()

    return app_commands.check(predicate)
