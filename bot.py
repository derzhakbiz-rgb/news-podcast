"""Регулярний новинний випуск: Telegram + DJ-стрічки -> Gemini -> озвучення + джингли -> GitHub Pages (RSS)."""
import asyncio
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import edge_tts
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from telethon import TelegramClient
from telethon.sessions import StringSession

import feed
import radio
import weather

try:  # httpx ставиться разом із google-genai; ловимо обриви з'єднання і таймаути
    import httpx
    TRANSPORT_ERRORS = (httpx.TransportError,)
except ImportError:
    TRANSPORT_ERRORS = ()

def _load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("config.json не прочитано, беру типові значення:", repr(e)[:150])
        return {}


CFG = _load_config(os.environ.get("CONFIG_PATH") or "config.json")


def opt(key: str, env: str, default):
    """Значення з панелі (config.json) > змінна середовища > типове."""
    v = CFG.get(key)
    if v is None or v == "":
        v = os.environ.get(env)
    if v is None or v == "":
        return default
    if isinstance(default, bool):
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


ENABLED = opt("enabled", "BOT_ENABLED", True)
CHANNELS = opt("channels", "TG_CHANNELS", "")
GEMINI_MODEL = opt("gemini_model", "GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = opt("gemini_fallback_model", "GEMINI_FALLBACK_MODEL",
                           "gemini-3.5-flash-lite,gemini-3.6-flash")  # запасні моделі через кому
NEWS_WINDOW_MIN = opt("news_window_min", "NEWS_WINDOW_MIN", 180)  # новини за останні 3 години
DEDUPE = opt("dedupe", "DEDUPE", True)                      # не повторювати пости з попереднього випуску
REPEAT_WHEN_EMPTY = opt("repeat_when_empty", "REPEAT_WHEN_EMPTY", True)  # немає нового — повтор останніх 3 год
MAX_POSTS = opt("max_posts", "MAX_POSTS", 40)
MIN_SENTENCES = opt("min_sentences", "MIN_SENTENCES", 1)
KEEP_EPISODES = opt("keep_episodes", "KEEP_EPISODES", 24)
TTS_ENGINE = opt("tts_engine", "TTS_ENGINE", "gemini").lower()  # gemini | edge
TTS_MODEL = opt("tts_model", "TTS_MODEL", "gemini-2.5-flash-preview-tts")
TTS_MAX_BYTES = opt("tts_max_bytes", "TTS_MAX_BYTES", 3900)  # ліміт на один запит озвучення (стиль + текст)
ASSET_DIR = opt("asset_dir", "ASSET_DIR", "assets")
MP3_BITRATE = opt("mp3_bitrate", "MP3_BITRATE", "192k")  # стерео-mp3, кбіт/с
BED_ENABLED = opt("bed_enabled", "BED_ENABLED", True)
BED_UNDER_DB = opt("bed_under_db", "BED_UNDER_DB", -20.0)  # фон під голосом, дБ відносно голосу
BED_GAP_DB = opt("bed_gap_db", "BED_GAP_DB", -12.0)        # фон у паузах, дБ відносно голосу
JINGLES_ENABLED = opt("jingles_enabled", "JINGLES_ENABLED", True)
JINGLE_GAIN_DB = opt("jingle_gain_db", "JINGLE_GAIN_DB", 0.0)
STING_GAIN_DB = opt("sting_gain_db", "STING_GAIN_DB", 0.0)
# Звукові пакети за часом доби ефіру (назви — у soundpacks.py)
PACK_BY_PART = {
    "ранок": opt("pack_morning", "PACK_MORNING", "tech_house"),
    "день": opt("pack_day", "PACK_DAY", "neutral_house"),
    "вечір": opt("pack_evening", "PACK_EVENING", "deep_house"),
    "ніч": opt("pack_night", "PACK_NIGHT", "night_chill"),
}
WEATHER_ENABLED = opt("weather_enabled", "WEATHER_ENABLED", True)
WEATHER_WHERE = opt("weather_where", "WEATHER_WHERE", "у Києві")
WEATHER_LAT = opt("lat", "WEATHER_LAT", 50.45)
WEATHER_LON = opt("lon", "WEATHER_LON", 30.52)
SQUALL_MS = opt("squall_ms", "SQUALL_MS", float(weather.SQUALL_MS))  # пориви, від яких згадуємо шквал
DONATE_URL = opt("donate_url", "DONATE_URL", "https://keksfm.kiev.ua")
DONATE_TEXT = opt("donate_text", "DONATE_TEXT",
                  "Підтримайте розвиток станції донатом: посилання на нашому сайті, кекс еф ем, крапка, кієв, крапка, ю а.")
