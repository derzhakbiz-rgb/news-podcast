"""Погода з Open-Meteo (безкоштовно для некомерційного використання, без ключа).
Дані: https://open-meteo.com"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

WMO = {
    0: "ясно", 1: "переважно ясно", 2: "мінлива хмарність", 3: "хмарно",
    45: "туман", 48: "паморозний туман", 51: "легка мряка", 53: "мряка", 55: "густа мряка",
    56: "крижана мряка", 57: "крижана мряка", 61: "невеликий дощ", 63: "дощ", 65: "сильний дощ",
    66: "крижаний дощ", 67: "крижаний дощ", 71: "невеликий сніг", 73: "сніг", 75: "сильний сніг",
    77: "снігова крупа", 80: "короткочасні дощі", 81: "зливи", 82: "сильні зливи",
    85: "снігопад", 86: "сильний снігопад", 95: "гроза", 96: "гроза з градом", 99: "гроза з сильним градом",
}


def fetch(lat: float, lon: float) -> dict:
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
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


def summarize(data: dict, hours: int = 12, now_utc: datetime | None = None) -> dict:
    """Стисла картина на найближчі години: точки (зараз, +3, +6, +12) і діапазони."""
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
                        "w": round(_num(h["wind_speed_10m"][i])), "off": off})
    temps = [h["temperature_2m"][i] for i in rng]
    return {
        "points": pts,
        "tmin": round(min(temps)), "tmax": round(max(temps)),
        "pmax": round(max(_num(h["precipitation_probability"][i]) for i in rng)),
        "wmax": round(max(_num(h["wind_speed_10m"][i]) for i in rng)),
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


def prompt_lines(w: dict) -> str:
    out = []
    for p in w["points"]:
        when = "Зараз" if p["off"] == 0 else f"Згодом ({_label(p['hour'])})"
        out.append(f"{when}: {deg(p['t'])} градусів, {p['desc']}, "
                   f"вітер {p['w']} м/с, імовірність опадів {p['p']}%.")
    out.append(f"На найближчий час загалом: температура від {deg(w['tmin'])} до {deg(w['tmax'])} градусів, "
               f"опади до {w['pmax']}%, вітер до {w['wmax']} м/с.")
    return "\n".join(out)


def allowed_numbers(w: dict) -> set[int]:
    return {int(n) for n in re.findall(r"\d+", prompt_lines(w))}


def numbers_ok(text: str, w: dict) -> bool:
    """Усі числа у тексті погоди мають бути з даних (захист від вигадок)."""
    ok = allowed_numbers(w)
    return all(int(n) in ok for n in re.findall(r"\d+", text))


def fallback_text(where: str, w: dict) -> str:
    now = w["points"][0]
    rain = (f" Імовірність опадів до {w['pmax']} відсотків." if w["pmax"] >= 20
            else " Істотних опадів не очікується.")
    return (f"Погода {where} на найближчі години: зараз {deg(now['t'])} градусів, {now['desc']}. "
            f"Далі температура від {deg(w['tmin'])} до {deg(w['tmax'])} градусів.{rain} "
            f"Вітер до {w['wmax']} метрів на секунду.")
