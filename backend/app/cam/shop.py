"""Atölye yardımcıları: prove-out (programın makinede ilk denenmesi) sırasında operatöre destek.

Buradaki her şey DETERMİNİSTİKTİR. LLM bu fonksiyonları araç olarak çağırır ve sonucu insan
diline çevirir; koordinat, mesafe, ofset gibi sayıları kendisi hesaplamaz. Hiçbir fonksiyon
makineye bir şey göndermez veya programı değiştirmez — yalnızca okur, hesaplar ve önerir.

- line_state: bir satır çalışmadan önceki/sonraki makine durumu ve hareketin anlamı
- prove_out_checklist: ilk denemede yavaşlanması / kontrol edilmesi gereken satırlar
- wear_offset: ölçülen sapmadan aşınma ofseti veya CAM düzeltmesi önerisi
- diff_programs: makinede değiştirilen programın onaylı sürümle farkı ve riskleri
"""
from __future__ import annotations

import difflib
import math
import re
from typing import Any

from .interpreter import interpret, parse_line

MOTION_TR = {0: "G0 hızlı hareket", 1: "G1 doğrusal kesme", 2: "G2 saat yönü yay", 3: "G3 saat yönü tersi yay",
             73: "G73 hızlı gagalamalı delme", 81: "G81 delme", 82: "G82 beklemeli delme", 83: "G83 gagalamalı delme",
             84: "G84 kılavuz çekme", 85: "G85 raybalama"}
KIND_TR = {"rapid": "hızlı hareket (G0)", "feed": "kesme hareketi (G1)", "arc": "yay (G2/G3)", "drill": "delme döngüsü"}
NEAR_MM = 2.0  # hızlı hareketin parçaya "yakın" sayıldığı mesafe


def _r(v: float | None, d: int = 3) -> float | None:
    return None if v is None else round(float(v), d) + 0.0


def _vec(p) -> list[float | None]:
    return [_r(c) for c in p] if p is not None else [None, None, None]


