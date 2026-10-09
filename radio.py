"""Звук радіовипуску (numpy + scipy + ffmpeg):
- синтез джинглів і коротких перебивок у стилі танцювального радіо;
- нарізка озвучення на окремі новини за паузами;
- зведення голосу з музикою і експорт mp3.

Свої звуки можна покласти в папку assets/ у репозиторії:
open*.mp3|wav (початок), close*.mp3|wav (кінець), sting*.mp3|wav (перебивки між новинами).
Для типів, яких там немає, використовуються синтезовані звуки.
"""
import glob
import os
import random
import subprocess

import numpy as np
from scipy import signal

SR = 44100
FRAME = SR // 100  # 10 мс


# ----------------------------------------------------------------- ffmpeg / рівні

def _run(cmd, data=None) -> bytes:
    r = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr.decode(errors="ignore")[-300:])
    return r.stdout


def _tempo(tempo: float, pitch: float = 0.0) -> list:
    """Ланцюжок фільтрів ffmpeg: темп і (необов'язково) висота голосу в півтонах (мінус = нижче).
    Висота змінюється через asetrate, довжина відновлюється atempo; темп множиться окремо."""
    parts = []
    if abs(pitch) > 0.01:
        f = 2 ** (pitch / 12)
        parts += [f"aresample={SR}", f"asetrate={SR * f:.2f}", f"aresample={SR}"]
        tempo = tempo / f
    if abs(tempo - 1.0) > 0.005:
        parts.append(f"atempo={tempo:.4f}")
    return ["-af", ",".join(parts)] if parts else []


def decode_file(path: str, tempo: float = 1.0, channels: int = 1, pitch: float = 0.0) -> np.ndarray:
    out = _run(["ffmpeg", "-v", "error", "-i", path, *_tempo(tempo, pitch),
                "-f", "f32le", "-ac", str(channels), "-ar", str(SR), "-"])
    x = np.frombuffer(out, dtype="<f4").astype(np.float32)
    if channels == 2:
        return x[: len(x) // 2 * 2].reshape(-1, 2)
    return x


def decode_pcm16(pcm: bytes, rate: int = 24000, tempo: float = 1.0, pitch: float = 0.0) -> np.ndarray:
    out = _run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
                *_tempo(tempo, pitch), "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"], data=pcm)
    return np.frombuffer(out, dtype="<f4").astype(np.float32)


def _frame_rms(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0)
    f = x[: n * FRAME].astype(np.float64)
    f = f.reshape(n, FRAME, -1) if f.ndim == 2 else f.reshape(n, FRAME, 1)
    return np.sqrt(np.mean(f ** 2, axis=(1, 2)))


def rms_db(x: np.ndarray) -> float:
    """Середній рівень за «активними» фреймами (тиша не знижує оцінку)."""
    r = _frame_rms(x)
    act = r[r > 10 ** (-50 / 20)]
    if len(act) == 0:
        return -60.0
    return 20 * np.log10(np.sqrt(np.mean(act ** 2)) + 1e-12)


def normalize(x: np.ndarray, target_db: float, peak: float = 0.89) -> np.ndarray:
    y = x * 10 ** ((target_db - rms_db(x)) / 20)
    p = float(np.max(np.abs(y))) if len(y) else 0.0
    return (y * (peak / p) if p > peak else y).astype(np.float32)


def trim_silence(x: np.ndarray, thresh_db: float = -48, keep_ms: int = 70) -> np.ndarray:
    r = _frame_rms(x)
    idx = np.where(r > 10 ** (thresh_db / 20))[0]
    if len(idx) == 0:
        return x
    keep = keep_ms * SR // 1000
    a = max(0, idx[0] * FRAME - keep)
    b = min(len(x), (idx[-1] + 1) * FRAME + keep)
    return x[a:b]


