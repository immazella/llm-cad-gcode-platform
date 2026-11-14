"""Çalışma alanı: aktif model, G-code kayıtları, işleme ayarları ve araç (tool) uygulamaları.

Hem REST API hem de LLM tool calling aynı fonksiyonları çağırır; böylece
arayüzdeki bir buton ile sohbetteki bir komut birebir aynı işi yapar.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from .cad.features import CadError
from .cad.model import CadModel, load_step
from .cam.config import MachiningConfig
from .cam.generator import generate_gcode
from .cam.profiles import Setup, get_profile, list_profiles
from .cam.validator import validate

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


class ToolError(Exception):
    pass


from .i18n import tr, error_message
from .validation_i18n import present_report
from .presentation_i18n import present_profile, present_materials, present_cutting, present_warning


class Workspace:
    def __init__(self) -> None:
        self.model: CadModel | None = None
        self.gcodes: dict[str, dict[str, Any]] = {}
        self.current_gcode: str | None = None
        self.config = MachiningConfig()

    # ------------------------------------------------------------------ yardımcı
    def require_model(self) -> CadModel:
        if self.model is None:
            raise ToolError("Henüz aktif bir model yok. Önce bir model oluşturun veya STEP dosyası yükleyin.")
        return self.model

    def state(self) -> dict[str, Any]:
        g = self.gcodes.get(self.current_gcode) if self.current_gcode else None
        return {
            "model": self.model.summary() if self.model else None,
            "gcode": self.gcode_meta(g) if g else None,
            "config": self.config.model_dump(),
            "profile": present_profile(self.config.profile.model_dump()),
        }

    def context(self, selection: dict[str, Any] | list[dict[str, Any]] | None = None) -> str:
        """LLM'ye verilen kompakt [BAĞLAM] bloğu (web sohbeti ve MCP get_context aynı metni kullanır)."""
        from .llm.prompts import context_block

        gmeta = self.gcode_meta(self.gcodes[self.current_gcode]) if self.current_gcode in self.gcodes else None
        from .cam.materials import material

        st, cfg = self.config.setup, self.config
        prof = {**cfg.profile.summary(), "work_offset": list(st.offset_for(cfg.profile)), "wcs": st.wcs,
                "setup": f"malzeme {material(st.material)['name']} ({st.material}), iş sıfırı {st.origin_xy}, G{st.wcs}, "
                         f"ham parça yan payı {st.stock_side:g} mm, üst payı {st.stock_top:g} mm, kesme verisi {cfg.cutting_mode}, "
                         f"soğutma {cfg.cutting.coolant}",
                "tools": ", ".join(f"T{t.number} {t.label()}" + (f" [{t.diameter:g} mm]" if "Ø" not in t.label() else "") for t in cfg.tools)}
        return context_block(self.model.summary() if self.model else None, selection, gmeta, prof)

    @staticmethod
    def gcode_meta(g: dict[str, Any]) -> dict[str, Any]:
        meta = {k: g.get(k) for k in ("gcode_id", "source", "model_id", "model_version", "plan", "warnings", "validation", "line_count",
                                      "stale", "generated_for", "post", "work_origin_model", "stock_model", "setup", "cutting_mode")}
        return {**meta, "validation": present_report(meta["validation"]),
                "warnings": [present_warning(w) for w in (meta["warnings"] or [])]}

    # ------------------------------------------------------------------ model işlemleri
    def create_box(self, length: float, width: float, height: float, name: str | None = None) -> dict:
        # Default name is chosen once, in the creation language; it is stored data afterwards.
        default = tr(f"Kutu {length:g}x{width:g}x{height:g}", f"Box {length:g}x{width:g}x{height:g}")
        self.model = CadModel(name or default, {"type": "box", "length": float(length), "width": float(width), "height": float(height)})
        return {"ok": True, "message": tr("Kutu/plaka oluşturuldu.", "Box/plate created."), "model": self.model.summary()}

    def create_cylinder(self, radius: float, height: float, name: str | None = None) -> dict:
        default = tr(f"Silindir R{radius:g} H{height:g}", f"Cylinder R{radius:g} H{height:g}")
        self.model = CadModel(name or default, {"type": "cylinder", "radius": float(radius), "height": float(height)})
        return {"ok": True, "message": tr("Silindir oluşturuldu.", "Cylinder created."), "model": self.model.summary()}

    def load_step_file(self, path: Path, name: str) -> dict:
        self.model = load_step(path, name)
        s = self.model.summary()
        return {"ok": True, "message": tr(f"STEP yüklendi: {len(s['holes'])} delik, {len(s['pockets'])} cep tanındı.", f"STEP loaded: recognized {len(s['holes'])} holes and {len(s['pockets'])} pockets."), "model": s}

    def add_hole(self, diameter: float, x: float = 0, y: float = 0, depth: float | None = None, face_id: str | None = None) -> dict:
        m = self.require_model()
        fid = m.add_hole(float(x), float(y), float(diameter), depth, face_id)
        return {"ok": True, "feature_id": fid, "holes": m.summary()["holes"]}

    def add_pocket(self, length: float, width: float, depth: float, x: float = 0, y: float = 0, corner_radius: float = 0, face_id: str | None = None) -> dict:
        m = self.require_model()
        fid = m.add_pocket(float(x), float(y), float(length), float(width), float(depth), float(corner_radius or 0), face_id)
        return {"ok": True, "feature_id": fid, "pockets": m.summary()["pockets"]}

    # ------------------------------------------------------------------ kazıma (gravür)
    def _engrave_width(self, depth: float, width: float | None) -> float:
        """Çizgi genişliği verilmezse listedeki V-uçla bu derinlikte kesilecek genişlik (yoksa 0,5 mm)."""
        if width:
            return float(width)
        import math

        vb = sorted((t for t in self.config.tools if t.type == "vbit"), key=lambda t: t.angle or 90)
        if vb:
            return round(max(0.1, 2 * float(depth) * math.tan(math.radians((vb[0].angle or 90) / 2))), 3)
        return 0.5

    def _engrave_result(self, fid: str) -> dict:
        from .cad.engrave import placed_strokes

        m = self.require_model()
        f = m.feature_by_id(fid)
        strokes = placed_strokes(f)
        xs = [p[0] for s in strokes for p in s]
        ys = [p[1] for s in strokes for p in s]
        return {"ok": True, "feature_id": fid, "stroke_count": len(strokes), "depth": f["depth"], "width": f["width"],
                "extent_xy": {"x": [round(min(xs), 2), round(max(xs), 2)], "y": [round(min(ys), 2), round(max(ys), 2)]}}

    def add_engraving_text(self, text: str, height: float = 6.0, x: float = 0, y: float = 0, depth: float = 0.3,
                           width: float | None = None, rotation: float = 0, font: str = "tek_cizgi", face_id: str | None = None) -> dict:
        m = self.require_model()
        fid = m.add_engraving("text", float(x), float(y), float(depth), self._engrave_width(depth, width), float(rotation or 0),
                              face_id, text=str(text), height=float(height), font=font)
        return self._engrave_result(fid)

    def add_engraving_path(self, paths: list, closed: bool | list[bool] = False, depth: float = 0.3,
                           width: float | None = None, face_id: str | None = None) -> dict:
        m = self.require_model()
        if paths and isinstance(paths[0], (list, tuple)) and paths[0] and isinstance(paths[0][0], (int, float)):
            paths = [paths]  # tek yol düz nokta listesi olarak verildiyse
        flags = closed if isinstance(closed, list) else [bool(closed)] * len(paths)
        if len(flags) != len(paths):
            raise ToolError("closed listesi yol sayısıyla aynı uzunlukta olmalı.")
        fid = m.add_engraving("path", 0, 0, float(depth), self._engrave_width(depth, width), 0, face_id, paths=paths, closed=flags)
        return self._engrave_result(fid)

    def add_engraving_svg(self, svg: str, size: float = 30.0, x: float = 0, y: float = 0, depth: float = 0.3,
                          width: float | None = None, rotation: float = 0, face_id: str | None = None, name: str = "logo") -> dict:
        m = self.require_model()
        fid = m.add_engraving("svg", float(x), float(y), float(depth), self._engrave_width(depth, width), float(rotation or 0),
                              face_id, svg=svg, size=float(size), name=name)
        return self._engrave_result(fid)

    def add_fillet(self, radius: float, edges: str = "vertical") -> dict:
        fid = self.require_model().add_edge_feature("fillet", float(radius), edges)
        return {"ok": True, "feature_id": fid}

    def add_chamfer(self, size: float, edges: str = "top") -> dict:
        fid = self.require_model().add_edge_feature("chamfer", float(size), edges)
        return {"ok": True, "feature_id": fid}

    def modify_base(self, length=None, width=None, height=None, radius=None) -> dict:
        m = self.require_model()
        m.modify_base(length=length, width=width, height=height, radius=radius)
        return {"ok": True, "base": m.base, "bbox": m.summary()["bbox"]}

    def modify_hole(self, hole_id: str, new_diameter=None, x=None, y=None, depth=None, through=None) -> dict:
        m = self.require_model()
        fid = m.modify_hole(hole_id, new_diameter, x, y, depth, through)
        return {"ok": True, "hole_id": fid, "holes": m.summary()["holes"]}

    def modify_feature(self, feature_id: str, params: dict) -> dict:
        m = self.require_model()
        m.modify_feature(feature_id, **(params or {}))
        return {"ok": True, "feature": m.feature_by_id(feature_id)}

    def delete_feature(self, feature_id: str) -> dict:
        m = self.require_model()
        m.delete_feature(feature_id)
        return {"ok": True, "features": m.summary()["features"]}

    def undo(self) -> dict:
        what = self.require_model().undo()
        return {"ok": True, "undone": what}

    def get_geometry_info(self) -> dict:
        return self.require_model().summary()

    def get_holes(self) -> dict:
        return {"holes": self.require_model().summary()["holes"]}

    def get_face_info(self, face_id: str) -> dict:
        return self.require_model().face_info(face_id)

    def export_step(self) -> dict:
        m = self.require_model()
        path = DATA_DIR / f"{m.id}_v{m.version}.step"
        m.export_step(path)
        return {"ok": True, "download_url": "/api/models/current/export.step", "file": path.name}

    # ------------------------------------------------------------------ G-code
    def _store_gcode(self, text: str, source: str, extra: dict | None = None) -> dict:
        gid = uuid.uuid4().hex[:8]
        stock_h = (extra or {}).get("stock_height")
        rec = {
            "gcode_id": gid,
            "source": source,
            "text": text,
            "model_id": self.model.id if (self.model and source == "generated") else None,
            "model_version": self.model.version if (self.model and source == "generated") else None,
            "plan": (extra or {}).get("plan", []),
            "warnings": (extra or {}).get("warnings", []),
            "stock_height": stock_h,
            "line_count": len(text.splitlines()),
            "validation": None,
            "stale": False,
            # üretilen programlar belirli bir profil/post için yazılır; yüklenenler için None
            "generated_for": (extra or {}).get("profile_id"),
            "post": (extra or {}).get("post"),
            # iş sıfırının model koordinatındaki yeri + ham parça (simülasyon, atölye çarpışma kontrolü)
            "work_origin_model": (extra or {}).get("work_origin_model"),
            "stock_model": (extra or {}).get("stock_model"),
            "setup": (extra or {}).get("setup"),
            "cutting_mode": (extra or {}).get("cutting_mode"),
        }
        rec["validation"] = self._validate(rec)
        self.gcodes[gid] = rec
        self.current_gcode = gid
        (DATA_DIR / f"{gid}.nc").write_text(text)
        return rec

    def generate_gcode(self, operations: list[str] | None = None, mill_tool_diameter: float | None = None, **cutting: Any) -> dict:
        m = self.require_model()
        cfg = self.config.model_copy(deep=True)
        if operations:
            from .cam.config import OP_TYPES

            bad = set(operations) - set(OP_TYPES)
            if bad:
                raise ToolError(f"Geçersiz operasyon(lar): {', '.join(bad)}. Kullanılabilir: {', '.join(OP_TYPES)}")
            cfg.operations = operations
            # helisel delik delmenin parçasıdır (matkabı olmayan delikler); açık/kapalı durumu op_settings.helical.enabled
        if mill_tool_diameter:
            t = next((t for t in cfg.tools if t.is_mill and abs(t.diameter - mill_tool_diameter) < 1e-6), None)
            if t is None:
                num = max(t.number for t in cfg.tools) + 1
                from .cam.config import Tool
                t = Tool(number=num, type="endmill", diameter=float(mill_tool_diameter), description=f"Ø{mill_tool_diameter:g} parmak freze")
                cfg.tools.append(t)
            cfg.mill_tool = t.number
            for op in ("pocket", "contour", "helical"):  # açık istek operasyon bazlı takım seçiminin önüne geçer
                cfg.op_settings[op].tool = t.number
        # Bu üretim için verilen kesme değerleri: devir/ilerleme vb. operasyon ayarı olarak (otomatik modda da geçerli),
        # güvenlik yükseklikleri ve soğutma genel ayara yazılır.
        mill_map = {"spindle_rpm": "rpm", "feed_rate": "feed", "plunge_rate": "plunge", "depth_per_pass": "stepdown",
                    "stepover_ratio": "stepover_ratio"}
        drill_map = {"drill_rpm": "rpm", "drill_feed": "feed", "peck_depth": "peck"}
        for k, v in cutting.items():
            if v is None:
                continue
            if k == "material":
                cfg.setup.material = self._material_id(str(v))
                continue
            if k in mill_map:
                for op in ("face", "helical", "pocket", "engrave", "contour"):
                    setattr(cfg.op_settings[op], mill_map[k], v)
            elif k in drill_map:
                setattr(cfg.op_settings["drill"], drill_map[k], v)
            if hasattr(cfg.cutting, k):
                setattr(cfg.cutting, k, v)
        res = generate_gcode(m, cfg)
        rec = self._store_gcode(res["text"], "generated", res)
        return {"ok": True, "gcode_id": rec["gcode_id"], "profile": self.config.profile.summary(), "post": rec["post"],
                "plan": rec["plan"], "warnings": [present_warning(w) for w in rec["warnings"]], "setup": rec["setup"], "cutting_mode": rec["cutting_mode"],
                "line_count": rec["line_count"], "validation_summary": {"valid": rec["validation"]["valid"], "counts": rec["validation"]["counts"],
                                                                       "estimated_time_s": rec["validation"]["stats"]["estimated_time_s"]}}

    def _validate(self, rec: dict[str, Any]) -> dict[str, Any]:
        return validate(rec["text"], self.config.profile, self.config.setup, rec.get("stock_height"), self.config.tool_lengths())

    def revalidate_current(self) -> dict[str, Any] | None:
        rec = self.gcodes.get(self.current_gcode) if self.current_gcode else None
        if rec is None:
            return None
        rec["validation"] = self._validate(rec)
        return rec["validation"]

    def upload_gcode(self, text: str, name: str = "yüklenen") -> dict:
        return self._store_gcode(text, f"upload:{name}")

    def validate_gcode(self, gcode_id: str | None = None) -> dict:
        gid = gcode_id or self.current_gcode
        if not gid or gid not in self.gcodes:
            raise ToolError("Doğrulanacak G-code yok. Önce G-code üretin veya yükleyin.")
        rec = self.gcodes[gid]
        rec["validation"] = self._validate(rec)
        v = rec["validation"]
        out = {"gcode_id": gid, "profile": v["profile"], "valid": v["valid"], "counts": v["counts"], "levels": v["levels"],
               "issues": v["issues"][:25], "stats": v["stats"], "not_checked": v["not_checked"]}
        if rec.get("generated_for") and rec["generated_for"] != self.config.profile_id:
            out["note"] = tr(f"Bu program '{rec['generated_for']}' profili için üretildi ama şu an '{self.config.profile_id}' profiline göre doğrulandı. "
                             "Bu makine için yeniden üretmek gerekebilir.",
                             f"This program was generated for '{rec['generated_for']}' but validated against '{self.config.profile_id}'. "
                             "It may need to be regenerated for this machine.")
        return out

    def set_tool_length(self, tool_number: int, length: float | None) -> dict:
        t = self.config.tool(int(tool_number))
        if t is None:
            raise ToolError(f"T{tool_number} takım listesinde yok. Mevcut: " + ", ".join(f"T{x.number}" for x in self.config.tools))
        if length is not None and length < 0:
            raise ToolError("Takım boyu negatif olamaz.")
        t.length = None if length is None else float(length)
        out: dict[str, Any] = {"ok": True, "tool": t.model_dump()}
        v = self.revalidate_current()
        if v is not None:
            out["revalidation"] = {"valid": v["valid"], "counts": v["counts"]}
        if not self.config.profile.requires_tool_length_comp:
            out["note"] = tr(f"{self.config.profile.name} G43 H kullanmaz; takım boyu bu makinede doğrulamayı etkilemez.",
                             f"{self.config.profile.name} does not use G43 H; tool length does not affect validation on this machine.")
        return out

    # ------------------------------------------------------------------ takım kütüphanesi / kurulum / operasyonlar
    @staticmethod
    def _material_id(v: str) -> str:
        from .cam.materials import MATERIALS

        if v in MATERIALS:
            return v
        vl = v.lower()
        for k, m in MATERIALS.items():
            if vl in m["name"].lower() or vl.replace(" ", "") in k:
                return k
        raise ToolError(f"Bilinmeyen malzeme '{v}'. Kullanılabilir: " + ", ".join(f"{k} ({m['name']})" for k, m in MATERIALS.items()))

    def list_materials(self) -> dict:
        from .cam.materials import NOTE, list_materials

        return present_materials({"active": self.config.setup.material, "materials": list_materials(), "note": NOTE})

    def recommend_cutting(self, tool_number: int | None = None, tool_type: str | None = None, diameter: float | None = None,
                          flutes: int | None = None, tool_material: str | None = None, material: str | None = None) -> dict:
        from .cam.materials import recommend

        t = self.config.tool(int(tool_number)) if tool_number is not None else None
        if tool_number is not None and t is None:
            raise ToolError(f"T{tool_number} takım listesinde yok.")
        ttype = tool_type or (t.type if t else "endmill")
        d = diameter or (t.diameter if t else None)
        if not d:
            raise ToolError("tool_number veya diameter verin.")
        p = self.config.profile
        mid = self._material_id(material) if material else self.config.setup.material
        rec = recommend(ttype, float(d), int(flutes or (t.flutes if t else 2)), tool_material or (t.tool_material if t else "carbide"),
                        mid, max_rpm=p.max_spindle, min_rpm=p.min_spindle, max_feed=p.max_feed)
        return present_cutting({"ok": True, "tool": t.model_dump() if t else None, "machine": p.name, **rec})

    def upsert_tool(self, number: int, **fields: Any) -> dict:
        from .cam.config import Tool

        fields = {k: v for k, v in fields.items() if v is not None}
        cur = self.config.tool(int(number))
        try:
            t = Tool.model_validate({**(cur.model_dump() if cur else {"type": "endmill", "diameter": 6}), **fields, "number": int(number)})
        except Exception as exc:
            raise ToolError(f"Geçersiz takım: {exc}") from exc
        self.config.tools = sorted([x for x in self.config.tools if x.number != t.number] + [t], key=lambda x: x.number)
        v = self.revalidate_current()
        return {"ok": True, "created": cur is None, "tool": t.model_dump(),
                **({"revalidation": {"valid": v["valid"], "counts": v["counts"]}} if v else {})}

    def delete_tool(self, number: int) -> dict:
        if self.config.tool(int(number)) is None:
            raise ToolError(f"T{number} takım listesinde yok.")
        if len(self.config.tools) == 1:
            raise ToolError("Son takım silinemez.")
        self.config.tools = [t for t in self.config.tools if t.number != int(number)]
        for o in self.config.op_settings.values():
            if o.tool == int(number):
                o.tool = None
        if self.config.mill_tool == int(number):
            self.config.mill_tool = next((t.number for t in self.config.tools if t.is_mill), self.config.tools[0].number)
        return {"ok": True, "deleted": int(number), "tools": [t.number for t in self.config.tools]}

    def set_setup(self, material: str | None = None, origin_xy: str | None = None, wcs: int | None = None,
                  stock_side: float | None = None, stock_top: float | None = None, cutting_mode: str | None = None,
                  coolant: str | None = None) -> dict:
        upd: dict[str, Any] = {}
        if material is not None:
            upd["material"] = self._material_id(material)
        if origin_xy is not None:
            upd["origin_xy"] = origin_xy
        if wcs is not None:
            if int(wcs) not in self.config.profile.work_offsets:
                raise ToolError(f"G{wcs} bu makinede tanımlı değil: " + ", ".join(f"G{w}" for w in self.config.profile.work_offsets))
            upd["wcs"] = int(wcs)
        if stock_side is not None:
            upd["stock_side"] = float(stock_side)
        if stock_top is not None:
            upd["stock_top"] = float(stock_top)
        try:
            self.config.setup = Setup.model_validate({**self.config.setup.model_dump(), **upd})
            if cutting_mode is not None:
                self.config.cutting_mode = cutting_mode  # type: ignore[assignment]
            if coolant is not None:
                self.config.cutting = self.config.cutting.model_validate({**self.config.cutting.model_dump(), "coolant": coolant})
            self.config = self.config.model_validate(self.config.model_dump())
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Geçersiz kurulum değeri: {exc}") from exc
        out: dict[str, Any] = {"ok": True, "setup": self.config.setup.model_dump(), "cutting_mode": self.config.cutting_mode,
                               "coolant": self.config.cutting.coolant}
        if self.current_gcode and self.gcodes[self.current_gcode]["source"] == "generated":
            out["note"] = tr("Kurulum değişti: mevcut G-code eski kuruluma göre üretildi, generate_gcode ile yeniden üretin.",
                             "Setup changed: the current G-code was generated for the previous setup; regenerate it with generate_gcode.")
        return out

    def set_operation(self, op: str, **fields: Any) -> dict:
        from .cam.config import OP_TYPES, OpSettings

        if op not in OP_TYPES:
            raise ToolError(f"Bilinmeyen operasyon '{op}'. Kullanılabilir: {', '.join(OP_TYPES)}")
        clear = set(fields.pop("clear", None) or [])
        cur = self.config.op_settings[op].model_dump()
        for k in clear:
            if k in cur:
                cur[k] = OpSettings.model_fields[k].default
        cur.update({k: v for k, v in fields.items() if v is not None})
        if cur.get("tool") is not None and self.config.tool(int(cur["tool"])) is None:
            raise ToolError(f"T{cur['tool']} takım listesinde yok.")
        try:
            self.config.op_settings[op] = OpSettings.model_validate(cur)
        except Exception as exc:
            raise ToolError(f"Geçersiz operasyon ayarı: {exc}") from exc
        if self.config.op_settings[op].enabled and op not in self.config.operations:
            self.config.operations = [o for o in OP_TYPES if o in {*self.config.operations, op}]
        return {"ok": True, "op": op, "settings": self.config.op_settings[op].model_dump()}

    # ------------------------------------------------------------------ atölye (prove-out desteği)
    def get_line_state(self, line: int, gcode_id: str | None = None) -> dict:
        from .cam.shop import line_state
        return line_state(self, int(line), gcode_id)

    def prove_out_checklist(self, gcode_id: str | None = None) -> dict:
        from .cam.shop import prove_out_checklist
        return prove_out_checklist(self, gcode_id)

    def explain_alarm(self, code: str, line: int | None = None, controller: str | None = None) -> dict:
        from .cam.shop import explain_alarm
        return explain_alarm(self, str(code), int(line) if line is not None else None, controller)

    def suggest_wear_offset(self, feature: str, nominal: float, measured: float, tool_number: int | None = None,
                            tolerance: float | None = None) -> dict:
        from .cam.shop import wear_offset
        return wear_offset(self, feature, float(nominal), float(measured), tool_number, tolerance)

    def diff_programs(self, base_id: str | None = None, edited_id: str | None = None) -> dict:
        from .cam.shop import diff_programs
        edited = edited_id or self.current_gcode
        if not edited:
            raise ToolError("Karşılaştırılacak program yok.")
        if base_id is None:
            gens = [g for g, r in self.gcodes.items() if g != edited and r["source"] == "generated"]
            if not gens:
                raise ToolError("Karşılaştırılacak üretilmiş (onaylı) bir program yok; base_id verin.")
            base_id = gens[-1]
        return diff_programs(self, base_id, edited)

    # ------------------------------------------------------------------ makine profili
    def list_machine_profiles(self) -> dict:
        return {"active": self.config.profile_id,
                "profiles": [{**p.summary(), "description": present_profile(p.model_dump())["description"], "tool_changer": p.tool_changer,
                              "canned_cycles": p.canned_cycles, "max_spindle": p.max_spindle, "max_feed": p.max_feed,
                              "travel": {a: [t.min, t.max] for a, t in p.travel.items()}} for p in list_profiles()]}

    def set_machine_profile(self, profile_id: str, work_offset: list[float] | None = None) -> dict:
        try:
            prof = get_profile(profile_id)
        except KeyError as exc:
            raise ToolError(str(exc.args[0])) from exc
        if work_offset is not None and len(work_offset) != 3:
            raise ToolError("work_offset üç değer olmalı: [X, Y, Z] (makine koordinatında).")
        changed = prof.id != self.config.profile_id
        self.config.profile_id = prof.id
        if changed or work_offset is not None:  # yalnızca iş sıfırının makinedeki yeri sıfırlanır; ham parça/malzeme kalır
            self.config.setup = self.config.setup.model_copy(update={"work_offset": tuple(work_offset) if work_offset else None})
            if self.config.setup.wcs not in prof.work_offsets:
                self.config.setup.wcs = prof.work_offsets[0] if prof.work_offsets else 54
        out: dict[str, Any] = {"ok": True, "profile": prof.summary(), "work_offset": list(self.config.setup.offset_for(prof))}
        v = self.revalidate_current()
        if v is not None:
            rec = self.gcodes[self.current_gcode]
            out["revalidation"] = {"gcode_id": rec["gcode_id"], "valid": v["valid"], "counts": v["counts"]}
            if rec.get("generated_for") and rec["generated_for"] != prof.id:
                out["note"] = tr(f"Mevcut program '{rec['generated_for']}' profili için üretilmişti; bu makine için G-code'u yeniden üretin.",
                                 f"The current program was generated for profile '{rec['generated_for']}'; regenerate G-code for this machine.")
        return out

    def mark_stale(self) -> None:
        g = self.gcodes.get(self.current_gcode) if self.current_gcode else None
        if g and self.model and g["model_id"] == self.model.id and g["model_version"] != self.model.version:
            g["stale"] = True


