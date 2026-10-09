"""Погода з Open-Meteo (безкоштовно для некомерційного використання, без ключа).
Дані: https://open-meteo.com
Швидкість вітру не озвучується; про вітер говоримо лише при шквалі (пориви від SQUALL_MS)."""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SQUALL_MS = 18  # пориви вітру (м/с), починаючи з яких згадуємо шквал; у ефірі цифра не звучить

WMO = {
    0: "ясно", 1: "переважно ясно", 2: "мінлива хмарність", 3: "хмарно",
    45: "туман", 48: "паморозний туман", 51: "легка мряка", 53: "мряка", 55: "густа мряка",
    56: "крижана мряка", 57: "крижана мряка", 61: "невеликий дощ", 63: "дощ", 65: "сильний дощ",
    66: "крижаний дощ", 67: "крижаний дощ", 71: "невеликий сніг", 73: "сніг", 75: "сильний сніг",
    77: "снігова крупа", 80: "короткочасні дощі", 81: "зливи", 82: "сильні зливи",
    85: "снігопад", 86: "сильний снігопад", 95: "гроза", 96: "гроза з градом", 99: "гроза з сильним градом",
}
HAZARD_CODES = {56, 57, 65, 66, 67, 75, 82, 86, 95, 96, 99}  # небезпечні явища


def fetch(lat: float, lon: float) -> dict:
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,weather_code,wind_gusts_10m",
        "wind_speed_unit": "ms", "timezone": "auto", "forecast_days": 2,
    })
    url = "https://api.open-meteo.com/v1/forecast?" + q
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except Exception as e:  # мережа/ліміт: пробуємо ще
            last = e
            time.sleep(3)
    raise last


def _num(v, default=0):
    return default if v is None else v


def summarize(data: dict, hours: int = 12, now_utc: datetime | None = None,
              squall_ms: float = SQUALL_MS) -> dict:
    """Стисла картина на найближчі години: точки (зараз, +3, +6, +12), діапазони, шквал, небезпечні явища."""
    h = data["hourly"]
    offset = timedelta(seconds=data.get("utc_offset_seconds", 0))
    now_local = ((now_utc or datetime.now(timezone.utc)) + offset).replace(tzinfo=None)
    times = [datetime.fromisoformat(t) for t in h["time"]]
    idx = max((i for i, t in enumerate(times) if t <= now_local), default=0)
    end = min(len(times), idx + hours + 1)
    rng = range(idx, end)
    pts = []
    for off in (0, 3, 6, 12):
        i = idx + off
        if i < len(times):
            pts.append({"hour": times[i].hour, "t": round(h["temperature_2m"][i]),
                        "p": round(_num(h["precipitation_probability"][i])),
                        "desc": WMO.get(int(_num(h["weather_code"][i])), "мінлива хмарність"),
                        "off": off})
    temps = [h["temperature_2m"][i] for i in rng]
    gmax = max(_num(h["wind_gusts_10m"][i]) for i in rng)
    hazards = sorted({WMO[int(h["weather_code"][i])] for i in rng
                      if int(_num(h["weather_code"][i])) in HAZARD_CODES})
    return {
        "points": pts,
        "tmin": round(min(temps)), "tmax": round(max(temps)),
        "pmax": round(max(_num(h["precipitation_probability"][i]) for i in rng)),
        "squall": gmax >= squall_ms,
        "hazards": hazards,
    }


def deg(v: int) -> str:
    return f"мінус {abs(v)}" if v < 0 else (f"плюс {v}" if v > 0 else "0")


def _label(hour: int) -> str:
    return ("вранці" if 5 <= hour < 12 else "вдень" if 12 <= hour < 17
            else "ввечері" if 17 <= hour < 22 else "вночі")


TIME_RE = re.compile(r"\d{1,2}\s*[:.]\s*\d{2}|\bо\s+\d|\bгодин|\bхвилин", re.I)


def mentions_time(text: str) -> bool:
    """True, якщо в тексті названо конкретний час (14:30, «о 18», «через 3 години»)."""
    return bool(TIME_RE.search(text))


WIND_RE = re.compile(r"\d+\s*(м/с|метр\w*\s+на\s+секунд\w*)|вітер|вітру|вітром|вітри", re.I)


def mentions_wind(text: str) -> bool:
    return bool(WIND_RE.search(text))


WIND_SPEED_RE = re.compile(r"\d+\s*(м/с|метр\w*\s+на\s+секунд\w*)", re.I)


def mentions_wind_speed(text: str) -> bool:
    """Швидкість вітру в метрах за секунду в ефірі не звучить ніколи."""
    return bool(WIND_SPEED_RE.search(text))


def prompt_lines(w: dict) -> str:
    out = []
    for p in w["points"]:
        when = "Зараз" if p["off"] == 0 else f"Згодом ({_label(p['hour'])})"
        out.append(f"{when}: {deg(p['t'])} градусів, {p['desc']}, імовірність опадів {p['p']}%.")
    out.append(f"На найближчий час загалом: температура від {deg(w['tmin'])} до {deg(w['tmax'])} градусів, "
               f"опади до {w['pmax']}%.")
    if w["squall"]:
        out.append("УВАГА: очікується шквальний вітер із дуже сильними поривами — обов'язково попередь про це, "
                   "без цифр.")
    if w["hazards"]:
        out.append("УВАГА: можливі небезпечні явища: " + ", ".join(w["hazards"]) + " — коротко попередь.")
    return "\n".join(out)


