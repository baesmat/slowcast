"""Neural text-to-speech via edge-tts.

Chunks are synthesized concurrently (bounded), concatenated in order, and the
duration is taken from the last sentence/word boundary event — no ffprobe or
mutagen needed.
"""

from __future__ import annotations

import asyncio
import logging

import edge_tts

log = logging.getLogger("slowcast.tts")

# edge-tts reports boundaries in 100-nanosecond units (Windows FILETIME ticks)
TICKS_PER_SECOND = 10_000_000

CONCURRENCY = 3
CHUNK_TIMEOUT_S = 300
ATTEMPTS = 3


class TTSError(Exception):
    pass


class SpeechResult:
    __slots__ = ("audio", "duration_s")

    def __init__(self, audio: bytes, duration_s: float) -> None:
        self.audio = audio
        self.duration_s = duration_s


async def _drain(communicate, parts: list[bytes]) -> int:
    """Consume a synthesis stream; return the last boundary tick."""
    last_tick = 0
    async for event in communicate.stream():
        kind = event.get("type")
        if kind == "audio":
            parts.append(event["data"])
        elif kind in ("WordBoundary", "SentenceBoundary"):
            last_tick = max(last_tick, int(event["offset"]) + int(event.get("duration", 0)))
    return last_tick


async def _synthesize_chunk(
    chunk: str, voice: str, rate: str, sem: asyncio.Semaphore
) -> tuple[bytes, int]:
    async with sem:
        for attempt in range(1, ATTEMPTS + 1):  # retry transient websocket stalls
            parts: list[bytes] = []
            last_tick = 0

            try:
                communicate = edge_tts.Communicate(chunk, voice=voice, rate=rate)
                log.info("chunk start (%d chars, attempt %d)", len(chunk), attempt)
                last_tick = await asyncio.wait_for(
                    _drain(communicate, parts), timeout=CHUNK_TIMEOUT_S
                )
                log.info("chunk done (%d bytes)", len(b"".join(parts)))
            except asyncio.TimeoutError:
                if attempt == ATTEMPTS:
                    raise TTSError(f"edge-tts timed out after {CHUNK_TIMEOUT_S}s") from None
                continue
            except TTSError:
                raise
            except Exception as exc:  # network and service errors alike
                if attempt == ATTEMPTS:
                    raise TTSError(f"edge-tts failed: {exc}") from exc
                await asyncio.sleep(2)
                continue

            audio = b"".join(parts)
            if not audio:
                if attempt == ATTEMPTS:
                    raise TTSError("edge-tts returned no audio")
                continue
            return audio, last_tick
    raise TTSError("unreachable")  # pragma: no cover


async def synthesize(chunks: list[str], voice: str, rate: str = "+0%") -> SpeechResult:
    if not chunks:
        raise TTSError("no text to synthesize")

    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(_synthesize_chunk(c, voice, rate, sem) for c in chunks))
    audio = b"".join(part for part, _ in results)
    last_tick = max((tick for _, tick in results), default=0)
    if not audio:
        raise TTSError("edge-tts returned no audio")
    return SpeechResult(audio=audio, duration_s=round(last_tick / TICKS_PER_SECOND, 2))


def synthesize_sync(chunks: list[str], voice: str, rate: str = "+0%") -> SpeechResult:
    return asyncio.run(synthesize(chunks, voice, rate))
