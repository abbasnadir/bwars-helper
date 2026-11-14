# Local Ollama Roleplay Bot

A self-contained Discord roleplay bot. The owner sets up a character in a text channel, and the character chats with everyone there using a local [Ollama](https://ollama.com/) model.

This folder is independent of the main bwars-helper bot in the repository root. It is a separate Discord application with its own token, cogs, utilities, database and dependencies. It runs locally because it needs an Ollama server, while the main bot is hosted.

## What it does

- Owner-only `/roleplay setup` command, exposed only to **user-installed apps** (not as a server-wide command).
- Per-channel characters with a name, persona, scenario, style notes, model, temperature, and history length.
- Chats in configured channels with multiple people, combining quick bursts of messages into one reply and skipping filler like "uhh" or "lol".
- Keeps the last few messages verbatim and remembers important facts long-term (pinned by the owner or learned from chat).
- Uses local Ollama models only. No cloud AI API is involved.
- Owner-only tuning commands for personality/behaviour adjustments.
- SQLite storage for setups and conversation memory.

## Requirements

- Python 3.10+
- Discord bot token and your Discord user ID
- Ollama running locally, with at least one model pulled:
  ```bash
  ollama serve
  ollama pull llama3.2
  ```

## Discord application setup

1. Create an application at <https://discord.com/developers/applications>.
2. **Bot → Privileged Gateway Intents**: enable **Message Content Intent**.
3. **Installation**:
   - Enable **Guild Install** so the bot can join the server and read/send messages in the roleplay channel.
   - Enable **User Install** so the owner can use the slash commands.
   - For the guild install, include the `bot` and `applications.commands` scopes.
   - For the user install, include the `applications.commands` scope.
4. Invite the bot to the server with permission to **View Channel** and **Send Messages** in the roleplay channel.
5. Install the app to your own user account (the owner's account). This makes the user-install slash commands available to you.
6. Commands are synced globally on the first startup and whenever the command tree changes (`SYNC_COMMANDS=auto`). Discord can take a short while to show them. Use `/roleplay` in a server you and the bot share.

The roleplay commands are marked `integration_types: [USER_INSTALL]`, so they are not published as server commands that everyone in the guild can see. Every command also checks `OWNER_ID` on top of that. If you want only your own account to install the app, keep the application private in the Developer Portal.

## Install and run

```bash
cd roleplay
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in DISCORD_BOT_TOKEN and OWNER_ID
python bot.py
```

Never commit `.env`. Only `.env.example` should contain placeholders.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | required | Bot token |
| `OWNER_ID` | required | Your Discord user ID; only this user can run management commands |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama server |
| `OLLAMA_MODEL` | first available model | Default model for new setups |
| `OLLAMA_TIMEOUT` | `180` | Request timeout in seconds |
| `OLLAMA_MAX_CONCURRENCY` | `1` | Simultaneous Ollama requests; replies always run before background memory jobs |
| `OLLAMA_NUM_CTX` | `4096` | Context window requested from Ollama; smaller is faster |
| `OLLAMA_NUM_PREDICT` | `128` | Hard cap on tokens per reply; smaller is faster |
| `OLLAMA_NUM_THREAD` | `6` | CPU threads used by Ollama |
| `OLLAMA_NUM_BATCH` | `128` | Prompt batch size |
| `OLLAMA_KEEP_ALIVE` | `2h` | How long the model stays loaded between messages |
| `OLLAMA_SUMMARY_MODEL` | roleplay model | Optional model used for memory extraction |
| `OLLAMA_THINK` | `false` | Allow reasoning models to emit hidden thinking tokens |
| `ROLEPLAY_DB` | `data/roleplay.db` | SQLite database path |
| `ROLEPLAY_COMPACT_AFTER` | `10` | Extra messages beyond the recent window before compaction |
| `ROLEPLAY_HISTORY_CHARS` | `4000` | Characters of recent chat sent to the model |
| `ROLEPLAY_MAX_REPLY_CHARS` | `1200` | Hard character cap for a reply (always under Discord's 2000 limit) |
| `ROLEPLAY_GATHER_SECONDS` | `1.2` | Quiet time to wait for follow-up messages before replying |
| `ROLEPLAY_MAX_GATHER_SECONDS` | `8` | Longest wait, even while someone keeps typing |
| `ROLEPLAY_MAX_SENTENCES` | `3` | Maximum sentences per reply (`0` = no limit) |
| `ROLEPLAY_MAX_ACTIONS` | `1` | Maximum `*italic actions*` per reply (`0` = speech only) |
| `ROLEPLAY_MEMORY_FACTS` | `15` | Automatically learned facts kept per channel |
| `ROLEPLAY_MEMORY_CHARS` | `1500` | Character budget for memory in the prompt |
| `OLLAMA_SUMMARY_PREDICT` | `400` | Token budget for memory extraction |
| `SYNC_COMMANDS` | `auto` | `auto` syncs only when commands change, `always` syncs every startup, `never` disables syncing |
| `LOG_LEVEL` | `INFO` | Python log level |

## Commands

All commands are owner-only and user-install-only.

| Command | Purpose |
| --- | --- |
| `/roleplay setup` | Create or replace a character in a channel |
| `/roleplay tune` | Add a behaviour note, change the temperature, or clear notes |
| `/roleplay status` | Show the setup and memory stats |
| `/roleplay memory` | Show the pinned and learned long-term memory |
| `/roleplay remember` | Pin an important fact the character never forgets |
| `/roleplay forget` | Remove a memory by its id |
| `/roleplay reset` | Clear stored messages and learned memory (pinned memory is kept) |
| `/roleplay toggle` | Pause or resume replies |
| `/roleplay remove` | Delete the setup and all stored memory |
| `/roleplay models` | List local Ollama models |
| `/roleplay list` | List configured channels |

### Example setup

```text
/roleplay setup
  channel: #tavern
  name: Mira
  persona: A sharp-tongued elven innkeeper who hides a soft spot for lost travellers. She speaks in dry one-liners and always has a story about the last person who broke her favourite mug.
  scenario: A rainy night at the Gilded Antler inn. The common room is nearly empty.
  model: llama3.2
  temperature: 0.8
  history: 10
```

Then talk normally in `#tavern`; see [When the character replies](#when-the-character-replies). Start a message with `//` to talk out of character: the bot ignores it and does not store it.

Temperature is limited to `0.0–1.2`; small local models become incoherent above that. `0.7–0.9` works well.

Use `fresh_start:true` when replacing a character if you also want to wipe the old conversation and learned memory. Pinned memory is always kept.

Use `/roleplay tune` for adjustments without rewriting the whole persona:

```text
/roleplay tune channel:#tavern instruction:Be more sarcastic.
/roleplay tune channel:#tavern temperature:0.7
```

## When the character replies

The bot doesn't answer every single message:

- **Bursts are combined.** After a message, the bot waits briefly (`ROLEPLAY_GATHER_SECONDS`) for follow-ups. If the same person sends two messages in a row, they are joined into one turn and answered once. If several people talk at the same time, they get one reply that addresses all of them.
- **It waits while people type.** If someone starts typing again right after sending a message, the bot holds its reply (up to `ROLEPLAY_MAX_GATHER_SECONDS`) so it can answer everything together.
- **Filler is skipped.** Messages like `uhh`, `hmm`, `lol`, `LMAOOO`, emoji-only, `???`, or keyboard mashing get no reply and are not stored.
- **Short answers need a question.** `yes`, `ok`, `nah` and similar get a reply only if the character's last line was a question. Otherwise they are stored as context without a reply.
- **Direct messages always get a reply.** If a message mentions the bot, replies to one of its messages, or contains the character's name, it is answered even if it's short.
- **Out of character:** messages starting with `//` are ignored completely.

Each request also tells the model who it is talking to, e.g. `(RAM is talking to you.)`. It uses the name shown in chat, plus the `@username` when two people share a display name, so it doesn't mix people up.

## Context and memory

The bot stores each human message and each character reply in SQLite.

- **Recent chat:** the last `history` messages (up to `ROLEPLAY_HISTORY_CHARS`) are sent to the model verbatim.
- **Long-term memory:** a short list of important facts, shown to the model on every reply.
  - **Pinned** facts are added by the owner with `/roleplay remember` and are never removed automatically.
  - **Learned** facts are extracted in the background once the channel has more than `history + ROLEPLAY_COMPACT_AFTER` messages: names, relationships, promises and debts, goals, important items/places, and major events. Jokes, spam, and talk about the bot are skipped.
  - Facts are deduplicated, and any fact naming something that never appeared in the chat is rejected, to stop the model inventing details.
  - At most `ROLEPLAY_MEMORY_FACTS` learned facts are kept; when there are more, they are merged and the least important ones are dropped.
  - After facts are extracted, the old messages they came from are deleted.
- `/roleplay memory` shows everything with ids; `/roleplay forget` removes a wrong fact.
- `/roleplay reset` clears messages and learned facts but keeps pinned facts.

For better memory quality, set `OLLAMA_SUMMARY_MODEL` to a larger model you have locally. Memory runs in the background, so it never delays replies.

## Reply style

- Replies are 1–3 sentences of mostly spoken dialogue (`ROLEPLAY_MAX_SENTENCES`).
- At most one short `*action*` is kept per reply (`ROLEPLAY_MAX_ACTIONS`; `0` = speech only). Extra or overly long actions are removed.
- If the model replies with only narration, it is asked once more for spoken words.
- Leading `Name:` labels, lines written for other participants, wrapping quotes, and half-finished sentences are cleaned up.

## Speed tips

Local generation on a CPU is bound by tokens per second, so short replies are the biggest win:

- The prompt asks for 1-3 short sentences, and generation stops at the first paragraph break or before another speaker's line.
- `OLLAMA_NUM_PREDICT` caps the reply length hard; keep it around `96-160`.
- `OLLAMA_NUM_THREAD` should match physical CPU cores. On a 12-thread laptop, `6` was noticeably faster than `12`.
- `OLLAMA_KEEP_ALIVE` keeps the model in memory so the first reply after a restart is the only slow one.
- `ROLEPLAY_HISTORY_CHARS` and `OLLAMA_NUM_CTX` control how much chat is processed; lower them for faster replies.
- A smaller model (for example a 1B–2B model) is much faster than a 3B+ model on CPU.
- Replies are capped at `ROLEPLAY_MAX_REPLY_CHARS`, so they always fit in a single Discord message.

## Notes

- "Fine-tuning" here means adjusting the character's persona and style notes at runtime; it does not train model weights. All management commands, including tuning, are owner-only. Participants influence the character through the conversation itself.
- Replies are sent with `allowed_mentions=none`, so the model cannot ping `@everyone`, roles, or users.
- Conversation content is stored locally in `data/roleplay.db`.
- The bot ignores other bots and non-standard system messages to avoid reply loops.
- If the Ollama server is unavailable, the bot posts a short warning in the channel and logs the error.

## Troubleshooting

**"The application did not respond" / `Unknown interaction` (10062).** Discord gives the bot 3 seconds to acknowledge a command. The bot now acknowledges commands before doing anything else, and logs how old the interaction was when this fails:

- If the age is well over 3 seconds, the machine or network was too busy. Heavy Ollama generation can starve the bot of CPU. Keep `OLLAMA_MAX_CONCURRENCY=1` and `OLLAMA_NUM_THREAD` below your CPU's thread count.
- Make sure only **one** copy of the bot runs with this token. A second copy (another terminal, a service, or another bot using the same token) causes `Unknown interaction` and `already acknowledged` errors.
