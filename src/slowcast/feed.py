"""iTunes-compatible podcast RSS generation, no feed library needed."""

from __future__ import annotations

import sqlite3
import time
from email.utils import formatdate
from xml.sax.saxutils import escape

from . import db

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

def _duration(seconds: float) -> str:  # type: ignore[valid-type]
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_feed(
    conn: sqlite3.Connection,
    *,
    title: str,
    link: str,
    description: str,
    audio_base_url: str,
    author: str = "Slowcast",
) -> str:  # type: ignore[valid-type]
    items = []
    for row in db.ready_articles(conn):
        audio_url = f"{audio_base_url}/audio/{row['id']}.mp3"
        pub = formatdate(row["created_at"], localtime=False, usegmt=True)
        items.append(f"""    <item>
      <title>{escape(row['title'] or f"Article #{row['id']}")}</title>
      <guid isPermaLink="false">slowcast-article-{row['id']}</guid>
      <pubDate>{pub}</pubDate>
      <description>{escape(row['title'] or '')}</description>
      <enclosure url="{escape(audio_url)}" length="{row['size_bytes']}" type="audio/mpeg"/>
      <itunes:duration>{_duration(row['duration_s'])}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")

    last_build = formatdate(time.time(), localtime=False, usegmt=True)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:itunes="{ITUNES_NS}" version="2.0">
  <channel>
    <title>{escape(title)}</title>
    <link>{escape(link)}</link>
    <description>{escape(description)}</description>
    <language>en-us</language>
    <lastBuildDate>{last_build}</lastBuildDate>
    <itunes:author>{escape(author)}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    {chr(10).join(items)}
  </channel>
</rss>
"""
