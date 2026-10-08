"""Сім звукових пакетів (від нейтрального house до trance і нічного chill).
Кожен повертає: intro, outro, st1, st2, st3, bed, weather (стерео-масиви). Детерміновано."""
import numpy as np

from packgen import *  # noqa: F401,F403
import packgen as G
import radio as R

ADD = G.add


# ------------------------------------------------------------------ лінії (бас, гармонія, арпеджіо)
ACID_PAT = [(0, 1, 0), (0, 0, 0), (12, 0, 0), (0, 0, 0), (3, 0, 1), (0, 0, 0), (10, 1, 0), (0, 0, 0),
            (0, 0, 0), (7, 0, 1), (0, 0, 0), (12, 1, 0), (0, 0, 0), (0, 0, 0), (5, 0, 0), (3, 0, 1)]


def bassline(buf, g, tb, root, style, rng, lv, k=0):
    if style == "off":
        for b in range(4):
            ADD(buf, R.bass(mtof(root - 24 + (7 if b == 3 else 0)), g.beat * 0.42, 0.5), tb + b * g.beat + g.beat / 2, lv)
    elif style == "sub":
        ADD(buf, R.bass(mtof(root - 24), g.bar * 0.92, 0.62), tb, lv)
        ADD(buf, R.bass(mtof(root - 12), g.beat * 0.5, 0.35), tb + 2.5 * g.beat, lv)
    elif style == "tech":
        for s, off in ((0, 0), (3, 0), (6, 3), (8, 0), (11, 7), (14, 0)):
            ADD(buf, R.bass(mtof(root - 24 + off), g.step * 1.6, 0.55), tb + s * g.step, lv)
    elif style == "roll":
        for b in range(4):
            for s in (1, 2, 3):
                ADD(buf, R.bass(mtof(root - 24), g.step * 0.92, 0.5), tb + b * g.beat + s * g.step, lv)
    elif style == "acid":
        prev = None
        for i, (semi, acc, slide) in enumerate(ACID_PAT):
            if semi == 0 and acc == 0 and slide == 0 and i not in (0, 2):
                continue
            f = mtof(root - 24 + semi)
            ADD(buf, G.acid(f, g.step * (1.9 if slide else 0.9), accent=bool(acc), f_from=(prev if slide else None),
                            vol=0.5 * lv), tb + i * g.step, 1.0, pan=0.0)
            prev = f


def harmony(buf, g, tb, root, iv, style, lv, k=0, rng=None):
    if style == "stab":
        for beat, pan in ((0.5, -0.3), (2.5, 0.3), (3.5, -0.2)):
            ADD(buf, R.stab(chord(root, iv, 1), 0.33, 0.30), tb + beat * g.beat, lv)
    elif style == "stab_sparse":
        ADD(buf, R.stab(chord(root, iv[:3], 1), 0.22, 0.26), tb + 1.5 * g.beat, lv)
    elif style == "super_gate":
        x = sum(G.supersaw(f, g.bar, 0.16, decay=0.0) for f in chord(root, iv, 0))
        ADD(buf, G.duck(G.gate(x, g, "xx.xx.xx.xx.xx.x"), g, 0.45), tb, lv)
    elif style == "super_long":
        x = sum(G.supersaw(f, g.bar + 0.1, 0.15, atk=0.05, rel=0.2) for f in chord(root, iv, 0))
        ADD(buf, G.duck(x, g, 0.5), tb, lv)


