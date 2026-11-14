import logging
import math

import discord

log = logging.getLogger(__name__)

UNKNOWN_INTERACTION = 10062
ALREADY_ACKNOWLEDGED = 40060


def _age(interaction: discord.Interaction) -> float:
    return (discord.utils.utcnow() - interaction.created_at).total_seconds()


async def defer_ephemeral(interaction: discord.Interaction) -> bool:
    """Acknowledge an interaction. Returns False if it can no longer be answered."""
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True
    except discord.HTTPException as exc:
        if exc.code == ALREADY_ACKNOWLEDGED:
            log.debug("Interaction %s was already acknowledged", interaction.id)
            return True
        if exc.code == UNKNOWN_INTERACTION:
            latency = interaction.client.latency
            log.warning(
                "Interaction %s expired before it could be acknowledged: it was %.1fs old "
                "(Discord allows 3s; gateway latency %s). Usually the bot machine or network "
                "is overloaded, or another copy of the bot is running with the same token.",
                interaction.id,
                _age(interaction),
                f"{latency * 1000:.0f} ms" if math.isfinite(latency) else "unknown",
            )
            return False
        log.warning("Could not acknowledge interaction %s", interaction.id, exc_info=True)
        return False


async def respond(interaction: discord.Interaction, content: str = "", **kwargs) -> None:
    """Send a response or followup, quietly giving up on expired interactions."""
    payload = dict(kwargs)
    if content:
        payload["content"] = content

    if not interaction.response.is_done():
        try:
            await interaction.response.send_message(**payload)
            return
        except discord.HTTPException as exc:
            if exc.code != ALREADY_ACKNOWLEDGED:
                if exc.code == UNKNOWN_INTERACTION:
                    log.info("Interaction %s expired before a response could be sent", interaction.id)
                else:
                    log.warning("Could not respond to interaction %s", interaction.id, exc_info=True)
                return

    try:
        await interaction.followup.send(**payload)
    except discord.HTTPException as exc:
        if exc.code == UNKNOWN_INTERACTION:
            log.info("Interaction %s expired before it could reply", interaction.id)
        else:
            log.warning("Could not respond to interaction %s", interaction.id, exc_info=True)
