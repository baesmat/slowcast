"""Render pipeline: URL -> extracted text -> chunks -> speech -> files.

The worker consumes the queue one article at a time; every transition is
written to the database so the UI always reflects reality.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from . import db
from .chunks import chunk_text
from .config import Config
from .extract import ExtractError, extract, fetch
from .tts import TTSError, synthesize

log = logging.getLogger("slowcast.pipeline")

MAX_ATTEMPTS = 2


def audio_path(cfg: Config, article_id: int) -> Path:  # type: ignore[valid-type]
    return cfg.audio_dir / f"article-{article_id}.mp3"


class Renderer:
    def __init__(self, cfg: Config, conn: sqlite3.Connection) -> None:  # type: ignore[valid-type]
        self.cfg = cfg
        self.conn = conn

    async def process(self, article_id: int) -> None:
        row = db.get_article(self.conn, article_id)
        if row is None:
            return
        url = row["url"]
        db.update_article(self.conn, article_id, status="rendering", error="")
        try:
            _, html = await fetch(url, timeout=self.cfg.fetch_timeout)
            article = extract(html, url=url)

            body = f"{article.title}. {article.text}" if article.title else article.text
            chunks = chunk_text(body, max_chars=self.cfg.max_chunk_chars)

            speech = await synthesize(chunks, voice=self.cfg.edge_voice)

            out = audio_path(self.cfg, article_id)
            out.write_bytes(speech.audio)

            db.update_article(
                self.conn,
                article_id,
                status="ready",
                title=article.title,
                duration_s=speech.duration_s,
                size_bytes=len(speech.audio),
                audio_file=str(out.relative_to(self.cfg.data_dir)),
                error="",
            )
            log.info(
                "article %s ready: %.1fs, %d bytes",
                article_id, speech.duration_s, len(speech.audio),
            )
        except (ExtractError, TTSError) as exc:
            log.warning("article %s failed: %s", article_id, exc)
            db.update_article(self.conn, article_id, status="error", error=str(exc)[:500])
        except Exception as exc:  # noqa: BLE001 - keep worker alive no matter what
            log.exception("article %s crashed", article_id)
            db.update_article(self.conn, article_id, status="error", error=f"internal: {exc}"[:500])

    async def step(self) -> bool:
        """Process one queued article. Returns True if it did work."""
        row = db.next_queued(self.conn)
        if row is None:
            return False
        await self.process(row["id"])
        return True

    async def retry(self, article_id: int) -> bool:  # type: ignore[valid-type]
        row = db.get_article(self.conn, article_id)
        if row is None or row["status"] not in ("error", "ready"):
            return False
        path = audio_path(self.cfg, article_id)
        if path.exists():
            path.unlink()
        db.update_article(
            self.conn,
            article_id,
            status="queued",
            error="",
            audio_file="",
            duration_s=0,
            size_bytes=0,
        )
        return True


class Worker:
    """Background loop draining the queue. Poll interval is injectable for tests."""

    def __init__(self, renderer: Renderer, poll_seconds: float = 2.0) -> None:  # type: ignore[valid-type]
        self.renderer = renderer
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:  # type: ignore[valid-type]
        self._task = asyncio.get_running_loop().create_task(self.run())

    async def run(self) -> None:  # type: ignore[valid-type]
        while True:
            try:
                did_work = await self.renderer.step()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("worker loop error")
                did_work = False
            await asyncio.sleep(0.1 if did_work else self.poll_seconds)

    async def stop(self) -> None:  # type: ignore[valid-type]
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
