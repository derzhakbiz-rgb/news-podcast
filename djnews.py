"""Свіжі матеріали музичних видань (RSS) для блоку «новини про топових діджеїв».
Лише стандартна бібліотека. Беремо заголовок і коротку анотацію; у ефір іде власний переказ одним
реченням із посиланням на джерело в описі випуску."""
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (compatible; KexFM-NewsBot/1.0)"


def _text(fragment: str) -> str:
    t = html.unescape(re.sub(r"(?s)<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", t).strip()


def _clean(text: str, limit: int = 240) -> str:
    """Перший абзац із реальним текстом (пропускає картинки, «Continue reading», «The post ... appeared»)."""
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text or "")
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", text) or [text]
    for p in paras:
        t = _text(p)
        if t and not re.match(r"(?i)(continue reading|the post\b)", t):
            return t[:limit]
    return ""


def _source_name(url: str, title: str | None) -> str:
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return host or (title or "feed")


def parse_feed(xml_bytes: bytes, url: str = "") -> list[dict]:
    """Розбирає RSS 2.0. Дата може бути порожньою (Mixmag) — тоді date=None."""
    root = ET.fromstring(xml_bytes)
    chan = root.find("channel")
    if chan is None:
        return []
    source = _source_name(url, (chan.findtext("title") or "").strip())
    items = []
    for it in chan.findall("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        if not title:
            continue
        date = None
        raw = (it.findtext("pubDate") or "").strip()
        if raw:
            try:
                d = parsedate_to_datetime(raw)
                date = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                date = None
        items.append({"source": source, "title": title, "summary": _clean(it.findtext("description") or ""),
                      "link": (it.findtext("link") or "").strip(), "date": date})
    return items


def fetch_feed(url: str, timeout: int = 20) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_feed(r.read(), url)


def recent(feeds: list[str], days: int = 7, per_feed: int = 15, max_total: int = 30,
           now: datetime | None = None) -> list[dict]:
    """Датовані матеріали — лише за останні `days` днів; недатовані (стрічка без pubDate) — перші per_feed
    як «найновіші в стрічці». Збої окремих стрічок не зупиняють роботу."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    dated, undated = [], []
    for url in feeds:
        try:
            items = fetch_feed(url)
        except Exception as e:
            print(f"DJ-стрічка недоступна ({url}): {repr(e)[:120]}")
            continue
        for it in items[:per_feed]:
            if it["date"] is None:
                undated.append(it)
            elif it["date"] >= cutoff:
                dated.append(it)
    dated.sort(key=lambda x: x["date"], reverse=True)
    return (dated + undated)[:max_total]


def prompt_list(items: list[dict]) -> str:
    out = []
    for i, it in enumerate(items, 1):
        when = it["date"].strftime("%Y-%m-%d") if it["date"] else "дата невідома, серед найновіших у стрічці"
        out.append(f"{i}. [{it['source']}, {when}] {it['title']} — {it['summary']}")
    return "\n".join(out)
