"""Deterministik takım yolu ve G-code üretimi (3 eksen freze).

Akış: CAD modeli → üretim planı (hangi özellik, hangi takım, hangi operasyon)
      → takım yolu IR'si (ir.py, makineden bağımsız) → post-processor → G-code metni.

Bu modül G-code YAZMAZ; yalnızca IR üretir. Hangi kontrol ünitesine hangi komutların
yazılacağına seçili makine profilinin post-processor'ı karar verir (posts/).
LLM bu modüle yalnızca parametre verir; koordinat hesabı tamamen buradadır.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_CompCurve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection

from ..cad.engrave import placed_strokes
from ..cad.model import CadModel
from ..cad.occ_utils import bbox
from . import ir
from .config import MachiningConfig, Tool
from .ir import fmt
from .posts import get_post

Pt = tuple[float, float]


class IRBuilder:
    """Takım yolunu IR işlemleri olarak kaydeder (G-code yazmaz)."""

    def __init__(self) -> None:
        self.ops: list[ir.Op] = []

    def comment(self, s: str) -> None:
        self.ops.append(ir.Comment(s))

    def rapid(self, x=None, y=None, z=None) -> None:
        self.ops.append(ir.Rapid(x, y, z))

    def line(self, x=None, y=None, z=None, f=None) -> None:
        self.ops.append(ir.Linear(x, y, z, f))

    def arc(self, cw: bool, x, y, z, i, j, f) -> None:
        self.ops.append(ir.Arc(x, y, z, i, j, cw, f))

    def add(self, op: ir.Op) -> None:
        self.ops.append(op)


def wire_points(wire: cq.Wire, deflection: float = 0.01) -> list[Pt]:
    curve = BRepAdaptor_CompCurve(wire.wrapped)
    disc = GCPnts_QuasiUniformDeflection(curve, deflection)
    pts: list[Pt] = []
    for i in range(1, disc.NbPoints() + 1):
        p = curve.Value(disc.Parameter(i))
        q = (p.X(), p.Y())
        if not pts or math.dist(pts[-1], q) > 1e-4:
            pts.append(q)
    if len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-4:
        pts.pop()
    return pts


def signed_area(pts: list[Pt]) -> float:
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))


def oriented(pts: list[Pt], ccw: bool) -> list[Pt]:
    if (signed_area(pts) > 0) != ccw:
        pts = pts[::-1]
    return pts


def order_strokes(strokes: list[list[Pt]], start: Pt = (0.0, 0.0)) -> list[list[Pt]]:
    """En yakın komşu sıralaması: boşta (G0) yol kısalır. Açık vuruşlar ters çevrilebilir,
    kapalı vuruşlar en yakın köşeden başlatılır."""
    rest = [list(s) for s in strokes]
    out: list[list[Pt]] = []
    cur = start
    while rest:
        best = None
        for i, s in enumerate(rest):
            closed = len(s) > 2 and math.dist(s[0], s[-1]) < 1e-6
            if closed:
                j = min(range(len(s) - 1), key=lambda k: math.dist(cur, s[k]))
                cand, d = s[j:-1] + s[:j] + [s[j]], math.dist(cur, s[j])
            else:
                d0, d1 = math.dist(cur, s[0]), math.dist(cur, s[-1])
                cand, d = (s, d0) if d0 <= d1 else (s[::-1], d1)
            if best is None or d < best[0]:
                best = (d, i, cand)
        _, i, cand = best
        rest.pop(i)
        out.append(cand)
        cur = cand[-1]
    return out


def depth_levels(z_top: float, z_bottom: float, step: float) -> list[float]:
    n = max(1, math.ceil((z_top - z_bottom) / step - 1e-9))
    return [z_top - (z_top - z_bottom) * k / n for k in range(1, n + 1)]


ORIGIN_TR = {"center": "ham parça üst yüzey merkezi", "front_left": "ham parça üst yüzey sol ön köşesi (Xmin, Ymin)",
             "front_right": "ham parça üst yüzey sağ ön köşesi (Xmax, Ymin)", "back_left": "ham parça üst yüzey sol arka köşesi (Xmin, Ymax)",
             "back_right": "ham parça üst yüzey sağ arka köşesi (Xmax, Ymax)"}
OP_TR = {"face": "alın frezeleme", "drill": "delme", "helical": "helisel delik", "pocket": "cep", "engrave": "kazıma", "contour": "dış kontur"}


class CD:
    """Bir operasyonun çözümlenmiş kesme verisi."""

    def __init__(self, rpm: float, feed: float, plunge: float, stepdown: float, stepover: float, peck: float | None, source: str):
        self.rpm, self.feed, self.plunge, self.stepdown, self.stepover, self.peck, self.source = rpm, feed, plunge, stepdown, stepover, peck, source

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rpm": round(self.rpm), "feed": round(self.feed, 1)}
        if self.peck is not None:  # matkap: kademe/yan paso anlamsız
            out["peck"] = round(self.peck, 3)
        else:
            out.update(plunge=round(self.plunge, 1), stepdown=round(self.stepdown, 3), stepover_ratio=round(self.stepover, 3))
        out["source"] = self.source
        return out


class GcodeGenerator:
    def __init__(self, model: CadModel, cfg: MachiningConfig):
        self.m = model
        self.cfg = cfg
        self.profile = cfg.profile
        self.c = cfg.cutting.model_copy()
        self.setup = cfg.setup
        self.w = IRBuilder()
        self.warnings: list[str] = []
        self.plan: list[dict[str, Any]] = []
        bb = bbox(model.shape)
        self.ztop, self.zbot = bb.zmax, bb.zmin
        st = self.setup
        # Ham parça = parça sınır kutusu + paylar. Z0 = ham parçanın üst yüzeyi.
        self.stock = {"xmin": bb.xmin - st.stock_side, "xmax": bb.xmax + st.stock_side,
                      "ymin": bb.ymin - st.stock_side, "ymax": bb.ymax + st.stock_side,
                      "zmin": bb.zmin, "zmax": bb.zmax + st.stock_top}
        s = self.stock
        ox = {"center": (s["xmin"] + s["xmax"]) / 2, "front_left": s["xmin"], "back_left": s["xmin"],
              "front_right": s["xmax"], "back_right": s["xmax"]}[st.origin_xy]
        oy = {"center": (s["ymin"] + s["ymax"]) / 2, "front_left": s["ymin"], "front_right": s["ymin"],
              "back_left": s["ymax"], "back_right": s["ymax"]}[st.origin_xy]
        self.origin = (ox, oy, s["zmax"])
        self.current_tool: int | None = None
        self.current_rpm: float | None = None
        self._cd_cache: dict[tuple, CD] = {}
        if st.wcs not in self.profile.work_offsets:
            self.warnings.append(f"G{st.wcs} {self.profile.name} için tanımlı iş koordinatları arasında değil "
                                 f"({', '.join(f'G{w}' for w in self.profile.work_offsets)}).")
        if self.c.coolant != "off" and self.c.coolant not in self.profile.coolant_modes:
            self.warnings.append(f"{self.profile.name} '{self.c.coolant}' soğutmayı desteklemiyor "
                                 f"({', '.join(self.profile.coolant_modes) or 'soğutma yok'}); soğutma kapatıldı.")
            self.c.coolant = "off"

    # model koordinatı -> iş koordinatı (seçilen iş sıfırına göre; Z0 = ham parça üst yüzeyi)
    def X(self, x: float) -> float:
        return x - self.origin[0]

    def Y(self, y: float) -> float:
        return y - self.origin[1]

    def Z(self, z: float) -> float:
        return z - self.origin[2]

    # ------------------------------------------------------------------ kesme verisi
    def ops_cfg(self, op_type: str):
        return self.cfg.op_settings[op_type]

    def _preset(self, tool: Tool, name: str | None):
        mat = self.setup.material
        if name:
            p = next((p for p in tool.presets if p.name == name), None)
            if p is None:
                self.warnings.append(f"T{tool.number}: '{name}' adlı preset yok; malzemeye göre seçildi.")
            else:
                return p
        return (next((p for p in tool.presets if p.material == mat), None)
                or next((p for p in tool.presets if p.material is None), None))

    def cd(self, op_type: str, tool: Tool) -> CD:
        """Kesme verisi: operasyon ayarı > takım preseti > malzeme tablosu (otomatik) / elle ayarlar; sonra makine sınırları."""
        key = (op_type, tool.number)
        if key in self._cd_cache:
            return self._cd_cache[key]
        from .materials import recommend

        o, c, p = self.ops_cfg(op_type), self.c, self.profile
        is_drill = tool.type in ("drill", "spot_drill")
        if self.cfg.cutting_mode == "manual":
            rpm, feed = (c.drill_rpm, c.drill_feed) if is_drill else (c.spindle_rpm, c.feed_rate)
            plunge, stepdown, stepover, peck, source = c.plunge_rate, c.depth_per_pass, c.stepover_ratio, c.peck_depth, "elle ayarlar"
            if is_drill:
                plunge = feed
        else:
            rec = recommend(tool.type, tool.diameter, tool.flutes, tool.tool_material, self.setup.material)
            rpm, feed, plunge = rec["rpm"], rec["feed"], rec["plunge"]
            stepdown = rec["stepdown"] or c.depth_per_pass
            stepover = rec["stepover_ratio"] or c.stepover_ratio
            peck = rec.get("peck") or c.peck_depth
            source = f"malzeme tablosu ({rec['material_name']})"
            pr = self._preset(tool, o.preset)
            if pr is not None:
                if pr.vc:
                    rpm = 1000 * pr.vc / (math.pi * tool.diameter)
                if pr.rpm:
                    rpm = pr.rpm
                fz = pr.fz or rec["fz"]
                if pr.fz or pr.vc or pr.rpm:
                    feed = rpm * rec["flutes"] * fz
                if pr.feed:
                    feed = pr.feed
                plunge = pr.plunge or feed * (rec["plunge"] / max(rec["feed"], 1e-9))
                stepdown = pr.stepdown or stepdown
                stepover = pr.stepover_ratio or stepover
                source = f"T{tool.number} preseti '{pr.name}'"
        if o.rpm or o.feed or o.plunge or o.stepdown or o.stepover_ratio:
            source += " + operasyon ayarı"
        if o.rpm:
            if not o.feed:
                feed *= o.rpm / rpm  # talaş kalınlığı sabit
            rpm = o.rpm
        feed = o.feed or feed
        plunge = o.plunge or plunge
        stepdown = o.stepdown or stepdown
        stepover = o.stepover_ratio or stepover
        peck = o.peck or peck
        # makine sınırları
        if rpm > p.max_spindle:
            ratio = p.max_spindle / rpm
            self.warnings.append(f"{OP_TR[op_type]} T{tool.number}: devir {rpm:.0f} → {fmt(p.max_spindle)} dev/dk (makine sınırı); "
                                 f"ilerlemeler aynı oranda (×{ratio:.2f}) düşürüldü.")
            rpm, feed, plunge = p.max_spindle, feed * ratio, plunge * ratio
        elif 0 < rpm < p.min_spindle:
            self.warnings.append(f"{OP_TR[op_type]} T{tool.number}: devir {rpm:.0f} → {fmt(p.min_spindle)} dev/dk (iş milinin alt sınırı).")
            rpm = p.min_spindle
        for name in ("feed", "plunge"):
            v = locals()[name]
            if v > p.max_feed:
                self.warnings.append(f"{OP_TR[op_type]} T{tool.number}: { {"feed": "ilerleme", "plunge": "dalma"}[name]} {v:.0f} → {fmt(p.max_feed)} mm/dk (makine ilerleme sınırı).")
                if name == "feed":
                    feed = p.max_feed
                else:
                    plunge = p.max_feed
        out = CD(rpm, feed, plunge, stepdown, stepover, peck if is_drill else None, source)
        self._cd_cache[key] = out
        return out

    def _pick_tool(self, op_type: str, default: Tool | None, need: str = "mill") -> Tool | None:
        """Operasyon ayarında takım seçilmişse onu kullan (türü uygunsa), yoksa varsayılanı."""
        n = self.ops_cfg(op_type).tool
        if n is None:
            return default
        t = self.cfg.tool(n)
        if t is None:
            self.warnings.append(f"{OP_TR[op_type]}: T{n} takım listesinde yok; otomatik seçim kullanıldı.")
            return default
        ok = {"mill": t.is_mill, "engrave": t.type in ("vbit", "chamfer") or t.is_mill, "drill": t.type in ("drill",)}[need]
        if not ok:
            self.warnings.append(f"{OP_TR[op_type]}: T{n} ({t.label()}) bu operasyona uygun değil; otomatik seçim kullanıldı.")
            return default
        return t

    def _check_tool(self, tool: Tool, depth: float, what: str) -> None:
        p = self.profile
        if p.max_tool_diameter and tool.diameter > p.max_tool_diameter:
            self.warnings.append(f"T{tool.number} Ø{fmt(tool.diameter)} makinenin en büyük takım çapını ({fmt(p.max_tool_diameter)}) aşıyor.")
        if tool.flute_length and depth > tool.flute_length + 1e-6:
            self.warnings.append(f"{what}: derinlik {fmt(depth)} mm, T{tool.number} ağız boyundan ({fmt(tool.flute_length)} mm) fazla; "
                                 "gövde malzemeye sürtünür. Daha uzun ağızlı takım seçin.")

    # ------------------------------------------------------------------ plan
    def build_plan(self) -> None:
        default_mill = self.cfg.tool(self.cfg.mill_tool)
        if default_mill is None or not default_mill.is_mill:
            default_mill = next((t for t in sorted(self.cfg.tools, key=lambda t: t.diameter) if t.is_mill), None)
            if default_mill is None:
                self.warnings.append("Takım listesinde düz tabanlı parmak freze yok; frezeleme operasyonları atlandı.")
        ops = [o for o in self.cfg.operations if self.ops_cfg(o).enabled]
        mill = self._pick_tool("pocket", default_mill)
        if self.setup.stock_top > 1e-6:
            ft = self._pick_tool("face", max((t for t in self.cfg.tools if t.is_mill), key=lambda t: t.diameter, default=None)) \
                if "face" in ops else None
            if ft:
                self.plan.append({"op": "face", "feature": f"üst yüzey ({fmt(self.setup.stock_top)} mm)", "tool": ft.number})
            else:
                self.warnings.append(f"Ham parçanın {fmt(self.setup.stock_top)} mm üst payı alın frezeleme ile kaldırılmıyor "
                                     "(operasyon kapalı veya freze yok); üst yüzeydeki işlemler bu payın altından başlar.")
        if "drill" in ops:
            hel_tool = self._pick_tool("helical", default_mill)
            forced = self.ops_cfg("drill").tool
            for h in self.m.holes:
                ax = h["axis_dir"]
                if abs(ax[2] - 1) > 1e-6:
                    self.warnings.append(f"{h['id']}: eksen +Z değil (3 eksen tezgâhta üstten işlenemez), atlandı.")
                    continue
                drill = next((t for t in self.cfg.tools if t.type == "drill" and abs(t.diameter - h["diameter"]) <= 0.05
                              and (forced is None or t.number == forced)), None)
                if drill:
                    cyc = self.ops_cfg("drill").cycle
                    if cyc == "auto":
                        cyc = "G83" if h["depth"] > 3 * drill.diameter else "G81"
                    self.plan.append({"op": "drill", "feature": h["id"], "tool": drill.number, "hole": h, "cycle": cyc})
                elif hel_tool and h["diameter"] > hel_tool.diameter * 1.05 and self.ops_cfg("helical").enabled:
                    self.plan.append({"op": "helical", "feature": h["id"], "tool": hel_tool.number, "hole": h})
                elif not self.ops_cfg("helical").enabled:
                    self.warnings.append(f"{h['id']} (Ø{h['diameter']}): Ø{h['diameter']} matkap yok ve helisel delik kapalı, atlandı.")
                else:
                    self.warnings.append(f"{h['id']} (Ø{h['diameter']}): uygun matkap/freze yok, atlandı. Takım listesine Ø{h['diameter']} matkap ekleyin.")
        if "pocket" in ops and mill:
            for p in self.m.pockets:
                self.plan.append({"op": "pocket", "feature": p["id"], "tool": mill.number, "pocket": p})
        if "contour" in ops:
            ct = self._pick_tool("contour", default_mill)
            if ct is None:
                pass
            elif self.m.contour_wire() is None:
                self.warnings.append("Alt yüzeyde düzlemsel dış sınır bulunamadı; kontur işleme atlandı.")
            else:
                self.plan.append({"op": "contour", "feature": "dış kontur", "tool": ct.number})
        if "engrave" in ops:
            self._plan_engraving(default_mill)
        # Sıra: alın → matkaplar (çapa göre) → üst yüzey kazımaları → freze operasyonları → cep tabanı kazımaları → dış kontur.
        # Üst yüzey kazıması matkap ile freze arasına alınır: V-uç için ek takım değişimi olmaz.
        # Cep tabanındaki kazıma cepten sonra (taban açılmış olmalı), parçayı serbest bırakan konturdan önce.
        prio = {"face": -1, "drill": 0, "engrave_top": 1, "helical": 2, "pocket": 3, "engrave_floor": 4, "contour": 5}

        def key(o: dict) -> tuple:
            k = o["op"] if o["op"] != "engrave" else ("engrave_top" if o["on_top"] else "engrave_floor")
            return (prio[k], o["tool"] if o["op"] == "drill" else 0)

        self.plan.sort(key=key)

    def _plan_engraving(self, mill: Tool | None) -> None:
        feats = [f for f in self.m.features if f["type"] == "engrave"]
        if not feats:
            return
        vbits = sorted((t for t in self.cfg.tools if t.type == "vbit"), key=lambda t: t.angle or 90)
        endmills = sorted((t for t in self.cfg.tools if t.type == "endmill"), key=lambda t: t.diameter)
        for f in feats:
            if vbits:
                tool = vbits[0]
                cut_w = 2 * f["depth"] * math.tan(math.radians((tool.angle or 90) / 2))
                if abs(cut_w - f["width"]) > max(0.1, 0.25 * f["width"]):
                    self.warnings.append(f"{f['id']}: {fmt(tool.angle or 90)}° V-uç {fmt(f['depth'])} mm derinlikte {fmt(cut_w)} mm genişlik keser "
                                         f"(modeldeki çizgi genişliği {fmt(f['width'])} mm). Derinliği veya genişliği uyumlu hâle getirin.")
            elif endmills:
                tool = endmills[0]
                if tool.diameter > f["width"] * 1.25:
                    self.warnings.append(f"{f['id']}: takım listesinde V-uç yok; Ø{fmt(tool.diameter)} freze ile kazınacak, "
                                         f"çizgi {fmt(f['width'])} mm yerine {fmt(tool.diameter)} mm olur. Takım listesine V-uç ekleyin.")
            else:
                self.warnings.append(f"{f['id']}: kazıma için V-uç veya parmak freze yok, atlandı.")
                continue
            on_top = abs(f["origin"][2] - self.ztop) < 1e-4
            self.plan.append({"op": "engrave", "feature": f["id"], "tool": tool.number, "engrave": f, "on_top": on_top})

    # ------------------------------------------------------------------ operasyonlar
    def tool_change(self, tool: Tool, rpm: float) -> None:
        w, c = self.w, self.c
        if self.current_tool == tool.number:
            if self.current_rpm is None or abs(self.current_rpm - rpm) > 0.5:
                w.add(ir.Spindle(on=True, rpm=rpm))  # aynı takım, farklı operasyon devri
                self.current_rpm = rpm
            return
        first = self.current_tool is None
        if not first:
            w.rapid(z=c.safe_z)
            w.add(ir.Spindle(on=False))
            if c.coolant != "off":
                w.add(ir.Coolant(on=False))
        w.add(ir.ToolChange(tool, first=first))
        w.add(ir.Spindle(on=True, rpm=rpm))
        if c.coolant != "off":
            w.add(ir.Coolant(on=True, mode=c.coolant, code=self.profile.coolant_modes.get(c.coolant, "M8")))
        w.rapid(z=c.safe_z)
        self.current_tool, self.current_rpm = tool.number, rpm

    def _entry(self, op_type: str, path: list[Pt], z_from: float, z_to: float, d: CD, tool: Tool) -> int:
        """Kapalı yolun başına iniş. Rampa: yol boyunca ramp_angle eğimiyle (gerekirse birkaç tur) iner.
        Dönüş: yolun hangi köşesinden devam edileceği (rampa bittiği yer)."""
        w, o = self.w, self.ops_cfg(op_type)
        if o.entry != "ramp" or len(path) < 2 or z_from - z_to < 1e-6:
            w.line(z=z_to, f=d.plunge)
            return 0
        slope = math.tan(math.radians(max(0.5, min(o.ramp_angle, 45))))
        per = sum(math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path)))
        if per * slope < 1e-3:
            w.line(z=z_to, f=d.plunge)
            return 0
        w.line(z=z_from, f=d.plunge)
        z, i, n = z_from, 0, len(path)
        guard = 0
        while z > z_to + 1e-6 and guard < 10000:
            a, b = path[i % n], path[(i + 1) % n]
            seg = math.dist(a, b)
            dz = seg * slope
            if z - dz <= z_to:
                t = (z - z_to) / dz if dz > 0 else 1.0
                q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                w.line(x=self.X(q[0]), y=self.Y(q[1]), z=z_to, f=d.feed)
                # rampanın bittiği noktadan yolu tamamla, böylece rampa altında kalan malzeme de temizlenir
                for k in range(1, n + 1):
                    r = path[(i + k) % n]
                    w.line(x=self.X(r[0]), y=self.Y(r[1]), f=d.feed)
                w.line(x=self.X(q[0]), y=self.Y(q[1]), f=d.feed)
                w.line(x=self.X(path[(i + 1) % n][0]), y=self.Y(path[(i + 1) % n][1]), f=d.feed)
                return (i + 1) % n
            z -= dz
            w.line(x=self.X(b[0]), y=self.Y(b[1]), z=z, f=d.feed)
            i += 1
            guard += 1
        return i % n

    def op_face(self, op: dict) -> None:
        """Ham parçanın üst payını zikzak alın frezeleme ile kaldırır (ham parça + takım yarıçapı kadar taşar)."""
        w, c = self.w, self.c
        tool = self.cfg.tool(op["tool"])
        d = self.cd("face", tool)
        s, r = self.stock, tool.diameter / 2
        step = tool.diameter * min(d.stepover, 0.75)
        z_final = self.Z(self.ztop) + self.ops_cfg("face").stock_z
        x0, x1 = s["xmin"] - r * 1.2, s["xmax"] + r * 1.2
        ys: list[float] = []
        y = s["ymin"] + r * 0.5
        while y < s["ymax"] - r * 0.5 + 1e-6:
            ys.append(y)
            y += step
        if not ys or ys[-1] < s["ymax"] - r * 0.5:
            ys.append(s["ymax"] - r * 0.5)
        w.comment(f"Alin frezeleme: ust pay {fmt(self.setup.stock_top)} mm, {len(ys)} paso, D{fmt(tool.diameter)} freze")
        for z in depth_levels(0.0, z_final, d.stepdown):
            w.rapid(x=self.X(x0), y=self.Y(ys[0]))
            w.rapid(z=1.0)
            w.line(z=z, f=d.plunge)
            for k, yy in enumerate(ys):
                xa, xb = (x0, x1) if k % 2 == 0 else (x1, x0)
                w.line(x=self.X(xa), y=self.Y(yy), f=d.feed)
                w.line(x=self.X(xb), y=self.Y(yy), f=d.feed)
            w.rapid(z=c.safe_z)

    def op_drill(self, op: dict) -> None:
        w, c = self.w, self.c
        h, tool = op["hole"], self.cfg.tool(op["tool"])
        d = self.cd("drill", tool)
        x, y = self.X(h["top_point"][0]), self.Y(h["top_point"][1])
        z_top = self.Z(h["top_point"][2])
        tip = 0.3 * tool.diameter if (tool.angle or 118) >= 110 else 0.5 * tool.diameter
        if h["through"]:
            z_end = z_top - h["depth"] - tip - c.breakthrough
        else:
            z_end = z_top - h["depth"]
            self.warnings.append(f"{h['id']}: kör delik matkapla delinecek; taban {fmt(tool.angle or 118)}° uç açısıyla konik kalır.")
        self._check_tool(tool, z_top - z_end, h["id"])
        # Delik ağzı üst yüzeyin altındaysa (ör. cep tabanında) ve cep henüz işlenmediyse, ağzın üstünde hâlâ
        # malzeme vardır: R düzlemi stok üst yüzeyinin üstünde tutulur, aradaki malzeme ilerlemeyle delinir.
        r = max(z_top, 0.0) + c.retract_z
        w.comment(f"{op['feature']}: Ø{fmt(h['diameter'])} {'boydan boya' if h['through'] else 'kör'} delik, "
                  f"{'gagalamalı' if op['cycle'] == 'G83' else 'düz'} delme")
        w.add(ir.DrillCycle(x=x, y=y, z_bottom=z_end, r_plane=r, clearance_z=c.safe_z, feed=d.feed,
                            peck=(d.peck or c.peck_depth) if op["cycle"] == "G83" else None))

    def op_helical(self, op: dict) -> None:
        w, c = self.w, self.c
        h, tool = op["hole"], self.cfg.tool(op["tool"])
        d = self.cd("helical", tool)
        o = self.ops_cfg("helical")
        cx, cy = self.X(h["top_point"][0]), self.Y(h["top_point"][1])
        z_top = self.Z(h["top_point"][2])
        z_end = z_top - h["depth"] - (c.breakthrough if h["through"] else -o.stock_z)
        rp = (h["diameter"] - 2 * o.stock_xy - tool.diameter) / 2
        if rp <= 0:
            self.warnings.append(f"{h['id']}: bırakılan pay ile delik takımdan dar kalıyor, atlandı.")
            return
        pitch = min(d.stepdown, tool.diameter * 0.25)
        self._check_tool(tool, z_top - z_end, h["id"])
        w.comment(f"{op['feature']}: Ø{fmt(h['diameter'])} delik, helisel interpolasyon (Ø{fmt(tool.diameter)} freze)")
        w.rapid(x=cx + rp, y=cy)
        # ağız cep tabanındaysa helis stok üst yüzeyinden başlar (cep bu operasyondan sonra işlenir)
        z_start = max(z_top, 0.0)
        w.rapid(z=z_start + 1)
        w.line(z=z_start, f=d.plunge)
        z = z_start
        while z > z_end + 1e-6:
            z = max(z - pitch, z_end)
            # tam tur = iki yarım daire (bazı kontrol üniteleri tek komutta 360° kabul etmez)
            w.arc(False, cx - rp, cy, None, -rp, 0, d.feed)
            w.arc(False, cx + rp, cy, z, rp, 0, d.feed)
        w.arc(False, cx - rp, cy, None, -rp, 0, d.feed)  # tabanda temizleme turu
        w.arc(False, cx + rp, cy, None, rp, 0, d.feed)
        w.line(x=cx, y=cy, f=d.feed)
        w.rapid(z=c.safe_z)

    def _pocket_loops(self, boundary, r: float, step: float) -> list[list[Pt]]:
        loops: list[list[Pt]] = []
        off = r
        while True:
            try:
                res = boundary.offset2D(-off, "arc")
            except Exception:
                break
            res = [wi for wi in res if wi.IsClosed()]
            if not res:
                break
            pts = wire_points(res[0])
            if len(pts) < 3 or abs(signed_area(pts)) < 1e-3:
                break
            loops.append(oriented(pts, ccw=True))  # iç bölgede saat yönü tersi = tırmanma frezeleme
            off += step
        loops.reverse()  # içten dışa
        return loops

    def _pocket_level(self, loops: list[list[Pt]], z: float, z_start: float, d: CD, tool: Tool, z_prev: float | None = None) -> None:
        w = self.w
        sx, sy = loops[0][0]
        w.rapid(x=self.X(sx), y=self.Y(sy))
        # hızlı iniş ham parça üst yüzeyinin üstünde biter (alın frezeleme yapılmış olsa da: doğrulayıcı dolu blok varsayar)
        w.rapid(z=max(z_start, 0.0) + 1)
        k0 = self._entry("pocket", loops[0], (z_start if z_prev is None else z_prev) + 0.5, z, d, tool)
        first = loops[0][k0:] + loops[0][:k0]
        for i, loop in enumerate([first] + loops[1:]):
            w.line(x=self.X(loop[0][0]), y=self.Y(loop[0][1]), f=d.feed)
            for q in loop[1:] + [loop[0]]:
                w.line(x=self.X(q[0]), y=self.Y(q[1]), f=d.feed)

    def op_pocket(self, op: dict) -> None:
        w, c = self.w, self.c
        p, tool = op["pocket"], self.cfg.tool(op["tool"])
        d, o = self.cd("pocket", tool), self.ops_cfg("pocket")
        r = tool.diameter / 2
        step = tool.diameter * d.stepover
        boundary = self.m.faces[p["_face_idx"]].outerWire()
        fa = o.finish_allowance if o.finish_pass else 0.0
        final = self._pocket_loops(boundary, r + o.stock_xy, step)
        rough = self._pocket_loops(boundary, r + o.stock_xy + fa, step) if fa > 0 else final
        if not final or not rough:
            self.warnings.append(f"{p['id']}: cep Ø{fmt(tool.diameter)} takım için çok dar, atlandı.")
            return
        feat = next((f for f in self.m.features if f["id"] == p.get("feature_id")), None)
        if feat and feat.get("corner_radius", 0) < r - 1e-6:
            self.warnings.append(f"{p['id']}: köşe yarıçapı ({feat.get('corner_radius', 0)}) takım yarıçapından ({fmt(r)}) küçük; köşelerde malzeme kalır.")
        z_start = self.Z(p["top_z"])
        z_floor = self.Z(p["floor_z"]) + o.stock_z
        self._check_tool(tool, z_start - z_floor, p["id"])
        entry = "rampa" if o.entry == "ramp" else "dalma"
        w.comment(f"{op['feature']}: cep bosaltma, derinlik {fmt(p['depth'])} mm, {len(rough)} ofset turu, giris: {entry}"
                  + (f", son paso payi {fmt(fa)} mm" if fa > 0 else ""))
        prev = None
        for z in depth_levels(z_start, z_floor + fa, d.stepdown):
            self._pocket_level(rough, z, z_start, d, tool, prev)
            prev = z
            w.rapid(z=c.safe_z)
        if fa > 0:
            w.comment(f"{op['feature']}: son paso (taban + duvar)")
            self._pocket_level(final, z_floor, z_start, d, tool, prev)
            w.rapid(z=c.safe_z)

    def op_engrave(self, op: dict) -> None:
        """Çizgi takip: takım merkezi her vuruşu sabit derinlikte izler."""
        w, c = self.w, self.c
        f = op["engrave"]
        tool = self.cfg.tool(op["tool"])
        d = self.cd("engrave", tool)
        z_surf = self.Z(f["origin"][2])
        z_clear = max(z_surf, 0.0) + 1.0  # vuruşlar arası geçiş yüksekliği (stok üst yüzeyinin 1 mm üstü)
        depth = max(f["depth"] - self.ops_cfg("engrave").stock_z, 0.01)
        levels = depth_levels(z_surf, z_surf - depth, d.stepdown)
        strokes = [[(self.X(x), self.Y(y)) for x, y in s] for s in placed_strokes(f)]
        ordered = order_strokes(strokes)
        src = {"text": f"yazı '{f.get('text', '')}'", "path": "yol", "svg": "SVG logo"}[f["source"]]
        w.comment(f"{f['id']}: kazima ({src}), derinlik {fmt(f['depth'])} mm, {len(ordered)} vurus, {len(levels)} kademe")
        w.rapid(z=c.safe_z)
        for pts in ordered:
            closed = len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6
            w.rapid(x=pts[0][0], y=pts[0][1])
            w.rapid(z=z_clear)
            for k, z in enumerate(levels):
                w.line(z=z, f=d.plunge)
                for x, y in pts[1:]:
                    w.line(x=x, y=y, f=d.feed)
                if not closed and k < len(levels) - 1:
                    pts = pts[::-1]  # açık vuruşta sonraki kademeye geri dönmeden ters yönde devam et
            w.rapid(z=z_clear)
        w.rapid(z=c.safe_z)

    def _contour_pass(self, pts: list[Pt], levels: list[float], d: CD, tool: Tool) -> None:
        w = self.w
        prev = 0.0
        for z in levels:
            w.rapid(x=self.X(pts[0][0]), y=self.Y(pts[0][1]))
            w.rapid(z=1.0)
            k0 = self._entry("contour", pts, min(prev + 0.5, 0.5), z, d, tool)  # rampa bir önceki kademeden başlar
            prev = z
            path = pts[k0:] + pts[:k0]
            for q in path[1:] + [path[0]]:
                w.line(x=self.X(q[0]), y=self.Y(q[1]), f=d.feed)
            w.rapid(z=self.c.safe_z)

    def op_contour(self, op: dict) -> None:
        w, c = self.w, self.c
        tool = self.cfg.tool(op["tool"])
        d, o = self.cd("contour", tool), self.ops_cfg("contour")
        r = tool.diameter / 2
        wire = self.m.contour_wire()
        fa = o.finish_allowance if o.finish_pass else 0.0
        z_end = self.Z(self.zbot) - c.breakthrough
        self._check_tool(tool, -z_end, "Dış kontur")
        w.comment(f"Dış kontur: {fmt(self.ztop - self.zbot)} mm yükseklik, Ø{fmt(tool.diameter)} freze"
                  + (f", son paso payi {fmt(fa)} mm" if fa > 0 else ""))
        self.warnings.append("Dış kontur boydan boya kesiliyor; tutma köprüsü (tab) yok, parçayı sabitlemeyi unutmayın.")
        rough = oriented(wire_points(wire.offset2D(r + o.stock_xy + fa, "arc")[0]), ccw=False)  # saat yönü = tırmanma
        self._contour_pass(rough, depth_levels(0.0, z_end, d.stepdown), d, tool)
        if fa > 0:
            fin = oriented(wire_points(wire.offset2D(r + o.stock_xy, "arc")[0]), ccw=False)
            fl = tool.flute_length or (-z_end)
            w.comment("Dış kontur: son paso (duvar)")
            self._contour_pass(fin, depth_levels(0.0, z_end, max(fl - 0.5, d.stepdown)), d, tool)

    # ------------------------------------------------------------------ ana
    def build_program(self) -> ir.Program:
        self.build_plan()
        w, c = self.w, self.c
        w.rapid(z=c.safe_z)
        dispatch = {"face": self.op_face, "drill": self.op_drill, "helical": self.op_helical, "pocket": self.op_pocket,
                    "engrave": self.op_engrave, "contour": self.op_contour}
        for op in self.plan:
            tool = self.cfg.tool(op["tool"])
            op["cd"] = self.cd(op["op"], tool)
            self.tool_change(tool, op["cd"].rpm)
            dispatch[op["op"]](op)
        w.rapid(z=c.safe_z)
        w.add(ir.Spindle(on=False))
        if c.coolant != "off":
            w.add(ir.Coolant(on=False))
        st = self.setup
        from .materials import material

        header = [
            f"Model: {self.m.name} (v{self.m.version}) - {datetime.now():%Y-%m-%d %H:%M}",
            f"Makine: {self.profile.name} | Malzeme: {material(st.material)['name']}",
            f"Is sifiri G{st.wcs}: {ORIGIN_TR[st.origin_xy]}, Z0 = ham parca ust yuzeyi",
            f"Ham parca: {fmt(self.stock['xmax'] - self.stock['xmin'])} x {fmt(self.stock['ymax'] - self.stock['ymin'])} x "
            f"{fmt(self.stock['zmax'] - self.stock['zmin'])} mm (yan pay {fmt(st.stock_side)}, ust pay {fmt(st.stock_top)})",
            "UYARI: Prototip ciktisidir. Tezgahta kullanmadan once simule edin ve dogrulayin.",
        ]
        return ir.Program(name=self.m.name, header=header, ops=w.ops, safe_z=c.safe_z, wcs=st.wcs)

    def generate(self) -> dict[str, Any]:
        prog = self.build_program()
        post = get_post(self.profile)
        text = post.run(prog)

        def cycle_label(o: dict) -> dict:
            if "cycle" not in o:
                return {}
            return {"cycle": o["cycle"] if self.profile.canned_cycles else f"{o['cycle']} → G0/G1"}

        plan_public = [{"op": o["op"], "feature": o["feature"], "tool": o["tool"], **cycle_label(o),
                        **(o["cd"].public() if "cd" in o else {})} for o in self.plan]
        if not self.plan:
            self.warnings.append("İşlenecek operasyon bulunamadı.")
        ox, oy, oz = self.origin
        return {"text": text, "plan": plan_public, "warnings": list(dict.fromkeys(self.warnings + post.notes)),
                "stock_height": round(self.stock["zmax"] - self.stock["zmin"], 4), "profile_id": self.profile.id, "post": post.id,
                "ir_op_count": len(prog.ops),
                # iş sıfırının model koordinatı + ham parça kutusu (model koordinatında): simülasyon ve atölye bunu kullanır
                "work_origin_model": [round(ox, 4), round(oy, 4), round(oz, 4)],
                "stock_model": {k: round(v, 4) for k, v in self.stock.items()},
                "setup": {"wcs": self.setup.wcs, "origin_xy": self.setup.origin_xy, "material": self.setup.material,
                          "stock_side": self.setup.stock_side, "stock_top": self.setup.stock_top},
                "cutting_mode": self.cfg.cutting_mode}


def generate_gcode(model: CadModel, cfg: MachiningConfig) -> dict[str, Any]:
    return GcodeGenerator(model, cfg).generate()
