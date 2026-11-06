from __future__ import annotations

import xml.etree.ElementTree as ET

import conftest
from slowcast import db

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def _post(client, url="https://example.com/post"):
    return client.post("/api/articles", json={"url": url}, headers={"X-Token": "testtoken"})


def _app(client):
    return client.app


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_api_requires_token(client):
    assert client.post("/api/articles", json={"url": "http://x"}).status_code == 401
    assert client.get("/api/articles").status_code == 401
    assert client.get("/api/articles/1").status_code == 401
    assert client.delete("/api/articles/1").status_code == 401
    assert client.post("/api/articles/1/retry").status_code == 401


def test_create_and_get_article(client):
    conftest.auth(client)
    r = _post(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued"
    assert client.get(f"/api/articles/{body['id']}").json()["url"] == "https://example.com/post"


def test_create_rejects_bad_url(client):
    conftest.auth(client)
    assert client.post("/api/articles", json={"url": "ftp://nope"}).status_code == 422
    assert client.post("/api/articles", json={"url": "not a url"}).status_code == 422


def test_get_and_delete_missing_404(client):
    conftest.auth(client)
    assert client.get("/api/articles/999").status_code == 404
    assert client.delete("/api/articles/999").status_code == 404


def test_retry_only_valid_states(client):
    conftest.auth(client)
    assert client.post("/api/articles/999/retry").status_code == 404
    aid = _post(client).json()["id"]
    # queued articles cannot be retried
    assert client.post(f"/api/articles/{aid}/retry").status_code == 404
    # error articles can
    db.update_article(_app(client).state.conn, aid, status="error", error="x")
    assert client.post(f"/api/articles/{aid}/retry").json() == {"id": aid, "status": "queued"}


def test_delete_removes_row(client):
    conftest.auth(client)
    aid = _post(client).json()["id"]
    assert client.delete(f"/api/articles/{aid}").json() == {"deleted": aid}
    assert client.get(f"/api/articles/{aid}").status_code == 404


def test_feed_token_enforced(client):
    assert client.get("/feed/wrongtoken.xml").status_code == 404
    r = client.get("/feed/feedtoken123.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/rss+xml")
    assert ET.fromstring(r.content).tag == "rss"


def _make_ready(client, aid, title="A great piece"):
    data_dir = _app(client).state.renderer.cfg.data_dir
    audio = data_dir / "audio" / f"article-{aid}.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"0123456789" * 10)
    db.update_article(
        _app(client).state.conn,
        aid,
        status="ready",
        title=title,
        duration_s=95,
        size_bytes=100,
        audio_file=f"audio/article-{aid}.mp3",
    )
    return audio


def test_feed_lists_ready_articles_with_enclosure(client):
    conftest.auth(client)
    aid = _post(client).json()["id"]
    _make_ready(client, aid)

    root = ET.fromstring(client.get("/feed/feedtoken123.xml").content)
    items = root.findall(".//item")
    assert len(items) == 1
    enc = items[0].find("enclosure")
    assert enc is not None
    assert enc.get("type") == "audio/mpeg"
    assert enc.get("length") == "100"
    assert enc.get("url", "").endswith(f"/audio/{aid}.mp3")
    assert items[0].findtext("title") == "A great piece"
    assert items[0].findtext(f"{ITUNES}duration") == "1:35"


def test_feed_only_ready(client):
    conftest.auth(client)
    _post(client, "https://example.com/queued-only")
    root = ET.fromstring(client.get("/feed/feedtoken123.xml").content)
    assert root.findall(".//item") == []


def test_audio_full_and_ranges(client):
    conftest.auth(client)
    aid = _post(client).json()["id"]
    audio = _make_ready(client, aid)
    size = len(audio.read_bytes())

    r = client.get(f"/audio/{aid}.mp3")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == b"0123456789" * 10

    r = client.get(f"/audio/{aid}.mp3", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 2-5/{size}"
    assert r.content == b"2345"

    r = client.get(f"/audio/{aid}.mp3", headers={"Range": "bytes=-3"})
    assert r.status_code == 206
    assert r.content == b"789"

    r = client.get(f"/audio/{aid}.mp3", headers={"Range": "bytes=15-99"})
    assert r.status_code == 206
    data = b"0123456789" * 10
    assert r.content == data[15:]  # clamped to EOF

    r = client.get(f"/audio/{aid}.mp3", headers={"Range": "bytes=999999-"})
    assert r.status_code == 416


def test_audio_missing_file_404(client):
    conftest.auth(client)
    aid = _post(client).json()["id"]
    db.update_article(_app(client).state.conn, aid, status="ready")
    r = client.get(f"/audio/{aid}.mp3")
    assert r.status_code == 404
    assert "retry" in r.json()["error"]


def test_ui_login_flow(client):
    # wrong token keeps you on the login page
    r = client.post("/login", data={"token": "nope"}, follow_redirects=False)
    assert r.status_code == 401
    # correct token sets the cookie and redirects
    r = client.post("/login", data={"token": "testtoken"}, follow_redirects=False)
    assert r.status_code == 303
    assert "sc_token" in r.headers["set-cookie"]
    # with the cookie, the library renders
    conftest.auth(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "Slowcast" in page.text
    assert "feed/feedtoken123.xml" in page.text
