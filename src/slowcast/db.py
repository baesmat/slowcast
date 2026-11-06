"""SQLite persistence. Small, synchronous, WAL mode.

One shared connection serves both the request threadpool and the background
worker: the connection is created with check_same_thread=False on purpose and
every statement runs under a re-entrant lock.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',   -- queued|rendering|ready|error
    error TEXT NOT NULL DEFAULT '',
    audio_file TEXT NOT NULL DEFAULT '',     -- path relative to the data dir
    duration_s REAL NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

READY = "ready"

# single shared connection + worker task => serialize every statement
LOCK = threading.RLock()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with LOCK:
        conn.executescript(SCHEMA)
        conn.commit()


def now() -> float:
    return time.time()


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    with LOCK:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with LOCK:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def feed_token(conn: sqlite3.Connection, default: str = "") -> str:
    """Return the stable per-installation feed token, creating one on first use."""
    token = get_meta(conn, "feed_token")
    if token:
        return token
    token = default or uuid.uuid4().hex[:12]
    set_meta(conn, "feed_token", token)
    return token


def add_article(conn: sqlite3.Connection, url: str) -> sqlite3.Row:
    with LOCK:
        cur = conn.execute(
            "INSERT INTO articles(url, status, created_at, updated_at) VALUES(?,?,?,?)",
            (url, "queued", now(), now()),
        )
        conn.commit()
        return get_article(conn, cur.lastrowid)  # type: ignore[arg-type,return-value]


def get_article(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    with LOCK:
        return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


def list_articles(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    with LOCK:
        return conn.execute(
            "SELECT * FROM articles ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def ready_articles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    with LOCK:
        return conn.execute(
            "SELECT * FROM articles WHERE status='ready' ORDER BY id ASC"
        ).fetchall()


def update_article(conn: sqlite3.Connection, article_id: int, **fields: object) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [now(), article_id]
    with LOCK:
        conn.execute(f"UPDATE articles SET {cols}, updated_at=? WHERE id=?", values)
        conn.commit()


def delete_article(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    with LOCK:
        row = get_article(conn, article_id)
        if row is None:
            return None
        conn.execute("DELETE FROM articles WHERE id=?", (article_id,))
        conn.commit()
    return row


def next_queued(conn: sqlite3.Connection) -> sqlite3.Row | None:
    with LOCK:
        return conn.execute(
            "SELECT * FROM articles WHERE status='queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
