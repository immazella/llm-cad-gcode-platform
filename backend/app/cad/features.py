"""Parametrik özelliklerin (delik, cep, kazıma, pah, kavis) CadQuery ile uygulanması.

Özellikler yüzey indeksine değil geometrik konuma (origin + normal) bağlı saklanır.
Böylece model yeniden oluşturulduğunda yüzey numaraları değişse bile özellikler
doğru yerde kalır ve kullanıcının seçtiği elemanın kimliği korunur.
"""
from __future__ import annotations

from typing import Any

import cadquery as cq

from .occ_utils import bbox, edge_face_map, edge_neighbor_faces, is_seam, surface_type


class CadError(Exception):
    """Kullanıcıya/LLM'ye gösterilebilecek CAD hatası."""


def _v(x: Any) -> cq.Vector:
    return cq.Vector(*x)


def make_base(base: dict) -> cq.Shape:
    t = base["type"]
    if t == "box":
        L, W, H = base["length"], base["width"], base["height"]
        if min(L, W, H) <= 0:
            raise CadError("Kutu ölçüleri pozitif olmalı.")
        # XY'de merkezli, alt yüzey Z=0'da
        return cq.Solid.makeBox(L, W, H, pnt=cq.Vector(-L / 2, -W / 2, 0))
    if t == "cylinder":
        r, h = base["radius"], base["height"]
        if r <= 0 or h <= 0:
            raise CadError("Silindir yarıçapı ve yüksekliği pozitif olmalı.")
        return cq.Solid.makeCylinder(r, h)
    raise CadError(f"Bilinmeyen taban tipi: {t}")


def hole_tool(feat: dict, size_hint: float) -> cq.Solid:
    o, n = _v(feat["origin"]), _v(feat["normal"]).normalized()
    r = feat["diameter"] / 2.0
    if r <= 0:
        raise CadError("Delik çapı pozitif olmalı.")
    depth = feat.get("depth")
    length = (depth if depth else size_hint * 2) + 1.0
    # Yüzeyin 1 mm üstünden başlar: çakışık yüzey problemi olmasın
    return cq.Solid.makeCylinder(r, length, pnt=o + n * 1.0, dir=-n)


def pocket_tool(feat: dict) -> cq.Shape:
    o, n = _v(feat["origin"]), _v(feat["normal"]).normalized()
    xdir = _v(feat.get("xdir", [1, 0, 0]))
    L, W, D = feat["length"], feat["width"], feat["depth"]
    rc = feat.get("corner_radius", 0) or 0
    if min(L, W, D) <= 0:
        raise CadError("Cep ölçüleri pozitif olmalı.")
    if rc * 2 >= min(L, W):
        raise CadError("Köşe yarıçapı cebin kısa kenarının yarısından küçük olmalı.")
    plane = cq.Plane(origin=o + n * 1.0, xDir=xdir, normal=n)
    sk = cq.Workplane(plane).sketch().rect(L, W)
    if rc > 0:
        sk = sk.vertices().fillet(rc)
    return sk.finalize().extrude(-(D + 1.0)).val()


def select_edges(shape: cq.Shape, which: str) -> list[cq.Edge]:
    """Kavis/pah için kenar seçimi: vertical | top | bottom | all."""
    emap = edge_face_map(shape)
    bb = bbox(shape)
    res: list[cq.Edge] = []
    for e in shape.Edges():
        if is_seam(e, emap):
            continue
        faces = edge_neighbor_faces(e, emap)
        if which == "all":
            res.append(e)
        elif which == "vertical":
            if e.geomType() != "LINE":
                continue
            d = (e.endPoint() - e.startPoint()).normalized()
            if abs(abs(d.z) - 1) < 1e-6 and all(surface_type(f) == "plane" for f in faces):
                # sadece dış köşeler (kutunun dış dikey kenarları)
                c = e.Center()
                if abs(abs(c.x) - max(abs(bb.xmin), abs(bb.xmax))) < 1e-3 or abs(abs(c.y) - max(abs(bb.ymin), abs(bb.ymax))) < 1e-3:
                    res.append(e)
        elif which in ("top", "bottom"):
            z = bb.zmax if which == "top" else bb.zmin
            if abs(e.Center().z - z) < 1e-4 and abs(e.startPoint().z - z) < 1e-4:
                res.append(e)
        else:
            raise CadError("edges parametresi vertical | top | bottom | all olmalı.")
    return res


def apply_features(shape: cq.Shape, features: list[dict]) -> tuple[cq.Shape, dict[str, cq.Shape]]:
    """Özellikleri sırayla uygular. Ayrıca her kesme özelliğinin 'takım katısını'
    döndürür; bunlar sonradan yüzey→özellik eşleştirmesinde kullanılır."""
    tools: dict[str, cq.Shape] = {}
    size_hint = bbox(shape).DiagonalLength
    engraved: dict[float, Any] = {}  # yüzey Z'si → o yüzeyde daha önce kazınmış bölge
    for f in features:
        t = f["type"]
        try:
            if t == "hole":
                tool = hole_tool(f, size_hint)
                shape = shape.cut(tool)
                tools[f["id"]] = tool
            elif t == "pocket":
                tool = pocket_tool(f)
                shape = shape.cut(tool)
                tools[f["id"]] = tool
            elif t == "engrave":
                from .engrave import groove_tool  # döngüsel içe aktarmayı önle

                z = round(f["origin"][2], 4)
                tool, region = groove_tool(shape, f, engraved.get(z))
                engraved[z] = region if z not in engraved else engraved[z].union(region)
                shape = shape.cut(tool)
                tools[f["id"]] = tool
            elif t in ("fillet", "chamfer"):
                edges = select_edges(shape, f.get("edges", "vertical"))
                if not edges:
                    raise CadError("Seçilen kenar grubu boş.")
                if t == "fillet":
                    shape = shape.fillet(f["radius"], edges)
                else:
                    shape = shape.chamfer(f["size"], None, edges)
            else:
                raise CadError(f"Bilinmeyen özellik tipi: {t}")
        except CadError:
            raise
        except Exception as exc:  # OCC hataları
            raise CadError(f"'{f['id']}' özelliği uygulanamadı: {exc}") from exc
        if not shape.isValid():
            raise CadError(f"'{f['id']}' özelliği geçersiz bir geometri üretti.")
    return shape, tools