FEED_EXPLICIT = opt("rss_explicit", "RSS_EXPLICIT", False)  # позначка «explicit» у RSS: за замовчуванням вимкнена
EXTRA_INSTRUCTIONS = opt("extra_instructions", "EXTRA_INSTRUCTIONS", "")
VOICE_DB = -19.0
# Ведучі (дві жінки), чергуються: завжди береться «наступна» після тієї, що вела попередній випуск.
NAME_1 = opt("name_1", "NAME_1", "Ангеліна")
VOICE_1 = opt("voice_1", "VOICE_1", "Zephyr")  # Bright — дзвінкий, світлий голос
SPEED_1 = opt("speed_1", "SPEED_1", 0.95)
PITCH_1 = opt("pitch_1", "PITCH_1", 0.0)
STYLE_1 = opt("style_1", "STYLE_1",
              "Read as Angelina, a radio news host with a bright, clear, ringing and beautiful voice: friendly, "
              "lively and elegant, crisp diction, a smile in the voice, moderate unhurried pace, correct Ukrainian "
              "word stress, a one-second pause between items; for sad or war-related items speak calmly and seriously:")
NAME_2 = opt("name_2", "NAME_2", "Лера")
VOICE_2 = opt("voice_2", "VOICE_2", "Sulafat")  # Warm — природний, приємний голос
SPEED_2 = opt("speed_2", "SPEED_2", 0.95)
PITCH_2 = opt("pitch_2", "PITCH_2", 0.0)
STYLE_2 = opt("style_2", "STYLE_2",
              "Read as Lera, a radio news host with a natural, pleasant and beautiful voice: warm, clear and "
              "confident, even pace, correct Ukrainian word stress, a one-second pause between items; "
              "for sad or war-related items speak calmly and seriously:")
STATION = opt("station", "STATION", "Служба новин Кекс-радіокомпані")  # вимовляється дослівно, не відмінюється