class _Part:
    """Programın iş koordinatı ile CAD modeli arasındaki köprü.

    İş sıfırının model koordinatındaki yeri G-code üretilirken kaydedilir (work_origin_model); eski kayıtlarda
    yoksa parça üst yüzeyinin merkezi varsayılır. Aşağıdaki sınırlar İŞ koordinatındadır.
    """

    def __init__(self, ws: Any, rec: dict[str, Any]):
        m = ws.model
        self.model = m if (m is not None and rec.get("model_id") in (None, m.id)) else None
        self.ox = self.oy = 0.0
        if self.model is not None:
            bb = self.model.bbox
            o = rec.get("work_origin_model") or [(bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, bb.zmax]
            self.ox, self.oy, self.top = float(o[0]), float(o[1]), float(o[2])
            self.xmin, self.xmax = bb.xmin - self.ox, bb.xmax - self.ox
            self.ymin, self.ymax = bb.ymin - self.oy, bb.ymax - self.oy
            self.zbottom = bb.zmin - self.top
        else:
            self.top = 0.0
            self.xmin = self.xmax = self.ymin = self.ymax = None
            self.zbottom = -(rec.get("stock_height") or 0.0) or None

    def over_part(self, p) -> bool:
        if self.xmin is None or p[0] is None or p[1] is None:
            return True  # bilinmiyorsa en kötüsünü varsay
        return self.xmin - 1e-6 <= p[0] <= self.xmax + 1e-6 and self.ymin - 1e-6 <= p[1] <= self.ymax + 1e-6

    def distance(self, p) -> tuple[float | None, bool]:
        """(parçaya en kısa mesafe mm, nokta malzemenin içinde mi). Model yoksa yalnızca Z0 düzlemine göre."""
        if None in p:
            return None, False
        if self.model is None:
            return p[2], p[2] < -1e-6
        import cadquery as cq
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_IN

        q = (p[0] + self.ox, p[1] + self.oy, p[2] + self.top)
        d = self.model.shape.distance(cq.Vertex.makeVertex(*q))
        inside = False
        for s in self.model.shape.Solids():
            cls = BRepClass3d_SolidClassifier(s.wrapped, gp_Pnt(*q), 1e-6)
            if cls.State() == TopAbs_IN:
                inside = True
                break
        return d, inside

    def segment_min(self, a, b, n: int = 12) -> tuple[float | None, bool]:
        """Doğru parçası boyunca parçaya en yakın mesafe ve malzemeye girip girmediği (örnekleyerek)."""
        if None in a or None in b:
            return self.distance(b)
        best, hit = None, False
        for k in range(n + 1):
            t = k / n
            d, ins = self.distance([a[i] + (b[i] - a[i]) * t for i in range(3)])
            hit = hit or ins
            if d is not None and (best is None or d < best):
                best = d
        return best, hit


def _state_dict(st, ws) -> dict[str, Any]:
    tool = ws.config.tool(st.tool) if st.tool is not None else None
    return {
        "tool": st.tool,
        "tool_desc": (tool.description or f"{tool.type} Ø{tool.diameter:g}") if tool else None,
        "tool_length": tool.length if tool else None,
        "pending_tool": st.pending_tool if st.pending_tool != st.tool else None,
        "motion": MOTION_TR.get(st.motion, None if st.motion is None else f"G{st.motion}"),
        "feed_mm_min": _r(st.feed, 1),
        "spindle": {"on": st.spindle_on, "rpm": st.spindle_speed},
        "coolant": st.coolant,
        "units": "mm (G21)" if st.units_mm else "inç (G20)",
        "distance_mode": "mutlak (G90)" if st.absolute else "artımsal (G91)",
        "tool_length_comp": f"G43 H{st.tlo}" if st.tlo is not None else "yok (G49)",
        "position_work": _vec(st.pos),
        "program_ended": st.ended,
    }


def _rec(ws: Any, gcode_id: str | None) -> dict[str, Any]:
    from ..workspace import ToolError

    gid = gcode_id or ws.current_gcode
    if not gid or gid not in ws.gcodes:
        raise ToolError("Aktif G-code yok. Önce G-code üretin veya yükleyin.")
    return ws.gcodes[gid]


def find_n_line(text: str, n: int) -> int | None:
    """Programda N numarası kullanılıyorsa N<n> bloğunun dosyadaki satırı; yoksa None."""
    pat = re.compile(rf"^\s*N0*{n}(?!\d)", re.I)
    for i, l in enumerate(text.splitlines(), 1):
        if pat.match(l):
            return i
    return None


def _machine(p, offset, tl: float) -> list[float | None]:
    return [None if p[i] is None else _r(p[i] + offset[i] + (tl if i == 2 else 0.0)) for i in range(3)]


def _tool_first_lines(events: list[dict]) -> dict[int, int]:
    """Her takım değişiminden sonraki ilk hareket satırı → takım numarası."""
    out: dict[int, int] = {}
    pending = None
    for ev in events:
        if ev["type"] == "line" and 6 in ev["m"]:
            pending = True
        elif ev["type"] == "move" and pending:
            out[ev["line"]] = 1
            pending = None
    return out


# ------------------------------------------------------------------------------------------- satır durumu
def line_state(ws: Any, line: int, gcode_id: str | None = None) -> dict[str, Any]:
    from ..workspace import ToolError

    rec = _rec(ws, gcode_id)
    text = rec["text"]
    lines = text.splitlines()
    if not 1 <= line <= len(lines):
        raise ToolError(f"Satır {line} yok; program {len(lines)} satır.")
    rapid = ws.config.profile.rapid_feed
    before = interpret("\n".join(lines[: line - 1]), rapid)["final_state"]
    full = interpret(text, rapid)
    after = interpret("\n".join(lines[:line]), rapid)["final_state"]
    pl = parse_line(line, lines[line - 1])
    evs = [e for e in full["events"] if e["line"] == line]
    move = next((e for e in evs if e["type"] == "move"), None)
    canned = next((e for e in evs if e["type"] == "canned"), None)
    offset = ws.config.setup.offset_for(ws.config.profile)
    tls = ws.config.tool_lengths()
    part = _Part(ws, rec)

    out: dict[str, Any] = {
        "gcode_id": rec["gcode_id"], "line": line, "text": lines[line - 1].strip(), "code": pl.code,
        "comment_only": not pl.words,
        "before": _state_dict(before, ws), "after": _state_dict(after, ws),
        "profile": ws.config.profile.summary(), "work_offset": list(offset),
    }
    changes = []
    if after.tool != before.tool:
        changes.append(f"takım değişir: T{before.tool} → T{after.tool}")
    if after.spindle_on != before.spindle_on:
        changes.append("iş mili " + ("başlar" if after.spindle_on else "durur") + (f" (S{after.spindle_speed:g})" if after.spindle_on and after.spindle_speed else ""))
    if after.coolant != before.coolant:
        changes.append("soğutma " + ("açılır" if after.coolant else "kapanır"))
    if after.tlo != before.tlo:
        changes.append("takım boyu telafisi " + (f"devreye girer (G43 H{after.tlo})" if after.tlo is not None else "iptal edilir"))
    if after.ended and not before.ended:
        changes.append("program biter")
    out["changes"] = changes

    warnings: list[str] = []
    if move is not None:
        s, e = move["start"], move["end"]
        h = move.get("tlo")
        tl = tls.get(h, 0.0) if h is not None else 0.0
        length = math.dist(s, e) if None not in s and None not in e else None
        dist_end, inside_end = part.distance(e)
        seg_min, seg_hit = part.segment_min(s, e)
        out["move"] = {
            "kind": move["kind"], "kind_tr": KIND_TR.get(move["kind"], move["kind"]),
            "start_work": _vec(s), "end_work": _vec(e),
            "end_machine": _machine(e, offset, tl), "length_mm": _r(length, 2),
            "feed_mm_min": None if move["kind"] == "rapid" else _r(move.get("feed"), 1),
            # Z0 = ham parçanın üst yüzeyi. Mesafe/çarpışma ise BİTMİŞ parça modeline göredir
            # (bitmiş parçada kalan malzeme hiç kesilmez; ona değen hızlı hareket kesin çarpışmadır).
            "end_above_top_mm": _r(e[2], 3) if e[2] is not None else None,
            "distance_to_finished_part_mm": _r(dist_end, 3), "end_inside_finished_part": inside_end,
            "min_distance_along_move_mm": _r(seg_min, 3), "passes_through_finished_part": seg_hit,
            "part_model": part.model is not None,
        }
        risk = _rapid_risk(move, part)
        if risk:
            warnings.append(risk[1])
        if move["kind"] in ("feed", "arc", "drill") and not move.get("spindle_on"):
            warnings.append("İş mili kapalıyken kesme hareketi.")
        if h is not None and h not in tls:
            warnings.append(f"H{h} takım boyu sistemde girilmemiş; Z konumu makinedeki ofset tablosuna bağlı, ofseti kontrol et.")
        first = _tool_first_lines(full["events"])
        if line in first:
            warnings.append(f"T{before.tool or after.tool} takımının değişimden sonraki ilk hareketi: takım boyu ve iş sıfırı doğruysa güvenli.")
    if canned is not None:
        out["canned"] = {k: canned.get(k) for k in ("cycle", "r", "z", "has_q", "feed")}
    v = rec.get("validation") or {}
    out["issues"] = [{k: i.get(k) for k in ("severity", "code", "message", "suggestion")} for i in v.get("issues", []) if i.get("line") == line]
    out["warnings"] = warnings
    out["summary"] = _summary(out)
    return out


def _rapid_risk(ev: dict, part: _Part) -> tuple[str, str] | None:
    """Hızlı hareketin riski: (severity, açıklama) veya None.
    - bitmiş parçanın malzemesinden geçiyorsa: kesin çarpışma (error)
    - parça üzerinde Z0'ın (ham üst yüzey) altına iniyorsa: ham malzemeye dalma riski (error)
    - parça üzerinde iniyor ve üst yüzeye NEAR_MM'den fazla yaklaşıyorsa: yavaşla (warning)"""
    if ev["kind"] != "rapid":
        return None
    s, e = ev["start"], ev["end"]
    d, hit = part.segment_min(s, e)
    if hit:
        return "error", "Hızlı hareket bitmiş parçanın malzemesinden geçiyor: çarpışma!"
    if e[2] is None or not part.over_part(e):
        return None
    descending = s[2] is None or e[2] < s[2] - 1e-9
    if descending and e[2] < -1e-6:
        return "error", f"Hızlı hareket parçanın üzerinde Z{e[2]:g}'a, ham üst yüzeyin altına iniyor; bu bölge önceden boşaltılmadıysa çarpışma."
    if descending and e[2] < NEAR_MM:
        return "warning", f"Hızlı hareket parça üst yüzeyine {e[2]:.2f} mm yaklaşıyor; ilk denemede rapid'i %25'e indir."
    return None


def _summary(s: dict[str, Any]) -> str:
    if s["comment_only"]:
        return f"N{s['line']}: yorum/boş satır, makine bir şey yapmaz."
    parts = []
    mv = s.get("move")
    t = s["after"]["tool"]
    td = s["after"]["tool_desc"]
    if mv:
        e = mv["end_work"]
        where = " ".join(f"{a}{c:g}" for a, c in zip("XYZ", e) if c is not None)
        who = f"T{t} ({td})" if t is not None else "takım"
        parts.append(f"{who} {where} noktasına {mv['kind_tr']} yapar")
        z = mv["end_above_top_mm"]
        if z is not None:
            parts.append(f"parça üst yüzeyinin {z:g} mm üstünde durur" if z > 1e-6 else
                         "parça üst yüzeyi hizasında durur" if abs(z) <= 1e-6 else f"üst yüzeyin {abs(z):g} mm altına iner")
    parts += s["changes"]
    text = f"N{s['line']}: " + ("; ".join(parts) if parts else "durum komutu (hareket yok)") + "."
    if s["warnings"]:
        text += " DİKKAT: " + " ".join(s["warnings"])
    return text


# ------------------------------------------------------------------------------------------- prove-out listesi
def prove_out_checklist(ws: Any, gcode_id: str | None = None) -> dict[str, Any]:
    rec = _rec(ws, gcode_id)
    res = interpret(rec["text"], ws.config.profile.rapid_feed)
    part = _Part(ws, rec)
    tls = ws.config.tool_lengths()
    items: list[dict[str, Any]] = []

    def add(line, kind, severity, message, action):
        items.append({"line": line, "kind": kind, "severity": severity, "message": message, "action": action})

    tool = None
    near_flagged: set = set()
    approach_line = None
    awaiting_first_move = False
    awaiting_first_cut = False
    for ev in res["events"]:
        ln = ev["line"]
        if ev["type"] == "line":
            if 6 in ev["m"]:
                tool = ev["vals"].get("T", ev["state"].get("tool"))
                tool = int(tool) if tool is not None else None
                t = ws.config.tool(tool) if tool is not None else None
                known = tool in tls
                add(ln, "tool_change", "info" if known else "warning",
                    f"T{tool} takılıyor" + (f" ({t.description})" if t and t.description else "") +
                    ("" if known else " — takım boyu sistemde yok"),
                    "Takımın magazinde doğru cepte olduğunu ve boyunun ofset tablosuna girildiğini kontrol et.")
                awaiting_first_move = awaiting_first_cut = True
            if any(m in (3, 4) for m in ev["m"]) and "S" in ev["vals"]:
                add(ln, "spindle", "info", f"İş mili S{ev['vals']['S']:g} ile başlıyor.", "Yön ve devri dinleyerek doğrula.")
        elif ev["type"] == "move":
            s, e, kind = ev["start"], ev["end"], ev["kind"]
            if awaiting_first_move and kind == "rapid" and e[0] is not None and e[1] is not None:
                add(ln, "first_approach", "warning",
                    f"T{tool} değişimden sonra parçanın üzerine ilk gidiş (X{e[0]:g} Y{e[1]:g}" + (f" Z{e[2]:g})" if e[2] is not None else ")"),
                    "Rapid'i %25'e indir, tek blokta çalıştır, kalan mesafeyi izle.")
                awaiting_first_move = False
                approach_line = ln
            if kind == "rapid" and ln != approach_line:
                risk = _rapid_risk(ev, part)
                if risk and risk[0] == "warning" and tool in near_flagged:
                    risk = None  # aynı takım için yalnızca ilk yaklaşmayı listele; sonrakiler aynı desen
                if risk:
                    if risk[0] == "warning":
                        near_flagged.add(tool)
                    add(ln, "rapid_collision" if risk[0] == "error" else "rapid_near_part", risk[0], risk[1],
                        "Çalıştırma; programı ve iş sıfırını kontrol et." if risk[0] == "error" else "Rapid'i %25'e indir, kalan mesafeyi izle.")
            elif awaiting_first_cut and kind in ("feed", "arc", "drill"):
                if e[2] is not None and e[2] <= 1e-6:  # ham üst yüzeyin altına iniyor: malzemeye giriş
                    add(ln, "first_cut", "warning", f"T{tool} malzemeye ilk kez giriyor ({KIND_TR[kind]}).",
                        "İlerlemeyi %50'ye indir; sesi ve talaşı izle, sorun varsa feed hold.")
                    awaiting_first_cut = False
    v = rec.get("validation") or {}
    for i in v.get("issues", []):
        if i.get("severity") in ("error", "warning") and i.get("line"):
            add(i["line"], f"validator:{i['code']}", i["severity"], i["message"], i.get("suggestion") or "Doğrulama raporuna bak.")
    items.sort(key=lambda x: (x["line"], {"error": 0, "warning": 1, "info": 2}[x["severity"]]))

    offset = ws.config.setup.offset_for(ws.config.profile)
    tools_used = sorted({it["message"].split()[0] for it in items if it["kind"] == "tool_change"})
    prep = [
        f"İş sıfırı (G54) makinede X{offset[0]:g} Y{offset[1]:g} Z{offset[2]:g} olarak ayarlı mı? (iş sıfırı = parça üst yüzeyinin merkezi)",
        "Takım listesindeki her takımın boyu ölçülüp ofset tablosuna girildi mi?",
        "İlk çalıştırmada Z ofsetini yukarı kaydırıp havada kesim (dry run) yap.",
        "Rapid override %25, feed override %50 ile başla; acil durdurma elinin altında olsun.",
    ]
    return {"gcode_id": rec["gcode_id"], "profile": ws.config.profile.summary(), "preparation": prep,
            "tools": tools_used, "items": items, "counts": {s: sum(1 for i in items if i["severity"] == s) for s in ("error", "warning", "info")},
            "part_model": part.model is not None}


# ------------------------------------------------------------------------------------------- ölçü düzeltme
FEATURES = {
    "pocket": "iç ölçü (cep genişliği/boyu)",
    "slot": "iç ölçü (kanal genişliği)",
    "bore": "frezeyle açılmış delik çapı (helisel)",
    "contour": "dış ölçü (dış kontur)",
    "depth": "derinlik / yükseklik",
    "drilled_hole": "matkapla delinmiş delik çapı",
}


def wear_offset(ws: Any, feature: str, nominal: float, measured: float, tool_number: int | None = None,
                tolerance: float | None = None, gcode_id: str | None = None) -> dict[str, Any]:
    from ..workspace import ToolError

    if feature not in FEATURES:
        raise ToolError(f"feature: {' | '.join(FEATURES)}")
    err = measured - nominal
    out: dict[str, Any] = {"feature": feature, "feature_tr": FEATURES[feature], "nominal": nominal, "measured": measured,
                           "error_mm": _r(err, 4), "tolerance": tolerance}
    if tolerance is not None and abs(err) <= tolerance:
        out.update(action="none", message=f"Sapma {err:+.3f} mm, tolerans (±{tolerance:g}) içinde; düzeltme gerekmez.")
        return out
    if abs(err) > 0.2:
        out["warning"] = (f"Sapma {err:+.3f} mm; aşınma ofsetiyle düzeltilemeyecek kadar büyük olabilir. Ölçümü, takım çapını, "
                          "iş sıfırını ve programı kontrol et.")
    if feature == "drilled_hole":
        out.update(action="tool", message=f"Delik çapını matkap belirler ({err:+.3f} mm). Ofsetle düzelmez: matkabı kontrol et, "
                   "gerekirse bir sonraki ölçü matkap, rayba veya frezeyle büyütme kullan.")
        return out
    rec = ws.gcodes.get(gcode_id or ws.current_gcode or "")
    uses_comp = bool(rec and re.search(r"\bG4[12]\b", rec["text"].upper()))
    tool = ws.config.tool(tool_number) if tool_number is not None else ws.config.tool(ws.config.mill_tool)
    if feature == "depth":
        # derin olması gerekirken sığ (err < 0) → takım daha uzun kabul edilmeli → H aşınması negatif
        wear = err
        out.update(action="length_wear", axis="Z", wear_mm=_r(wear, 4),
                   message=f"Takım boyu aşınma değerine {wear:+.3f} mm ekle (H{tool.number if tool else ''}). "
                           f"{'Sığ kaldığı için takım daha derine iner.' if err < 0 else 'Derin kaldığı için takım daha yukarıda kalır.'}")
        return out
    # Çift taraflı ölçü: yarıçap düzeltmesi sapmanın yarısı. İç ölçü dar → daha çok kes → yarıçap aşınması negatif.
    radial = err / 2 if feature in ("pocket", "slot", "bore") else -err / 2
    out.update(radial_wear_mm=_r(radial, 4), uses_cutter_comp=uses_comp)
    if uses_comp:
        out.update(action="radius_wear",
                   message=f"Takım yarıçap aşınma değerine (D{tool.number if tool else ''}) {radial:+.4f} mm ekle; program G41/G42 kullandığı için makinede düzelir.")
    else:
        d_eff = (tool.diameter if tool else 0) + 2 * radial
        out.update(action="cam", effective_diameter=_r(d_eff, 4) if tool else None,
                   message=("Program takım yarıçap telafisi (G41/G42) kullanmıyor; makinedeki D aşınma değeri yola etki etmez. "
                            + (f"CAM'de T{tool.number} çapını Ø{tool.diameter:g} yerine Ø{d_eff:.3f} girip programı yeniden üret."
                               if tool else "CAM'de takım çapını buna göre düzeltip programı yeniden üret.")))
    return out


# ------------------------------------------------------------------------------------------- program farkı
WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def _words(line: str) -> dict[str, list[float]]:
    from .interpreter import strip_comments

    code, _ = strip_comments(line.upper())
    out: dict[str, list[float]] = {}
    for m in WORD.finditer(code):
        out.setdefault(m.group(1), []).append(float(m.group(2)))
    return out


def _classify(old: str | None, new: str | None) -> list[tuple[str, str]]:
    """Değişikliğin riskleri: (severity, açıklama)."""
    o, n = _words(old or ""), _words(new or "")
    out: list[tuple[str, str]] = []
    gs_o, gs_n = set(o.get("G", [])), set(n.get("G", []))
    ms_o, ms_n = set(o.get("M", [])), set(n.get("M", []))
    rapid = 0 in gs_n or 0 in gs_o
    if "Z" in o and "Z" in n and n["Z"][0] < o["Z"][0] - 1e-9:
        out.append(("error" if rapid else "warning", f"Z {o['Z'][0]:g} → {n['Z'][0]:g}: {'hızlı hareket ' if rapid else ''}daha aşağı iniyor"))
    elif "Z" in o and "Z" in n and n["Z"][0] != o["Z"][0]:
        out.append(("info", f"Z {o['Z'][0]:g} → {n['Z'][0]:g}"))
    for ax in "XY":
        if ax in o and ax in n and n[ax][0] != o[ax][0]:
            out.append(("warning", f"{ax} {o[ax][0]:g} → {n[ax][0]:g}: konum değişti"))
    if "F" in o and "F" in n and n["F"][0] != o["F"][0]:
        up = n["F"][0] > o["F"][0]
        out.append(("warning" if up else "info", f"ilerleme F{o['F'][0]:g} → F{n['F'][0]:g} ({'arttı' if up else 'azaldı'})"))
    if "S" in o and "S" in n and n["S"][0] != o["S"][0]:
        out.append(("info", f"devir S{o['S'][0]:g} → S{n['S'][0]:g}"))
    for g, msg in ((43, "takım boyu telafisi (G43)"), (54, "iş koordinatı G54")):
        if g in gs_o and g not in gs_n:
            out.append(("error", f"{msg} kaldırılmış"))
    for m, msg in ((3, "iş mili başlatma (M3)"), (6, "takım değişimi (M6)"), (8, "soğutma (M8)")):
        if m in ms_o and m not in ms_n:
            out.append(("error" if m != 8 else "warning", f"{msg} kaldırılmış"))
        if m in ms_n and m not in ms_o:
            out.append(("info", f"{msg} eklenmiş"))
    if old is not None and new is None and o:
        out.append(("warning", "satır silinmiş"))
    if old is None and new is not None and n:
        out.append(("warning", "satır eklenmiş"))
    if not out and (old or "").strip() != (new or "").strip():
        out.append(("info", "yalnızca biçim/yorum değişikliği" if _words(old or "") == _words(new or "") else "değişiklik"))
    return out


def diff_programs(ws: Any, base_id: str, edited_id: str) -> dict[str, Any]:
    from ..workspace import ToolError

    for g in (base_id, edited_id):
        if g not in ws.gcodes:
            raise ToolError(f"'{g}' kimlikli G-code yok. Mevcut: {', '.join(ws.gcodes) or 'yok'}")
    a = ws.gcodes[base_id]["text"].splitlines()
    b = ws.gcodes[edited_id]["text"].splitlines()
    norm = lambda s: " ".join(s.upper().split())  # noqa: E731
    sm = difflib.SequenceMatcher(a=[norm(x) for x in a], b=[norm(x) for x in b], autojunk=False)
    changes: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        olds, news = a[i1:i2], b[j1:j2]
        for k in range(max(len(olds), len(news))):
            old = olds[k] if k < len(olds) else None
            new = news[k] if k < len(news) else None
            risks = _classify(old, new)
            sev = min((r[0] for r in risks), key=lambda s: {"error": 0, "warning": 1, "info": 2}[s], default="info")
            changes.append({"base_line": i1 + k + 1 if old is not None else None, "edited_line": j1 + k + 1 if new is not None else None,
                            "old": old, "new": new, "severity": sev, "risks": [r[1] for r in risks]})
    va, vb = ws.gcodes[base_id].get("validation") or {}, ws.gcodes[edited_id].get("validation") or {}
    counts = {s: sum(1 for c in changes if c["severity"] == s) for s in ("error", "warning", "info")}
    return {"base_id": base_id, "edited_id": edited_id, "change_count": len(changes), "counts": counts,
            "changes": changes[:200], "truncated": len(changes) > 200,
            "validation": {"base": (va.get("counts") or {}), "edited": (vb.get("counts") or {})},
            "note": "Makinede yapılan değişiklikler CAM kaynağına işlenmezse bir sonraki üretimde kaybolur."}


# ------------------------------------------------------------------------------------------- alarm bilgi tabanı
ALARM_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent.parent / "alarms"


def _load_kb(controller: str) -> dict[str, Any]:
    import json

    p = ALARM_DIR / f"{controller}.json"
    if not p.exists():
        return {"controller": controller, "source": None, "note": "Bu kontrol ünitesi için alarm bilgi tabanı yok.", "entries": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _norm_code(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(s)).lower()


def explain_alarm(ws: Any, code: str, line: int | None = None, controller: str | None = None,
                  gcode_id: str | None = None) -> dict[str, Any]:
    """Alarm kodunu kontrol ünitesinin kaynaklı bilgi tabanından bulur; satır verilirse o satırın durumunu ekler.
    Kodun anlamı ASLA tahmin edilmez: bilgi tabanında yoksa found=false döner."""
    ctrl = controller or ws.config.profile.controller
    kb = _load_kb(ctrl)
    want = _norm_code(code)
    hit = None
    for e in kb["entries"]:
        keys = {_norm_code(e["code"]), *(_norm_code(a) for a in e.get("aliases", []))}
        if want in keys:
            hit = e
            break
    if hit is None and ctrl == "grbl" and want.isdigit():
        hit = next((e for e in kb["entries"] if _norm_code(e["code"]) == f"error:{want}"), None)
    out: dict[str, Any] = {"controller": ctrl, "query": code, "found": hit is not None, "source": kb.get("source"),
                           "kb_note": kb.get("note")}
    if hit is None:
        others = []
        for other in ("fanuc", "linuxcnc", "grbl"):
            if other == ctrl:
                continue
            for e in _load_kb(other)["entries"]:
                if want in {_norm_code(e["code"]), *(_norm_code(x) for x in e.get("aliases", []))}:
                    others.append({"controller": other, "code": e["code"], "title": e["title"]})
                    break
        if others:
            out["found_in_other"] = others  # makine profili yanlış seçilmiş olabilir
        out["note"] = (f"'{code}' kodu {ctrl} alarm bilgi tabanında yok. Anlamını tahmin etme; kontrol ünitesinin kılavuzuna bak."
                       + (" (Bilgi tabanı bu kontrol ünitesi için boş.)" if not kb["entries"] else ""))
    else:
        out["alarm"] = hit
    if line is not None:
        try:
            st = line_state(ws, line, gcode_id)
            out["line_state"] = {k: st[k] for k in ("line", "text", "summary", "issues", "warnings") if k in st}
            rel = set(hit.get("related_rules", [])) if hit else set()
            if rel:
                out["matching_issues"] = [i for i in st["issues"] if i["code"] in rel]
        except Exception as exc:  # satır yok vb.
            out["line_error"] = str(exc)
    return out
