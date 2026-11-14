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
from ..i18n import tr, language, error_message
from ..validation_i18n import present_issue
from ..alarm_i18n import present_alarm

MOTION_TR = {0: "G0 hızlı hareket", 1: "G1 doğrusal kesme", 2: "G2 saat yönü yay", 3: "G3 saat yönü tersi yay",
             73: "G73 hızlı gagalamalı delme", 81: "G81 delme", 82: "G82 beklemeli delme", 83: "G83 gagalamalı delme",
             84: "G84 kılavuz çekme", 85: "G85 raybalama"}
KIND_TR = {"rapid": "hızlı hareket (G0)", "feed": "kesme hareketi (G1)", "arc": "yay (G2/G3)", "drill": "delme döngüsü"}
LABEL_EN = {'G0 hızlı hareket': 'G0 rapid motion', 'G1 doğrusal kesme': 'G1 linear cutting', 'G2 saat yönü yay': 'G2 clockwise arc', 'G3 saat yönü tersi yay': 'G3 counterclockwise arc', 'G73 hızlı gagalamalı delme': 'G73 high-speed peck drilling', 'G81 delme': 'G81 drilling', 'G82 beklemeli delme': 'G82 dwell drilling', 'G83 gagalamalı delme': 'G83 peck drilling', 'G84 kılavuz çekme': 'G84 tapping', 'G85 raybalama': 'G85 boring', 'hızlı hareket (G0)': 'rapid motion (G0)', 'kesme hareketi (G1)': 'cutting motion (G1)', 'yay (G2/G3)': 'arc (G2/G3)', 'delme döngüsü': 'drilling cycle', 'iç ölçü (cep genişliği/boyu)': 'internal dimension (pocket width/length)', 'iç ölçü (kanal genişliği)': 'internal dimension (slot width)', 'frezeyle açılmış delik çapı (helisel)': 'milled bore diameter (helical)', 'dış ölçü (dış kontur)': 'external dimension (outer contour)', 'derinlik / yükseklik': 'depth / height', 'matkapla delinmiş delik çapı': 'drilled hole diameter'}


def label(text):
    return LABEL_EN.get(text, text) if language() == "en" else text


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
        "motion": label(MOTION_TR.get(st.motion, None if st.motion is None else f"G{st.motion}")),
        "feed_mm_min": _r(st.feed, 1),
        "spindle": {"on": st.spindle_on, "rpm": st.spindle_speed},
        "coolant": st.coolant,
        "units": "mm (G21)" if st.units_mm else tr('inç (G20)', 'inch (G20)'),
        "distance_mode": tr('mutlak (G90)', 'absolute (G90)') if st.absolute else tr('artımsal (G91)', 'incremental (G91)'),
        "tool_length_comp": f"G43 H{st.tlo}" if st.tlo is not None else tr('yok (G49)', 'none (G49)'),
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
        changes.append(tr(f"takım değişir: T{before.tool} → T{after.tool}", f"tool changes: T{before.tool} → T{after.tool}"))
    if after.spindle_on != before.spindle_on:
        changes.append(tr("iş mili " + ("başlar" if after.spindle_on else "durur"), "spindle " + ("starts" if after.spindle_on else "stops")) + (f" (S{after.spindle_speed:g})" if after.spindle_on and after.spindle_speed else ""))
    if after.coolant != before.coolant:
        changes.append(tr("soğutma " + ("açılır" if after.coolant else "kapanır"), "coolant " + ("starts" if after.coolant else "stops")))
    if after.tlo != before.tlo:
        changes.append(tr("takım boyu telafisi " + (f"devreye girer (G43 H{after.tlo})" if after.tlo is not None else "iptal edilir"), "tool length compensation " + (f"enabled (G43 H{after.tlo})" if after.tlo is not None else "cancelled")))
    if after.ended and not before.ended:
        changes.append(tr("program biter", "program ends"))
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
            "kind": move["kind"], "kind_tr": label(KIND_TR.get(move["kind"], move["kind"])),
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
            warnings.append(tr('İş mili kapalıyken kesme hareketi.', 'Cutting motion with the spindle stopped.'))
        if h is not None and h not in tls:
            warnings.append(tr(f"H{h} takım boyu sistemde girilmemiş; Z konumu makinedeki ofset tablosuna bağlı, ofseti kontrol et.", f"H{h} tool length is not entered; Z position depends on the machine offset table. Check the offset."))
        first = _tool_first_lines(full["events"])
        if line in first:
            warnings.append(tr(f"T{before.tool or after.tool} takımının değişimden sonraki ilk hareketi: takım boyu ve iş sıfırı doğruysa güvenli.", f"First move after changing to T{before.tool or after.tool}: verify tool length and work offset for safety."))
    if canned is not None:
        out["canned"] = {k: canned.get(k) for k in ("cycle", "r", "z", "has_q", "feed")}
    v = rec.get("validation") or {}
    out["issues"] = [{k: present_issue(i).get(k) for k in ("severity", "code", "message", "suggestion")} for i in v.get("issues", []) if i.get("line") == line]
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
        return "error", tr('Hızlı hareket bitmiş parçanın malzemesinden geçiyor: çarpışma!', 'Rapid motion passes through finished-part material: collision!')
    if e[2] is None or not part.over_part(e):
        return None
    descending = s[2] is None or e[2] < s[2] - 1e-9
    if descending and e[2] < -1e-6:
        return "error", tr(f"Hızlı hareket parçanın üzerinde Z{e[2]:g}'a, ham üst yüzeyin altına iniyor; bu bölge önceden boşaltılmadıysa çarpışma.", f"Rapid motion descends over the part to Z{e[2]:g}, below stock top; collision unless this region has already been cleared.")
    if descending and e[2] < NEAR_MM:
        return "warning", tr(f"Hızlı hareket parça üst yüzeyine {e[2]:.2f} mm yaklaşıyor; ilk denemede rapid'i %25'e indir.", f"Rapid motion approaches within {e[2]:.2f} mm of the part top; reduce rapid override to 25% for prove-out.")
    return None


