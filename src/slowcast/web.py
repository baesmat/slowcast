"""HTTP layer: JSON API, podcast feed, audio streaming, single-page UI."""

from __future__ import annotations

import html
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from . import db, feed
from .config import Config
from .pipeline import Renderer, Worker, audio_path

COOKIE = "sc_token"
CHUNK = 64 * 1024


class NewArticle(BaseModel):
    url: str


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.ensure_dirs()
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = db.connect(cfg.db_path)
        db.init_db(conn)
        db.feed_token(conn, cfg.feed_token)  # materialize a stable token on first boot
        renderer = Renderer(cfg, conn)
        worker = Worker(renderer)
        worker.start()
        app.state.conn = conn
        app.state.renderer = renderer
        app.state.worker = worker
        yield
        await worker.stop()
        conn.close()

    app = FastAPI(title="Slowcast", version="0.1.0", lifespan=lifespan)

    # ---------------------------------------------------------------- helpers

    def conn(request: Request) -> sqlite3.Connection:  # type: ignore[valid-type]
        return request.app.state.conn  # type: ignore[no-any-return]

    def authorized(request: Request) -> bool:
        if request.headers.get("x-token") == cfg.token:
            return True
        if request.cookies.get(COOKIE) == cfg.token:
            return True
        return False

    def require_auth(request: Request) -> JSONResponse | None:  # type: ignore[valid-type]
        if authorized(request):
            return None
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def feed_url(request: Request) -> str:
        token = db.feed_token(conn(request), cfg.feed_token)
        base = cfg.public_url or str(request.base_url).rstrip("/")
        return f"{base}/feed/{token}.xml"

    # ------------------------------------------------------------- JSON API

    @app.get("/healthz")
    def healthz() -> dict:  # type: ignore[valid-type]
        return {"ok": True}

    @app.post("/api/articles", status_code=201)
    async def add_article(body: NewArticle, request: Request):
        if err := require_auth(request):
            return err
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            return JSONResponse({"error": "url must start with http(s)://"}, status_code=422)
        row = db.add_article(conn(request), url)
        return {"id": row["id"], "url": row["url"], "status": row["status"]}

    @app.get("/api/articles")
    async def list_articles(request: Request):
        if err := require_auth(request):
            return err
        rows = db.list_articles(conn(request))
        return [dict(row) | {"audio_url": f"/audio/{row['id']}.mp3" if row["status"] == "ready" else None} for row in rows]

    @app.get("/api/articles/{article_id}")
    async def get_article(article_id: int, request: Request):
        if err := require_auth(request):
            return err
        row = db.get_article(conn(request), article_id)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return dict(row)

    @app.delete("/api/articles/{article_id}")
    async def delete_article(article_id: int, request: Request):
        if err := require_auth(request):
            return err
        c = conn(request)
        row = db.delete_article(c, article_id)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        path = audio_path(cfg, article_id)
        if path.exists():
            path.unlink()
        return {"deleted": article_id}

    @app.post("/api/articles/{article_id}/retry")
    async def retry_article(article_id: int, request: Request):
        if err := require_auth(request):
            return err
        ok = await request.app.state.renderer.retry(article_id)
        if not ok:
            return JSONResponse({"error": "nothing to retry"}, status_code=404)
        return {"id": article_id, "status": "queued"}

    # ---------------------------------------------------------------- audio

    @app.get("/audio/{article_id}.mp3")
    def audio(article_id: int, request: Request):
        c = conn(request)
        row = db.get_article(c, article_id)
        if row is None or row["status"] != "ready":
            return JSONResponse({"error": "not found"}, status_code=404)
        path = audio_path(cfg, article_id)
        if not path.exists():
            return JSONResponse({"error": "audio missing, retry the article"}, status_code=404)

        size = path.stat().st_size
        range_header = request.headers.get("range")
        if not range_header:
            return _file_response(path, status=200, size=size)

        try:
            start, end = _parse_range(range_header, size)
        except ValueError:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
            )
        return _range_response(path, start, end, size)

    def _file_response(path: Path, status: int, size: int) -> StreamingResponse:
        return _stream(path, 0, size - 1, status)

    def _range_response(path: Path, start: int, end: int, size: int) -> StreamingResponse:
        return _stream(path, start, end, 206, size)

    def _stream(path: Path, start: int, end: int, status: int, size: int | None = None):
        total = size if size is not None else path.stat().st_size
        length = end - start + 1
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": "audio/mpeg",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"

        def iterator():
            with path.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    data = fh.read(min(CHUNK, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(iterator(), status_code=status, headers=headers)

    def _parse_range(header: str, size: int) -> tuple[int, int]:
        value = header.strip()
        if not value.startswith("bytes="):
            raise ValueError("bad range unit")
        spec = value[len("bytes="):]
        first, _, last = spec.partition("-")
        if first == "":
            suffix = int(last)
            if suffix == 0:
                raise ValueError("empty suffix range")
            return max(0, size - suffix), size - 1
        start = int(first)
        if start >= size:
            raise ValueError("range start beyond EOF")
        end = int(last) if last else size - 1
        return start, min(end, size - 1)

    # ----------------------------------------------------------------- feed

    @app.get("/feed/{token}.xml")
    def podcast_feed(token: str, request: Request):
        c = conn(request)
        real = db.feed_token(c, cfg.feed_token)
        if token != real:
            return JSONResponse({"error": "unknown feed token"}, status_code=404)
        base = cfg.public_url or str(request.base_url).rstrip("/")
        xml = feed.render_feed(
            c,
            title="Slowcast",
            link=cfg.public_url or str(request.base_url).rstrip("/"),
            description="Articles I saved, narrated.",
            audio_base_url=base,
        )
        return Response(content=xml, media_type="application/rss+xml; charset=utf-8")

    # ------------------------------------------------------------------- UI

    @app.get("/login", response_class=HTMLResponse)
    def login_page() -> str:  # type: ignore[valid-type]
        return _page(_login_form(), cfg)

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        if str(form.get("token", "")) != cfg.token:
            return HTMLResponse(_page(_login_form(error="wrong token"), cfg), status_code=401)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, cfg.token, httponly=True, samesite="lax")
        return resp

    @app.get("/add", response_class=HTMLResponse)
    def add_form(request: Request, url: str = ""):
        return _page(_add_form(html.escape(url, quote=True)), cfg, request)

    @app.post("/ui/add")
    async def ui_add(request: Request):
        if not authorized(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        url = str(form.get("url", "")).strip()
        if url.startswith(("http://", "https://")):
            db.add_article(conn(request), url)
        return RedirectResponse("/", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if not authorized(request):
            return _page(_login_form(), cfg)
        rows = [dict(r) | {"audio_url": f"/audio/{r['id']}.mp3"} for r in db.list_articles(conn(request))]
        return _page(_list_view(rows, feed_url(request)), cfg)

    return app


# --------------------------------------------------------------------- views

_STYLE = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0e1113;color:#d6d3cd;font:16px/1.55 ui-sans-serif,system-ui,sans-serif}
main{max-width:680px;margin:0 auto;padding:2.5rem 1.25rem 4rem}
h1{font-size:1.5rem;letter-spacing:-.02em}
h1 .slow{color:#7fb069}
.muted{color:#8a8f98;font-size:.85rem}
form.add{display:flex;gap:.5rem;margin:1.2rem 0 1.8rem}
input[type=text],input[type=url]{flex:1;background:#181c1f;border:1px solid #2c3236;border-radius:8px;color:#e8e6e3;padding:.55rem .7rem;font-size:.95rem}
button{background:#2f6f3e;border:0;border-radius:8px;color:#fff;padding:.5rem .9rem;font-weight:600;cursor:pointer}
button.ghost{background:transparent;border:1px solid #2c3236;color:#a7acb2}
.card{background:#15191c;border:1px solid #23282c;border-radius:10px;padding:.9rem 1rem;margin:.7rem 0}
.card .row{display:flex;justify-content:space-between;align-items:baseline;gap:.8rem}
.title{font-weight:600}
.badge{font-size:.7rem;letter-spacing:.05em;text-transform:uppercase;border-radius:99px;padding:.1rem .5rem;border:1px solid}
.badge.queued{color:#d8b247;border-color:#4d4326}.badge.rendering{color:#6aa1c8;border-color:#2b4257}
.badge.ready{color:#7fb069;border-color:#37502c}.badge.error{color:#cf6f5f;border-color:#5c3129}
audio{width:100%;margin-top:.5rem}
.meta{color:#8a8f98;font-size:.78rem;margin-top:.25rem}
.feed{background:#181c1f;border:1px dashed #2c3236;border-radius:8px;padding:.6rem .8rem;font-size:.8rem;margin:1rem 0;word-break:break-all}
.actions{display:flex;gap:.4rem}
.error{color:#cf6f5f;font-size:.85rem}
a{color:#7fb069}
"""

_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{style}</style></head>
<body><main>{body}</main></body></html>"""


def _page(body: str, cfg: Config, request: Request | None = None) -> str:  # type: ignore[valid-type]
    return _SHELL.format(title="Slowcast", style=_STYLE, body=body)


def _login_form(error: str = "") -> str:  # type: ignore[valid-type]
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""
    <h1><span class="slow">🐢</span> Slowcast</h1>
    <p class="muted">Your reading list, narrated.</p>
    <form method="post" action="/login" class="add">
      <input type="password" name="token" placeholder="access token" autofocus>
      <button>Sign in</button>
    </form>{err}"""


def _add_form(url: str) -> str:  # type: ignore[valid-type]
    return f"""
    <h1><span class="slow">🐢</span> Slowcast</h1>
    <form method="post" action="/ui/add" class="add">
      <input type="url" name="url" value="{url}" placeholder="https://article-to-narrate.example" autofocus>
      <button>Queue</button>
    </form>
    <p class="muted"><a href="/">&larr; back to library</a></p>"""


def _status_badge(status: str) -> str:  # type: ignore[valid-type]
    return f'<span class="badge {status}">{status}</span>'


def _fmt_duration(seconds: float) -> str:  # type: ignore[valid-type]
    seconds = int(seconds)
    m, s = divmod(max(seconds, 0), 60)
    return f"{m}:{s:02d}"


def _card(row: dict) -> str:  # type: ignore[valid-type]
    title = html.escape(row["title"] or row["url"])
    meta = []
    if row["duration_s"]:
        meta.append(_fmt_duration(row["duration_s"]))
    if row["size_bytes"]:
        meta.append(f"{row['size_bytes'] // 1024} KB")
    meta.append(f"#{row['id']}")
    player = f'<audio controls preload="none" src="{row["audio_url"]}"></audio>' if row["status"] == "ready" else ""
    err = f'<div class="error">{html.escape(row["error"])}</div>' if row["error"] else ""
    return f"""
    <div class="card" data-id="{row['id']}">
      <div class="row">
        <span class="title">{title}</span>
        {_status_badge(row['status'])}
      </div>
      {player}{err}
      <div class="meta">{" · ".join(meta)}</div>
      <div class="actions" style="margin-top:.5rem">
        <button class="ghost" onclick="retryArticle({row['id']})">retry</button>
        <button class="ghost" onclick="deleteArticle({row['id']})">delete</button>
      </div>
    </div>"""


def _list_view(rows: list[dict], feed_link: str) -> str:  # type: ignore[valid-type]
    cards = "".join(_card(r) for r in rows) or '<p class="muted">Nothing yet — add your first article above.</p>'
    return f"""
    <h1><span class="slow">🐢</span> Slowcast</h1>
    <form class="add" method="post" action="/ui/add">
      <input type="url" name="url" placeholder="https://paste-an-article.here" required>
      <button>Narrate</button>
    </form>
    <div class="feed">Podcast feed: <span id="feed">{html.escape(feed_link)}</span>
      <button class="ghost" style="margin-left:.5rem" onclick="copyFeed()">copy</button></div>
    <div id="list">{cards}</div>
    <p class="muted">Subscribe to the feed in any podcast app; new narrations appear automatically.</p>
    <script>
    const H={{'X-Token': document.cookie.includes('sc_token') ? '' : ''}};
    async function post(url,method){{await fetch(url,{{method,headers:{{'X-Token':getTok()}}}});location.reload();}}
    function retryArticle(id){{post(`/api/articles/${{id}}/retry`,'POST')}}
    function deleteArticle(id){{post(`/api/articles/${{id}}`,'DELETE')}}
    function getTok(){{const m=document.cookie.match(/sc_token=([^;]+)/);return m?decodeURIComponent(m[1]):'';}}
    function copyFeed(){{navigator.clipboard.writeText(document.getElementById('feed').textContent);}}
    </script>"""
