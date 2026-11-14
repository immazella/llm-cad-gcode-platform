"""G-code ayrıştırıcı ve yorumlayıcı.

Hem doğrulayıcı (validator.py) hem de takım yolu görselleştirmesi bu modülü kullanır.
Modellenen alt küme: Fanuc/LinuxCNC/GRBL tarzı 3 eksen freze komutları.
Hangi kodun makinede geçerli olduğuna yorumlayıcı değil, makine profili karar verir.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

WORD_RE = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
# Delme döngüleri: simülasyonda hepsi "XY'ye git, R'ye in, Z'ye del, geri çık" olarak modellenir
CANNED = {73, 74, 76, 81, 82, 83, 84, 85, 86, 87, 88, 89}
# Etkisi bu yorumlayıcıda modellenen kodlar. Bir kodun makinede desteklenip desteklenmediği
# ise makine profiline bağlıdır (profiles.py); profil destekleyip burada modellenmeyen kodlar
# doğrulama raporunda "simülasyonda modellenmedi" bilgisi olarak işaretlenir.
SIMULATED_G = {0, 1, 2, 3, 4, 17, 18, 19, 20, 21, 40, 43, 49, 54, 55, 56, 57, 58, 59, 61, 64, 80, 90, 91, 94, 98, 99} | CANNED
SIMULATED_M = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 30}
# Eksen kelimesi taşısa da "hareket" olmayan ya da konumu bilinmez yapan kodlar
NON_MOTION_AXIS = {4, 10, 28, 28.1, 30, 30.1, 38.2, 38.3, 38.4, 38.5, 43.1, 52, 53, 92, 92.1}
LOSES_POSITION = {28, 30, 38.2, 38.3, 38.4, 38.5, 53, 92}
MODAL_GROUPS = {
    "hareket": {0, 1, 2, 3, 80} | CANNED,
    "düzlem": {17, 18, 19},
    "birim": {20, 21},
    "mesafe": {90, 91},
    "geri çekilme": {98, 99},
    "iş koordinatı": {54, 55, 56, 57, 58, 59},
    "takım boyu": {43, 49},
}
VALID_LETTERS = set("GMXYZIJKRFSTPQNHDO")


@dataclass
class Segment:
    kind: str  # rapid | feed | arc | drill
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    line: int
    feed: float | None
    points: list[tuple[float, float, float]] = field(default_factory=list)  # yay ara noktaları
    arc: dict[str, Any] | None = None

    @property
    def length(self) -> float:
        pts = [self.start, *self.points, self.end] if self.points else [self.start, self.end]
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


@dataclass
class ParsedLine:
    number: int  # 1 tabanlı
    raw: str
    code: str  # yorumsuz
    words: list[tuple[str, float]]
    errors: list[str] = field(default_factory=list)


@dataclass
class State:
    units_mm: bool = True
    absolute: bool = True
    plane: int = 17
    motion: int | None = None
    feed: float | None = None
    spindle_speed: float | None = None
    spindle_on: bool = False
    tool: int | None = None
    pending_tool: int | None = None
    coolant: bool = False
    retract_mode: int = 98
    tlo: int | None = None  # G43 ile etkin takım boyu telafisi (H numarası); G49 iptal eder
    pos: list[float | None] = field(default_factory=lambda: [None, None, None])
    ended: bool = False
    seen_units: bool = False
    seen_distance: bool = False


def strip_comments(line: str) -> tuple[str, list[str]]:
    errors = []
    out, depth = [], 0
    for ch in line:
        if ch == "(":
            if depth:
                errors.append("İç içe parantez yorum")
            depth += 1
        elif ch == ")":
            if depth == 0:
                errors.append("Kapanmayan/fazla ')' karakteri")
            else:
                depth -= 1
        elif ch == ";" and depth == 0:
            break
        elif depth == 0:
            out.append(ch)
    if depth:
        errors.append("Yorum parantezi kapatılmamış")
    return "".join(out).strip(), errors


def parse_line(number: int, raw: str) -> ParsedLine:
    code, errors = strip_comments(raw)
    if code in ("%", ""):
        return ParsedLine(number, raw, "", [], errors)
    words: list[tuple[str, float]] = []
    pos = 0
    for m in WORD_RE.finditer(code):
        gap = code[pos : m.start()].strip()
        if gap:
            errors.append(f"Tanınmayan ifade: '{gap}'")
        letter = m.group(1).upper()
        if letter not in VALID_LETTERS:
            errors.append(f"Geçersiz adres harfi: '{letter}'")
        words.append((letter, float(m.group(2))))
        pos = m.end()
    tail = code[pos:].strip()
    if tail:
        errors.append(f"Tanınmayan ifade: '{tail}'")
    return ParsedLine(number, raw, code, words, errors)


def arc_points(start, end, center, cw: bool, plane_z_start: float, plane_z_end: float, seg_len: float = 0.5):
    """G17 düzleminde yayı ara noktalara böler (helis dahil)."""
    sx, sy = start[0] - center[0], start[1] - center[1]
    ex, ey = end[0] - center[0], end[1] - center[1]
    r = math.hypot(sx, sy)
    a0, a1 = math.atan2(sy, sx), math.atan2(ey, ex)
    if cw:
        sweep = a0 - a1
        if sweep <= 1e-9:
            sweep += 2 * math.pi
        sweep = -sweep
    else:
        sweep = a1 - a0
        if sweep <= 1e-9:
            sweep += 2 * math.pi
    n = max(4, int(abs(sweep) * r / seg_len))
    pts = []
    for k in range(1, n):
        a = a0 + sweep * k / n
        z = plane_z_start + (plane_z_end - plane_z_start) * k / n
        pts.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a), z))
    return pts, sweep, r


def interpret(text: str, rapid_feed: float = 3000.0) -> dict[str, Any]:
    """G-code'u satır satır yorumlar. Döndürür: satırlar, hareket segmentleri, olaylar."""
    st = State()
    lines = [parse_line(i + 1, l) for i, l in enumerate(text.splitlines())]
    segments: list[Segment] = []
    events: list[dict[str, Any]] = []  # doğrulayıcı için satır bazlı durum anlık görüntüleri

    def ev(pl: ParsedLine, **kw):
        events.append({"line": pl.number, **kw})

    for pl in lines:
        if not pl.words:
            continue
        g_codes = [int(v) if float(v).is_integer() else v for l, v in pl.words if l == "G"]
        m_codes = [int(v) if float(v).is_integer() else v for l, v in pl.words if l == "M"]
        vals: dict[str, float] = {}
        dup = set()
        for l, v in pl.words:
            if l in ("G", "M"):
                continue
            if l in vals:
                dup.add(l)
            vals[l] = v
        snapshot_before = {"spindle_on": st.spindle_on, "pos": list(st.pos), "tool": st.tool}
        ev(pl, type="line", g=g_codes, m=m_codes, vals=vals, dup=sorted(dup), state=snapshot_before, ended=st.ended)

        scale = 1.0 if st.units_mm else 25.4
        # modal olmayan / durum komutları
        for g in g_codes:
            if g in (20, 21):
                st.units_mm = g == 21
                st.seen_units = True
                scale = 1.0 if st.units_mm else 25.4
            elif g in (90, 91):
                st.absolute = g == 90
                st.seen_distance = True
            elif g in (17, 18, 19):
                st.plane = g
            elif g in (98, 99):
                st.retract_mode = g
            elif g == 80:
                st.motion = None
            elif g in (0, 1, 2, 3) or g in CANNED:
                st.motion = g
            elif g == 43:
                h = vals.get("H")
                st.tlo = int(h) if h is not None else (st.tool or st.pending_tool)
            elif g == 49:
                st.tlo = None
        if "F" in vals:
            st.feed = vals["F"] * scale
        if "S" in vals:
            st.spindle_speed = vals["S"]
        if "T" in vals:
            st.pending_tool = int(vals["T"])
        for m in m_codes:
            if m in (3, 4):
                st.spindle_on = True
            elif m == 5:
                st.spindle_on = False
            elif m == 6:
                st.tool = st.pending_tool
            elif m == 8:
                st.coolant = True
            elif m == 9:
                st.coolant = False
            elif m in (2, 30):
                st.spindle_on = False
                st.ended = True

        has_axis = any(a in vals for a in "XYZ")
        special = [g for g in g_codes if g in NON_MOTION_AXIS]
        if special:
            if any(g in LOSES_POSITION for g in special):
                # G28/G30/G53/G92/G38: konum (iş koordinatında) bu yorumlayıcıda takip edilmez
                st.pos = [None, None, None]
                ev(pl, type="position_lost", codes=special)
            continue
        if not has_axis:
            continue
        motion = st.motion
        if motion is None:
            ev(pl, type="axis_without_motion")
            continue

        start = list(st.pos)
        target: list[float | None] = list(st.pos)
        for i, a in enumerate("XYZ"):
            if a in vals:
                v = vals[a] * scale
                if st.absolute or start[i] is None:
                    target[i] = v
                else:
                    target[i] = start[i] + v

        if motion in CANNED:
            # delme döngüsü: XY'ye hızlı git, R'ye in, Z'ye del, geri çık
            r_level = vals.get("R")
            z_level = vals.get("Z")
            ev(pl, type="canned", cycle=motion, has_r=r_level is not None, has_z=z_level is not None,
               has_q="Q" in vals, r=r_level, z=z_level, spindle_on=st.spindle_on, feed=st.feed, tlo=st.tlo)
            if r_level is None or z_level is None or None in (target[0], target[1]):
                continue
            r_level *= scale
            z_level = target[2]
            z0 = start[2] if start[2] is not None else r_level
            p0 = (target[0], target[1], z0)
            if start[0] is not None and start[1] is not None:
                segments.append(Segment("rapid", (start[0], start[1], z0), p0, pl.number, rapid_feed))
            segments.append(Segment("rapid", p0, (target[0], target[1], r_level), pl.number, rapid_feed))
            segments.append(Segment("drill", (target[0], target[1], r_level), (target[0], target[1], z_level), pl.number, st.feed))
            back = z0 if st.retract_mode == 98 else r_level
            segments.append(Segment("rapid", (target[0], target[1], z_level), (target[0], target[1], back), pl.number, rapid_feed))
            st.pos = [target[0], target[1], back]
            ev(pl, type="move", kind="drill", start=start, end=[target[0], target[1], z_level],
               spindle_on=st.spindle_on, feed=st.feed, tlo=st.tlo, extremes=[[target[0], target[1], z_level], [target[0], target[1], max(back, r_level)]])
            continue

        if None in start:
            # başlangıç konumu bilinmiyor: ilk hareketten sonra bilinir hâle gelir
            st.pos = target
            ev(pl, type="move", kind="rapid" if motion == 0 else "feed", start=start, end=target,
               spindle_on=st.spindle_on, feed=st.feed, tlo=st.tlo, extremes=[[c for c in target]])
            continue

        if motion in (0, 1):
            kind = "rapid" if motion == 0 else "feed"
            segments.append(Segment(kind, tuple(start), tuple(target), pl.number, rapid_feed if motion == 0 else st.feed))
            ev(pl, type="move", kind=kind, start=start, end=target, spindle_on=st.spindle_on, feed=st.feed,
               tlo=st.tlo, extremes=[start, target])
        else:
            cw = motion == 2
            arc_info: dict[str, Any] = {"cw": cw, "plane": st.plane}
            if st.plane != 17:
                arc_info["error"] = "plane"
                segments.append(Segment("feed", tuple(start), tuple(target), pl.number, st.feed))
            elif "R" in vals:
                rr = vals["R"] * scale
                dx, dy = target[0] - start[0], target[1] - start[1]
                chord = math.hypot(dx, dy)
                arc_info.update(mode="R", r=rr, chord=chord)
                if abs(rr) * 2 < chord - 1e-4 or chord < 1e-9:
                    arc_info["error"] = "radius_too_small" if chord >= 1e-9 else "zero_chord"
                    center = None
                else:
                    h = math.sqrt(max(rr * rr - (chord / 2) ** 2, 0))
                    mx, my = (start[0] + target[0]) / 2, (start[1] + target[1]) / 2
                    ux, uy = -dy / chord, dx / chord
                    sign = (-1 if cw else 1) * (1 if rr > 0 else -1)
                    center = (mx + sign * ux * h, my + sign * uy * h)
            elif "I" in vals or "J" in vals:
                center = (start[0] + vals.get("I", 0) * scale, start[1] + vals.get("J", 0) * scale)
                r1 = math.dist(center, start[:2])
                r2 = math.dist(center, target[:2])
                arc_info.update(mode="IJ", r_start=r1, r_end=r2)
            else:
                arc_info["error"] = "missing_center"
                center = None
            if center is not None and "error" not in arc_info:
                pts, sweep, r = arc_points(start, target, center, cw, start[2], target[2])
                seg = Segment("arc", tuple(start), tuple(target), pl.number, st.feed, points=pts,
                              arc={"center": center, "r": r, "cw": cw})
                segments.append(seg)
                allpts = [start, *[list(p) for p in pts], target]
                xs, ys, zs = zip(*allpts)
                ext = [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]]
            elif "error" not in arc_info:
                ext = [start, target]
            else:
                ext = [start, target]
            ev(pl, type="move", kind="arc", start=start, end=target, spindle_on=st.spindle_on, feed=st.feed,
               tlo=st.tlo, arc=arc_info, extremes=ext)
        st.pos = target

    return {"lines": lines, "segments": segments, "events": events, "final_state": st}