def _summary(s: dict[str, Any]) -> str:
    if s["comment_only"]:
        return tr(f"N{s['line']}: yorum/boş satır, makine bir şey yapmaz.", f"N{s['line']}: comment/blank line; no machine action.")
    parts = []
    mv = s.get("move")
    t = s["after"]["tool"]
    td = s["after"]["tool_desc"]
    if mv:
        e = mv["end_work"]
        where = " ".join(f"{a}{c:g}" for a, c in zip("XYZ", e) if c is not None)
        who = f"T{t} ({td})" if t is not None else tr("takım", "tool")
        parts.append(tr(f"{who} {where} noktasına {mv['kind_tr']} yapar", f"{who} performs {mv['kind_tr']} to {where}"))
        z = mv["end_above_top_mm"]
        if z is not None:
            parts.append(tr(f"parça üst yüzeyinin {z:g} mm üstünde durur", f"stops {z:g} mm above the part top") if z > 1e-6 else
                         tr("parça üst yüzeyi hizasında durur", "stops at the part top") if abs(z) <= 1e-6 else tr(f"üst yüzeyin {abs(z):g} mm altına iner", f"descends {abs(z):g} mm below the top"))
    parts += s["changes"]
    text = f"N{s['line']}: " + ("; ".join(parts) if parts else tr("durum komutu (hareket yok)", "state command (no motion)")) + "."
    if s["warnings"]:
        text += tr(" DİKKAT: ", " WARNING: ") + " ".join(s["warnings"])
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
                    tr(f"T{tool} takılıyor", f"T{tool} is being loaded") + (f" ({t.description})" if t and t.description else "") +
                    ("" if known else tr(" — takım boyu sistemde yok", " — tool length is not entered")),
                    tr('Takımın magazinde doğru cepte olduğunu ve boyunun ofset tablosuna girildiğini kontrol et.', 'Check that the tool is in the correct magazine pocket and its length is entered in the offset table.'))
                awaiting_first_move = awaiting_first_cut = True
            if any(m in (3, 4) for m in ev["m"]) and "S" in ev["vals"]:
                add(ln, "spindle", "info", tr(f"İş mili S{ev['vals']['S']:g} ile başlıyor.", f"Spindle starts at S{ev['vals']['S']:g}."), tr('Yön ve devri dinleyerek doğrula.', 'Verify spindle direction and speed.'))
        elif ev["type"] == "move":
            s, e, kind = ev["start"], ev["end"], ev["kind"]
            if awaiting_first_move and kind == "rapid" and e[0] is not None and e[1] is not None:
                add(ln, "first_approach", "warning",
                    tr(f"T{tool} değişimden sonra parçanın üzerine ilk gidiş (X{e[0]:g} Y{e[1]:g}", f"T{tool} first approach over the part after tool change (X{e[0]:g} Y{e[1]:g}") + (f" Z{e[2]:g})" if e[2] is not None else ")"),
                    tr("Rapid'i %25'e indir, tek blokta çalıştır, kalan mesafeyi izle.", 'Reduce rapid override to 25%, use single block and monitor distance to go.'))
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
                        tr('Çalıştırma; programı ve iş sıfırını kontrol et.', 'Do not run; check the program and work offset.') if risk[0] == "error" else tr("Rapid'i %25'e indir, kalan mesafeyi izle.", 'Reduce rapid override to 25% and monitor distance to go.'))
            elif awaiting_first_cut and kind in ("feed", "arc", "drill"):
                if e[2] is not None and e[2] <= 1e-6:  # ham üst yüzeyin altına iniyor: malzemeye giriş
                    add(ln, "first_cut", "warning", tr(f"T{tool} malzemeye ilk kez giriyor ({KIND_TR[kind]}).", f"T{tool} enters material for the first time ({label(KIND_TR[kind])})."),
                        tr("İlerlemeyi %50'ye indir; sesi ve talaşı izle, sorun varsa feed hold.", 'Reduce feed override to 50%; monitor sound and chips, and use feed hold if there is a problem.'))
                    awaiting_first_cut = False
    v = rec.get("validation") or {}
    for raw_issue in v.get("issues", []):
        i = present_issue(raw_issue)
        if i.get("severity") in ("error", "warning") and i.get("line"):
            add(i["line"], f"validator:{i['code']}", i["severity"], i["message"], i.get("suggestion") or tr('Doğrulama raporuna bak.', 'See the validation report.'))
    items.sort(key=lambda x: (x["line"], {"error": 0, "warning": 1, "info": 2}[x["severity"]]))

    offset = ws.config.setup.offset_for(ws.config.profile)
    tools_used = sorted({it["message"].split()[0] for it in items if it["kind"] == "tool_change"})
    prep = [
        tr(f"İş sıfırı (G54) makinede X{offset[0]:g} Y{offset[1]:g} Z{offset[2]:g} olarak ayarlı mı? (iş sıfırı = parça üst yüzeyinin merkezi)", f"Is the work offset (G54) set to X{offset[0]:g} Y{offset[1]:g} Z{offset[2]:g} on the machine? (work zero = center of the part top)"),
        tr('Takım listesindeki her takımın boyu ölçülüp ofset tablosuna girildi mi?', 'Has every tool length been measured and entered in the offset table?'),
        tr('İlk çalıştırmada Z ofsetini yukarı kaydırıp havada kesim (dry run) yap.', 'For the first run, raise the Z offset and perform a dry run above the part.'),
        tr('Rapid override %25, feed override %50 ile başla; acil durdurma elinin altında olsun.', 'Start at 25% rapid and 50% feed override; keep the emergency stop within reach.'),
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
    out: dict[str, Any] = {"feature": feature, "feature_tr": label(FEATURES[feature]), "nominal": nominal, "measured": measured,
                           "error_mm": _r(err, 4), "tolerance": tolerance}
    if tolerance is not None and abs(err) <= tolerance:
        out.update(action="none", message=tr(f"Sapma {err:+.3f} mm, tolerans (±{tolerance:g}) içinde; düzeltme gerekmez.", f"Deviation {err:+.3f} mm is within tolerance (±{tolerance:g}); no adjustment needed."))
        return out
    if abs(err) > 0.2:
        out["warning"] = tr(f"Sapma {err:+.3f} mm; aşınma ofsetiyle düzeltilemeyecek kadar büyük olabilir. Ölçümü, takım çapını, "
                            "iş sıfırını ve programı kontrol et.",
                            f"Deviation {err:+.3f} mm may be too large for wear compensation. Check the measurement, tool diameter, work offset and program.")
    if feature == "drilled_hole":
        out.update(action="tool", message=tr(f"Delik çapını matkap belirler ({err:+.3f} mm). Ofsetle düzelmez: matkabı kontrol et, "
                              "gerekirse bir sonraki ölçü matkap, rayba veya frezeyle büyütme kullan.",
                              f"The drill determines the hole diameter ({err:+.3f} mm). Offsets cannot correct it: inspect the drill and consider a larger drill, reamer or milling."))
        return out
    rec = ws.gcodes.get(gcode_id or ws.current_gcode or "")
    uses_comp = bool(rec and re.search(r"\bG4[12]\b", rec["text"].upper()))
    tool = ws.config.tool(tool_number) if tool_number is not None else ws.config.tool(ws.config.mill_tool)
    if feature == "depth":
        # derin olması gerekirken sığ (err < 0) → takım daha uzun kabul edilmeli → H aşınması negatif
        wear = err
        out.update(action="length_wear", axis="Z", wear_mm=_r(wear, 4),
                   message=tr(f"Takım boyu aşınma değerine {wear:+.3f} mm ekle (H{tool.number if tool else ''}). "
                              f"{'Sığ kaldığı için takım daha derine iner.' if err < 0 else 'Derin kaldığı için takım daha yukarıda kalır.'}",
                              f"Add {wear:+.3f} mm to tool length wear (H{tool.number if tool else ''}). "
                              + ("The shallow cut will move deeper." if err < 0 else "The deep cut will move higher.")))
        return out
    # Çift taraflı ölçü: yarıçap düzeltmesi sapmanın yarısı. İç ölçü dar → daha çok kes → yarıçap aşınması negatif.
    radial = err / 2 if feature in ("pocket", "slot", "bore") else -err / 2
    out.update(radial_wear_mm=_r(radial, 4), uses_cutter_comp=uses_comp)
    if uses_comp:
        out.update(action="radius_wear",
                   message=tr(f"Takım yarıçap aşınma değerine (D{tool.number if tool else ''}) {radial:+.4f} mm ekle; program G41/G42 kullandığı için makinede düzelir.",
                              f"Add {radial:+.4f} mm to cutter radius wear (D{tool.number if tool else ''}); the program uses G41/G42 so this adjusts the path on the machine."))
    else:
        d_eff = (tool.diameter if tool else 0) + 2 * radial
        out.update(action="cam", effective_diameter=_r(d_eff, 4) if tool else None,
                   message=tr("Program takım yarıçap telafisi (G41/G42) kullanmıyor; makinedeki D aşınma değeri yola etki etmez. "
                              + (f"CAM'de T{tool.number} çapını Ø{tool.diameter:g} yerine Ø{d_eff:.3f} girip programı yeniden üret."
                                 if tool else "CAM'de takım çapını buna göre düzeltip programı yeniden üret."),
                              "The program does not use cutter radius compensation (G41/G42); machine D wear does not affect the path. "
                              + (f"In CAM, set T{tool.number} diameter to Ø{d_eff:.3f} instead of Ø{tool.diameter:g} and regenerate the program."
                                 if tool else "Adjust the tool diameter in CAM and regenerate the program.")))
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
        out.append(("error" if rapid else "warning", tr(f"Z {o['Z'][0]:g} → {n['Z'][0]:g}: {'hızlı hareket ' if rapid else ''}daha aşağı iniyor", f"Z {o['Z'][0]:g} → {n['Z'][0]:g}: {'rapid motion ' if rapid else ''}moves lower")))
    elif "Z" in o and "Z" in n and n["Z"][0] != o["Z"][0]:
        out.append(("info", f"Z {o['Z'][0]:g} → {n['Z'][0]:g}"))
    for ax in "XY":
        if ax in o and ax in n and n[ax][0] != o[ax][0]:
            out.append(("warning", tr(f"{ax} {o[ax][0]:g} → {n[ax][0]:g}: konum değişti", f"{ax} {o[ax][0]:g} → {n[ax][0]:g}: position changed")))
    if "F" in o and "F" in n and n["F"][0] != o["F"][0]:
        up = n["F"][0] > o["F"][0]
        out.append(("warning" if up else "info", tr(f"ilerleme F{o['F'][0]:g} → F{n['F'][0]:g} ({'arttı' if up else 'azaldı'})", f"feed F{o['F'][0]:g} → F{n['F'][0]:g} ({'increased' if up else 'decreased'})")))
    if "S" in o and "S" in n and n["S"][0] != o["S"][0]:
        out.append(("info", tr(f"devir S{o['S'][0]:g} → S{n['S'][0]:g}", f"speed S{o['S'][0]:g} → S{n['S'][0]:g}")))
    for g, msg in ((43, tr("takım boyu telafisi (G43)", "tool length compensation (G43)")), (54, tr("iş koordinatı G54", "work coordinate G54"))):
        if g in gs_o and g not in gs_n:
            out.append(("error", tr(f"{msg} kaldırılmış", f"{msg} removed")))
    for m, msg in ((3, tr("iş mili başlatma (M3)", "spindle start (M3)")), (6, tr("takım değişimi (M6)", "tool change (M6)")), (8, tr("soğutma (M8)", "coolant (M8)"))):
        if m in ms_o and m not in ms_n:
            out.append(("error" if m != 8 else "warning", tr(f"{msg} kaldırılmış", f"{msg} removed")))
        if m in ms_n and m not in ms_o:
            out.append(("info", tr(f"{msg} eklenmiş", f"{msg} added")))
    if old is not None and new is None and o:
        out.append(("warning", tr('satır silinmiş', 'line deleted')))
    if old is None and new is not None and n:
        out.append(("warning", tr('satır eklenmiş', 'line added')))
    if not out and (old or "").strip() != (new or "").strip():
        out.append(("info", tr('yalnızca biçim/yorum değişikliği', 'format/comment change only') if _words(old or "") == _words(new or "") else tr("değişiklik", "change")))
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
            "note": tr('Makinede yapılan değişiklikler CAM kaynağına işlenmezse bir sonraki üretimde kaybolur.', 'Changes made on the machine will be lost at the next generation unless they are applied to the CAM source.')}


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
            out["line_error"] = error_message(str(exc))
    return present_alarm(out)
