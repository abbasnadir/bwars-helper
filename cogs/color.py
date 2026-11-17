import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_host_or_admin
from utils.colors import (
    NAME_RGB_STORE,
    canonical_name,
    color_name,
    is_exact_name,
    normalise_name,
    parse_color,
    role_name_for,
    to_hex,
)
from utils.config import (
    ROLE_ARCHIVE,
    ROLE_COLOR_MAX,
    ROLE_COLOR_MIN,
    ROLE_HOST,
    ROLE_HOST_PLUS,
    ROLE_JAILED,
    ROLE_SPECTATOR,
)
from utils.ratelimit import RateLimiter, deny_if_limited

log = logging.getLogger(__name__)

# Color roles live in this band, and Discord allows 250 roles per server, so fill at most this many.
# Roles are read from discord.py's role/member cache; nothing extra is stored in memory.
MAX_COLOR_ROLES = 50
# Creating roles is the expensive part, so cap how fast one server can do it.
ROLE_CREATE_LIMITER = RateLimiter(rate=10, per=600.0)

COLOR_HELP = "Use a hex code like `#ff8800`, RGB values like `255, 136, 0`, or a color name like `orange`."
NO_MENTIONS = discord.AllowedMentions.none()

# Only roles this bot created may be deleted, so their IDs are kept on disk. Hand-made roles in the
# band are still reused and removed from members, just never deleted. A few IDs per guild is tiny.
STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "color_roles.json"

# Roles that control access (via channel overwrites, not role permissions), so they must never be handed out
# as a "color", even if someone drags them between the anchors.
PROTECTED_ROLES = {ROLE_HOST, ROLE_HOST_PLUS, ROLE_JAILED, ROLE_SPECTATOR, ROLE_ARCHIVE} - {0}


def is_color_role(role: discord.Role, low: discord.Role, high: discord.Role) -> bool:
    """Any regular role between the two anchors is a color role, whatever its name.

    This lets roles made by hand (before this cog existed) be reused, removed and cleaned up too.
    Bot-managed roles are excluded because Discord owns them and they are never color roles.
    """
    return not role.managed and not role.is_default() and low.position < role.position < high.position


def is_assignable(role: discord.Role, low: discord.Role, high: discord.Role) -> bool:
    """A band role that is safe to give out: it only changes a name's color, never what someone can do."""
    return is_color_role(role, low, high) and role.permissions.value == 0 and role.id not in PROTECTED_ROLES


def band_roles(guild: discord.Guild, low: discord.Role, high: discord.Role):
    """Color roles between the anchors, read from discord.py's role cache only.

    Autocomplete runs on every keystroke, so it must never call the API; this helper keeps that explicit.
    """
    return [r for r in guild.roles if is_color_role(r, low, high)]


def role_value(rgb: tuple[int, int, int]) -> int:
    # Discord treats 0 as "no color", so pure black becomes the closest visible black.
    return int(to_hex(rgb)[1:], 16) or 0x010101


def sort_by_position(roles):
    """Bottom to top. Discord orders equal positions by ID, with the higher ID lower."""
    return sorted(roles, key=lambda r: (r.position, -r.id))


def free_name(base: str, taken: set[str]) -> str:
    """`base`, or `base-2`, `base-3`... whichever isn't taken. `taken` holds normalised names."""
    candidate, suffix = base, 2
    while normalise_name(candidate) in taken:
        candidate, suffix = f"{base}-{suffix}", suffix + 1
    return candidate


