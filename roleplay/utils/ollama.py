from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
import time
from typing import NamedTuple, Optional, Union

import aiohttp

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Raised when the local Ollama server cannot satisfy a request."""


class ChatResult(NamedTuple):
    content: str
    # "stop" when the model finished naturally, "length" when num_predict cut it off.
    done_reason: str


PRIORITY_REPLY = 0
PRIORITY_BACKGROUND = 10


class _PrioritySlots:
    """A semaphore that hands free slots to the lowest priority number first."""

    def __init__(self, slots: int):
        self._free = max(1, slots)
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._counter = itertools.count()

    async def acquire(self, priority: int) -> None:
        if self._free > 0 and not self._waiters:
            self._free -= 1
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, next(self._counter), future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                # The slot was handed over just as we were cancelled.
                self.release()
            raise

    def release(self) -> None:
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)
                return
        self._free += 1


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 180.0,
        max_concurrency: int = 1,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._slots = _PrioritySlots(max_concurrency)
        self._models_cache: tuple[float, list[str]] = (0.0, [])
        self._models_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        priority: int = PRIORITY_REPLY,
    ):
        await self.start()
        await self._slots.acquire(priority)
        try:
            try:
                async with self._session.request(
                    method, f"{self.base_url}{path}", json=payload
                ) as response:
                    text = await response.text()
                    if response.status >= 400:
                        detail = text.strip().replace("\n", " ")[:300]
                        raise OllamaError(
                            f"Ollama returned HTTP {response.status}: {detail or 'no details'}"
                        )
                    try:
                        return json.loads(text)
                    except ValueError as exc:
                        raise OllamaError("Ollama returned an invalid JSON response.") from exc
            except asyncio.TimeoutError as exc:
                raise OllamaError(
                    f"Ollama did not respond within {self.timeout.total:.0f}s."
                ) from exc
            except aiohttp.ClientError as exc:
                raise OllamaError(
                    f"Could not reach Ollama at {self.base_url}. Is it running? ({exc})"
                ) from exc
        finally:
            self._slots.release()

    async def list_models(self, *, force: bool = False) -> list[str]:
        now = time.monotonic()
        cached_at, cached = self._models_cache
        if not force and cached and now - cached_at < 30:
            return list(cached)

        async with self._models_lock:
            # Re-check after waiting for another caller's refresh.
            cached_at, cached = self._models_cache
            if not force and cached and time.monotonic() - cached_at < 30:
                return list(cached)

            data = await self._request("GET", "/api/tags")
            models = sorted(
                {
                    model.get("name", "")
                    for model in data.get("models", [])
                    if isinstance(model, dict) and model.get("name")
                }
            )
            self._models_cache = (time.monotonic(), models)
            return list(models)

    async def resolve_model(self, preferred: Optional[str] = None) -> str:
        if preferred and preferred.strip():
            return preferred.strip()
        models = await self.list_models()
        if not models:
            raise OllamaError(
                "No local Ollama models were found. Pull one first, for example: "
                "`ollama pull llama3.2`"
            )
        return models[0]

    async def chat(self, **kwargs) -> str:
        return (await self.chat_result(**kwargs)).content

    async def chat_result(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float = 0.8,
        num_ctx: int = 4096,
        num_predict: int = 128,
        think: bool = False,
        stop: Optional[list[str]] = None,
        keep_alive: str = "2h",
        num_thread: Optional[int] = None,
        num_batch: Optional[int] = None,
        format: Optional[Union[str, dict]] = None,
        options: Optional[dict] = None,
        priority: int = PRIORITY_REPLY,
    ) -> ChatResult:
        merged = {
            "temperature": max(0.0, min(2.0, float(temperature))),
            "num_ctx": max(1024, int(num_ctx)),
            "num_predict": max(16, int(num_predict)),
        }
        if stop:
            merged["stop"] = [item for item in stop if item]
        if num_thread:
            merged["num_thread"] = max(1, int(num_thread))
        if num_batch:
            merged["num_batch"] = max(1, int(num_batch))
        if options:
            merged.update(options)

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            # Keep the model resident so the next reply does not pay a multi-second
            # cold-load penalty.
            "keep_alive": keep_alive,
            # Reasoning models (Qwen 3, DeepSeek-R1, ...) otherwise spend the
            # whole reply budget on hidden "thinking" and return empty content.
            "think": bool(think),
            "options": merged,
        }
        if format is not None:
            payload["format"] = format

        data = await self._request("POST", "/api/chat", payload, priority=priority)
        message = data.get("message") or {}
        content = message.get("content", "")
        thinking = message.get("thinking", "")
        done_reason = str(data.get("done_reason") or "")

        if isinstance(content, str) and content.strip():
            return ChatResult(content.strip(), done_reason)

        # Some models ignore think=false and still emit only reasoning text.
        # Using it is better than failing a free-form reply outright.
        if format is None and isinstance(thinking, str) and thinking.strip():
            log.warning(
                "Model %s returned reasoning text but no reply (done_reason=%s); "
                "using the reasoning text instead",
                model,
                done_reason,
            )
            return ChatResult(thinking.strip(), done_reason)

        raise OllamaError(
            f"Ollama returned an empty response (done_reason={done_reason or 'unknown'})."
        )