# Model değiştiren araçlar (frontend'in modeli yeniden çekmesi için)
MODEL_TOOLS = {"create_box", "create_cylinder", "add_hole", "add_pocket", "add_engraving_text", "add_engraving_path",
               "add_fillet", "add_chamfer", "modify_base",
               "modify_hole", "modify_feature", "delete_feature", "undo"}
GCODE_TOOLS = {"generate_gcode", "validate_gcode", "set_machine_profile", "set_tool_length"}
# Makine/takım/kurulum ayarını değiştiren araçlar (frontend'in ayarları yeniden çekmesi için)
CONFIG_TOOLS = {"set_machine_profile", "set_tool_length", "upsert_tool", "delete_tool", "set_setup", "set_operation"}


def run_tool(ws: Workspace, name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn: Callable | None = getattr(ws, name, None) if name in TOOL_NAMES else None
    if fn is None:
        return {"ok": False, "error": error_message(f"Bilinmeyen araç: {name}")}
    try:
        result = fn(**(args or {}))
        # The validator stores canonical records; localize only this tool response.
        return present_report(result) if name == "validate_gcode" else result
    except (CadError, ToolError) as exc:
        return {"ok": False, "error": error_message(str(exc))}
    except TypeError as exc:
        return {"ok": False, "error": error_message(f"Geçersiz parametre: {exc}")}
    except Exception as exc:  # beklenmeyen
        return {"ok": False, "error": error_message(f"İşlem başarısız: {type(exc).__name__}: {exc}")}


from .llm.tool_schemas import TOOL_SCHEMAS  # noqa: E402

TOOL_NAMES = {t["name"] for t in TOOL_SCHEMAS}
