"""Щогодинний новинний випуск: Telegram -> Gemini -> Edge TTS -> GitHub Pages (RSS)."""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import edge_tts
from google import genai
from pydub import AudioSegment
from telethon import TelegramClient
from telethon.sessions import StringSession

import feed

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
WINDOW_MIN = int(os.environ.get("WINDOW_MIN", "60"))
MAX_POSTS = int(os.environ.get("MAX_POSTS", "40"))
KEEP_EPISODES = int(os.environ.get("KEEP_EPISODES", "48"))  # 48 випусків = дві доби

PROMPT = """Ти — ведучий радійних новин УКРАЇНСЬКОЮ мовою.
Нижче пости з новинних каналів (можуть бути російською або українською).

Для кожної окремої новини напиши ОДНЕ коротке речення українською, без вигадування фактів.
Дублікати об'єднуй. Рекламу і пости без новинного змісту пропускай.

Тон обирай залежно від теми:
- Якщо новина стосується війни, обстрілів, тривог, жертв, поранених, аварій,
  катастроф чи будь-якої іншої людської трагедії — подавай її СТРИМАНО і СЕРЙОЗНО,
  без жодного жарту чи гри слів.
- Для решти новин (побутові, культурні, спортивні, курйозні, економічні тощо)
  можна використовувати легку, доброзичливу, жартівливу подачу.
Якщо сумніваєшся, до якої категорії віднести новину — обирай серйозний тон.

Відповідь — лише JSON-масив рядків.

Пости:
{posts}
"""


async def fetch_posts() -> list[str]:
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MIN)
    posts: list[str] = []
    async with client:
        for channel in os.environ["TG_CHANNELS"].split(","):
            async for msg in client.iter_messages(channel.strip(), limit=50):
                if msg.date < cutoff:
                    break
                if msg.text and len(msg.text) > 40:
                    posts.append(msg.text[:1500])
    return posts[:MAX_POSTS]


def summarize(posts: list[str]) -> list[str]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    joined = "\n---\n".join(posts)
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=PROMPT.format(posts=joined),
        config={"response_mime_type": "application/json"},
    )
    items = json.loads(resp.text)
    return [s.strip() for s in items if isinstance(s, str) and s.strip()]


def pick_voice(now: datetime) -> str:
    """Чергування дикторів: парна година — Остап, непарна — Поліна."""
    return "uk-UA-OstapNeural" if now.hour % 2 == 0 else "uk-UA-PolinaNeural"


async def make_audio(lines: list[str], out_path: str) -> float:
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    voice = pick_voice(now)
    intro = f"Новини, {now:%H} година."
    outro = "Це були новини. До зустрічі через годину!"
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate([intro, *lines, outro]):
            p = os.path.join(tmp, f"{i}.mp3")
            await edge_tts.Communicate(text, voice).save(p)
            parts.append(AudioSegment.from_mp3(p))
        pause = AudioSegment.silent(600)
        episode = parts[0]
        for seg in parts[1:]:
            episode += pause + seg
        episode.export(out_path, format="mp3", bitrate="64k")
    return len(episode) / 1000


async def main() -> None:
    posts = await fetch_posts()
    if not posts:
        print("Нових постів немає, пропускаю випуск.")
        return
    lines = summarize(posts)
    if not lines:
        print("Gemini не повернув новин, пропускаю.")
        return
    out = "episode.mp3"
    dur = await make_audio(lines, out)
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    title = f"Новини {now:%d.%m %H:00}"
    cfg = {
        "base_url": os.environ["SITE_BASE_URL"],
        "title": os.environ.get("PODCAST_TITLE", "Години новин"),
        "description": os.environ.get("PODCAST_DESC", "Щогодинний випуск коротких новин з гумором."),
        "email": os.environ["PODCAST_EMAIL"],
    }
    feed.publish(os.environ.get("SITE_DIR", "site"), out, title,
                 "\n".join(f"• {l}" for l in lines), int(dur), now, KEEP_EPISODES, cfg)
    print(f"Готово: {len(lines)} новин, {dur:.0f} с")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("ПОМИЛКА:", repr(e), file=sys.stderr)
        raise
