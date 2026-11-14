"""Render a sample HTML transcript offline with the bundled chat_exporter wheel.

Usage: python docs/sample_transcript.py sample.html

All users, messages and IDs are made up. Discord's API is never contacted: the only
call that would reach Discord (listing who reacted) is replaced by a stub.
"""
import asyncio
import datetime as dt
import sys

import discord
import chat_exporter

EPOCH = 1420070400000


def snowflake(when: dt.datetime, n: int = 0) -> int:
    return ((int(when.timestamp() * 1000) - EPOCH) << 22) + n


BASE = dt.datetime(2026, 10, 3, 18, 0, tzinfo=dt.timezone.utc)

USERS = {
    "host": {"id": str(snowflake(BASE - dt.timedelta(days=900), 1)), "username": "game_host", "global_name": "Game Host"},
    "mira": {"id": str(snowflake(BASE - dt.timedelta(days=800), 2)), "username": "mira", "global_name": "Mira"},
    "juno": {"id": str(snowflake(BASE - dt.timedelta(days=700), 3)), "username": "juno", "global_name": "Juno"},
    "okoro": {"id": str(snowflake(BASE - dt.timedelta(days=600), 4)), "username": "okoro", "global_name": "Okoro"},
    "tess": {"id": str(snowflake(BASE - dt.timedelta(days=500), 5)), "username": "tess", "global_name": "Tess"},
}
for u in USERS.values():
    u.update({"discriminator": "0", "avatar": None, "bot": False})

GUILD_ID = snowflake(BASE - dt.timedelta(days=1000), 9)
ROLE_HOST = snowflake(BASE - dt.timedelta(days=999), 10)
ROLE_PLAYER = snowflake(BASE - dt.timedelta(days=999), 11)
CHANNEL_ID = snowflake(BASE - dt.timedelta(days=2), 12)

MESSAGES = [
    ("host", 0, "**Day 2 has started.** You have 24 hours to discuss. Votes go in `#votes`, not here.", [("\N{THUMBS UP SIGN}", ["mira", "juno", "okoro", "tess"])]),
    ("mira", 2, "Morning all. Nobody died last night, so somebody got protected.", []),
    ("juno", 3, "Or the attack was blocked. I'm not claiming anything yet.", [("\N{EYES}", ["mira", "tess"])]),
    ("okoro", 6, "I'll say it: Tess was very quiet yesterday.", []),
    ("tess", 7, "I was at work! Read back, I posted three times.", [("\N{FACE WITH TEARS OF JOY}", ["juno"])]),
    ("mira", 9, "That's true, she did. I'd rather look at who pushed the Day 1 vote so hard.", []),
    ("host", 30, "Reminder: 12 hours left on Day 2.", [("\N{ALARM CLOCK}", ["mira", "okoro"])]),
]


def user_payload(key):
    return dict(USERS[key])


async def main(out_path: str):
    client = discord.Client(intents=discord.Intents.default())
    state = client._connection

    reactions_by_message = {}

    async def fake_get_reaction_users(channel_id, message_id, emoji, limit, after=None, type=None):
        # Stand-in for GET /channels/{id}/messages/{id}/reactions/{emoji}
        if after is not None:
            return []
        return [user_payload(k) for k in reactions_by_message[int(message_id)].get(emoji, [])][:limit]

    state.http.get_reaction_users = fake_get_reaction_users

    guild = discord.Guild(
        data={
            "id": str(GUILD_ID),
            "name": "Sample Server",
            "icon": None,
            "roles": [
                {"id": str(GUILD_ID), "name": "@everyone", "color": 0, "position": 0, "permissions": "0", "hoist": False, "managed": False, "mentionable": False},
                {"id": str(ROLE_HOST), "name": "Host for Game 3", "color": 0xE74C3C, "position": 2, "permissions": "0", "hoist": True, "managed": False, "mentionable": False},
                {"id": str(ROLE_PLAYER), "name": "In game 3", "color": 0x3498DB, "position": 1, "permissions": "0", "hoist": False, "managed": False, "mentionable": False},
            ],
            "emojis": [],
            "features": [],
            "member_count": 5,
        },
        state=state,
    )
    state._add_guild(guild)

    for key, u in USERS.items():
        roles = [str(ROLE_HOST)] if key == "host" else [str(ROLE_PLAYER)]
        member = discord.Member(
            data={"user": u, "roles": roles, "joined_at": (BASE - dt.timedelta(days=120)).isoformat(), "deaf": False, "mute": False, "flags": 0},
            guild=guild,
            state=state,
        )
        guild._add_member(member)

    channel = discord.TextChannel(
        state=state,
        guild=guild,
        data={
            "id": str(CHANNEL_ID),
            "type": 0,
            "name": "day-chat",
            "position": 1,
            "parent_id": None,
            "topic": "Day discussion for The game 3",
            "nsfw": False,
            "permission_overwrites": [],
        },
    )
    guild._add_channel(channel)

    messages = []
    for i, (key, minutes, content, reactions) in enumerate(MESSAGES):
        when = BASE + dt.timedelta(minutes=minutes)
        mid = snowflake(when, 100 + i)
        reactions_by_message[mid] = {emoji: who for emoji, who in reactions}
        data = {
            "id": str(mid),
            "channel_id": str(CHANNEL_ID),
            "guild_id": str(GUILD_ID),
            "author": user_payload(key),
            "member": {"roles": [str(ROLE_HOST)] if key == "host" else [str(ROLE_PLAYER)], "joined_at": (BASE - dt.timedelta(days=120)).isoformat(), "deaf": False, "mute": False, "flags": 0},
            "content": content,
            "timestamp": when.isoformat(),
            "edited_timestamp": None,
            "tts": False,
            "mention_everyone": False,
            "mentions": [],
            "mention_roles": [],
            "attachments": [],
            "embeds": [],
            "components": [],
            "pinned": False,
            "type": 0,
            "reactions": [
                {"count": len(who), "me": False, "emoji": {"id": None, "name": emoji}, "count_details": {"burst": 0, "normal": len(who)}, "burst_colors": [], "me_burst": False}
                for emoji, who in reactions
            ],
        }
        messages.append(discord.Message(state=state, channel=channel, data=data))

    count = await chat_exporter.export_to_file(
        channel,
        out_path,
        messages=messages,
        tz_info="UTC",
        guild=guild,
        bot=client,
        fetch_reactors=True,
    )
    print(f"exported {count} messages to {out_path}")
    await client.http.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
