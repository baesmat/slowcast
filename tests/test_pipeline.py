from __future__ import annotations

import asyncio

import pytest

import conftest  # noqa: F401  (path setup via rootdir)
from slowcast import db
from slowcast.extract import ExtractError, _fallback_title, extract
from slowcast.tts import SpeechResult

PAGE = """
<html><head><title>Test Page &amp; Notes</title></head>
<body><article><h1>Test Page</h1>
<p>This is the first paragraph with enough words to be extracted properly by the parser.</p>
<p>Second paragraph follows here with more interesting content about turtles and patience.</p>
</article></body></html>
"""


def test_extract_pulls_title_and_text():
    article = extract(PAGE, url="http://example.com/x")
    assert "Test Page" in article.title
    assert "turtles" in article.text
    assert "<p>" not in article.text


def test_extract_fails_on_empty_body():
    with pytest.raises(ExtractError):
        extract("<html><head><title>t</title></head><body></body></html>", url="http://x")


def test_fallback_title_strips_tags():
    assert _fallback_title("<title>Plain <b>bold</b>  title</title>") == "Plain bold title"
    assert _fallback_title("<html>") == ""


def make_renderer(tmp_path, monkeypatch, *, fetch=None, synth=None):
    cfg = conftest.make_cfg(tmp_path)
    cfg.ensure_dirs()
    import slowcast.pipeline as pipeline_mod

    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    if fetch is not None:
        monkeypatch.setattr(pipeline_mod, "fetch", fetch)
    if synth is not None:
        monkeypatch.setattr(pipeline_mod, "synthesize", synth)
    return cfg, pipeline_mod.Renderer(cfg, conn)


def test_pipeline_success(tmp_path, monkeypatch):
    async def fake_fetch(url, timeout=25.0):
        return "http://final/", PAGE

    async def fake_synth(chunks, voice, rate="+0%"):
        assert chunks, "chunks must be non-empty"
        return SpeechResult(b"mp3-bytes", 42.5)

    cfg, renderer = make_renderer(tmp_path, monkeypatch, fetch=fake_fetch, synth=fake_synth)
    row = db.add_article(conn := renderer.conn, "http://example.com/a")

    asyncio.run(renderer.process(row["id"]))

    row = db.get_article(conn, row["id"])
    assert row["status"] == "ready"
    assert row["duration_s"] == 42.5
    assert row["size_bytes"] == len(b"mp3-bytes")
    assert "Test Page" in row["title"]
    assert (cfg.audio_dir / f"article-{row['id']}.mp3").read_bytes() == b"mp3-bytes"


def test_pipeline_fetch_failure_marks_error(tmp_path, monkeypatch):
    async def bad_fetch(url, timeout=25.0):
        raise ExtractError("fetch failed: boom")

    async def fail_synth(*a, **k):  # pragma: no cover
        raise AssertionError("should not be reached")

    cfg, renderer = make_renderer(tmp_path, monkeypatch, fetch=bad_fetch, synth=fail_synth)
    row = db.add_article(renderer.conn, "http://example.com/b")

    asyncio.run(renderer.process(row["id"]))

    row = db.get_article(renderer.conn, row["id"])
    assert row["status"] == "error"
    assert "boom" in row["error"]


def test_step_noop_when_queue_empty(tmp_path, monkeypatch):
    _, renderer = make_renderer(tmp_path, monkeypatch)
    assert asyncio.run(renderer.step()) is False


def test_retry_resets_state(tmp_path, monkeypatch):
    cfg, renderer = make_renderer(tmp_path, monkeypatch)
    from slowcast.pipeline import audio_path

    row = db.add_article(renderer.conn, "http://example.com/c")
    db.update_article(renderer.conn, row["id"], status="error", error="old")
    path = audio_path(cfg, row["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stale")

    assert asyncio.run(renderer.retry(row["id"])) is True
    row = db.get_article(renderer.conn, row["id"])
    assert row["status"] == "queued"
    assert row["error"] == ""
    assert not path.exists()
    # retrying a queued article is a no-op
    assert asyncio.run(renderer.retry(row["id"])) is False
