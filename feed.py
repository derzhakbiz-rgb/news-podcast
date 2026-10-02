"""Публікація епізоду на GitHub Pages: кладе mp3 у папку сайту, оновлює feed.xml,
видаляє старі випуски. Тільки стандартна бібліотека."""
import json
import os
from datetime import datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape


def _hms(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build_feed(episodes: list[dict], cfg: dict) -> str:
    base = cfg["base_url"].rstrip("/")
    items = []
    for e in episodes:
        pub = format_datetime(datetime.fromisoformat(e["pub"]))
        url = f"{base}/{e['file']}"
        items.append(f"""    <item>
      <title>{escape(e['title'])}</title>
      <description>{escape(e['description'])}</description>
      <pubDate>{pub}</pubDate>
      <guid isPermaLink="false">{escape(e['file'])}</guid>
      <enclosure url="{escape(url)}" length="{e['size']}" type="audio/mpeg"/>
      <itunes:duration>{_hms(e['duration'])}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{escape(cfg['title'])}</title>
    <link>{escape(base)}/</link>
    <description>{escape(cfg['description'])}</description>
    <language>uk</language>
    <itunes:author>{escape(cfg['title'])}</itunes:author>
    <itunes:owner>
      <itunes:name>{escape(cfg['title'])}</itunes:name>
      <itunes:email>{escape(cfg['email'])}</itunes:email>
    </itunes:owner>
    <itunes:image href="{escape(base)}/cover.jpg"/>
    <itunes:category text="News"/>
    <itunes:explicit>false</itunes:explicit>
{chr(10).join(items)}
  </channel>
</rss>
"""


def publish(site_dir: str, mp3_path: str, title: str, description: str,
            duration: int, now: datetime, keep: int, cfg: dict) -> None:
    os.makedirs(site_dir, exist_ok=True)
    manifest_path = os.path.join(site_dir, "episodes.json")
    episodes: list[dict] = []
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            episodes = json.load(f)

    fname = f"ep_{now:%Y%m%d_%H}.mp3"
    dest = os.path.join(site_dir, fname)
    with open(mp3_path, "rb") as src, open(dest, "wb") as dst:
        dst.write(src.read())

    episodes = [e for e in episodes if e["file"] != fname]  # повторний запуск тієї ж години
    episodes.append({
        "file": fname, "title": title, "description": description,
        "pub": now.isoformat(), "duration": duration,
        "size": os.path.getsize(dest),
    })
    episodes.sort(key=lambda e: e["pub"], reverse=True)

    for old in episodes[keep:]:
        p = os.path.join(site_dir, old["file"])
        if os.path.exists(p):
            os.remove(p)
            print("Видалено старий випуск:", old["title"])
    episodes = episodes[:keep]

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(episodes, f, ensure_ascii=False, indent=1)
    with open(os.path.join(site_dir, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(build_feed(episodes, cfg))
    print(f"У фіді {len(episodes)} випусків")
