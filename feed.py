"""Публікація епізоду на GitHub Pages: кладе mp3 у папку сайту, оновлює feed.xml,
видаляє старі випуски. Тільки стандартна бібліотека."""
import html
import json
import os
from datetime import datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape


def _hms(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build_feed(episodes: list[dict], cfg: dict) -> str:
    base = cfg["base_url"].rstrip("/")
    explicit = "true" if cfg.get("explicit") else "false"
    email = (cfg.get("email") or "").strip()
    owner = (f"    <itunes:owner>\n      <itunes:name>{escape(cfg['title'])}</itunes:name>\n"
             f"      <itunes:email>{escape(email)}</itunes:email>\n    </itunes:owner>\n") if email else ""
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
      <itunes:explicit>{explicit}</itunes:explicit>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{escape(cfg['title'])}</title>
    <link>{escape(base)}/</link>
    <description>{escape(cfg['description'])}</description>
    <language>uk</language>
    <itunes:author>{escape(cfg['title'])}</itunes:author>
{owner}    <itunes:image href="{escape(base)}/cover.jpg"/>
    <itunes:category text="News"/>
    <itunes:explicit>{explicit}</itunes:explicit>
{chr(10).join(items)}
  </channel>
</rss>
"""


def write_index(site_dir: str, episodes: list[dict], cfg: dict) -> None:
    """Проста головна сторінка сайту: Telegram-посилання, RSS і список випусків із плеєрами."""
    e = html.escape
    donate = cfg.get("donate_url", "").strip()
    tg_html = (f'<a class="btn" href="{e(donate)}">Підтримати розвиток станції</a>' if donate else "")
    rows = []
    for ep in episodes:
        rows.append(
            f'<section><h3>{e(ep["title"])}</h3><audio controls preload="none" src="{e(ep["file"])}"></audio>'
            f'<details><summary>Про що випуск</summary><pre>{e(ep["description"])}</pre></details></section>')
    credit = ('<p class="muted">Погода: дані <a href="https://open-meteo.com/">Open-Meteo.com</a>.</p>'
              if cfg.get("weather_credit") else "")
    page = f"""<!doctype html><html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(cfg['title'])}</title>
<style>:root{{color-scheme:dark light}}body{{font:16px/1.5 system-ui,sans-serif;max-width:720px;margin:0 auto;padding:20px}}
section{{border:1px solid #8884;border-radius:12px;padding:12px 16px;margin:12px 0}}h3{{margin:0 0 8px}}
audio{{width:100%}}.btn{{display:inline-block;padding:10px 16px;border-radius:10px;background:#2a7fff;color:#fff;text-decoration:none;margin-right:8px}}
pre{{white-space:pre-wrap;font:inherit}}.muted{{opacity:.7;font-size:14px}}</style></head><body>
<h1>{e(cfg['title'])}</h1><p>{e(cfg['description'])}</p>
<p>{tg_html}<a class="btn" style="background:#555" href="feed.xml">RSS-стрічка</a></p>
{"".join(rows)}{credit}</body></html>"""
    with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def last_meta(site_dir: str) -> dict:
    """Службові дані останнього випуску (ведуча, які пости вже прозвучали), якщо вони є."""
    try:
        with open(os.path.join(site_dir, "episodes.json"), encoding="utf-8") as fh:
            eps = json.load(fh)
        eps.sort(key=lambda e: e["pub"], reverse=True)
        return dict(eps[0].get("meta") or {}) if eps else {}
    except (OSError, ValueError, KeyError, IndexError):
        return {}


def publish(site_dir: str, mp3_path: str, title: str, description: str,
            duration: int, now: datetime, keep: int, cfg: dict, meta: dict | None = None) -> None:
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
        "size": os.path.getsize(dest), "meta": meta or {},
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
    write_index(site_dir, episodes, cfg)
    print(f"У фіді {len(episodes)} випусків")
