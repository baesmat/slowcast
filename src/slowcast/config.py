"""Runtime configuration, all from environment variables (SC_* prefix)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    # where the SQLite database lives
    db_path: Path = field(default_factory=lambda: Path(_env("SC_DB_PATH", "slowcast.db")))
    # root for generated audio files
    data_dir: Path = field(default_factory=lambda: Path(_env("SC_DATA_DIR", "data")))
    # shared secret for the API and the web UI
    token: str = field(default_factory=lambda: _env("SC_TOKEN", "change-me"))
    # per-feed secret embedded in the podcast RSS URL
    feed_token: str = field(default_factory=lambda: _env("SC_FEED_TOKEN", ""))
    # publicly reachable base URL, used for enclosure links in the feed
    public_url: str = field(default_factory=lambda: _env("SC_PUBLIC_URL", "http://localhost:8330").rstrip("/"))
    edge_voice: str = field(default_factory=lambda: _env("SC_VOICE", DEFAULT_VOICE))
    host: str = field(default_factory=lambda: _env("SC_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("SC_PORT", "8330")))
    # pipeline tuning
    max_chunk_chars: int = field(default_factory=lambda: int(_env("SC_MAX_CHUNK_CHARS", "2000")))
    fetch_timeout: float = field(default_factory=lambda: float(_env("SC_FETCH_TIMEOUT", "25")))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "audio").mkdir(parents=True, exist_ok=True)

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"


def load_config() -> Config:
    return Config()
