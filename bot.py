"""Регулярний новинний випуск: Telegram + DJ-стрічки -> Gemini -> озвучення + джингли -> GitHub Pages (RSS)."""
import asyncio
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
# Озвучка ЛИШЕ Gemini TTS. Запасного «поганого» голосу немає: якщо Gemini не відповідає, бот довго й терпляче
# повторює запит (зростаючі паузи, обидві найкращі моделі), а якщо не вийшло — повторює ПОПЕРЕДНІЙ випуск.
TTS_MODELS = [m.strip() for m in opt("tts_models", "TTS_MODELS",
              "gemini-2.5-flash-preview-tts,gemini-2.5-pro-preview-tts").split(",") if m.strip()]
TTS_RETRY_MIN = opt("tts_retry_min", "TTS_RETRY_MIN", 40)      # скільки хвилин боротися за якісну озвучку
TTS_MAX_ATTEMPTS = opt("tts_max_attempts", "TTS_MAX_ATTEMPTS", 20)  # стеля спроб за один випуск (береже квоту)
TTS_AIR_MARGIN_MIN = opt("tts_air_margin_min", "TTS_AIR_MARGIN_MIN", 8)  # не боротися за озвучку пізніше, ніж за стільки хвилин до ефіру
TTS_DELAYS = [20, 30, 45, 60, 90, 120]  # зростаючі паузи між спробами, далі по 120 с
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
DONATE_ENABLED = opt("donate_enabled", "DONATE_ENABLED", True)        # ненав'язливий заклик у кінці випуску
TELEGRAM_ENABLED = opt("telegram_enabled", "TELEGRAM_ENABLED", True)  # згадка телеграм-каналу в мові ведучих
TELEGRAM_NAME = opt("telegram_name", "TELEGRAM_NAME", "Кекс-ньюс")
WEATHER_LEAD = opt("weather_lead", "WEATHER_LEAD",
                   "І про погоду в Києві.\nА тепер про погоду в Києві.\nІ нарешті про погоду в Києві.")
WEATHER_LEADS = [x.strip() for x in WEATHER_LEAD.splitlines() if x.strip()]  # явний вступ до погоди (по одному в рядку)
FEED_EXPLICIT = opt("rss_explicit", "RSS_EXPLICIT", False)  # позначка «explicit» у RSS: за замовчуванням вимкнена
EXTRA_INSTRUCTIONS = opt("extra_instructions", "EXTRA_INSTRUCTIONS", "")
VOICE_DB = -19.0
# Ведучі (дві жінки), чергуються: завжди береться «наступна» після тієї, що вела попередній випуск.
STATION_SPOKEN = opt("station_spoken", "STATION_SPOKEN", "Служба новин кекс рАдіо-кОмпані")  # велика голосна = наголос
_STRESS = "(a capitalized vowel in the middle of a word marks the stressed vowel)"
_SERIOUS = ("Our country is at war: for attacks, explosions, casualties and deaths speak gravely, slowly, with a "
            "heavy restrained tone. Crisp Ukrainian diction, pronounce every ending in full, correct word stress "
            + _STRESS + ", a one-second pause between items:")
NAME_1 = opt("h1_name", "H1_NAME", "Ангеліна")
VOICE_1 = opt("h1_voice", "H1_VOICE", "Leda")  # Youthful — молодий голос
SPEED_1 = opt("h1_speed", "H1_SPEED", 0.95)
PITCH_1 = opt("h1_pitch", "H1_PITCH", 1.5)     # півтони: плюс = вище й молодше
STYLE_1 = opt("h1_style", "H1_STYLE",
              "Read as Angelina, a 19-year-old woman, radio news host: a young, feminine, high, bright and ringing "
              "voice, but a SERIOUS, composed, firm newsroom delivery, no smiling and no softness. " + _SERIOUS)
