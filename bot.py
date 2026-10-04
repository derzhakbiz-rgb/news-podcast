"""Регулярний новинний випуск: Telegram -> Gemini -> Edge TTS -> GitHub Pages (RSS)."""
import asyncio
import json
import re
import time
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import edge_tts
from google import genai
from google.genai import errors as genai_errors
from pydub import AudioSegment
from telethon import TelegramClient
from telethon.sessions import StringSession

import feed

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "")  # необов'язково: запасна модель
WINDOW_MIN = int(os.environ.get("WINDOW_MIN", "120"))
MAX_POSTS = int(os.environ.get("MAX_POSTS", "40"))
KEEP_EPISODES = int(os.environ.get("KEEP_EPISODES", "24"))  # 24 випуски = дві доби при запуску раз на 2 години
MIN_SENTENCES = int(os.environ.get("MIN_SENTENCES", "1"))  # мінімум речень у пості (1 = одне речення вже ок)

PROMPT = """Ти — ведучий радійних новин УКРАЇНСЬКОЮ мовою.
Нижче пости з новинних каналів (можуть бути російською або українською).

Для кожної окремої новини напиши ОДНЕ коротке речення українською, без вигадування фактів.
Дублікати об'єднуй.

МОВА (обов'язково): якщо пост російською — переклади його зміст на українську.
Уся відповідь має бути ЛИШЕ українською: жодного російського слова і жодних літер ы, э, ъ, ё.
Власні назви пиши за українською нормою (Київ, а не Киев; Харків, а не Харьков).

Пропускай: рекламу, заклики підписатися, надіслати новину, донатити, службові підписи
та пости без новинного змісту.

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


URL_RE = re.compile(r"https?://\S+|www\.\S+|t\.me/\S+", re.I)
SENT_SPLIT = re.compile(r"[.!?\u2026]+(?:\s|$)|\n+")
# Заклики підписатися тощо (навмисно вузький шаблон, щоб не зачепити "підписав закон")
PROMO_RE = re.compile(
    r"(під|под)пис(уйт\w*|уйся|уйсь|ись|атись|атися|аться|ывайт\w*|ывайся)"
    r"|subscribe"
    r"|наш(ого|ому)?\s+(канал|чат|бот|телеграм)\w*"
    r"|(надішл|надсил|присл|пришл|отправ)\w*\s+(нам\s+)?(новин|новост)\w*"
    r"|чита(й|йте)\s+нас",
    re.I,
)


def strip_promo(text: str) -> str:
    """Видаляє речення/рядки-заклики (\"підписуйтесь на наш канал\" тощо)."""
    out = []
    for line in text.splitlines():
        sents = re.split(r"(?<=[.!?\u2026])\s+", line)
        kept = [x for x in sents if not PROMO_RE.search(x)]
        if kept:
            out.append(" ".join(kept))
    return "\n".join(out).strip()


def clean_post(text: str) -> str:
    t = URL_RE.sub(" ", text)
    t = strip_promo(t)
    t = re.sub(r"[#@]\S+", " ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def is_substantial(clean: str) -> bool:
    """False, якщо після очищення нічого не лишилось або лишилось менше MIN_SENTENCES речень."""
    if not re.search(r"\w", clean):
        return False
    parts = [p for p in SENT_SPLIT.split(clean) if len(p.split()) >= 3]
    return len(parts) >= MIN_SENTENCES


async def fetch_posts() -> list[str]:
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MIN)
    posts: list[str] = []
    skipped = 0
    async with client:
        for channel in os.environ["TG_CHANNELS"].split(","):
            async for msg in client.iter_messages(channel.strip(), limit=50):
                if msg.date < cutoff:
                    break
                if not msg.text:  # пост без тексту (самі картинка/відео)
                    continue
                clean = clean_post(msg.text)
                if not is_substantial(clean):
                    skipped += 1
                    continue
                posts.append(clean[:1500])
    print(f"Пропущено порожніх/коротких постів: {skipped}, взято: {len(posts)}")
    return posts[:MAX_POSTS]


RU_LETTERS = re.compile(r"[ыэъёЫЭЪЁ]")  # літер немає в українській абетці
UA_LETTERS = re.compile(r"[іїєґІЇЄҐ]")  # у російській їх немає


def looks_russian(line: str) -> bool:
    """Підозра на російську: літери ы/э/ъ/ё, або довге речення без жодної і/ї/є/ґ."""
    return bool(RU_LETTERS.search(line)) or (len(line) > 40 and not UA_LETTERS.search(line))


STRICT_NOTE = ("\n\nУВАГА: у попередній відповіді були російські слова. Перевір кожне речення: "
               "лише українська мова, жодних літер ы, э, ъ, ё.")


RETRY_DELAYS = [20, 40, 80, 120]  # секунди між повторами при 503/429


def _generate(client, prompt: str):
    """Запит до Gemini з повторами: 503 (перевантаження) і 429 (ліміт) зазвичай тимчасові."""
    models = [GEMINI_MODEL] + ([GEMINI_FALLBACK_MODEL] if GEMINI_FALLBACK_MODEL else [])
    last = None
    for model in models:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
            except (genai_errors.ServerError, genai_errors.ClientError) as e:
                code = getattr(e, "code", None)
                if isinstance(e, genai_errors.ClientError) and code != 429:
                    raise  # 400/403/404 повтором не лікуються
                last = e
                if attempt < len(RETRY_DELAYS):
                    print(f"{model}: помилка {code}, чекаю {RETRY_DELAYS[attempt]} с і пробую знову")
                    time.sleep(RETRY_DELAYS[attempt])
    raise last


def _ask(client, posts: list[str], strict: bool) -> list[str]:
    prompt = PROMPT.format(posts="\n---\n".join(posts))
    if strict:
        prompt += STRICT_NOTE
    resp = _generate(client, prompt)
    items = json.loads(resp.text)
    return [x.strip() for x in items if isinstance(x, str) and x.strip()]


def summarize(posts: list[str]) -> list[str]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    items = _ask(client, posts, strict=False)
    if any(looks_russian(x) for x in items):
        print("Підозра на російську мову у відповіді, повторюю запит суворіше")
        items = _ask(client, posts, strict=True)
    return [x for x in items if not RU_LETTERS.search(x)]


def pick_voice(now: datetime) -> str:
    """Чергування дикторів при запуску раз на 2 години: щоразу інший голос."""
    return "uk-UA-OstapNeural" if (now.hour // 2) % 2 == 0 else "uk-UA-PolinaNeural"


async def make_audio(lines: list[str], out_path: str) -> float:
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    voice = pick_voice(now)
    intro = f"Новини, {now:%H} година."
    outro = "Це були новини. До зустрічі за дві години!"
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