PROMPT = """Ти — редакторка і ведуча випуску новин молодіжного танцювального радіо Кекс ФМ.
Говориш УКРАЇНСЬКОЮ мовою. Нижче пости з новинних каналів (можуть бути російською або українською).

ЗАВДАННЯ 1 — новини. Для кожної окремої новини напиши ОДНЕ коротке речення українською,
без вигадування фактів. Дублікати об'єднуй. Пропускай рекламу, заклики підписатися,
надіслати новину, донатити, службові підписи та пости без новинного змісту.

МОВА (обов'язково): якщо пост російською — переклади його зміст на українську.
Уся відповідь має бути ЛИШЕ українською: жодного російського слова і жодних літер ы, э, ъ, ё.
Власні назви пиши за українською нормою (Київ, а не Киев; Харків, а не Харьков).

ПОДАЧА: спокійна, тепла, професійна. БЕЗ жартів, іронії, каламбурів і сленгу — для всіх новин однаково.
Новини про війну, обстріли, жертв, аварії та іншу трагедію подавай стримано й серйозно.

ЗАВДАННЯ 2 — привітання (intro). Ведуча цього випуску: {host} (жінка), узгоджуй рід дієслів.
Напиши 1–2 короткі речення: назви себе ЛИШЕ у формі «З вами {host}» або «Це {host}» і назви
«{station}» ДОСЛІВНО (без відмінювання і без змін у написанні), привітайся відповідно до часу доби
(зараз {part}). НІКОЛИ не вживай «мене звати», «моє ім'я» чи подібні звороти. Манера цього разу: {vibe}.
НЕ називай конкретний час, години, хвилини, дату чи день тижня. Без жартів. Щоразу нове привітання,
без штампів на кшталт «шановні слухачі».

ЗАВДАННЯ 3 — прощання (outro). 1–2 короткі речення: згадай «Кекс ФМ», обов'язково побажай «{wish}»
(саме цими словами), назви себе у формі «З вами була {host}» (ніколи не «мене звати»). Без жартів
і без конкретного часу. Заклик до донату НЕ додавай: він звучить окремо перед прощанням.
{weather_task}{extra}
Відповідь — лише JSON-об'єкт з ключами {keys}.

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


async def fetch_posts(prev_keys: frozenset = frozenset()):
    """Повертає (тексти, ключі, режим, дата найновішого посту).
    Режим fresh — нові пости за останні NEWS_WINDOW_MIN хвилин (без тих, що вже звучали в попередньому випуску).
    Режим repeat — нового немає: беремо останні NEWS_WINDOW_MIN хвилин новин ДО найновішого посту каналу."""
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    if not CHANNELS.strip():
        raise SystemExit("Не задано канали-джерела (панель: Настройки -> Источники, або секрет TG_CHANNELS)")
    cand, skipped = [], 0
    async with client:
        for channel in [c.strip() for c in CHANNELS.split(",") if c.strip()]:
            async for msg in client.iter_messages(channel, limit=80):
                if not msg.text:  # пост без тексту (самі картинка/відео)
                    continue
                clean = clean_post(msg.text)
                if not is_substantial(clean):
                    skipped += 1
                    continue
                cand.append({"key": f"{channel}:{msg.id}", "date": msg.date, "text": clean[:900]})
    window = timedelta(minutes=NEWS_WINDOW_MIN)
    cutoff = datetime.now(timezone.utc) - window
    fresh = [c for c in cand if c["date"] >= cutoff and not (DEDUPE and c["key"] in prev_keys)]
    mode, chosen = "fresh", fresh
    if not fresh:
        if REPEAT_WHEN_EMPTY and cand:
            newest = max(c["date"] for c in cand)
            chosen = [c for c in cand if c["date"] >= newest - window]
            mode = "repeat"
        else:
            mode = "none"
    chosen.sort(key=lambda c: c["date"], reverse=True)
    chosen = chosen[:MAX_POSTS]
    newest_dt = max((c["date"] for c in chosen), default=None)
    print(f"Новини: режим={mode}, відібрано {len(chosen)}, нових за {NEWS_WINDOW_MIN} хв: {len(fresh)}, "
          f"відсіяно порожніх/коротких: {skipped}" + (f", найновіший пост: {newest_dt:%d.%m %H:%M} UTC" if newest_dt else ""))
    return [c["text"] for c in chosen], [c["key"] for c in chosen], mode, newest_dt


RU_LETTERS = re.compile(r"[ыэъёЫЭЪЁ]")  # літер немає в українській абетці
UA_LETTERS = re.compile(r"[іїєґІЇЄҐ]")  # у російській їх немає


def looks_russian(line: str) -> bool:
    """Підозра на російську: літери ы/э/ъ/ё, або довге речення без жодної і/ї/є/ґ."""
    return bool(RU_LETTERS.search(line)) or (len(line) > 40 and not UA_LETTERS.search(line))


# Слова, яких в українській немає. Для привітання, прощання і погоди (текст з нашою лексикою)
# евристика «довге речення без і/ї/є/ґ» дає хибні спрацьовування, тому там перевіряємо лише літери й ці слова.
RU_WORDS = re.compile(r"\b(что|это|как|был[аио]?|быть|или|будет|будут|сегодня|нет|его|есть|где|когда|после|"
                      r"только|если|также|более|очень|можно|может)\b", re.I)


def has_russian(line: str) -> bool:
    return bool(RU_LETTERS.search(line) or RU_WORDS.search(line))


STRICT_NOTE = ("\n\nУВАГА: у попередній відповіді були російські слова. Перевір кожне речення: "
               "лише українська мова, жодних літер ы, э, ъ, ё.")


RETRY_DELAYS = [15, 30, 60]  # секунди між повторами при 503/429/обриві з'єднання (потім — наступна модель)


HTTP_TIMEOUT_S = opt("http_timeout", "HTTP_TIMEOUT", 150)  # таймаут одного запиту до Gemini, секунди


def _client():
    try:
        opts = genai_types.HttpOptions(timeout=int(HTTP_TIMEOUT_S * 1000))
    except Exception:  # стара версія SDK без цього параметра
        return genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=opts)


def _parse_json(text: str):
    """JSON із відповіді моделі; терпимо до огорожі ```json і сміття навколо."""
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


def _model_chain() -> list[str]:
    chain = [GEMINI_MODEL]
    for m in GEMINI_FALLBACK_MODEL.split(","):
        m = m.strip()
        if m and m not in chain:
            chain.append(m)
    return chain


def _generate(client, prompt: str):
    """Запит до Gemini. 503/429 (перевантаження, ліміт) — повтори з паузами; якщо модель так і не
    відповіла або недоступна (403/404) — наступна модель із ланцюжка."""
    errors = []
    for model in _model_chain():
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                print(f"Gemini: відповіла модель {model}")
                return resp
            except (genai_errors.ServerError, genai_errors.ClientError, *TRANSPORT_ERRORS) as e:
                code = getattr(e, "code", None) or type(e).__name__
                if isinstance(e, genai_errors.ClientError) and code != 429:
                    errors.append(f"{model}: {code}")
                    print(f"{model}: помилка {code}, ця модель недоступна")
                    break  # повтором не лікується -> наступна модель
                if attempt < len(RETRY_DELAYS):
                    print(f"{model}: помилка {code}, чекаю {RETRY_DELAYS[attempt]} с і пробую знову")
                    time.sleep(RETRY_DELAYS[attempt])
                else:
                    errors.append(f"{model}: {code} після {len(RETRY_DELAYS) + 1} спроб")
                    print(f"{model}: не відповідає після {len(RETRY_DELAYS) + 1} спроб")
    raise RuntimeError("Жодна модель Gemini не відповіла: " + "; ".join(errors))


VIBES = [
    "тепло й доброзичливо",
    "спокійно й упевнено",
    "м'яко, ніби розмовляєш із добрим знайомим",
    "урочисто й ніжно",
    "бадьоро, але стримано",
    "затишно й неквапливо",
]
INTRO_FALLBACKS = [
    "Вітаю! З вами {host}, {station}. Найважливіші новини — просто зараз.",
    "Це {host}, {station}. Слухайте випуск новин.",
    "З вами {host}, {station}. Починаємо випуск.",
    "{host} на зв'язку, {station}. Найсвіжіші новини — далі.",
]
OUTRO_FALLBACKS = [
    "На цьому все. Це був Кекс ФМ, з вами була {host}. {Wish}!",
    "Новини від Кекс ФМ завершено, з вами була {host}. {Wish}!",
    "Кекс ФМ лишається з вами. З вами була {host}. {Wish}!",
]


def host_for(now: datetime, last_id: str | None = None) -> dict:
    """Дві ведучі чергуються: беремо «наступну» після тієї, що вела попередній випуск
    (за записом у стрічці). Без запису — за парністю години ефіру."""
    hosts = {
        "1": {"name": NAME_1, "gemini": VOICE_1, "style": STYLE_1, "speed": SPEED_1, "pitch": PITCH_1},
        "2": {"name": NAME_2, "gemini": VOICE_2, "style": STYLE_2, "speed": SPEED_2, "pitch": PITCH_2},
    }
    if last_id == "1":
        hid = "2"
    elif last_id == "2":
        hid = "1"
    else:
        hid = "1" if (now.hour // 2) % 2 == 0 else "2"
    return {**hosts[hid], "id": hid, "gender": "жінка", "edge": "uk-UA-PolinaNeural"}


def day_part(now: datetime) -> tuple[str, str]:
    """(частина доби, побажання на прощання). Після 22:00 — ніч, після 17:00 — вечір."""
    h = now.hour
    if h >= 22 or h < 5:
        return "ніч", "тихої ночі"
    if h >= 17:
        return "вечір", "гарного вечора"
    if h >= 12:
        return "день", "гарного дня"
    return "ранок", "гарного дня"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def make_context(now: datetime, host: dict, wsum: dict | None = None) -> dict:
    part, wish = day_part(now)
    ctx = {"host": host["name"], "gender": host["gender"], "station": STATION,
           "time": f"{now:%H:%M}", "part": part, "wish": wish, "vibe": random.choice(VIBES),
           "keys": '"intro" (рядок), "items" (масив рядків-новин), "outro" (рядок)',
           "weather_task": "", "extra": ""}
    if wsum:
        ctx["keys"] += ', "weather" (рядок)'
        ctx["weather_task"] = (
            f"\nЗАВДАННЯ 4 — погода (weather). Прогноз {WEATHER_WHERE}. Напиши 2–3 короткі речення спокійною "
            "мовою про найближчі години, без конкретного часу (не «о 18:00», а «зараз», «вранці», «вдень», "
            "«ввечері», «вночі»). Використовуй ЛИШЕ числа з даних нижче, записані ЦИФРАМИ; нічого не вигадуй. "
            "Температуру вимовляй зі словами «плюс»/«мінус». ПРО ВІТЕР не згадуй, якщо в даних немає "
            "попередження про шквал чи інше небезпечне явище, і НІКОЛИ не називай швидкість вітру. Без жартів.\n"
            "Дані:\n" + weather.prompt_lines(wsum) + "\n")
    if EXTRA_INSTRUCTIONS.strip():
        ctx["extra"] = ("\nДодаткові вказівки редактора (виконуй, якщо не суперечать правилам вище): "
                        + EXTRA_INSTRUCTIONS.strip() + "\n")
    return ctx


def _ask(client, posts: list[str], ctx: dict, strict: bool) -> dict:
    prompt = PROMPT.format(posts="\n---\n".join(posts), **ctx)
    if strict:
        prompt += STRICT_NOTE
    last = None
    for _ in range(2):
        resp = _generate(client, prompt)
        try:
            obj = _parse_json(resp.text)
            break
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            last = e
            print("Відповідь Gemini не схожа на JSON, повторюю запит")
    else:
        raise last
    if isinstance(obj, list):  # на випадок, якщо модель повернула лише масив новин
        obj = {"items": obj}
    items = [x.strip() for x in obj.get("items", []) if isinstance(x, str) and x.strip()]
    return {"intro": str(obj.get("intro") or "").strip(), "items": items,
            "outro": str(obj.get("outro") or "").strip(),
            "weather": str(obj.get("weather") or "").strip()}


NO_TIME_RE = re.compile(r"\d|\bгодин|\bхвилин", re.I)  # у привітанні/прощанні часу не називаємо
NO_NAME_RE = re.compile(r"мене\s+(звати|кличуть)|моє\s+ім['’]?я|my\s+name", re.I)  # «мене звати» — ніколи


def _first_name(host: str) -> str:
    return host.split()[0].lower()


def valid_intro(text: str, ctx: dict) -> bool:
    low = text.lower()
    return (bool(text) and _first_name(ctx["host"]) in low and STATION.lower() in low
            and not has_russian(text) and not NO_TIME_RE.search(text) and not NO_NAME_RE.search(text))


def valid_outro(text: str, ctx: dict) -> bool:
    low = text.lower()
    return (bool(text) and "кекс фм" in low and ctx["wish"] in low
            and not has_russian(text) and not NO_TIME_RE.search(text) and not NO_NAME_RE.search(text))


def valid_weather(text: str, wsum: dict) -> bool:
    """Погода: без часу, без швидкості вітру, про вітер лише при шквалі, числа лише з даних."""
    if not text or has_russian(text) or weather.mentions_time(text):
        return False
    if weather.mentions_wind_speed(text) or (weather.mentions_wind(text) and not wsum["squall"]):
        return False
    return weather.numbers_ok(text, wsum)


def summarize(posts: list[str], ctx: dict, wsum: dict | None = None):
    """Повертає (привітання, новини, прощання, текст погоди або None)."""
    client = _client()
    data = _ask(client, posts, ctx, strict=False)
    own = [data["intro"], data["outro"], data["weather"]]
    if any(looks_russian(x) for x in data["items"]) or any(has_russian(x) for x in own if x):
        print("Підозра на російську мову у відповіді, повторюю запит суворіше")
        data = _ask(client, posts, ctx, strict=True)
    items = [x for x in data["items"] if not RU_LETTERS.search(x)]
    intro, outro = data["intro"], data["outro"]
    if not valid_intro(intro, ctx):
        print("Привітання не пройшло перевірку, беру запасне")
        intro = random.choice(INTRO_FALLBACKS).format(host=ctx["host"], station=STATION)
    if not valid_outro(outro, ctx):
        print("Прощання не пройшло перевірку, беру запасне")
        outro = random.choice(OUTRO_FALLBACKS).format(host=ctx["host"], Wish=_cap(ctx["wish"]))
    wtext = None
    if wsum:
        if valid_weather(data["weather"], wsum):
            wtext = data["weather"]
        else:
            print("Прогноз від Gemini не пройшов перевірку, беру шаблон")
            wtext = weather.fallback_text(WEATHER_WHERE, wsum)
    return intro, items, outro, wtext


def fit_bytes(texts: list[str], kinds: list[str], limit: int):
    """Привітання, оголошення, погоду і прощання лишаємо завжди; новини додаємо, поки є місце."""
    used = sum(len(t.encode()) + 2 for t, k in zip(texts, kinds) if k != "news")
    keep = []
    for t, k in zip(texts, kinds):
        if k != "news":
            keep.append(True)
            continue
        n = len(t.encode()) + 2
        ok = used + n <= limit
        used += n if ok else 0
        keep.append(ok)
    if not all(keep):
        print(f"Текст завеликий для одного запиту озвучення: озвучено {keep.count(True)} з {len(keep)} фраз")
    return [t for t, k in zip(texts, keep) if k], [x for x, k in zip(kinds, keep) if k]


def gemini_tts_pcm(texts: list[str], kinds: list[str], voice: str, style: str):
    """Весь випуск одним запитом до Gemini TTS (бережемо добову квоту).
    Повертає сирий звук (PCM 24 кГц, 16 біт, моно), озвучені фрази та їхні типи."""
    used, used_kinds = fit_bytes(texts, kinds, TTS_MAX_BYTES - len(style.encode()) - 4)
    script = style + "\n\n" + "\n\n".join(used)
    client = _client()
    cfg = genai_types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=genai_types.SpeechConfig(
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )
    last = None
    for attempt in range(2):
        try:
            resp = client.models.generate_content(model=TTS_MODEL, contents=script, config=cfg)
            pcm = resp.candidates[0].content.parts[0].inline_data.data
            if not pcm:
                raise ValueError("порожня відповідь без аудіо")
            if len(pcm) / (24000 * 2) < 3:
                raise ValueError(f"аудіо підозріло коротке: {len(pcm) / 48000:.1f} с")
            return pcm, used, used_kinds
        except (genai_errors.ServerError, genai_errors.ClientError, *TRANSPORT_ERRORS) as e:
            last = e
            code = getattr(e, "code", None) or type(e).__name__
            if isinstance(e, genai_errors.ClientError) and code != 429:
                raise
            if attempt == 0:
                print(f"Gemini TTS: помилка {code}, чекаю 30 с і пробую ще раз")
                time.sleep(30)
    raise last


async def edge_parts(texts: list[str], voice: str, speed: float, pitch: float = 0.0) -> list:
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate(texts):
            p = os.path.join(tmp, f"{i}.mp3")
            await edge_tts.Communicate(text, voice).save(p)
            parts.append(radio.normalize(radio.decode_file(p, speed, pitch=pitch), VOICE_DB))
    return parts


async def make_audio(texts: list[str], kinds: list[str], host: dict, out_path: str, pack: str | None = None) -> float:
    """texts/kinds: привітання, оголошення, новини..., погода, прощання. Повертає секунди."""
    parts, used_kinds = None, kinds
    if TTS_ENGINE == "gemini":
        try:
            pcm, used, used_kinds = await asyncio.to_thread(gemini_tts_pcm, texts, kinds, host["gemini"], host["style"])
            voice = radio.normalize(radio.decode_pcm16(pcm, 24000, host["speed"], host["pitch"]), VOICE_DB)
            parts, gaps = radio.split_by_pauses(voice, len(used))
            if parts is None:
                print(f"Озвучення не вдалося розрізати на {len(used)} частин (знайдені паузи, мс: {gaps}); "
                      "перебивок між новинами не буде")
                parts = [voice]
            else:
                print(f"Озвучення розрізано на {len(parts)} частин, паузи між ними (мс): {gaps}")
            print(f"Озвучення: Gemini TTS ({TTS_MODEL}, голос {host['gemini']}, ведучий(а) {host['name']})")
        except Exception as e:
            print("Gemini TTS не вдався, переходжу на Edge TTS:", repr(e)[:300])
            parts, used_kinds = None, kinds
    if parts is None:
        parts = await edge_parts(texts, host["edge"], host["speed"], host["pitch"])
        print(f"Озвучення: Edge TTS (голос {host['edge']}, ведучий(а) {host['name']})")
    parts = [radio.trim_silence(p) for p in parts]

    try:
        assets = radio.load_assets(ASSET_DIR, pack=pack)
        print(f"Звуковий пакет: {pack or 'вбудований'}")
    except Exception as e:
        print("Не вдалося підготувати звуки, випуск буде без них:", repr(e)[:200])
        assets = {"open": [], "close": [], "sting": [], "bed": []}
    opener = random.choice(assets["open"]) if JINGLES_ENABLED and assets["open"] else None
    closer = random.choice(assets["close"]) if JINGLES_ENABLED and assets["close"] else None
    stings = assets["sting"] if JINGLES_ENABLED else []
    bed = assets["bed"][0] if BED_ENABLED and assets.get("bed") else None
    wsting = random.choice(assets["weather"]) if JINGLES_ENABLED and assets.get("weather") else None
    print(f"Звук: джингли={'так' if opener is not None else 'ні'}, перебивок={len(stings)}, "
          f"перебивка погоди={'так' if wsting is not None else 'ні'}, "
          f"фон={'так' if bed is not None else 'ні'} (під голосом {BED_UNDER_DB} дБ, у паузах {BED_GAP_DB} дБ)")
    track = radio.assemble(parts, used_kinds, opener, closer, stings, bed,
                           assets.get("bed_seamless", True), BED_UNDER_DB, BED_GAP_DB,
                           JINGLE_GAIN_DB, STING_GAIN_DB, weather_sting=wsting)
    radio.export_mp3(track, out_path, MP3_BITRATE)
    return len(track) / radio.SR


def air_time(now: datetime) -> datetime:
    """Випуск виходить в ефір на найближчу повну годину (збираємо його заздалегідь)."""
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


async def main() -> None:
    if not ENABLED:
        print("Випуски призупинено в панелі керування (enabled = false).")
        return
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    air = air_time(now)  # привітання, ведуча, пакет звуків і побажання — за часом ефіру, а не збирання
    site_dir = os.environ.get("SITE_DIR", "site")
    last = feed.last_meta(site_dir)
    host = host_for(air, last.get("host"))  # завжди «наступна» ведуча після попереднього випуску
    part, _ = day_part(air)
    pack = PACK_BY_PART.get(part)
    wsum = None
    if WEATHER_ENABLED:
        try:
            wsum = weather.summarize(weather.fetch(WEATHER_LAT, WEATHER_LON), squall_ms=SQUALL_MS)
        except Exception as e:
            print("Погода недоступна, випуск буде без неї:", repr(e)[:200])
    posts, keys, mode, newest = await fetch_posts(frozenset(last.get("posts", [])))
    if not posts:
        print("У каналах немає жодних придатних постів, пропускаю випуск.")
        return
    ctx = make_context(air, host, wsum)
    intro, items, outro, wtext = summarize(posts, ctx, wsum)
    if not items:
        print("Gemini не повернув новин, пропускаю.")
        return
    texts, kinds = [intro], ["intro"]
    texts += items; kinds += ["news"] * len(items)
    if wtext:
        texts.append(wtext); kinds.append("weather")
    if DONATE_TEXT.strip():
        texts.append(DONATE_TEXT.strip()); kinds.append("donate")
    texts.append(outro); kinds.append("outro")
    print(f"Ведуча: {host['name']} (попередній випуск вела: №{last.get('host', '—')}); пакет звуків: {pack}")
    print(f"Привітання: {intro}")
    print(f"Погода: {wtext}")
    print(f"Прощання: {outro}")
    out = "episode.mp3"
    dur = await make_audio(texts, kinds, host, out, pack)
    title = f"Новини {air:%d.%m %H:00}"
    cfg = {
        "base_url": os.environ["SITE_BASE_URL"],
        "title": opt("feed_title", "FEED_TITLE", "KEXXX NEWS"),
        "description": opt("feed_desc", "FEED_DESC", "Новини коротко і без хєрні"),
        "email": opt("feed_email", "FEED_EMAIL", "keksfm.kiev@gmail.com"),
        "explicit": FEED_EXPLICIT,
        "donate_url": DONATE_URL,
        "weather_credit": bool(wtext),
    }
    notes = [f"Ведуча: {host['name']}"]
    if mode == "repeat":
        notes.append("Нових матеріалів не було, повторюємо новини за останні години.")
    notes += [f"• {l}" for l in items]
    if wtext:
        notes.append(f"Погода: {wtext} (дані Open-Meteo.com)")
    if DONATE_URL:
        notes.append(f"Підтримати розвиток станції: {DONATE_URL}")
    feed.publish(site_dir, out, title, "\n".join(notes), int(dur), now, KEEP_EPISODES, cfg,
                 meta={"host": host["id"], "posts": keys, "mode": mode, "pack": pack})
    print(f"Готово: {len(items)} новин ({mode}), {dur:.0f} с")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write("published=true\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("ПОМИЛКА:", repr(e), file=sys.stderr)
        raise