def split_by_pauses(x: np.ndarray, parts: int, min_gap_ms: int = 250, edge_ms: int = 600, min_item_gap_ms: int = 600):
    """Ріже озвучення на `parts` шматків по найдовших паузах. Повертає (шматки, довжини пауз)
    або (None, []) якщо достатньо виразних пауз не знайдено."""
    if parts <= 1:
        return [x], []
    r = _frame_rms(x)
    thr = 10 ** ((rms_db(x) - 22) / 20)
    quiet = r < thr
    n, gaps, i = len(r), [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            gaps.append((i * 10, j * 10))
            i = j
        else:
            i += 1
    total_ms = n * 10
    gaps = [g for g in gaps if g[0] >= edge_ms and g[1] <= total_ms - edge_ms and g[1] - g[0] >= min_gap_ms]
    if len(gaps) < parts - 1:
        return None, [g[1] - g[0] for g in gaps]
    by_len = sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)
    chosen, others = by_len[: parts - 1], by_len[parts - 1:]
    shortest = min(g[1] - g[0] for g in chosen)
    longest_other = max((g[1] - g[0] for g in others), default=0)
    # Надійність нарізки: паузи між новинами мають явно відрізнятися від пауз усередині фраз.
    # Якщо ні — краще вставити перебивки не вдасться, ніж розрізати озвучення посеред речення.
    if shortest < min_item_gap_ms or (longest_other and shortest < 1.25 * longest_other):
        return None, [g[1] - g[0] for g in by_len[:parts + 2]]
    best = sorted(chosen)
    cuts = [((a + b) // 2) * SR // 1000 for a, b in best]
    segs, prev = [], 0
    for c in cuts:
        segs.append(x[prev:c])
        prev = c
    segs.append(x[prev:])
    return segs, [b - a for a, b in best]


# ----------------------------------------------------------------- синтез

def _t(d):
    return np.arange(int(d * SR)) / SR


def _lp(x, f, order=2):
    return signal.sosfilt(signal.butter(order, f, btype="low", fs=SR, output="sos"), x, axis=0)


def _hp(x, f, order=2):
    return signal.sosfilt(signal.butter(order, f, btype="high", fs=SR, output="sos"), x, axis=0)


def _bp(x, lo, hi):
    return signal.sosfilt(signal.butter(2, [lo, hi], btype="band", fs=SR, output="sos"), x, axis=0)


def mtof(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def _st(n):
    return np.zeros((int(n), 2), np.float32)


def _add(buf, snd, at, gain=1.0, pan=0.0):
    """Додає звук у стерео-буфер. Моно-звук ставиться на панораму pan (-1 ліво ... +1 право),
    стерео-звук додається як є."""
    if snd.ndim == 1 and buf.ndim == 2:
        left, right = 1 - max(0.0, pan), 1 + min(0.0, pan)
        snd = np.stack([snd * left, snd * right], axis=1)
    a = int(at * SR)
    if a < 0:
        snd, a = snd[-a:], 0
    b = a + len(snd)
    if b > len(buf):
        snd = snd[: len(buf) - a]
        b = len(buf)
    if len(snd) > 0:
        buf[a:b] += snd * gain


def kick(vol=1.0):
    t = _t(0.38)
    ph = 2 * np.pi * np.cumsum(48 + 125 * np.exp(-t * 32)) / SR
    return (np.sin(ph) * np.exp(-t * 7.5) + np.exp(-t * 700) * 0.3) * vol


def clap(rng, vol=0.7):
    t = _t(0.25)
    n = _bp(rng.standard_normal(len(t)), 900, 3000)
    env = np.exp(-t * 20) * (1 + 0.9 * (np.sin(2 * np.pi * 95 * t) > 0))
    return n * env * vol


def hat(rng, open_=False, vol=0.35):
    t = _t(0.22 if open_ else 0.06)
    return _hp(rng.standard_normal(len(t)), 7000) * np.exp(-t * (14 if open_ else 75)) * vol


def bass(f, d, vol=0.7):
    t = _t(d)
    saw = signal.sawtooth(2 * np.pi * f * t)
    x = _lp(saw, 260) * 0.8 + np.sin(2 * np.pi * f * t) * 0.6
    return np.tanh(x * 1.5) * np.minimum(1, t * 400) * np.exp(-t * (3.5 / max(d, 0.1))) * vol


def stab(freqs, d=0.24, vol=0.35):
    t = _t(d)
    sides = []
    for dets, ph in (((-0.008, 0.0, 0.006), 0.0), ((-0.005, 0.003, 0.009), 1.1)):
        x = np.zeros(len(t))
        for f in freqs:
            for det in dets:
                x += signal.sawtooth(2 * np.pi * f * (1 + det) * t + ph)
        sides.append(_lp(x / (3 * len(freqs)), 3200) * np.exp(-t * 11) * vol)
    return np.stack(sides, axis=1)


def pluck(f, d=0.16, vol=0.3):
    t = _t(d)
    x = signal.square(2 * np.pi * f * t, duty=0.35) + 0.5 * signal.sawtooth(2 * np.pi * f * 2 * t)
    return _lp(x, 4200) * np.exp(-t * 16) * np.minimum(1, t * 600) * vol


def bell(f, d=0.9, vol=0.3):
    t = _t(d)
    x = np.sin(2 * np.pi * f * t + 3.2 * np.exp(-t * 6) * np.sin(2 * np.pi * f * 2 * t))
    return x * np.exp(-t * 4.5) * vol


def riser(d, rng, vol=0.6):
    t = _t(d)
    n = rng.standard_normal((len(t), 2))  # окремий шум для L і R — широкий звук
    out, blocks = np.zeros((len(t), 2)), 24
    size = len(t) // blocks
    for i in range(blocks):
        a, b = i * size, (i + 1) * size if i < blocks - 1 else len(t)
        out[a:b] = _bp(n, 400 + 6000 * (i / blocks) ** 2, 900 + 9000 * (i / blocks) ** 2)[a:b]
    sweep = np.sin(2 * np.pi * np.cumsum(250 * 2 ** (t / d * 3.5)) / SR) * 0.25
    return (out * 2.0 + sweep[:, None]) * ((t / d) ** 2)[:, None] * vol


def rev_crash(d, rng, vol=0.5):
    t = _t(d)
    return _hp(rng.standard_normal((len(t), 2)), 3500) * ((t / d) ** 3)[:, None] * vol


def crash(d, rng, vol=0.5):
    t = _t(d)
    return _hp(rng.standard_normal((len(t), 2)), 4200) * np.exp(-t * (3.2 / d * 1.6))[:, None] * vol


def zap(d=0.3, f0=2600, f1=180, vol=0.35):
    t = _t(d)
    ph = 2 * np.pi * np.cumsum(f1 + (f0 - f1) * np.exp(-t * 14 / d)) / SR
    return np.sin(ph) * np.exp(-t * 6 / d) * vol


def whoosh(d, rng, up=True, vol=0.5):
    t = _t(d)
    n = rng.standard_normal((len(t), 2))
    out, blocks = np.zeros((len(t), 2)), 16
    size = len(t) // blocks
    for i in range(blocks):
        pos = i / blocks if up else 1 - i / blocks
        a, b = i * size, (i + 1) * size if i < blocks - 1 else len(t)
        out[a:b] = _bp(n, 300 + 5000 * pos ** 2, 700 + 8000 * pos ** 2)[a:b]
    env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 1.5
    return out * env[:, None] * 2.0 * vol


def boom(d=1.0, rng=None, vol=0.9):
    t = _t(d)
    ph = 2 * np.pi * np.cumsum(32 + 90 * np.exp(-t * 7)) / SR
    return np.sin(ph) * np.exp(-t * 3.2) * vol


def tape_stop(freqs, d=1.2, vol=0.4):
    t = _t(d)
    x = np.zeros(len(t))
    for f in freqs:
        ph = 2 * np.pi * np.cumsum(f * np.exp(-t * 2.6)) / SR
        x += signal.sawtooth(ph)
    return _lp(x / len(freqs), 2600) * np.exp(-t * 1.6) * vol


def echo(x, delay=0.18, fb=0.4, n=3):
    """Пінг-понг ехо: повтори перекидаються між каналами."""
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    out = np.concatenate([x, np.zeros((int(delay * SR * n), 2))])
    for i in range(1, n + 1):
        d = int(delay * SR * i)
        src = x[:, ::-1] if i % 2 else x
        out[d:d + len(x)] += src * fb ** i
    return out


def _widen(x, k):
    """Розширення стереобази: підсилює бічний канал, центр (кік, бас) лишається на місці."""
    mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2 * k
    return np.stack([mid + side, mid - side], axis=1).astype(np.float32)


def _finish(buf, target_db, peak=0.89, widen=1.0):
    if buf.ndim == 2 and widen != 1.0:
        buf = _widen(buf, widen)
    buf = np.tanh(buf * 1.1) / 1.1
    a, b = int(0.003 * SR), int(0.04 * SR)
    fi, fo = np.linspace(0, 1, a), np.linspace(1, 0, b)
    if buf.ndim == 2:
        fi, fo = fi[:, None], fo[:, None]
    buf[:a] *= fi
    buf[-b:] *= fo
    return normalize(buf.astype(np.float32), target_db, peak)


def make_open(seed, bpm, root, chord, arp):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat
    buf = _st(int((3 * bar + 1.6) * SR))
    _add(buf, riser(bar, rng), 0)
    roll = [bar - 2 * beat, bar - 1.5 * beat] + [bar - beat + i * beat / 4 for i in range(4)]
    for i, tt in enumerate(roll):
        _add(buf, clap(rng, 0.35 + 0.1 * i), tt)
    _add(buf, rev_crash(beat * 2, rng), bar - 2 * beat)
    d0 = bar
    _add(buf, crash(1.8, rng), d0, 0.6)
    _add(buf, boom(1.2), d0, 0.6)
    for b in range(8):
        _add(buf, kick(), d0 + b * beat)
        _add(buf, hat(rng, True), d0 + b * beat + beat / 2, pan=(-0.5 if b % 2 else 0.5))
        _add(buf, bass(mtof(root - 24 + (7 if b % 4 == 3 else 0)), beat * 0.45), d0 + b * beat + beat / 2)
    for b in (1, 3, 5, 7):
        _add(buf, clap(rng), d0 + b * beat)
    for b in (0, 2, 4, 6):
        _add(buf, hat(rng), d0 + b * beat + beat / 4, pan=(-0.6 if b % 4 == 0 else 0.6))
    for bar_i in (0, 1):
        _add(buf, stab([mtof(root + c) for c in chord], 0.3), d0 + bar_i * bar + 1.5 * beat)
    for i in range(16):
        _add(buf, pluck(mtof(root + 12 + arp[i % len(arp)])), d0 + bar + i * beat / 4, pan=0.7 * np.sin(i * 1.7))
    end = 3 * bar
    _add(buf, crash(1.6, rng), end, 0.7)
    _add(buf, stab([mtof(root + c) for c in chord], 0.9, 0.5), end)
    _add(buf, kick(), end)
    return _finish(buf, -21, widen=1.4)


def make_close(seed, bpm, root, chord, arp, tape=False):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat
    buf = _st(int((2 * bar + 2.2) * SR))
    for b in range(8 if not tape else 6):
        _add(buf, kick(), b * beat)
        _add(buf, hat(rng, True), b * beat + beat / 2, pan=(-0.5 if b % 2 else 0.5))
        _add(buf, bass(mtof(root - 24 + (5 if b % 4 == 2 else 0)), beat * 0.45), b * beat + beat / 2)
        if b % 2 == 1:
            _add(buf, clap(rng), b * beat)
    for i in range(24 if not tape else 20):
        _add(buf, pluck(mtof(root + 12 + arp[i % len(arp)])), i * beat / 4, pan=0.7 * np.sin(i * 1.7))
    end = 2 * bar if not tape else 1.5 * bar
    if tape:
        _add(buf, tape_stop([mtof(root + c) for c in chord], 1.6), end - 0.2)
    else:
        _add(buf, crash(2.0, rng), end, 0.7)
        _add(buf, stab([mtof(root + c) for c in chord], 1.1, 0.5), end)
        _add(buf, boom(1.4), end, 0.7)
    _add(buf, kick(), end)
    return _finish(buf, -21, widen=2.0)


def make_stingers():
    out = []
    r = np.random.default_rng(11)
    # 1: лазерний зап + хлопок + хет-рол
    b = _st(int(1.3 * SR))
    _add(b, zap(0.35), 0)
    _add(b, clap(r), 0.32, 0.9)
    for i in range(4):
        _add(b, hat(r), 0.5 + i * 0.07, pan=(-0.6 if i % 2 else 0.6))
    _add(b, kick(), 0.32)
    out.append(_finish(b, -23, widen=1.8))
    # 2: висхідне арпеджіо + ехо
    b = _st(int(1.2 * SR))
    for i, n in enumerate((0, 3, 7, 12, 15)):
        _add(b, pluck(mtof(60 + n), 0.14, 0.4), i * 0.1, pan=-0.7 + 0.35 * i)
    _add(b, kick(), 0.5)
    b = echo(b, 0.14, 0.35, 2)
    out.append(_finish(b, -23, widen=1.8))
    # 3: вуш + бум
    b = _st(int(1.5 * SR))
    _add(b, whoosh(0.7, r), 0)
    _add(b, boom(0.9), 0.62, 0.9)
    _add(b, crash(0.8, r, 0.4), 0.62)
    out.append(_finish(b, -23, widen=1.8))
    # 4: бас-вобл
    b = _st(int(1.4 * SR))
    t = _t(1.0)
    wob = np.tanh(2.5 * _lp(signal.sawtooth(2 * np.pi * 55 * t), 500)) * (0.55 + 0.45 * np.sin(2 * np.pi * 7 * t))
    _add(b, wob * np.exp(-t * 1.2) * 0.8, 0)
    _add(b, clap(r), 0.5)
    _add(b, hat(r, True), 0.25, pan=0.5)
    out.append(_finish(b, -23, widen=1.8))
    # 5: дзвіночок + кік
    b = _st(int(1.4 * SR))
    _add(b, bell(mtof(84), 0.9, 0.45), 0, pan=-0.35)
    _add(b, bell(mtof(91), 0.8, 0.3), 0.14, pan=0.4)
    _add(b, kick(), 0)
    _add(b, clap(r), 0.46)
    b = echo(b, 0.21, 0.3, 2)
    out.append(_finish(b, -23, widen=1.8))
    return out


def pad(freqs, d, vol=0.16):
    t = _t(d)
    env = np.minimum(1, t / 0.6) * np.minimum(1, (d - t) / 0.9)
    sides = []
    for dets, ph in (((-0.0045, 0.0005, 0.0035), 0.0), ((-0.0030, 0.0020, 0.0050), 1.3)):
        x = np.zeros(len(t))
        for f in freqs:
            for det in dets:
                x += signal.sawtooth(2 * np.pi * f * (1 + det) * t + ph)
        sides.append(_lp(x / (3 * len(freqs)), 1400) * env * vol)
    return np.stack(sides, axis=1)


def make_bed(seed=21, bpm=118):
    """Безшовна петля 8 тактів: тихий танцювальний фон (Am-F-C-G)."""
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat
    loop = int(8 * bar * SR)
    buf = _st(loop + 4 * SR)
    chords = [(57, (0, 3, 7)), (53, (0, 4, 7)), (48, (0, 4, 7)), (55, (0, 4, 7))]
    for ci, (root, ch) in enumerate(chords):
        t0 = ci * 2 * bar
        _add(buf, pad([mtof(root + 12 + c) for c in ch], 2 * bar + 0.8), t0)
        for b in range(8):
            _add(buf, bass(mtof(root - 12), beat * 0.4, 0.5), t0 + b * beat + beat / 2)
    for b in range(32):
        _add(buf, kick(), b * beat, 0.45)
        _add(buf, hat(rng, False, 0.22), b * beat + beat / 2, pan=(-0.5 if b % 2 else 0.5))
    for i in range(64):
        root, ch = chords[(i // 16) % 4]
        _add(buf, pluck(mtof(root + 24 + ch[(i * 3) % 3]), 0.14, 0.10), i * beat / 2, pan=0.8 * np.sin(i * 0.9))
    tail = buf[loop:].copy()
    out = buf[:loop].copy()
    out[: len(tail)] += tail[: len(out)]  # хвости загортаємо на початок: петля без шва
    return normalize(_widen(out, 2.2), -21)


def make_weather_jingle():
    """Особлива перебивка перед погодою: повітряні «крапельки» дзвіночків, пад і мерехтіння."""
    rng = np.random.default_rng(31)
    buf = _st(int(2.8 * SR))
    _add(buf, pad([mtof(n) for n in (60, 64, 67, 71)], 2.2, 0.20), 0.0)
    for i, n in enumerate((84, 88, 91, 95, 91, 88)):
        _add(buf, bell(mtof(n), 0.7, 0.28), 0.10 + i * 0.13, pan=-0.8 + 0.32 * i)
    _add(buf, whoosh(0.9, rng, up=True, vol=0.22), 0.0)
    for i in range(6):
        _add(buf, hat(rng, False, 0.12), 0.9 + i * 0.09, pan=(0.7 if i % 2 else -0.7))
    _add(buf, bell(mtof(96), 1.0, 0.34), 1.0)
    return _finish(echo(buf, 0.22, 0.35, 2), -22, widen=1.3)


def _tight(lst):
    return [trim_silence(x, -55, 120) for x in lst]


def synth_assets() -> dict:
    d = {
        "open": [
            make_open(1, 128, 57, (0, 3, 7), (0, 3, 7, 12, 7, 3, 0, -5)),   # A мінор
            make_open(2, 126, 50, (0, 3, 7, 10), (0, 7, 12, 10, 7, 3, 5, 3)),  # D мінор
        ],
        "close": [
            make_close(3, 128, 57, (0, 3, 7), (0, 3, 7, 12, 7, 3)),
            make_close(4, 126, 52, (0, 3, 7), (0, 5, 7, 12, 10, 7), tape=True),
        ],
        "sting": make_stingers(),
    }
    out = {k: _tight(v) for k, v in d.items()}
    out["bed"] = [make_bed()]
    out["weather"] = _tight([make_weather_jingle()])
    return out


def _db(v):
    return 10 ** (v / 20)


def load_assets(asset_dir: str = "assets", pack: str | None = None) -> dict:
    """Звуки для випуску. Пріоритет: власні файли в assets/ (open*, close*, sting*, weather*, bed*),
    далі звуковий пакет `pack` (див. soundpacks.py), далі вбудований синтез.
    Повертає також bed_seamless: True, якщо фон — наша безшовна петля."""
    synth, out, pk = None, {}, None
    for kind, target in (("open", -21), ("close", -21), ("sting", -23), ("weather", -22), ("bed", -19)):
        files = []
        for ext in ("mp3", "wav", "ogg", "m4a"):
            files += glob.glob(os.path.join(asset_dir, f"{kind}*.{ext}"))
        files = sorted(files)
        if files:
            if kind != "sting":
                files = [random.choice(files)]  # open/close/weather/bed: один випадковий файл
            out[kind] = [normalize(decode_file(f, channels=2), target) for f in files]
            if kind == "bed":
                out["bed_seamless"] = False
            continue
        if pack and pk is None:
            try:
                import soundpacks
                pk = soundpacks.build_pack(pack)
            except Exception as e:  # пакет не зібрався — лишаємось на вбудованому синтезі
                print(f"Звуковий пакет «{pack}» не зібрався ({repr(e)[:120]}), беру вбудований")
                pk = False
        if pk:
            out[kind] = [normalize(x, target) for x in pk[kind]]
        else:
            synth = synth or synth_assets()
            out[kind] = synth[kind]
        if kind == "bed":
            out["bed_seamless"] = True
    return out


def _as_stereo(x):
    return x if x is None or x.ndim == 2 else np.stack([x, x], axis=1)


def _mix(buf: np.ndarray, snd: np.ndarray, pos: int) -> np.ndarray:
    end = pos + len(snd)
    if end > len(buf):
        buf = np.concatenate([buf, np.zeros((end - len(buf),) + buf.shape[1:], np.float32)])
    buf[pos:end] += snd
    return buf


def _speech_env(vt: np.ndarray, n: int) -> np.ndarray:
    """0..1 — «зараз говорить голос» (швидка атака, повільний спад) для притискання фону."""
    r = _frame_rms(vt)
    active = (r > _db(-45)).astype(np.float64)
    env = np.zeros(len(active))
    y, a_att, a_rel = 0.0, 1 - np.exp(-1 / 5), 1 - np.exp(-1 / 35)
    for i, v in enumerate(active):
        y += (a_att if v > y else a_rel) * (v - y)
        env[i] = y
    xs = (np.arange(len(env)) + 0.5) * FRAME
    return np.interp(np.arange(n), xs, env) if len(env) else np.zeros(n)


def _tile(loop: np.ndarray, n: int, seamless: bool) -> np.ndarray:
    """Розтягує фон на n відліків. Наша петля безшовна; чужі треки зшиваємо кросфейдом."""
    if seamless:
        return np.tile(loop, (n // len(loop) + 1, 1))[:n]
    cf = min(SR, len(loop) // 4)
    out, fade = loop.copy(), np.linspace(0, 1, cf)[:, None]
    while len(out) < n:
        out = np.concatenate([out[:-cf], out[-cf:] * (1 - fade) + loop[:cf] * fade, loop[cf:]])
    return out[:n]


def assemble(parts, kinds=None, opener=None, closer=None, stingers=None, bed=None,
             bed_seamless=True, bed_under_db=-20.0, bed_gap_db=-12.0,
             jingle_gain_db=0.0, sting_gain_db=0.0, gap_ms: int = 700, weather_sting=None,
             pre_ms: int = 450, post_ms: int = 250) -> np.ndarray:
    """Зводить випуск у СТЕРЕО (n, 2). Голос — моно, по центру; музика, джингли й фон — стерео."""
    ms = lambda v: int(v * SR / 1000)
    kinds = kinds or ["news"] * len(parts)
    opener, closer, weather_sting, bed = map(_as_stereo, (opener, closer, weather_sting, bed))
    stingers = [_as_stereo(x) for x in (stingers or [])]
    track = np.zeros((0, 2), np.float32)
    vt = np.zeros(0, np.float32)  # окремо лише голос — для притискання фону
    cursor = 0
    if opener is not None:
        ov = min(ms(1800), len(opener) // 2)
        o = opener * _db(jingle_gain_db)
        o[-ov:] *= np.linspace(1.0, 0.18, ov)[:, None]  # музика «сідає» під привітання
        track = _mix(track, o, 0)
        cursor = len(o) - ov
    voice_start = cursor
    last = None
    for i, p in enumerate(parts):
        track = _mix(track, np.stack([p, p], axis=1), cursor)  # голос по центру
        vt = _mix(vt, p, cursor)
        end = cursor + len(p)
        if i < len(parts) - 1:
            use_w = kinds[i + 1] == "weather" and weather_sting is not None
            if (stingers or use_w) and kinds[i + 1] in ("news", "weather"):
                if use_w:
                    st = weather_sting * _db(sting_gain_db)  # особлива перебивка лише перед погодою
                else:
                    choices = [k for k in range(len(stingers)) if k != last] or [0]
                    last = random.choice(choices)
                    st = stingers[last] * _db(sting_gain_db)
                s0 = end + ms(pre_ms)  # тиша після голосу, потім перебивка, потім тиша перед наступною новиною
                track = _mix(track, st, s0)
                cursor = s0 + len(st) + ms(post_ms)
            else:
                cursor = end + ms(gap_ms)
        else:
            cursor = end
    bed_end = len(track)
    if closer is not None:
        ov = min(ms(1500), len(closer) // 2)
        c = closer * _db(jingle_gain_db)
        c[:ov] *= np.linspace(0.3, 1.0, ov)[:, None]  # музика піднімається під останні слова
        bed_end = max(0, cursor - ov)
        track = _mix(track, c, bed_end)
    if bed is not None and bed_end > voice_start:
        n = len(track)
        vt = np.concatenate([vt, np.zeros(max(0, n - len(vt)), np.float32)])
        env = _speech_env(vt, n)
        gap_g, under_g = _db(bed_gap_db), _db(bed_under_db)
        b = _tile(bed, n, bed_seamless)
        b = b - 0.55 * _bp(b, 500, 3200).astype(np.float32)  # звільняємо смугу мови
        g = (gap_g + (under_g - gap_g) * env).astype(np.float32)
        mask = np.zeros(n, np.float32)
        mask[voice_start:bed_end] = 1.0
        fi, fo = min(ms(1500), bed_end - voice_start), min(ms(1000), bed_end - voice_start)
        mask[voice_start:voice_start + fi] *= np.linspace(0, 1, fi)
        if fo > 0:
            mask[bed_end - fo:bed_end] *= np.linspace(1, 0, fo)
        track += b * (g * mask)[:, None]
    f = min(len(track), ms(600))
    track[-f:] *= np.linspace(1, 0, f)[:, None]
    peak = float(np.max(np.abs(track))) if len(track) else 0
    if peak > 0.95:
        track *= 0.95 / peak
    return track


def export_mp3(x: np.ndarray, path: str, bitrate: str = "96k") -> None:
    """Експорт у стерео-mp3 (моно-масив дублюється в обидва канали)."""
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()  # інтерливінг L,R,L,R...
    _run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
          "-codec:a", "libmp3lame", "-b:a", bitrate, "-ac", "2", path], data=pcm)