def arp(buf, g, tb, root, iv, style, lv, k=0, rng=None):
    tones = chord(root, iv, 1)
    n = len(tones)
    seq = [0, 1, 2, n - 1, 2, 1]
    if style == "pluck":
        for i in range(16):
            ADD(buf, R.pluck(tones[seq[i % len(seq)] % n] * (2 if (i // 8) % 2 else 1), 0.15, 0.2), tb + i * g.step, lv, pan=0.7 * np.sin(i * 1.7))
    elif style == "super":
        for i in range(16):
            ADD(buf, G.supersaw(tones[seq[i % len(seq)] % n] * 2, 0.16, 0.14, voices=5, decay=9.0, lp=5500), tb + i * g.step, lv)
    elif style == "bell":
        for i in range(0, 16, 3):
            ADD(buf, R.bell(tones[seq[(i // 3) % len(seq)] % n] * 2, 0.8, 0.20), tb + i * g.step, lv, pan=0.8 * np.sin(i))
    elif style == "blip":
        for i in (2, 5, 7, 10, 13):
            ADD(buf, G.blip(tones[(i // 2) % n] * 2, 0.12, 0.30), tb + i * g.step, lv, pan=0.6 * np.sin(i * 2.3))


def pad_layer(buf, g, t0, nbars, prog, lv, depth=0.5, vol=0.2, rng=None):
    pb = st((nbars * g.bar + 1.0) * SR)
    for k in range(nbars):
        root, iv = prog[k % len(prog)]
        ADD(pb, R.pad(chord(root, iv, 0), g.bar + 0.6, vol), k * g.bar)
    pb = G.duck(pb, g, depth, 0.2, offset=0.0)
    ADD(buf, pb, t0, lv)


def layers(buf, g, t0, nbars, rng, prog, S, lv=1.0, bar0=0, parts=("drums", "bass", "harm", "arp", "pad")):
    if "pad" in parts and S.get("pad"):
        pad_layer(buf, g, t0, nbars, prog, lv, S["pad"][0], S["pad"][1])
    for k in range(nbars):
        root, iv = prog[(bar0 + k) % len(prog)]
        tb = t0 + k * g.bar
        if "drums" in parts:
            G.drums(buf, g, tb, 4, rng, vol=lv, **S["drums"])
        if "bass" in parts and S.get("bass"):
            bassline(buf, g, tb, root, S["bass"], rng, lv, k)
        if "harm" in parts and S.get("harm"):
            harmony(buf, g, tb, root, iv, S["harm"], lv, k, rng)
        if "arp" in parts and S.get("arp"):
            arp(buf, g, tb, root, iv, S["arp"], lv, k, rng)


def impact(buf, g, t, rng, prog0, big=True):
    ADD(buf, R.crash(1.8, rng, 0.55 if big else 0.35), t)
    ADD(buf, R.boom(1.2, rng, 0.55), t)
    ADD(buf, R.kick(), t)


# ------------------------------------------------------------------ збірка стандартних ассетів пакета
def build_standard(S, rng, build="riser", finish="hit", intro_extra=None, outro_extra=None):
    g = Grid(S["bpm"])
    prog = S["prog"]
    out = {}
    # ---- INTRO: 1 такт нарощування + 3 такти груву + удар
    buf = st((4 * g.bar + 2.0) * SR)
    ADD(buf, R.riser(g.bar, rng, 0.5), 0)
    ADD(buf, R.rev_crash(g.beat * 2, rng, 0.4), g.bar - 2 * g.beat)
    for i in range(8):
        ADD(buf, R.hat(rng, False, 0.07 + 0.02 * i), i * g.beat / 2, 1.0, pan=(-0.5 if i % 2 else 0.5))
    if build == "snare":  # трансовий снейр-рол
        times = [g.bar - 2 * g.beat + i * g.beat / 2 for i in range(4)] + [g.bar - g.beat + i * g.beat / 4 for i in range(4)]
        for i, tt in enumerate(times):
            ADD(buf, G.snare(rng, 0.3 + 0.07 * i), tt)
    if intro_extra:
        intro_extra(buf, g, rng)
    impact(buf, g, g.bar, rng, prog[0])
    layers(buf, g, g.bar, 3, rng, prog, S)
    ADD(buf, R.crash(1.8, rng, 0.55), 4 * g.bar)
    ADD(buf, R.stab(chord(prog[0][0], prog[0][1], 1), 1.2, 0.45), 4 * g.bar)
    ADD(buf, R.kick(), 4 * g.bar)
    out["intro"] = master(buf, -17, 1.35)

    # ---- OUTRO: 2 такти груву + завершення
    buf = st((3 * g.bar + 2.4) * SR)
    layers(buf, g, 0, 2, rng, prog, S, 1.0, bar0=2)
    t_end = 2 * g.bar
    ADD(buf, R.rev_crash(g.beat * 2, rng, 0.3), t_end - 2 * g.beat)
    if finish == "hit":
        impact(buf, g, t_end, rng, prog[0])
        ADD(buf, R.stab(chord(prog[0][0], prog[0][1], 1), 1.4, 0.5), t_end)
    elif finish == "tape":
        ADD(buf, R.tape_stop(chord(prog[0][0], prog[0][1], 1), 1.8, 0.5), t_end - 0.1)
        ADD(buf, R.kick(), t_end)
    elif finish == "fade":
        ADD(buf, R.pad(chord(prog[0][0], prog[0][1], 0), 2.6, 0.30), t_end - 0.2)
        for i, n in enumerate((0, 7, 12, 15)):
            ADD(buf, R.bell(mtof(prog[0][0] + 24 + n), 1.0, 0.25), t_end + i * 0.18, 1.0, pan=-0.6 + 0.4 * i)
        ADD(buf, R.kick(), t_end)
    elif finish == "breakdown":  # трансове «затихання»: ударні виходять, лишається пад і арп
        ADD(buf, G.wash(R.pad(chord(prog[0][0], prog[0][1], 0), 2.8, 0.3), 0.6, 0.4), t_end - 0.2)
        ADD(buf, R.crash(2.0, rng, 0.45), t_end)
        ADD(buf, R.kick(), t_end)
    if outro_extra:
        outro_extra(buf, g, rng, t_end)
    out["outro"] = master(buf, -17, 1.35)

    # ---- BED: петля 8 тактів, тихіша (без ударів і крешів)
    n_bars = 8
    loop = int(n_bars * g.bar * SR)
    buf = st(loop + 4 * SR)
    layers(buf, g, 0, n_bars, rng, prog, S.get("bed", S), 0.8)
    bed = G.fold_loop(buf, loop)
    w = 2.2
    while True:
        y = G.finalize(R._widen(bed, w), -20)
        if G._corr(y) >= 0.55 or w <= 1.0:
            break
        w -= 0.2
    out["bed"] = y
    return out, g


def tail_fx(x, tail=0.9, mix=0.4):
    return G.wash(x, tail, mix)


# ================================================================== ПАК 1 — Neutral House
def pack1(rng):
    S = dict(bpm=124, prog=[(57, (0, 3, 7, 10)), (53, (0, 4, 7, 11)), (48, (0, 4, 7, 11)), (55, (0, 4, 7, 9))],
             drums=dict(hat="off", clap=True), bass="off", harm="stab")
    S["bed"] = dict(S, pad=(0.5, 0.12))
    out, g = build_standard(S, rng, build="riser", finish="hit")
    A = chord(57, (0, 3, 7, 10), 1)
    b = st(1.4 * SR)
    ADD(b, R.stab(A, 0.5, 0.45), 0); ADD(b, R.clap(rng, 0.8), 0); ADD(b, R.crash(0.9, rng, 0.35), 0)
    out["st1"] = master(b, -19, 1.5)
    b = st(1.5 * SR)
    for i in range(8):
        ADD(b, R.hat(rng, False, 0.12 + 0.03 * i), i * (0.42 - 0.04 * i) * 0.5, 1.0, pan=0.6 * (-1) ** i)
    ADD(b, R.whoosh(0.8, rng, True, 0.3), 0.1); ADD(b, R.kick(), 0.85); ADD(b, R.clap(rng, 0.6), 0.85)
    out["st2"] = master(b, -19, 1.5)
    b = st(1.8 * SR)
    for i, n in enumerate((0, 3, 7, 12)):
        ADD(b, R.pluck(mtof(69 + n), 0.2, 0.4), i * 0.13, 1.0, pan=-0.5 + 0.33 * i)
    out["st3"] = master(R.echo(b, 0.17, 0.4, 3), -19, 1.6)
    w = st(3.0 * SR)
    ADD(w, R.pad(chord(60, (0, 4, 7, 11), 0), 2.4, 0.2), 0)
    for i, n in enumerate((0, 4, 7, 11, 14)):
        ADD(w, R.bell(mtof(72 + n), 0.8, 0.26), 0.15 + i * 0.16, 1.0, pan=-0.7 + 0.35 * i)
    out["weather"] = master(R.echo(w, 0.2, 0.3, 2), -20, 1.5)
    return out


# ================================================================== ПАК 2 — Deep House
def pack2(rng):
    S = dict(bpm=120, prog=[(62, (0, 3, 7, 10, 14)), (58, (0, 4, 7, 11)), (55, (0, 3, 7, 10)), (57, (0, 5, 7, 10))],
             drums=dict(hat="shuf", clap=True, shaker=True, rimshot=True, kick=0.9), bass="sub", pad=(0.5, 0.22), arp="bell")
    out, g = build_standard(S, rng, build="riser", finish="fade")
    b = st(2.2 * SR)
    ADD(b, R.pad(chord(62, (0, 3, 7, 10), 0), 1.8, 0.3), 0)
    ADD(b, R.bell(mtof(86), 1.0, 0.3), 0.7); ADD(b, R.boom(1.0, rng, 0.35), 0.7)
    out["st1"] = master(G.wash(b, 0.7, 0.4), -19, 1.7)
    b = st(1.6 * SR)
    ADD(b, R.boom(1.3, rng, 0.8), 0); ADD(b, rim(rng, 0.6), 0.02); ADD(b, R.hat(rng, True, 0.25), 0.3, 1.0, pan=0.5)
    out["st2"] = master(b, -19, 1.4)
    b = st(2.0 * SR)
    for i, n in enumerate((0, 7, 10)):
        ADD(b, R.bell(mtof(74 + n), 0.9, 0.34), i * 0.22, 1.0, pan=-0.7 + 0.7 * i)
    out["st3"] = master(G.wash(R.echo(b, 0.23, 0.45, 3), 0.8, 0.45), -19, 1.7)
    w = st(3.2 * SR)
    ADD(w, R.pad(chord(62, (0, 3, 7, 10, 14), 0), 2.8, 0.22), 0)
    for i, n in enumerate((0, 7, 10, 14)):
        ADD(w, R.bell(mtof(74 + n), 1.0, 0.24), 0.3 + i * 0.3, 1.0, pan=-0.6 + 0.4 * i)
    out["weather"] = master(G.wash(w, 1.0, 0.45), -20, 1.7)
    return out


# ================================================================== ПАК 3 — Tech House
def pack3(rng):
    S = dict(bpm=126, prog=[(53, (0, 3, 7, 10)), (53, (0, 3, 7, 10)), (49, (0, 4, 7)), (51, (0, 4, 7))],
             drums=dict(hat="16", clap=True, rimshot=True, shaker=True), bass="tech", harm="stab_sparse", arp="blip")
    S["bed"] = dict(S, pad=(0.5, 0.12))
    out, g = build_standard(S, rng, build="riser", finish="tape")
    b = st(1.4 * SR)
    for i, f in enumerate((140, 105, 80)):
        ADD(b, G.tom(f, 0.3, 0.7), i * 0.14, 1.0, pan=0.5 - 0.5 * i)
    ADD(b, rim(rng, 0.6), 0.45); ADD(b, R.kick(), 0.5)
    out["st1"] = master(b, -19, 1.4)
    b = st(1.5 * SR)
    ADD(b, R.whoosh(0.9, rng, True, 0.35), 0); ADD(b, R.stab(chord(53, (0, 3, 7), 1), 0.35, 0.4), 0.85); ADD(b, R.clap(rng, 0.7), 0.85); ADD(b, R.kick(), 0.85)
    out["st2"] = master(b, -19, 1.5)
    b = st(1.7 * SR)
    for i, n in enumerate((0, 5, 3, 7, 12)):
        ADD(b, G.blip(mtof(65 + n), 0.14, 0.4), i * 0.11, 1.0, pan=-0.8 + 0.4 * i)
    out["st3"] = master(R.echo(b, 0.14, 0.4, 3), -19, 1.6)
    w = st(2.6 * SR)
    for i, n in enumerate((0, 7, 12, 7, 3, 10)):
        ADD(w, G.blip(mtof(69 + n), 0.16, 0.34), i * 0.16, 1.0, pan=0.7 * np.sin(i * 1.5))
    ADD(w, R.pad(chord(53, (0, 3, 7, 10), 1), 2.0, 0.14), 0.1)
    for i in range(6):
        ADD(w, R.hat(rng, False, 0.1), 0.05 + i * 0.16, 1.0, pan=0.5 * (-1) ** i)
    out["weather"] = master(R.echo(w, 0.18, 0.3, 2), -20, 1.5)
    return out


# ================================================================== ПАК 4 — Acid House
def pack4(rng):
    S = dict(bpm=126, prog=[(57, (0, 3, 7))] * 4,
             drums=dict(hat="off", clap=True, rimshot=False), bass="acid")
    S["bed"] = dict(S, drums=dict(hat="off", clap=True, kick=0.8), pad=(0.5, 0.11))

    def intro_extra(buf, g, rng):  # у першому такті соло ацид-лінії з відкриттям фільтра
        for i in (0, 3, 6, 8, 11, 14):
            ADD(buf, G.acid(mtof(33 + (0, 12, 3, 10, 7, 12)[(i // 3) % 6]), g.step * 1.4, cut_lo=200, cut_hi=1800 + 250 * i,
                            q=10, decay=6, accent=(i % 6 == 0), vol=0.4), i * g.step * 0.9)

    out, g = build_standard(S, rng, build="riser", finish="tape", intro_extra=intro_extra)
    b = st(1.5 * SR)
    ADD(b, G.acid(mtof(45), 1.2, cut_lo=200, cut_hi=4200, q=12, decay=3.2, accent=True, vol=0.5), 0, 1.0, pan=-0.6)
    out["st1"] = master(R.echo(b, 0.17, 0.4, 3), -19, 1.5)
    b = st(1.6 * SR)
    prev = None
    for i, (n, sl) in enumerate(((0, 0), (7, 1), (12, 1), (10, 1))):
        f = mtof(45 + n)
        ADD(b, G.acid(f, 0.28 if i < 3 else 0.5, cut_hi=3000, q=9, decay=9, f_from=prev if sl else None, accent=(i == 2), vol=0.5),
            i * 0.2, 1.0, pan=(-0.6, 0.6, -0.4, 0.4)[i])
        prev = f
    ADD(b, R.clap(rng, 0.7), 0.82)
    out["st2"] = master(R.echo(b, 0.15, 0.35, 2), -19, 1.5)
    b = st(1.7 * SR)
    ADD(b, G.acid(mtof(45), 1.0, cut_lo=150, cut_hi=5200, q=11, rise=True, vol=0.5), 0); ADD(b, R.crash(0.8, rng, 0.4), 1.0); ADD(b, R.kick(), 1.0)
    out["st3"] = master(b, -19, 1.5)
    w = st(2.8 * SR)
    for i, n in enumerate((0, 12, 7, 15, 10, 19)):
        ADD(w, G.acid(mtof(57 + n), 0.22, cut_lo=600, cut_hi=3500, q=14, decay=16, vol=0.35), i * 0.2, 1.0, pan=-0.7 + 0.28 * i)
    ADD(w, R.pad(chord(57, (0, 3, 7), 0), 2.2, 0.14), 0)
    out["weather"] = master(R.echo(w, 0.19, 0.35, 3), -20, 1.6)
    return out


# ================================================================== ПАК 5 — Classic Trance
def pack5(rng):
    S = dict(bpm=138, prog=[(57, (0, 3, 7)), (53, (0, 4, 7)), (60, (0, 4, 7)), (55, (0, 4, 7))],
             drums=dict(hat="off", clap=True, snare_on=True), bass="roll", harm="super_gate", arp="super", pad=(0.5, 0.16))
    S["bed"] = dict(S, drums=dict(hat="off", clap=False, kick=0.7), harm=None)
    out, g = build_standard(S, rng, build="snare", finish="breakdown")
    b = st(1.9 * SR)
    for i in range(10):
        ADD(b, G.snare(rng, 0.25 + 0.05 * i), i * (0.2 - 0.014 * i))
    ADD(b, R.crash(1.2, rng, 0.5), 1.15); ADD(b, R.kick(), 1.15)
    out["st1"] = master(b, -19, 1.5)
    b = st(2.0 * SR)
    for f in chord(57, (0, 3, 7), 0):
        ADD(b, G.supersaw(f, 1.2, 0.2, decay=2.5), 0)
    ADD(b, R.crash(1.4, rng, 0.35), 0); ADD(b, R.kick(), 0); ADD(b, R.boom(1.0, rng, 0.5), 0)
    out["st2"] = master(G.wash(b, 0.9, 0.5), -19, 1.7)
    b = st(1.8 * SR)
    for i, n in enumerate((0, 3, 7, 12, 15, 19, 24, 27)):
        ADD(b, G.supersaw(mtof(57 + 12 + n), 0.2, 0.2, voices=5, decay=7, lp=6000), i * 0.1, 1.0, pan=-0.6 + 0.17 * i)
    ADD(b, R.riser(0.9, rng, 0.35), 0); ADD(b, R.kick(), 0.85); ADD(b, R.boom(0.8, rng, 0.4), 0.85)
    out["st3"] = master(G.wash(b, 0.6, 0.4), -19, 1.6)
    w = st(3.0 * SR)
    for f in chord(57, (0, 3, 7, 10), 0):
        ADD(w, G.supersaw(f, 2.4, 0.1, atk=0.4, rel=0.9), 0)
    for i, n in enumerate((0, 7, 12, 15, 19)):
        ADD(w, G.supersaw(mtof(69 + n), 0.3, 0.12, voices=5, decay=6), 0.3 + i * 0.2, 1.0, pan=-0.6 + 0.3 * i)
    out["weather"] = master(G.wash(w, 1.0, 0.45), -20, 1.7)
    return out


# ================================================================== ПАК 6 — Uplifting / Dream Trance
def pack6(rng):
    S = dict(bpm=136, prog=[(54, (0, 3, 7)), (50, (0, 4, 7)), (57, (0, 4, 7)), (52, (0, 4, 7))],
             drums=dict(hat="off", clap=True, snare_on=True), bass="roll", harm="super_long", arp="pluck", pad=(0.45, 0.14))
    S["bed"] = dict(S, drums=dict(hat="off", clap=False, kick=0.6), harm=None, bass=None)
    out, g = build_standard(S, rng, build="snare", finish="fade")
    b = st(2.2 * SR)
    for i, n in enumerate((0, 2, 4, 7, 9, 12, 14, 16)):
        ADD(b, R.bell(mtof(78 + n), 0.8, 0.26), i * 0.09, 1.0, pan=-0.8 + 0.23 * i)
    out["st1"] = master(G.wash(b, 0.9, 0.5), -19, 1.7)
    b = st(2.0 * SR)
    for f in chord(50, (0, 4, 7), 1):
        ADD(b, G.supersaw(f, 1.5, 0.2, atk=0.7, rel=0.25), 0)
    ADD(b, R.crash(1.0, rng, 0.45), 1.5); ADD(b, R.kick(), 1.5)
    out["st2"] = master(G.wash(b, 0.6, 0.4), -19, 1.7)
    b = st(1.6 * SR)
    ADD(b, G.tom(120, 0.3, 0.7), 0); ADD(b, G.tom(90, 0.3, 0.7), 0.15)
    ADD(b, R.stab(chord(57, (0, 4, 7), 1), 0.5, 0.45), 0.4); ADD(b, R.clap(rng, 0.7), 0.4); ADD(b, R.kick(), 0.4)
    out["st3"] = master(G.wash(b, 0.6, 0.35), -19, 1.6)
    w = st(3.2 * SR)
    ADD(w, R.pad(chord(57, (0, 4, 7, 11), 0), 2.8, 0.2), 0)
    for i, n in enumerate((0, 4, 7, 11, 14, 11, 7)):
        ADD(w, R.bell(mtof(81 + n), 0.9, 0.24), 0.2 + i * 0.22, 1.0, pan=0.8 * np.sin(i * 1.1))
    out["weather"] = master(G.wash(w, 1.2, 0.5), -20, 1.8)
    return out


# ================================================================== ПАК 7 — Night Chill (ембієнт + чіл)
def pack7(rng):
    g = Grid(80)
    prog = [(50, (0, 4, 7, 11)), (47, (0, 3, 7, 10)), (55, (0, 4, 7, 11)), (52, (0, 3, 7, 10))]
    out = {}

    def groove(buf, t0, nbars, lv=1.0, bar0=0, beat=True):
        for k in range(nbars):
            root, iv = prog[(bar0 + k) % 4]
            tb = t0 + k * g.bar
            ADD(buf, R.pad(chord(root, iv, 0), g.bar + 1.4, 0.20), tb, lv)
            ADD(buf, R.pad(chord(root, iv, 1), g.bar + 1.4, 0.09), tb, lv)
            ADD(buf, R.bass(mtof(root - 24), g.bar * 0.9, 0.36), tb, lv)
            for j, i in enumerate((0, 2, 1, 3, 2, 1, 0, 2)):
                ADD(buf, R.bell(mtof(root + 24 + iv[i % len(iv)]), 1.1, 0.12), tb + j * g.beat * 0.5, lv, pan=0.7 * np.sin(j + k))
            if beat:
                for b in range(4):
                    if b in (0, 2):
                        ADD(buf, R.kick(), tb + b * g.beat, 0.30 * lv)
                    ADD(buf, R.hat(rng, False, 0.07), tb + b * g.beat + g.beat / 2, lv, pan=(-0.5 if b % 2 else 0.5))
                    if b in (1, 3):
                        ADD(buf, G.rim(rng, 0.16), tb + b * g.beat, lv, pan=0.3)

    # INTRO: довге «розкриття» пада, дзвіночки, м'який біт з другого такту
    buf = st((2 * g.bar + 3.5) * SR)
    ADD(buf, R.pad(chord(50, (0, 4, 7, 11), 0), 4.0, 0.26), 0)
    ADD(buf, R.whoosh(2.8, rng, True, 0.14), 0)
    groove(buf, 0, 1, 0.9, beat=False)
    groove(buf, g.bar, 1, 1.0, bar0=1, beat=True)
    for i, n in enumerate((0, 4, 7, 11, 14)):
        ADD(buf, R.bell(mtof(74 + n), 1.2, 0.22), 2 * g.bar + i * 0.16, 1.0, pan=-0.7 + 0.35 * i)
    ADD(buf, R.pad(chord(50, (0, 4, 7, 11), 1), 3.0, 0.22), 2 * g.bar)
    ADD(buf, G.crackle(rng, 2 * g.bar + 3.0, 6, 0.05), 0)
    out["intro"] = master(G.wash(buf, 1.2, 0.35), -17, 1.4)

    # OUTRO
    buf = st((g.bar + 4.0) * SR)
    groove(buf, 0, 1, 1.0, bar0=2, beat=True)
    t = g.bar
    for i, n in enumerate((14, 11, 7, 4, 0)):
        ADD(buf, R.bell(mtof(74 + n), 1.3, 0.2), t + i * 0.2, 1.0, pan=0.7 - 0.35 * i)
    ADD(buf, R.pad(chord(50, (0, 4, 7, 11), 0), 3.4, 0.24), t - 0.1)
    ADD(buf, G.crackle(rng, g.bar + 3.5, 6, 0.05), 0)
    out["outro"] = master(G.wash(buf, 1.4, 0.4), -17, 1.4)

    # ПЕРЕБИВКИ
    b = st(2.4 * SR)
    for i, n in enumerate((0, 4, 7, 11, 16)):
        ADD(b, R.bell(mtof(79 + n), 1.1, 0.26), i * 0.13, 1.0, pan=-0.7 + 0.35 * i)
    ADD(b, R.pad(chord(55, (0, 4, 7, 11), 1), 1.8, 0.16), 0)
    out["st1"] = master(G.wash(b, 0.9, 0.45), -19, 1.6)
    b = st(2.2 * SR)
    swell = R.pad(chord(50, (0, 4, 7, 11), 0), 1.4, 0.32)[::-1].copy()
    ADD(b, swell, 0); ADD(b, R.boom(1.2, rng, 0.35), 1.35); ADD(b, R.bell(mtof(86), 1.0, 0.22), 1.4, 1.0, pan=0.4)
    out["st2"] = master(G.wash(b, 0.8, 0.4), -19, 1.5)
    b = st(2.0 * SR)
    for i, n in enumerate((0, 7, 4)):
        ADD(b, R.bell(mtof(69 + n), 1.2, 0.3), i * 0.24, 1.0, pan=-0.6 + 0.6 * i)
    out["st3"] = master(G.wash(R.echo(b, 0.26, 0.4, 3), 1.0, 0.5), -19, 1.7)

    # ФОН: петля 8 тактів
    n_bars = 8
    loop = int(n_bars * g.bar * SR)
    buf = st(loop + 4 * SR)
    groove(buf, 0, n_bars, 0.8, beat=True)
    ADD(buf, G.crackle(rng, n_bars * g.bar + 3.0, 6, 0.05), 0)
    bed = G.fold_loop(buf, loop)
    w = 2.2
    while True:
        y = G.finalize(R._widen(bed, w), -20)
        if G._corr(y) >= 0.55 or w <= 1.0:
            break
        w -= 0.2
    out["bed"] = y

    # ПОГОДА
    w_ = st(3.4 * SR)
    ADD(w_, R.pad(chord(50, (0, 4, 7, 11), 0), 3.0, 0.2), 0)
    for i, n in enumerate((0, 7, 11, 14, 11)):
        ADD(w_, R.bell(mtof(74 + n), 1.0, 0.22), 0.3 + i * 0.3, 1.0, pan=0.7 * np.sin(i * 1.4))
    out["weather"] = master(G.wash(w_, 1.2, 0.5), -20, 1.7)
    return out


PACKS = [
    ("pack1_neutral_house", "Neutral House", 124, "Am", pack1),
    ("pack2_deep_house", "Deep House", 120, "Dm", pack2),
    ("pack3_tech_house", "Tech House", 126, "Fm", pack3),
    ("pack4_acid_house", "Acid House", 126, "Am", pack4),
    ("pack5_classic_trance", "Classic Trance", 138, "Am", pack5),
    ("pack6_uplifting_trance", "Uplifting Trance", 136, "F#m", pack6),
    ("pack7_night_chill", "Night Chill", 80, "D", pack7),
]

REGISTRY = {
    "neutral_house": (pack1, 101), "deep_house": (pack2, 102), "tech_house": (pack3, 103),
    "acid_house": (pack4, 104), "classic_trance": (pack5, 105), "uplifting_trance": (pack6, 106),
    "night_chill": (pack7, 107),
}
PACK_NAMES = list(REGISTRY)


def build_pack(name: str) -> dict:
    """Збирає пакет за назвою (детерміновано, ~1-3 с). Повертає ключі як у radio.load_assets."""
    fn, seed = REGISTRY[name]
    a = fn(np.random.default_rng(seed))
    return {"open": [a["intro"]], "close": [a["outro"]], "sting": [a["st1"], a["st2"], a["st3"]],
            "weather": [a["weather"]], "bed": [a["bed"]]}
