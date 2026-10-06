"""Регулярний новинний випуск: Telegram -> Gemini -> озвучення + джингли -> GitHub Pages (RSS)."""
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
WINDOW_MIN = opt("window_min", "WINDOW_MIN", 120)
MAX_POSTS = opt("max_posts", "MAX_POSTS", 40)
MIN_SENTENCES = opt("min_sentences", "MIN_SENTENCES", 1)
KEEP_EPISODES = opt("keep_episodes", "KEEP_EPISODES", 24)
TTS_ENGINE = opt("tts_engine", "TTS_ENGINE", "gemini").lower()  # gemini | edge
TTS_MODEL = opt("tts_model", "TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_VOICE_F = opt("voice_f", "GEMINI_VOICE_F", "Despina")  # жіночий голос: ведуча (Smooth)
GEMINI_VOICE_M = opt("voice_m", "GEMINI_VOICE_M", "Algieba")   # чоловічий голос: ведучий
NAME_F = opt("name_f", "NAME_F", "Ангеліна")
NAME_M = opt("name_m", "NAME_M", "Жека")
TTS_SPEED = opt("speed", "TTS_SPEED", 1.08)        # швидкість ведучого (чол.)
SPEED_F = opt("speed_f", "TTS_SPEED_F", 1.0)         # швидкість ведучої: повільніше = оксамитовіше
TTS_MAX_BYTES = opt("tts_max_bytes", "TTS_MAX_BYTES", 3900)  # ліміт на один запит озвучення (стиль + текст)
ASSET_DIR = opt("asset_dir", "ASSET_DIR", "assets")
MP3_BITRATE = opt("mp3_bitrate", "MP3_BITRATE", "192k")  # стерео-mp3, кбіт/с
BED_ENABLED = opt("bed_enabled", "BED_ENABLED", True)
BED_UNDER_DB = opt("bed_under_db", "BED_UNDER_DB", -20.0)  # фон під голосом, дБ відносно голосу
BED_GAP_DB = opt("bed_gap_db", "BED_GAP_DB", -12.0)        # фон у паузах, дБ відносно голосу
JINGLES_ENABLED = opt("jingles_enabled", "JINGLES_ENABLED", True)
JINGLE_GAIN_DB = opt("jingle_gain_db", "JINGLE_GAIN_DB", 0.0)
STING_GAIN_DB = opt("sting_gain_db", "STING_GAIN_DB", 0.0)
PROMO_ENABLED = opt("promo_enabled", "PROMO_ENABLED", True)
PROMO_TEXT = opt("promo_text", "PROMO_TEXT",
                 "Більш детально ознайомитися з цими та іншими новинами ви можете зараз "
                 "на нашому телеграм-каналі «Кекс-ньюс» за посиланням на сайті.")
TELEGRAM_URL = opt("telegram_url", "TELEGRAM_URL", "")
WEATHER_ENABLED = opt("weather_enabled", "WEATHER_ENABLED", True)
WEATHER_JOKE = opt("weather_joke", "WEATHER_JOKE", True)   # жарт у кінці прогнозу
WEATHER_WHERE = opt("weather_where", "WEATHER_WHERE", "у Києві")
WEATHER_LAT = opt("lat", "WEATHER_LAT", 50.45)
WEATHER_LON = opt("lon", "WEATHER_LON", 30.52)
EXTRA_INSTRUCTIONS = opt("extra_instructions", "EXTRA_INSTRUCTIONS", "")
VOICE_DB = -19.0
STYLE_M = opt("style_m", "TTS_STYLE_M",
              "Read as an energetic young host of an independent dance radio news bulletin: lively, "
              "natural, slightly brisk, correct Ukrainian word stress, a one-second pause between "
              "items; calm and serious for sad or war news:")
STYLE_F = opt("style_f", "TTS_STYLE_F",
              "Read as Angelina, a charismatic late-night radio news host with a sultry, seductive voice: "
              "low, smooth, husky and breathy, intimate and velvety, silky unhurried phrasing with a hint "
              "of a playful smile, correct Ukrainian word stress, a one-second pause between items; "
              "for sad or war-related items drop the seductive tone completely and speak calm and serious:")
STATION = opt("station", "STATION", "Служба новин Кекс-радіокомпані")  # вимовляється дослівно, не відмінюється

PROMPT = """Ти — редактор і ведучий випуску новин молодіжного танцювального незалежного радіо
Кекс ФМ. Говориш УКРАЇНСЬКОЮ мовою. Нижче пости з новинних каналів (можуть бути російською
або українською).

ЗАВДАННЯ 1 — новини. Для кожної окремої новини напиши ОДНЕ коротке речення українською,
без вигадування фактів. Дублікати об'єднуй. Пропускай рекламу, заклики підписатися,
надіслати новину, донатити, службові підписи та пости без новинного змісту.

МОВА (обов'язково): якщо пост російською — переклади його зміст на українську.
Уся відповідь має бути ЛИШЕ українською: жодного російського слова і жодних літер ы, э, ъ, ё.
Власні назви пиши за українською нормою (Київ, а не Киев; Харків, а не Харьков).

Тон новин обирай залежно від теми:
- Якщо новина стосується війни, обстрілів, тривог, жертв, поранених, аварій,
  катастроф чи будь-якої іншої людської трагедії — подавай її СТРИМАНО і СЕРЙОЗНО,
  без жодного жарту чи гри слів.
- Для решти новин (побутові, культурні, спортивні, курйозні, економічні тощо)
  можна використовувати легку, доброзичливу, жартівливу подачу.
Якщо сумніваєшся, до якої категорії віднести новину — обирай серйозний тон.

ЗАВДАННЯ 2 — привітання (intro). Ведучий(а) цього випуску: {host} ({gender}),
узгоджуй рід дієслів. Напиши 1–2 короткі речення: назвись іменем і назви
«{station}» ДОСЛІВНО (без відмінювання і без змін у написанні), привітайся відповідно до часу доби
(зараз {part}). Манера цього разу: {vibe}. НЕ називай конкретний час, години, хвилини, дату чи день тижня. Щоразу придумуй нове привітання,
уникай штампів на кшталт «шановні слухачі». Молодіжний сленг — помірно.

ЗАВДАННЯ 3 — прощання (outro). 1–2 короткі речення: згадай «Кекс ФМ»,
обов'язково побажай «{wish}» (саме цими словами), підпишись іменем {host}. Без згадок про конкретний час.

Якщо більшість новин трагічні чи воєнні, привітання і прощання — спокійні, без жартів.
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


async def fetch_posts() -> list[str]:
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    if not CHANNELS.strip():
        raise SystemExit("Не задано канали-джерела (панель: Настройки -> Источники, або секрет TG_CHANNELS)")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MIN)
    posts: list[str] = []
    skipped = 0
    async with client:
        for channel in [c for c in CHANNELS.split(",") if c.strip()]:
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


RETRY_DELAYS = [15, 30, 60]  # секунди між повторами при 503/429 (потім — наступна модель)


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
            except (genai_errors.ServerError, genai_errors.ClientError) as e:
                code = getattr(e, "code", None)
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
    "бадьоро й заводно, як ведучий ранкового шоу",
    "по-дружньому і невимушено, ніби пишеш другу",
    "з легкою іронією",
    "драйвово, як перед вечіркою",
    "жартома-урочисто, як у кіноанонсі",
    "зухвало, з посмішкою в голосі",
    "чілово й розслаблено",
]
JOKE_ANGLES = [
    "про одяг і парасольку", "про настрій і плани на вечір", "про каву чи чай", "про котів і сусідів",
    "про міську маршрутку", "про прогулянку чи пробіжку", "про вибір взуття", "про вихідні та відпочинок",
]
FEMALE_VIBES = [
    "оксамитово й ніжно, з грайливою посмішкою в голосі",
    "низьким довірливим тоном, ніби шепочеш на вухо",
    "спокусливо-грайливо, але стильно і без вульгарності",
]
INTRO_FALLBACKS = [
    "Привіт! З вами {host}, {station}. Що відбувається просто зараз — слухайте!",
    "Це {host}, {station}. Врубаємо новини!",
    "Агов! {host}, {station}, на зв'язку. Поїхали по новинах.",
    "{host} на зв'язку, {station}. Найсвіжіше — просто зараз!",
]
OUTRO_FALLBACKS = [
    "На цьому все. Це був Кекс ФМ, з вами {host}. {Wish}!",
    "Новини від Кекс ФМ завершено. Я — {host}. {Wish}!",
    "Кекс ФМ лишається з вами, а {host} прощається. {Wish}!",
]


def host_for(now: datetime) -> dict:
    """Чергування ведучих при запуску раз на 2 години: Жека (чол.) / Ангеліна (жін.)."""
    if (now.hour // 2) % 2 == 0:
        return {"name": NAME_M, "gender": "чоловік", "gemini": GEMINI_VOICE_M, "edge": "uk-UA-OstapNeural",
                "style": STYLE_M, "speed": TTS_SPEED}
    return {"name": NAME_F, "gender": "жінка", "gemini": GEMINI_VOICE_F, "edge": "uk-UA-PolinaNeural",
            "style": STYLE_F, "speed": SPEED_F}


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
           "time": f"{now:%H:%M}", "part": part, "wish": wish, "vibe": random.choice(VIBES + (FEMALE_VIBES if host["gender"] == "жінка" else [])),
           "keys": '"intro" (рядок), "items" (масив рядків-новин), "outro" (рядок)',
           "weather_task": "", "extra": ""}
    if wsum:
        ctx["keys"] += ', "weather" (рядок)'
        joke_task = ""
        if WEATHER_JOKE:
            ctx["keys"] += ', "weather_joke" (рядок)'
            joke_task = (
                "ДОДАТКОВО (weather_joke): ОДНЕ коротке, легке, доброзичливе жартівливе речення саме про цю "
                f"погоду, підхід: {random.choice(JOKE_ANGLES)}. Без цифр і конкретного часу. Якщо більшість "
                "новин трагічні чи воєнні або погода небезпечна (гроза, ожеледиця, сильний вітер) — "
                "залиш weather_joke порожнім рядком.\n")
        ctx["weather_task"] = (
            f"\nЗАВДАННЯ 4 — погода (weather). Прогноз {WEATHER_WHERE}. Напиши 2–3 короткі речення живою "
            "мовою про найближчі години, без конкретного часу (не «о 18:00», а «зараз», «вранці», «вдень», "
            "«ввечері», «вночі»). Використовуй ЛИШЕ числа з даних нижче, записані ЦИФРАМИ; нічого "
            "не вигадуй. Температуру вимовляй зі словами «плюс»/«мінус».\nДані:\n"
            + weather.prompt_lines(wsum) + "\n" + joke_task)
    if EXTRA_INSTRUCTIONS.strip():
        ctx["extra"] = ("\nДодаткові вказівки редактора (виконуй, якщо не суперечать правилам вище): "
                        + EXTRA_INSTRUCTIONS.strip() + "\n")
    return ctx


def _ask(client, posts: list[str], ctx: dict, strict: bool) -> dict:
    prompt = PROMPT.format(posts="\n---\n".join(posts), **ctx)
    if strict:
        prompt += STRICT_NOTE
    resp = _generate(client, prompt)
    obj = json.loads(resp.text)
    if isinstance(obj, list):  # на випадок, якщо модель повернула лише масив новин
        obj = {"items": obj}
    items = [x.strip() for x in obj.get("items", []) if isinstance(x, str) and x.strip()]
    return {"intro": str(obj.get("intro") or "").strip(), "items": items,
            "outro": str(obj.get("outro") or "").strip(),
            "weather": str(obj.get("weather") or "").strip(),
            "joke": (str(obj["weather_joke"]).strip() if obj.get("weather_joke") is not None else None)}


NO_TIME_RE = re.compile(r"\d|\bгодин|\bхвилин", re.I)  # у привітанні/прощанні часу не називаємо


def valid_intro(text: str, ctx: dict) -> bool:
    low = text.lower()
    return (bool(text) and ctx["host"].lower() in low and STATION.lower() in low
            and not looks_russian(text) and not NO_TIME_RE.search(text))


def valid_outro(text: str, ctx: dict) -> bool:
    low = text.lower()
    return (bool(text) and "кекс фм" in low and ctx["wish"] in low
            and not looks_russian(text) and not NO_TIME_RE.search(text))


def summarize(posts: list[str], ctx: dict, wsum: dict | None = None):
    """Повертає (привітання, новини, прощання, текст погоди або None)."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    data = _ask(client, posts, ctx, strict=False)
    texts = [data["intro"], *data["items"], data["outro"], data["weather"]]
    if any(looks_russian(x) for x in texts if x):
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
        wt = data["weather"]
        if (wt and not RU_LETTERS.search(wt) and not looks_russian(wt) and not weather.mentions_time(wt)
                and weather.numbers_ok(wt, wsum)):
            wtext = wt
        else:
            print("Прогноз від Gemini не пройшов перевірку, беру шаблон")
            wtext = weather.fallback_text(WEATHER_WHERE, wsum)
        joke = data["joke"]
        if WEATHER_JOKE and joke:
            if (weather.valid_joke(joke) and not RU_LETTERS.search(joke) and not looks_russian(joke)):
                print(f"Жарт про погоду: {joke}")
            else:
                joke = weather.fallback_joke(wsum)
                print(f"Жарт від Gemini не пройшов перевірку, беру запасний: {joke}")
            wtext = f"{wtext} {joke}"
        elif WEATHER_JOKE:
            print("Жарт про погоду: модель залишила порожнім (пропускаю)")
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
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
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
        except (genai_errors.ServerError, genai_errors.ClientError) as e:
            last = e
            code = getattr(e, "code", None)
            if isinstance(e, genai_errors.ClientError) and code != 429:
                raise
            if attempt == 0:
                print(f"Gemini TTS: помилка {code}, чекаю 30 с і пробую ще раз")
                time.sleep(30)
    raise last


async def edge_parts(texts: list[str], voice: str, speed: float) -> list:
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate(texts):
            p = os.path.join(tmp, f"{i}.mp3")
            await edge_tts.Communicate(text, voice).save(p)
            parts.append(radio.normalize(radio.decode_file(p, speed), VOICE_DB))
    return parts


async def make_audio(texts: list[str], kinds: list[str], host: dict, out_path: str) -> float:
    """texts/kinds: привітання, оголошення, новини..., погода, прощання. Повертає секунди."""
    parts, used_kinds = None, kinds
    if TTS_ENGINE == "gemini":
        try:
            pcm, used, used_kinds = await asyncio.to_thread(gemini_tts_pcm, texts, kinds, host["gemini"], host["style"])
            voice = radio.normalize(radio.decode_pcm16(pcm, 24000, host["speed"]), VOICE_DB)
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
        parts = await edge_parts(texts, host["edge"], host["speed"])
        print(f"Озвучення: Edge TTS (голос {host['edge']}, ведучий(а) {host['name']})")
    parts = [radio.trim_silence(p) for p in parts]

    try:
        assets = radio.load_assets(ASSET_DIR)
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
    air = air_time(now)  # привітання, ведучий і побажання — за часом ефіру, а не збирання
    host = host_for(air)
    wsum = None
    if WEATHER_ENABLED:
        try:
            wsum = weather.summarize(weather.fetch(WEATHER_LAT, WEATHER_LON))
        except Exception as e:
            print("Погода недоступна, випуск буде без неї:", repr(e)[:200])
    ctx = make_context(air, host, wsum)
    posts = await fetch_posts()
    if not posts:
        print("Нових постів немає, пропускаю випуск.")
        return
    intro, items, outro, wtext = summarize(posts, ctx, wsum)
    if not items:
        print("Gemini не повернув новин, пропускаю.")
        return
    texts, kinds = [intro], ["intro"]
    if PROMO_ENABLED and PROMO_TEXT.strip():
        texts.append(PROMO_TEXT.strip()); kinds.append("promo")
    texts += items; kinds += ["news"] * len(items)
    if wtext:
        texts.append(wtext); kinds.append("weather")
    texts.append(outro); kinds.append("outro")
    print(f"Ведучий(а): {host['name']}; привітання: {intro}")
    print(f"Погода: {wtext}")
    print(f"Прощання: {outro}")
    out = "episode.mp3"
    dur = await make_audio(texts, kinds, host, out)
    title = f"Новини {air:%d.%m %H:00}"
    cfg = {
        "base_url": os.environ["SITE_BASE_URL"],
        "title": opt("podcast_title", "PODCAST_TITLE", "Години новин"),
        "description": opt("podcast_desc", "PODCAST_DESC", "Регулярні випуски коротких новин з гумором."),
        "email": os.environ["PODCAST_EMAIL"],
        "telegram_url": TELEGRAM_URL,
        "weather_credit": bool(wtext),
    }
    notes = [f"Ведучий(а): {host['name']}", *[f"• {l}" for l in items]]
    if wtext:
        notes.append(f"Погода: {wtext} (дані Open-Meteo.com)")
    if TELEGRAM_URL:
        notes.append(f"Більше новин у Telegram: {TELEGRAM_URL}")
    feed.publish(os.environ.get("SITE_DIR", "site"), out, title, "\n".join(notes),
                 int(dur), now, KEEP_EPISODES, cfg)
    print(f"Готово: {len(items)} новин, {dur:.0f} с")
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