NAME_2 = opt("h2_name", "H2_NAME", "Лера")
VOICE_2 = opt("h2_voice", "H2_VOICE", "Kore")  # Firm — рівний, зібраний голос
SPEED_2 = opt("h2_speed", "H2_SPEED", 0.95)
PITCH_2 = opt("h2_pitch", "H2_PITCH", 0.0)
STYLE_2 = opt("h2_style", "H2_STYLE",
              "Read as Lera, a radio news host with a natural, beautiful, clear voice, but a SERIOUS, composed, firm "
              "newsroom delivery, no smiling and no softness. " + _SERIOUS)
STATION = opt("station", "STATION", "Служба новин Кекс-радіокомпані")  # у тексті; вимовляється як STATION_SPOKEN

PROMPT = """Ти — редакторка і ведуча випуску новин молодіжного танцювального радіо Кекс ФМ.
Україна у стані війни, тому випуск СЕРЙОЗНИЙ. Говориш УКРАЇНСЬКОЮ мовою. Нижче пости з новинних каналів
(можуть бути російською або українською).

ЗАВДАННЯ 1 — новини. Для кожної окремої новини напиши ОДНЕ коротке речення українською,
без вигадування фактів. Дублікати об'єднуй. Пропускай рекламу, заклики підписатися,
надіслати новину, донатити, службові підписи та пости без новинного змісту.

МОВА (обов'язково): якщо пост російською — переклади його зміст на українську.
Уся відповідь має бути ЛИШЕ українською: жодного російського слова і жодних літер ы, э, ъ, ё.
Власні назви пиши за українською нормою (Київ, а не Киев; Харків, а не Харьков).

ГРАМАТИКА І ЧИСЛА (обов'язково): літературна українська, граматично бездоганно. ВСІ числа пиши СЛОВАМИ
у правильному відмінку й роді, узгоджуючи з іменником (наприклад: «двоє загиблих», «п'ятнадцять поранених»,
«до двох тисяч осіб», «з двадцяти дронів збито дев'ятнадцять»). Жодних цифр у тексті.

ПОДАЧА: серйозна, зібрана, чітка; БЕЗ жартів, іронії, каламбурів і сленгу. Новини про обстріли, вибухи,
ракетні й дронові атаки, загиблих і поранених пиши прямо, точно й стримано, без пом'якшень і евфемізмів
(не «прилетіло», не «бавовна»): що сталося, де, скільки загиблих і поранених. Без емоційних оцінок.

ЗАВДАННЯ 2 — привітання (intro). Ведуча цього випуску: {host} (жінка), узгоджуй рід дієслів.
Напиши 1–2 короткі речення. Назви себе ЛИШЕ у формах на кшталт «З вами знову {host}», «Для вас працює {host}»,
«В ефірі {host}», «{host} знову з вами», «Новини для вас читає {host}» — щоразу інакше, як у різних
радіоефірах. НІКОЛИ не починай зі слова «Це» і не пиши «Це — {host}»; не вживай «мене звати», «моє ім'я».
Назви «{station}» ДОСЛІВНО (без відмінювання і без змін у написанні). Привітайся відповідно до часу доби
(зараз {part}). Манера цього разу: {vibe}. НЕ називай конкретний час, години, хвилини, дату чи день тижня.
Без жартів, без штампів на кшталт «шановні слухачі».

ЗАВДАННЯ 3 — прощання (outro). 1–2 короткі речення: згадай «Кекс ФМ», обов'язково побажай «{wish}»
(саме цими словами), назви себе у формі «З вами була {host}». Без жартів, без конкретного часу. Заклик
до донату і телеграм-канал НЕ згадуй: вони звучать окремо перед прощанням.
{closing_task}{extra}
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
    "зібрано й серйозно",
    "спокійно й твердо",
    "стримано й діловито",
    "рівно й зосереджено",
    "чітко й по-діловому",
    "серйозно й упевнено",
]
INTRO_FALLBACKS = [
    "З вами знову {host}, {station}. Слухайте випуск новин.",
    "Для вас працює {host}, {station}. Найважливіші новини — далі.",
    "В ефірі {host}, {station}. Починаємо випуск.",
    "{host} знову з вами. {station}, випуск новин.",
    "Новини для вас читає {host}, {station}.",
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
    return {**hosts[hid], "id": hid, "gender": "жінка"}


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
           "closing_task": "", "extra": ""}
    if TELEGRAM_ENABLED:
        ctx["keys"] += ', "telegram" (рядок)'
        ctx["closing_task"] += (
            "\nЗАВДАННЯ 4 — телеграм-канал (telegram). 1–2 короткі речення наприкінці випуску: нагадай про наш "
            f"телеграм-канал «{TELEGRAM_NAME}» (більше новин — там). НЕ згадуй сайт, посилання чи адресу. "
            "Без жартів, без конкретного часу.\n")
    if EXTRA_INSTRUCTIONS.strip():
        ctx["extra"] = ("\nДодаткові вказівки редактора (виконуй, якщо не суперечать правилам вище): "
                        + EXTRA_INSTRUCTIONS.strip() + "\n")
    return ctx


def _ask(client, posts: list[str], ctx: dict, strict: bool, note: str = "") -> dict:
    prompt = PROMPT.format(posts="\n---\n".join(posts), **ctx)
    if strict:
        prompt += STRICT_NOTE
    if note:
        prompt += note
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
            "telegram": str(obj.get("telegram") or "").strip()}


DIGITS_NOTE = ("\n\nУВАГА: у новинах були цифри. Запиши ВСІ числа словами, у правильному відмінку й роді, "
               "узгоджуючи з іменником (наприклад: «двоє загиблих», «п'ятнадцять поранених», «з двадцяти дронів»).")
NO_TIME_RE = re.compile(r"\d|\bгодин|\bхвилин", re.I)  # у привітанні/прощанні часу не називаємо
NO_NAME_RE = re.compile(r"мене\s+(звати|кличуть)|моє\s+ім['’]?я|my\s+name", re.I)  # «мене звати» — ніколи
NO_ADDR_RE = re.compile(r"keksfm|kiev\.ua|\.ua\b|https?:|www\.|крапк", re.I)  # адресу сайту в ефірі не називаємо


def _first_name(host: str) -> str:
    return host.split()[0].lower()


def _this_is(text: str, host: str) -> bool:
    """«Це — Ангеліна» / «Це Ангеліна» і будь-який вступ, що починається зі слова «Це», заборонені."""
    t = text.strip().lower()
    return t.startswith("це ") or t.startswith("це—") or bool(
        re.search(r"(?i)\bце\b\s*[—–\-]?\s*" + re.escape(_first_name(host)), text))


def valid_intro(text: str, ctx: dict) -> bool:
    low = text.lower()
    return (bool(text) and _first_name(ctx["host"]) in low and STATION.lower() in low
            and not has_russian(text) and not NO_TIME_RE.search(text) and not NO_NAME_RE.search(text)
            and not _this_is(text, ctx["host"]))


def valid_outro(text: str, ctx: dict) -> bool:
    low = text.lower()
    return (bool(text) and "кекс фм" in low and ctx["wish"] in low
            and not has_russian(text) and not NO_TIME_RE.search(text) and not NO_NAME_RE.search(text))


TELEGRAM_FALLBACKS = [
    "Більше новин читайте в нашому телеграм-каналі «{name}».",
    "Усі подробиці — в нашому телеграм-каналі «{name}».",
    "Не пропустіть більше новин у нашому телеграм-каналі «{name}».",
]


def valid_telegram(text: str) -> bool:
    low = text.lower()
    return (bool(text) and len(text) <= 240 and "телеграм" in low and TELEGRAM_NAME.lower() in low
            and "сайт" not in low and not NO_ADDR_RE.search(text) and not has_russian(text)
            and not NO_TIME_RE.search(text))


# Донат: дві дослівні формули чергуються від випуску до випуску. Адресу сайту не вимовляємо.
DONATE_VARIANTS = [
    [  # 0 — чашечка кави
        "Якщо ви хочете підтримати нас чашечкою кави, це можна зробити за посиланням на нашому сайті.",
        "А підтримати нас чашечкою кави можна за посиланням на нашому сайті.",
        "Будемо вдячні, якщо захочете підтримати нас чашечкою кави, за посиланням на нашому сайті.",
    ],
    [  # 1 — фінансово порадувати
        "Можете трошки фінансово нас порадувати, якщо вам подобається наша робота, за посиланням на нашому сайті.",
        "Хочете трошки фінансово нас порадувати, якщо вам подобається наша робота? Це можна зробити за посиланням на нашому сайті.",
        "Є й такий спосіб: трошки фінансово нас порадувати, якщо вам подобається наша робота, за посиланням на нашому сайті.",
    ],
]


def pick_donate(last_variant) -> tuple[str, int]:
    """Наступна за чергою формула: після 0 — 1, після 1 — 0; (текст, номер формули)."""
    v = 1 - last_variant if last_variant in (0, 1) else 0
    return random.choice(DONATE_VARIANTS[v]), v


def spoken(text: str) -> str:
    """Текст для озвучки: назву станції пишемо з наголосами (велика голосна = наголос)."""
    return re.sub(re.escape(STATION), STATION_SPOKEN, text, flags=re.I)


def summarize(posts: list[str], ctx: dict, wsum: dict | None = None):
    """Повертає (привітання, новини, прощання, текст погоди з явним вступом або None, телеграм або None)."""
    client = _client()
    data = _ask(client, posts, ctx, strict=False)
    own = [data["intro"], data["outro"], data["telegram"]]
    strict = any(looks_russian(x) for x in data["items"]) or any(has_russian(x) for x in own if x)
    note = DIGITS_NOTE if any(re.search(r"\d", x) for x in data["items"]) else ""
    if strict or note:
        print("Повторюю запит суворіше:" + (" російські слова;" if strict else "") + (" цифри в новинах;" if note else ""))
        data = _ask(client, posts, ctx, strict=strict, note=note)
    items = [x for x in data["items"] if not RU_LETTERS.search(x)]
    if any(re.search(r"\d", x) for x in items):
        print("Увага: у новинах лишилися цифри (модель не записала їх словами).")
    intro, outro = data["intro"], data["outro"]
    if not valid_intro(intro, ctx):
        print("Привітання не пройшло перевірку, беру запасне")
        intro = random.choice(INTRO_FALLBACKS).format(host=ctx["host"], station=STATION)
    if not valid_outro(outro, ctx):
        print("Прощання не пройшло перевірку, беру запасне")
        outro = random.choice(OUTRO_FALLBACKS).format(host=ctx["host"], Wish=_cap(ctx["wish"]))
    wtext = None
    if wsum:  # текст погоди складає код: усі числа словами в правильних відмінках, без швидкості вітру
        wtext = weather.compose_text(wsum)
        if WEATHER_LEADS:  # погоду оголошуємо явно: «І про погоду в Києві.»
            wtext = f"{random.choice(WEATHER_LEADS)} {wtext}"
    tg = None
    if TELEGRAM_ENABLED:
        tg = data["telegram"]
        if not valid_telegram(tg):
            print("Згадка телеграм-каналу не пройшла перевірку, беру запасну")
            tg = random.choice(TELEGRAM_FALLBACKS).format(name=TELEGRAM_NAME)
    return intro, items, outro, wtext, tg


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


class TTSUnavailable(Exception):
    """Gemini TTS так і не віддав якісну озвучку за відведений час."""


def _retry_after(e) -> float | None:
    m = re.search(r"retry\w*\D{0,14}(\d+(?:\.\d+)?)\s*s", str(e), re.I)
    return float(m.group(1)) if m else None


def gemini_tts_pcm(texts: list[str], kinds: list[str], voice: str, style: str, budget_s: float | None = None):
    """Озвучення всього випуску одним запитом. Запасного голосу НЕМАЄ: при збоях бот повторює запит із
    зростаючими паузами (20 → 120 с), чергуючи найкращі моделі, доки не мине TTS_RETRY_MIN хвилин або не буде
    TTS_MAX_ATTEMPTS спроб. 429 — чекаємо щонайменше хвилину (ліміт за хвилину); добова квота моделі вичерпана —
    модель відкладаємо; 400/403/404 — модель недоступна. Повертає (PCM 24 кГц 16 біт моно, фрази, типи)."""
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
    deadline = time.monotonic() + (TTS_RETRY_MIN * 60 if budget_s is None else budget_s)
    dead: set[str] = set()
    attempt, errors = 0, []
    while attempt < TTS_MAX_ATTEMPTS and (attempt == 0 or time.monotonic() < deadline):  # перша спроба — завжди
        models = [m for m in TTS_MODELS if m not in dead]
        if not models:
            break
        model = models[attempt % len(models)]
        attempt += 1
        try:
            resp = client.models.generate_content(model=model, contents=script, config=cfg)
            pcm = resp.candidates[0].content.parts[0].inline_data.data
            if not pcm:
                raise ValueError("порожня відповідь без аудіо")
            if len(pcm) / (24000 * 2) < 3:
                raise ValueError(f"аудіо підозріло коротке: {len(pcm) / 48000:.1f} с")
            print(f"Gemini TTS: готово ({model}, спроба {attempt})")
            return pcm, used, used_kinds
        except (genai_errors.ServerError, genai_errors.ClientError, *TRANSPORT_ERRORS,
                ValueError, IndexError, AttributeError) as e:
            code = getattr(e, "code", None) or type(e).__name__
            errors.append(f"{model}:{code}")
            if isinstance(e, genai_errors.ClientError) and code != 429:
                dead.add(model)
                print(f"Gemini TTS {model}: помилка {code}, модель недоступна")
                continue
            wait = TTS_DELAYS[min(attempt - 1, len(TTS_DELAYS) - 1)]
            if code == 429:
                if re.search(r"PerDay|per day|daily", str(e), re.I):
                    dead.add(model)
                    print(f"Gemini TTS {model}: добова квота вичерпана, відкладаю модель")
                    continue
                wait = max(wait, (_retry_after(e) or 0) + 2, 60)
            wait *= random.uniform(0.9, 1.15)  # невеликий розкид, щоб не бити в ліміт рівними інтервалами
            left = deadline - time.monotonic()
            if left <= 0:
                break
            wait = min(wait, left)
            print(f"Gemini TTS {model}: помилка {code}, спроба {attempt}, чекаю {wait:.0f} с")
            time.sleep(wait)
    raise TTSUnavailable(f"спроб: {attempt}, помилки: {', '.join(errors[-6:]) or 'немає'}")


async def make_audio(texts: list[str], kinds: list[str], host: dict, out_path: str, pack: str | None = None,
                     budget_s: float | None = None):
    """texts/kinds: привітання, новини..., погода, телеграм, донат, прощання. Повертає секунди або None,
    якщо якісної озвучки отримати не вдалося (тоді main повторить попередній випуск)."""
    try:
        pcm, used, used_kinds = await asyncio.to_thread(gemini_tts_pcm, texts, kinds, host["gemini"], host["style"], budget_s)
    except TTSUnavailable as e:
        print(f"::warning::Якісної озвучки Gemini отримати не вдалося ({e}). Запасного голосу немає.")
        return None
    voice = radio.normalize(radio.decode_pcm16(pcm, 24000, host["speed"], host["pitch"]), VOICE_DB)
    parts, gaps = radio.split_by_pauses(voice, len(used))
    if parts is None:
        print(f"Озвучення не вдалося розрізати на {len(used)} частин (знайдені паузи, мс: {gaps}); "
              "перебивок між новинами не буде")
        parts = [voice]
    else:
        print(f"Озвучення розрізано на {len(parts)} частин, паузи між ними (мс): {gaps}")
    print(f"Озвучення: Gemini TTS (голос {host['gemini']}, ведуча {host['name']})")
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
    intro, items, outro, wtext, tg = summarize(posts, ctx, wsum)
    if not items:
        print("Gemini не повернув новин, пропускаю.")
        return
    dn, dn_variant = pick_donate(last.get("donate")) if DONATE_ENABLED else (None, None)
    texts, kinds = [intro], ["intro"]
    texts += items; kinds += ["news"] * len(items)
    if wtext:
        texts.append(wtext); kinds.append("weather")
    if tg:  # наприкінці випуску, у мові ведучих: телеграм-канал, потім ненав'язливий донат, потім прощання
        texts.append(tg); kinds.append("tg")
    if dn:
        texts.append(dn); kinds.append("donate")
    texts.append(outro); kinds.append("outro")
    print(f"Ведуча: {host['name']} (попередній випуск вела: №{last.get('host', '—')}); пакет звуків: {pack}")
    print(f"Привітання: {intro}")
    print(f"Погода: {wtext}")
    print(f"Телеграм: {tg}")
    print(f"Донат (формула №{dn_variant}): {dn}")
    print(f"Прощання: {outro}")
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
    out = "episode.mp3"
    # бюджет повторів до Gemini: не довше TTS_RETRY_MIN і не пізніше ніж за TTS_AIR_MARGIN_MIN хвилин до ефіру
    to_air = (air - datetime.now(ZoneInfo("Europe/Kyiv"))).total_seconds()
    budget = max(0.0, min(TTS_RETRY_MIN * 60, to_air - TTS_AIR_MARGIN_MIN * 60))
    print(f"Бюджет часу на озвучку Gemini: {budget / 60:.0f} хв (до ефіру {to_air / 60:.0f} хв)")
    dur = await make_audio([spoken(t) for t in texts], kinds, host, out, pack, budget)
    gh_out = os.environ.get("GITHUB_OUTPUT")

    def mark_published():
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as f:
                f.write("published=true\n")

    if dur is None:  # якісної озвучки немає — краще повторити попередній випуск, ніж пустити поганий голос
        src = feed.latest_episode(site_dir)
        if not src:
            print("::error::Озвучки немає, а попереднього випуску для повтору теж немає.")
            raise SystemExit(1)
        feed.publish(site_dir, os.path.join(site_dir, src["file"]), f"{title} (повтор)",
                     "Повтор попереднього випуску: нову якісну озвучку зараз отримати не вдалося.\n"
                     + src.get("description", ""),
                     int(src.get("duration", 0)), now, KEEP_EPISODES, cfg,
                     meta={**(src.get("meta") or {}), "reused": True, "mode": "reuse"}, name_suffix="_r")
        print(f"Опубліковано повтор попереднього випуску ({src['file']}).")
        mark_published()
        return
    notes = [f"Ведуча: {host['name']}"]
    if mode == "repeat":
        notes.append("Нових матеріалів не було, повторюємо новини за останні години.")
    notes += [f"• {l}" for l in items]
    if wtext:
        notes.append(f"Погода: {wtext} (дані Open-Meteo.com)")
    if DONATE_URL:
        notes.append(f"Підтримати розвиток станції: {DONATE_URL}")
    feed.publish(site_dir, out, title, "\n".join(notes), int(dur), now, KEEP_EPISODES, cfg,
                 meta={"host": host["id"], "posts": keys, "mode": mode, "pack": pack, "donate": dn_variant})
    print(f"Готово: {len(items)} новин ({mode}), {dur:.0f} с")
    mark_published()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("ПОМИЛКА:", repr(e), file=sys.stderr)
        raise