class ColorCog(commands.Cog):
    color = app_commands.Group(name="color", description="Colored name roles (hosts only).")

    def __init__(self, bot):
        self.bot = bot
        # One lock for every server: role creation is rare, and this avoids a per-server table in memory.
        self._lock = asyncio.Lock()
        self._made_roles = self._load_made_roles()

    @staticmethod
    def _load_made_roles() -> dict[int, set[int]]:
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}

        made = {}
        for guild_id, role_ids in data.items():
            try:
                made[int(guild_id)] = {int(role_id) for role_id in role_ids}
            except (TypeError, ValueError):
                continue
        return made

    def _save_made_roles(self) -> None:
        data = {str(guild_id): sorted(role_ids) for guild_id, role_ids in self._made_roles.items()}
        # Write a temp file and swap it in, so a crash mid-write can't leave a half-written file behind.
        tmp_path = STATE_PATH.with_suffix(".tmp")
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp_path, STATE_PATH)
        except OSError:
            log.exception("Could not save color role state to %s", STATE_PATH)

    def _remember_role(self, guild_id: int, role_id: int) -> None:
        self._made_roles.setdefault(guild_id, set()).add(role_id)
        self._save_made_roles()

    def _forget_role(self, guild_id: int, role_id: int) -> None:
        role_ids = self._made_roles.get(guild_id)
        if not role_ids or role_id not in role_ids:
            return
        role_ids.discard(role_id)
        if not role_ids:
            self._made_roles.pop(guild_id, None)
        self._save_made_roles()

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        # Roles deleted by hand would otherwise stay in the file forever. This event arrives anyway.
        self._forget_role(role.guild.id, role.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        if self._made_roles.pop(guild.id, None) is not None:
            self._save_made_roles()

    def _band(self, guild: discord.Guild) -> tuple[discord.Role | None, discord.Role | None, str | None]:
        low = guild.get_role(ROLE_COLOR_MIN)
        high = guild.get_role(ROLE_COLOR_MAX)
        if low is None or high is None:
            return None, None, "Color roles aren't set up on this server."
        if low.position >= high.position:
            return None, None, "The color role anchors are in the wrong order."

        me = guild.me
        if not me.guild_permissions.manage_roles:
            return None, None, "I need the **Manage Roles** permission to manage color roles."
        if high >= me.top_role:
            return None, None, f"My highest role must be above {high.mention} to manage color roles."
        if high.position - low.position < 2:
            return None, None, f"There's no free slot for color roles between {low.mention} and {high.mention}."
        return low, high, None

    async def _can_target(self, interaction: discord.Interaction, member: discord.Member) -> bool:
        # Hosts run this command, so they may color anyone, including members above their own top role.
        # Only the color role itself has to sit below the bot's top role, which _band() already checks.
        if not member.bot:
            return True

        await interaction.response.send_message("Bots can't get color roles.", ephemeral=True)
        return False

    async def _pick_name(self, guild: discord.Guild, rgb, low: discord.Role, high: discord.Role, requester) -> str:
        """Name for a new color role, fixing older roles whose plain color name doesn't match their color.

        A role called "cyan" that isn't #00ffff is misleading. If the bot made it, it gets renamed to what it
        really is ("cyan-10f0f0") and the new, exact role takes "cyan". A hand-made role is never renamed, so
        the new one becomes "cyan-original" instead.
        """
        name = role_name_for(rgb)
        wanted = normalise_name(name)
        taken = {normalise_name(r.name) for r in guild.roles}
        if wanted not in taken:
            return name
        if not is_exact_name(rgb):
            # "cyan-10f0f0" only clashes if a role has that exact name with another color; just number it.
            return free_name(name, taken)

        made = self._made_roles.get(guild.id, set())
        clashing = [r for r in guild.roles if normalise_name(r.name) == wanted]
        renamed = set()
        for role in clashing:
            if role.id not in made or not is_color_role(role, low, high):
                continue
            # The role cache may lag behind the rename, so track the names handed out here ourselves.
            new_name = free_name(role_name_for(role.colour.to_rgb()), taken)
            try:
                await role.edit(name=new_name, reason=f"Renamed to match its real color; requested by {requester}")
            except discord.HTTPException:
                log.warning("Couldn't rename misleading color role %s in guild %s", role.id, guild.id)
                continue
            taken.add(normalise_name(new_name))
            renamed.add(role.id)

        if len(renamed) == len(clashing):
            return name
        return free_name(f"{name}-original", taken)

    async def _create_role(self, guild: discord.Guild, name: str, value: int, requester) -> discord.Role | None:
        reason = f"Color role requested by {requester} ({requester.id})"
        try:
            role = await guild.create_role(
                name=name,
                colour=discord.Colour(value),
                permissions=discord.Permissions.none(),
                hoist=False,
                mentionable=False,
                reason=reason,
            )
        except discord.HTTPException:
            log.exception("Failed to create color role %s in guild %s", name, guild.id)
            return None

        try:
            await self._place_in_band(guild, role, reason)
        except (discord.HTTPException, LookupError):
            log.exception("Failed to move color role %s in guild %s", role.id, guild.id)
            with contextlib.suppress(discord.HTTPException):
                await role.delete(reason="Couldn't place the color role between the anchor roles")
            return None
        self._remember_role(guild.id, role.id)
        return role

    async def _place_in_band(self, guild: discord.Guild, role: discord.Role, reason: str) -> None:
        # The cache may not have caught up with the new role yet, so read the order from the API.
        roles = sort_by_position(r for r in await guild.fetch_roles() if not r.is_default())
        ordered = [r for r in roles if r.id != role.id]
        ids = [r.id for r in ordered]
        low_index = ids.index(ROLE_COLOR_MIN)

        # Directly above the lower anchor, i.e. the bottom of the band. The new role was created at the
        # bottom and took position 1, so the roles below the anchor can shift down one to make room.
        ordered.insert(low_index + 1, role)

        # Only the roles from the bottom up to the new one are renumbered; nothing above it moves.
        positions = {
            r: position
            for position, r in enumerate(ordered[: low_index + 2], start=1)
            if r.position != position or r.id == role.id
        }
        await guild.edit_role_positions(positions, reason=reason)

    async def _delete_if_unused(self, guild: discord.Guild, roles) -> int:
        """Delete roles this bot made once nobody has them; hand-made roles are only unassigned.

        Member counts come from one API call instead of the member cache, so the bot doesn't have to
        keep every member of the server in memory. Returns how many roles were deleted.
        """
        made = self._made_roles.get(guild.id, set())
        candidates = [r for r in roles if r.id in made]
        if not candidates:
            return 0

        try:
            counts = {r.id: n for r, n in (await guild.role_member_counts()).items()}
        except discord.HTTPException:
            log.warning("Couldn't read role member counts in guild %s; keeping color roles for now", guild.id)
            return 0

        deleted = 0
        for role in candidates:
            # A missing entry means the role is gone or empty; a positive count means someone still has it.
            if counts.get(role.id, 0) > 0:
                continue
            try:
                await role.delete(reason="Color role no longer used")
                deleted += 1
            except discord.NotFound:
                pass  # Already gone; just stop tracking it.
            except discord.HTTPException:
                continue  # Still there (permissions, rate limit); try again next time.
            self._forget_role(guild.id, role.id)
        return deleted

    @color.command(name="add", description="Give a member a color role.")
    @app_commands.describe(
        color="A hex code (#ff8800), RGB values (255, 136, 0) or a color name (orange)",
        member="Who gets the color.",
    )
    @is_host_or_admin()
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        color: app_commands.Range[str, 1, 100],
    ):
        if not await self._can_target(interaction, member):
            return

        rgb = parse_color(color)
        if rgb is None:
            return await interaction.response.send_message(f"I couldn't read that color. {COLOR_HELP}", ephemeral=True)

        guild = interaction.guild
        low, high, error = self._band(guild)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)

        value = role_value(rgb)

        await interaction.response.defer(thinking=True)
        async with self._lock:
            color_roles = band_roles(guild, low, high)
            # Reuse a band role that already shows this color, even one made by hand, so no duplicates pile up.
            # Only permission-free, non-protected roles are reused, so a color can never grant access.
            role = next((r for r in color_roles if r.colour.value == value and is_assignable(r, low, high)), None)

            if role is None:
                in_use = len(color_roles)
                if in_use >= MAX_COLOR_ROLES:
                    # Bot-made roles can be left empty (e.g. removed by hand in Discord); clear them first.
                    in_use -= await self._delete_if_unused(guild, color_roles)
                if in_use >= MAX_COLOR_ROLES:
                    return await interaction.followup.send(
                        "This server has too many color roles. Remove some unused ones first.", ephemeral=True
                    )
                if await deny_if_limited(
                    interaction,
                    ROLE_CREATE_LIMITER,
                    guild.id,
                    message="Too many color roles are being made right now. Try again in {retry_after}s.",
                ):
                    return
                name = await self._pick_name(guild, rgb, low, high, interaction.user)
                role = await self._create_role(guild, name, value, interaction.user)
                if role is None:
                    return await interaction.followup.send(
                        "I couldn't create the color role. Check my permissions and role position.", ephemeral=True
                    )

            # Color roles share a height band, so an older one could hide the new color; swap it out.
            old_roles = [r for r in member.roles if r.id != role.id and is_color_role(r, low, high)]
            reason = f"Color set by {interaction.user} ({interaction.user.id})"
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason=reason)
            except discord.NotFound:
                return await interaction.followup.send(
                    "That color role no longer exists. Try again.", ephemeral=True
                )
            except discord.Forbidden:
                return await interaction.followup.send("I'm not allowed to change that member's roles.", ephemeral=True)

            swapped_out = []
            for old_role in old_roles:
                try:
                    await member.remove_roles(old_role, reason=reason)
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    return await interaction.followup.send(
                        "I'm not allowed to change that member's roles.", ephemeral=True
                    )
                swapped_out.append(old_role)
            await self._delete_if_unused(guild, swapped_out)

        await interaction.followup.send(
            f"{member.mention} now has the {role.mention} color (`{to_hex(rgb)}`).",
            allowed_mentions=NO_MENTIONS,
        )

    @color.command(name="remove", description="Remove a color role from a member.")
    @app_commands.describe(
        color="The color role's name (orange) or its color (#ff8800)",
        member="Whose color to remove.",
    )
    @is_host_or_admin()
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    async def remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        color: app_commands.Range[str, 1, 100],
    ):
        if not await self._can_target(interaction, member):
            return

        guild = interaction.guild
        low, high, error = self._band(guild)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)

        # Accept the role's name (any spelling), an alias ("aqua" for "cyan"), or the same hex/RGB value used to add it.
        names = {normalise_name(color)}
        values = set()
        rgb = parse_color(color)
        if rgb is not None:
            values.add(role_value(rgb))
            # Only real color names get aliases. A hex like #123456 must not match the role named after
            # its nearest color ("darkslateblue"), which is a different color.
            if normalise_name(color) in NAME_RGB_STORE:
                names.add(color_name(rgb))

        def matches(role: discord.Role) -> bool:
            return is_color_role(role, low, high) and (
                normalise_name(role.name) in names or role.colour.value in values
            )

        if not any(matches(r) for r in member.roles):
            return await interaction.response.send_message(
                f"{member.mention} doesn't have that color role.", ephemeral=True, allowed_mentions=NO_MENTIONS
            )

        await interaction.response.defer(thinking=True)
        async with self._lock:
            # Re-read inside the lock: a parallel add/remove can change the member or delete the role.
            roles = [r for r in member.roles if matches(r)]
            if not roles:
                return await interaction.followup.send(
                    f"{member.mention} doesn't have that color role.", ephemeral=True, allowed_mentions=NO_MENTIONS
                )

            removed = []
            reason = f"Color removed by {interaction.user} ({interaction.user.id})"
            for role in roles:
                try:
                    await member.remove_roles(role, reason=reason)
                except discord.NotFound:
                    # Code 10011: the role is already gone, so there is nothing left to remove.
                    continue
                except discord.Forbidden:
                    return await interaction.followup.send(
                        "I'm not allowed to change that member's roles.", ephemeral=True
                    )
                removed.append(role)

            if not removed:
                return await interaction.followup.send(
                    "That color role no longer exists. It may have been deleted already.", ephemeral=True
                )
            await self._delete_if_unused(guild, removed)

        # Role names are set by server staff and shown publicly here, so don't let them break the formatting.
        removed_names = ", ".join(f"**{discord.utils.escape_markdown(role.name)}**" for role in removed)
        await interaction.followup.send(
            f"Removed {removed_names} from {member.mention}.", allowed_mentions=NO_MENTIONS
        )

    @add.autocomplete("color")
    async def add_color_autocomplete(self, interaction: discord.Interaction, current: str):
        guild = interaction.guild
        low = guild.get_role(ROLE_COLOR_MIN) if guild else None
        high = guild.get_role(ROLE_COLOR_MAX) if guild else None
        if low is None or high is None:
            return []

        # Everything here comes from the role cache and the built-in name table: no API calls per keystroke.
        existing = {}
        for role in band_roles(guild, low, high):
            if role.colour.value and is_assignable(role, low, high):
                existing.setdefault(role.colour.value, role.name)

        choices: list[app_commands.Choice[str]] = []
        seen: set[int] = set()

        def offer(value: int, label: str) -> None:
            if value in seen or len(choices) >= 25:
                return
            seen.add(value)
            hex_value = f"#{value:06x}"
            role_name = existing.get(value)
            text = f"{role_name} ({hex_value}) - existing role" if role_name else f"{label} ({hex_value}) - new role"
            choices.append(app_commands.Choice(name=text[:100], value=hex_value))

        # What the typed color really is, and the named color it is close to, so the host can choose.
        rgb = parse_color(current) if current.strip() else None
        if rgb is not None:
            offer(role_value(rgb), role_name_for(rgb))
            if not is_exact_name(rgb):
                nearest = canonical_name(color_name(rgb))
                offer(role_value(NAME_RGB_STORE[nearest]), nearest)

        needle = normalise_name(current)
        for value, role_name in sorted(existing.items(), key=lambda item: normalise_name(item[1])):
            if needle in normalise_name(f"{role_name}{value:06x}"):
                offer(value, role_name)

        if needle:
            for name, named_rgb in NAME_RGB_STORE.items():
                if name.startswith(needle):
                    # Aliases share one role, e.g. typing "aqua" shows "aqua = cyan".
                    canonical = canonical_name(name)
                    offer(role_value(named_rgb), name if canonical == name else f"{name} = {canonical}")
        return choices

    @remove.autocomplete("color")
    async def remove_color_autocomplete(self, interaction: discord.Interaction, current: str):
        guild = interaction.guild
        low = guild.get_role(ROLE_COLOR_MIN) if guild else None
        high = guild.get_role(ROLE_COLOR_MAX) if guild else None
        if low is None or high is None:
            return []

        # Before a member is picked, offer every band role so role names are visible, not just color names.
        # Autocomplete may only carry the member's ID; look it up in the cache and never call the API.
        target = getattr(interaction.namespace, "member", None)
        member = target if isinstance(target, discord.Member) else None
        if member is None and target is not None:
            with contextlib.suppress(TypeError, ValueError):
                member = guild.get_member(int(getattr(target, "id", target)))
        roles = [r for r in member.roles if is_color_role(r, low, high)] if member else band_roles(guild, low, high)

        current = normalise_name(current)
        names = sorted({r.name for r in roles}, key=normalise_name)
        return [app_commands.Choice(name=n, value=n) for n in names if current in normalise_name(n)][:25]


async def setup(bot):
    await bot.add_cog(ColorCog(bot))