def toolpath_json(text: str, rapid_feed: float = 3000.0) -> dict[str, Any]:
    """Görselleştirme için: segmentler + süre tahmini."""
    res = interpret(text, rapid_feed)
    out, total_time, cut_len, rapid_len = [], 0.0, 0.0, 0.0
    mins = [math.inf] * 3
    maxs = [-math.inf] * 3
    for s in res["segments"]:
        L = s.length
        f = s.feed if s.feed and s.feed > 0 else (rapid_feed if s.kind == "rapid" else 100.0)
        t = L / f * 60.0  # saniye
        total_time += t
        if s.kind == "rapid":
            rapid_len += L
        else:
            cut_len += L
        pts = [s.start, *s.points, s.end]
        for p in pts:
            for i in range(3):
                mins[i] = min(mins[i], p[i])
                maxs[i] = max(maxs[i], p[i])
        out.append({"kind": s.kind, "line": s.line, "feed": f,
                    "points": [[round(c, 4) for c in p] for p in pts], "duration": round(t, 4)})
    return {
        "segments": out,
        "stats": {"segment_count": len(out), "cut_length_mm": round(cut_len, 1), "rapid_length_mm": round(rapid_len, 1),
                  "estimated_time_s": round(total_time, 1),
                  "bbox": None if not out else {"min": [round(v, 3) for v in mins], "max": [round(v, 3) for v in maxs]}},
    }