def allowed_numbers(w: dict) -> set[int]:
    return {int(n) for n in re.findall(r"\d+", prompt_lines(w))}


def numbers_ok(text: str, w: dict) -> bool:
    """Усі числа у тексті погоди мають бути з даних (захист від вигадок)."""
    ok = allowed_numbers(w)
    return all(int(n) in ok for n in re.findall(r"\d+", text))


# ---------------------------------------------------------------- числівники та узгодження
_U_NOM = ["нуль", "один", "два", "три", "чотири", "п'ять", "шість", "сім", "вісім", "дев'ять", "десять", "одинадцять",
          "дванадцять", "тринадцять", "чотирнадцять", "п'ятнадцять", "шістнадцять", "сімнадцять", "вісімнадцять",
          "дев'ятнадцять"]
_U_GEN = ["нуля", "одного", "двох", "трьох", "чотирьох", "п'яти", "шести", "семи", "восьми", "дев'яти", "десяти",
          "одинадцяти", "дванадцяти", "тринадцяти", "чотирнадцяти", "п'ятнадцяти", "шістнадцяти", "сімнадцяти",
          "вісімнадцяти", "дев'ятнадцяти"]
_T_NOM = {20: "двадцять", 30: "тридцять", 40: "сорок", 50: "п'ятдесят", 60: "шістдесят", 70: "сімдесят",
          80: "вісімдесят", 90: "дев'яносто"}
_T_GEN = {20: "двадцяти", 30: "тридцяти", 40: "сорока", 50: "п'ятдесяти", 60: "шістдесяти", 70: "сімдесяти",
          80: "вісімдесяти", 90: "дев'яноста"}


def num_words(n: int, case: str = "nom") -> str:
    """Числівник словами: nom — називний («двадцять п'ять»), gen — родовий («двадцяти п'яти»). 0–100."""
    n = abs(int(n))
    if n == 100:
        return "сто" if case == "nom" else "ста"
    units, tens = (_U_NOM, _T_NOM) if case == "nom" else (_U_GEN, _T_GEN)
    if n < 20:
        return units[n]
    t, u = n - n % 10, n % 10
    return tens[t] if u == 0 else f"{tens[t]} {units[u]}"


def _form(n: int, one: str, few: str, many: str) -> str:
    """Іменник після називного числівника: 1 градус, 2–4 градуси, 5–20 градусів, 21 градус…"""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _form_gen(n: int, sg: str, pl: str) -> str:
    """Іменник після родового числівника: до одного відсотка, до двох відсотків, до двадцяти одного відсотка."""
    n = abs(n)
    return sg if n % 10 == 1 and n % 100 != 11 else pl


def _sign(t: int) -> str:
    return "плюс " if t > 0 else ("мінус " if t < 0 else "")


def temp_nom(t: int) -> str:
    """«плюс шістнадцять градусів», «мінус три градуси», «нуль градусів»."""
    return f"{_sign(t)}{num_words(t)} {_form(t, 'градус', 'градуси', 'градусів')}"


def temp_range(lo: int, hi: int) -> str:
    """«від плюс дев'яти до плюс шістнадцяти градусів» або «близько плюс шістнадцяти градусів»."""
    if lo == hi:
        return f"близько {_sign(hi)}{num_words(hi, 'gen')} {_form_gen(hi, 'градуса', 'градусів')}"
    return (f"від {_sign(lo)}{num_words(lo, 'gen')} до {_sign(hi)}{num_words(hi, 'gen')} "
            f"{_form_gen(hi, 'градуса', 'градусів')}")


def percent_gen(p: int) -> str:
    return f"{num_words(p, 'gen')} {_form_gen(p, 'відсотка', 'відсотків')}"


def compose_text(w: dict, rng=None) -> str:
    """Текст погоди «у коді»: усі числа словами в правильних відмінках, без цифр і швидкості вітру.
    Три варіанти побудови — щоб випуски не звучали однаково."""
    import random as _r
    rng = rng or _r
    now = w["points"][0]
    rng_t = temp_range(w["tmin"], w["tmax"])
    if w["pmax"] >= 20:
        rain = rng.choice([f"Імовірність опадів — до {percent_gen(w['pmax'])}.",
                           f"Опади можливі, імовірність — до {percent_gen(w['pmax'])}."])
    else:
        rain = "Істотних опадів не очікується."
    head = rng.choice([
        f"Зараз {temp_nom(now['t'])}, {now['desc']}. У найближчі години температура — {rng_t}.",
        f"За вікном зараз {temp_nom(now['t'])}, {now['desc']}. Далі температура — {rng_t}.",
        f"Зараз {temp_nom(now['t'])}, {now['desc']}. Найближчими годинами — {rng_t}.",
    ])
    text = f"{head} {rain}"
    if w["squall"]:
        text += " Очікується шквальний вітер, будьте обережні."
    if w["hazards"]:
        text += " Можливі небезпечні явища: " + ", ".join(w["hazards"]) + "."
    return text


def fallback_text(where: str, w: dict) -> str:
    return compose_text(w)
