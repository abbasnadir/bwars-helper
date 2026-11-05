import math
import time
from collections import deque

import discord


class RateLimiter:
    """Sliding-window limiter: allows `rate` hits per `per` seconds for each key."""

    def __init__(self, rate: int, per: float):
        self.rate = rate
        self.per = per
        self._hits: dict = {}
        self._next_prune = 0.0

    def hit(self, key) -> float:
        """Records a hit. Returns 0 if allowed, otherwise the seconds to wait (the hit is not recorded)."""
        now = time.monotonic()
        self._prune(now)

        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.per:
            hits.popleft()

        if len(hits) >= self.rate:
            return self.per - (now - hits[0])

        hits.append(now)
        return 0.0

    def _prune(self, now: float) -> None:
        # Drop idle keys now and then so the table can't grow without bound.
        if now < self._next_prune:
            return
        self._next_prune = now + self.per
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self.per]
        for key in stale:
            del self._hits[key]


async def deny_if_limited(
    interaction: discord.Interaction,
    limiter: RateLimiter,
    key,
    message: str = "You're doing that too fast. Try again in {retry_after}s.",
) -> bool:
    """Returns True (after telling the user to slow down) if the key is over its limit."""
    retry_after = limiter.hit(key)
    if not retry_after:
        return False

    message = message.format(retry_after=math.ceil(retry_after))
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return True
