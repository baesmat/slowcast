# 🐢 Slowcast

**Your reading list, narrated.** A tiny self-hosted server that turns saved articles into a private podcast feed.

Save any URL — Slowcast extracts the article, narrates it with a neural voice, and publishes it to a personal podcast RSS feed. Subscribe once in any podcast app (Overcast, Pocket Casts, Apple Podcasts, AntennaPod…) and every saved article shows up as a new episode. Listen at 1× or 3×; the reading list finally gets read.

```
 ┌──────────┐   POST /api/articles    ┌───────────────────────────────────┐
 │ bookmarklet│ ─────────────────────▶ │  fetch → extract → chunk → TTS    │
 │  / API / UI │                        │  worker (async, retrying)         │
 └──────────┘                          └─────────────┬─────────────────────┘
                                             audio/article-N.mp3
 ┌──────────────┐   GET /feed/<token>.xml    ▼
 │ podcast app   │ ◀────────────────── private RSS w/ enclosures
 └──────────────┘
```

## Why

Every read-it-later app owns your queue in someone else's cloud. Slowcast is ~600 lines of Python, one SQLite file, no accounts, no telemetry — it runs on a Raspberry Pi and does exactly one thing.

## Quick start

### Docker

```bash
docker compose up -d
# UI:    http://localhost:8330
# token: change-me (set SC_TOKEN first!)
```

### From source

```bash
pip install slowcast            # or: pip install .
SC_TOKEN=unicorn slowcast --host 0.0.0.0 --port 8330
```

### Use it

1. Open the UI, sign in with your token.
2. Paste a URL → it lands in the queue, renders, and turns **ready** with an inline player.
3. Copy the podcast feed link from the UI into your player of choice. Done forever.

Programmatic:

```bash
curl -X POST http://localhost:8330/api/articles \
     -H 'X-Token: unicorn' -H 'Content-Type: application/json' \
     -d '{"url": "https://example.com/a-long-read"}'
```

Bookmarklet (drag to your bookmarks bar, from the UI):

```js
javascript:(()=>{window.open('http://your-slowcast:8330/add?url='+encodeURIComponent(location.href))})()
```

## API

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/api/articles` | `X-Token` | `{ "url": "https://…" }` → `201` queued |
| `GET` | `/api/articles` | `X-Token` | list with status/duration/size |
| `GET` | `/api/articles/:id` | `X-Token` | one article |
| `DELETE` | `/api/articles/:id` | `X-Token` | removes row + audio |
| `POST` | `/api/articles/:id/retry` | `X-Token` | re-render failed or old items |
| `GET` | `/audio/:id.mp3` | – | streams with Range support (seeking works) |
| `GET` | `/feed/:token.xml` | token in path | iTunes-compatible podcast RSS |
| `GET` | `/healthz` | – | for your uptime monitor |

## Configuration (env)

| Variable | Default | Purpose |
|---|---|---|
| `SC_TOKEN` | `change-me` | UI + API secret — **set this** |
| `SC_FEED_TOKEN` | random, persisted | secret in the podcast feed URL |
| `SC_PUBLIC_URL` | `http://localhost:8330` | base URL used in feed enclosures |
| `SC_VOICE` | `en-US-AndrewMultilingualNeural` | any edge-tts voice (`en-GB-SoniaNeural`, `de-DE-KatjaNeural`, …) |
| `SC_DB_PATH` | `slowcast.db` | SQLite location |
| `SC_DATA_DIR` | `data` | generated audio |
| `SC_MAX_CHUNK_CHARS` | `3000` | TTS chunk size |
| `SC_HOST` / `SC_PORT` | `127.0.0.1` / `8330` | bind address |

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check src tests
```

The test suite stubs the network and the speech backend; nothing external is touched. `make`-free, on purpose.

## Design notes

- **One process.** FastAPI app + asyncio worker in the same loop; SQLite in WAL mode. The queue is a table; the worker drains it, one article at a time.
- **Duration without ffprobe.** edge-tts emits word-boundary events with timestamps; the last one *is* the duration. No audio-tooling dependency at runtime — MP3 chunks are concatenated as streams.
- **Range requests by hand** so podcast apps can seek into long episodes (`bytes=` parser, ~30 lines).
- **Failure is a state, not a crash.** Fetch/extract/TTS errors land in the DB with a message; the UI offers one-click retry.

## Roadmap

- [ ] Per-voice playlists / multi-feed channels
- [ ] Intro/outro stitching, playback-rate render presets
- [ ] Email-in submission (mail a link, get an episode)

MIT © Rebwar
