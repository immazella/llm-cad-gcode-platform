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
            "profile": self.config.profile.model_dump(),
        }

    @staticmethod
    def gcode_meta(g: dict[str, Any]) -> dict[str, Any]:
        return {k: g.get(k) for k in ("gcode_id", "source", "model_id", "model_version", "plan", "warnings", "validation", "line_count",
                                      "stale", "generated_for", "post")}

    # ------------------------------------------------------------------ model işlemleri
    def create_box(self, length: float, width: float, height: float, name: str | None = None) -> dict:
        self.model = CadModel(name or f"Kutu {length:g}x{width:g}x{height:g}", {"type": "box", "length": float(length), "width": float(width), "height": float(height)})
        return {"ok": True, "message": "Kutu/plaka oluşturuldu.", "model": self.model.summary()}

    def create_cylinder(self, radius: float, height: float, name: str | None = None) -> dict:
        self.model = CadModel(name or f"Silindir R{radius:g} H{height:g}", {"type": "cylinder", "radius": float(radius), "height": float(height)})
        return {"ok": True, "message": "Silindir oluşturuldu.", "model": self.model.summary()}

    def load_step_file(self, path: Path, name: str) -> dict:
        self.model = load_step(path, name)
        s = self.model.summary()
        return {"ok": True, "message": f"STEP yüklendi: {len(s['holes'])} delik, {len(s['pockets'])} cep tanındı.", "model": s}

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
            bad = set(operations) - {"drill", "pocket", "engrave", "contour"}
            if bad:
                raise ToolError(f"Geçersiz operasyon(lar): {', '.join(bad)}. Kullanılabilir: drill, pocket, engrave, contour")
            cfg.operations = operations
        if mill_tool_diameter:
            t = next((t for t in cfg.tools if t.type == "endmill" and abs(t.diameter - mill_tool_diameter) < 1e-6), None)
            if t is None:
                num = max(t.number for t in cfg.tools) + 1
                from .cam.config import Tool
                t = Tool(number=num, type="endmill", diameter=float(mill_tool_diameter), description=f"Ø{mill_tool_diameter:g} parmak freze")
                cfg.tools.append(t)
            cfg.mill_tool = t.number
        for k, v in cutting.items():
            if v is not None and hasattr(cfg.cutting, k):
                setattr(cfg.cutting, k, v)
        res = generate_gcode(m, cfg)
        rec = self._store_gcode(res["text"], "generated", res)
        return {"ok": True, "gcode_id": rec["gcode_id"], "profile": self.config.profile.summary(), "post": rec["post"],
                "plan": rec["plan"], "warnings": rec["warnings"],
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
            out["note"] = (f"Bu program '{rec['generated_for']}' profili için üretildi ama şu an '{self.config.profile_id}' profiline göre doğrulandı. "
                           "Bu makine için yeniden üretmek gerekebilir.")
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
            out["note"] = f"{self.config.profile.name} G43 H kullanmaz; takım boyu bu makinede doğrulamayı etkilemez."
        return out

    # ------------------------------------------------------------------ makine profili
    def list_machine_profiles(self) -> dict:
        return {"active": self.config.profile_id,
                "profiles": [{**p.summary(), "description": p.description, "tool_changer": p.tool_changer,
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
        if changed or work_offset is not None:
            self.config.setup = Setup(work_offset=tuple(work_offset) if work_offset else None)
        out: dict[str, Any] = {"ok": True, "profile": prof.summary(), "work_offset": list(self.config.setup.offset_for(prof))}
        v = self.revalidate_current()
        if v is not None:
            rec = self.gcodes[self.current_gcode]
            out["revalidation"] = {"gcode_id": rec["gcode_id"], "valid": v["valid"], "counts": v["counts"]}
            if rec.get("generated_for") and rec["generated_for"] != prof.id:
                out["note"] = f"Mevcut program '{rec['generated_for']}' profili için üretilmişti; bu makine için G-code'u yeniden üretin."
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


def run_tool(ws: Workspace, name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn: Callable | None = getattr(ws, name, None) if name in TOOL_NAMES else None
    if fn is None:
        return {"ok": False, "error": f"Bilinmeyen araç: {name}"}
    try:
        return fn(**(args or {}))
    except (CadError, ToolError) as exc:
        return {"ok": False, "error": str(exc)}
    except TypeError as exc:
        return {"ok": False, "error": f"Geçersiz parametre: {exc}"}
    except Exception as exc:  # beklenmeyen
        return {"ok": False, "error": f"İşlem başarısız: {type(exc).__name__}: {exc}"}


from .llm.tool_schemas import TOOL_SCHEMAS  # noqa: E402

TOOL_NAMES = {t["name"] for t in TOOL_SCHEMAS}
