import asyncio
import contextlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

import chat_exporter
import discord

# Keep peak memory + API rate to one transcript at a time.
_export_lock = asyncio.Lock()


@dataclass
class TranscriptFile:
    path: str
    message_count: int
    size: int


def is_export_running() -> bool:
    return _export_lock.locked()


@contextlib.asynccontextmanager
async def build_transcript(
    channel,
    *,
    bot: discord.Client,
    fetch_reactors: bool,
    should_stop: Callable[[], bool],
):
    """Streams a channel's history into an HTML transcript on disk.

    Yields a TranscriptFile, or None if no messages were exported. The file is deleted on exit.
    """
    async with _export_lock:
        with tempfile.TemporaryDirectory(prefix="transcript-") as tmp_dir:
            out_path = os.path.join(tmp_dir, "transcript.html")
            count = await chat_exporter.export_to_file(
                channel,
                out_path,
                tz_info="UTC",
                guild=channel.guild,
                bot=bot,
                fetch_reactors=fetch_reactors,
                should_stop=should_stop,
            )
            yield TranscriptFile(out_path, count, os.path.getsize(out_path)) if count else None
