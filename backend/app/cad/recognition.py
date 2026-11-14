"""Geometrik özellik tanıma.

Hem parametrik modellerde hem de dışarıdan yüklenen STEP dosyalarında çalışır:
- Delikler: içbükey, tam (360°) silindirik yüzey grupları
- Cepler: üst seviyenin altındaki, kenarları parçanın dış sınırına değmeyen +Z tabanlı düzlemsel yüzeyler
- Dış kontur: alt yüzeyin dış teli
"""
from __future__ import annotations

import math
from typing import Any

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools

from .occ_utils import bbox, edge_face_map, edge_neighbor_faces, point_on_face, surface_type, vec

TOL = 1e-3


def map_faces_to_features(faces: list[cq.Face], tools: dict[str, cq.Shape]) -> dict[int, str]:
    """Yüzey indeksinden özellik kimliğine eşleme.

    Kesme işleminden doğan her yüzey, onu üreten takım katısının sınırı üzerinde
    yer alır. Yüzey üzerindeki bir noktanın takım katısının kabuğuna uzaklığı ~0 ise
    yüzey o özelliğe aittir. Sonraki özellik öncelikli (son kesen kazanır).
    """
    from .engrave import face_in_groove

    result: dict[int, str] = {}
    if not tools:
        return result
    shells = {fid: (t, None if hasattr(t, "_engrave") else cq.Compound.makeCompound(t.Faces())) for fid, t in tools.items()}
    for i, face in enumerate(faces):
        pv = point_on_face(face)
        p = cq.Vertex.makeVertex(*pv.toTuple())
        for fid, (tool, sh) in shells.items():
            try:
                if sh is None:  # kazıma oluğu: hızlı 2B test
                    if face_in_groove(tool, pv):
                        result[i] = fid
                elif sh.distance(p) < TOL:
                    result[i] = fid
            except Exception:
                pass
    return result


def _axis_key(p: cq.Vector, d: cq.Vector) -> tuple[cq.Vector, cq.Vector]:
    d = d.normalized()
    if d.z < -1e-9 or (abs(d.z) < 1e-9 and (d.y < -1e-9 or (abs(d.y) < 1e-9 and d.x < 0))):
        d = -d
    # eksen üzerindeki, orijine en yakın nokta
    p0 = p - d * p.dot(d)
    return p0, d


def detect_holes(shape: cq.Shape, faces: list[cq.Face], face_feature: dict[int, str]) -> list[dict[str, Any]]:
    groups: list[dict] = []
    for i, f in enumerate(faces):
        if surface_type(f) != "cylinder":
            continue
        cyl = BRepAdaptor_Surface(f.wrapped).Cylinder()
        ax = cyl.Axis()
        r = cyl.Radius()
        ap = cq.Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
        ad = cq.Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z())
        # içbükey mi? (yüzey normali eksene doğru bakıyorsa delik)
        p = point_on_face(f)
        n = f.normalAt(p)
        rel = p - ap
        radial = rel - ad * rel.dot(ad)
        if n.dot(-radial) <= 0:
            continue  # dışbükey: mil / göbek
        p0, d = _axis_key(ap, ad)
        umin, umax, _, _ = BRepTools.UVBounds_s(f.wrapped)
        span = umax - umin
        fb = bbox(f)
        corners = [cq.Vector(x, y, z) for x in (fb.xmin, fb.xmax) for y in (fb.ymin, fb.ymax) for z in (fb.zmin, fb.zmax)]
        # eksen boyunca uzanım: köşe vertex'leri (silindirde dikiş uçları) yeterli
        ts = [(cq.Vector(*v.toTuple()) - p0).dot(d) for v in f.Vertices()] or [(c - p0).dot(d) for c in corners]
        for g in groups:
            if abs(g["r"] - r) < TOL and abs(abs(g["d"].dot(d)) - 1) < 1e-6 and (g["p0"] - p0).Length < TOL:
                g["faces"].append(i)
                g["span"] += span
                g["ts"].extend(ts)
                break
        else:
            groups.append({"r": r, "d": d, "p0": p0, "faces": [i], "span": span, "ts": ts})

    holes: list[dict[str, Any]] = []
    for g in groups:
        if g["span"] < 2 * math.pi - 0.01 or not g["ts"]:
            continue  # kısmi silindir (köşe radyüsü vb.) delik değildir
        tmin, tmax = min(g["ts"]), max(g["ts"])
        d, p0 = g["d"], g["p0"]
        eps = 0.05
        inside_hi = shape.isInside(p0 + d * (tmax + eps), 1e-4)
        inside_lo = shape.isInside(p0 + d * (tmin - eps), 1e-4)
        through = not inside_hi and not inside_lo
        if not inside_hi:  # +d ucu açık
            out_dir, top_t, bottom_t = d, tmax, tmin
        else:
            out_dir, top_t, bottom_t = -d, tmin, tmax
        feat = next((face_feature[i] for i in g["faces"] if i in face_feature), None)
        holes.append(
            {
                "feature_id": feat,
                "diameter": round(2 * g["r"], 4),
                "radius": round(g["r"], 4),
                "depth": round(tmax - tmin, 4),
                "through": through,
                "top_point": vec(p0 + d * top_t),
                "bottom_point": vec(p0 + d * bottom_t),
                "axis_dir": vec(out_dir),
                "face_ids": [f"f{i}" for i in g["faces"]],
                "_face_idx": g["faces"],
            }
        )
    # kararlı sıralama: konuma göre
    holes.sort(key=lambda h: (round(h["top_point"][0], 2), round(h["top_point"][1], 2), round(h["top_point"][2], 2)))
    n = 0
    for h in holes:
        if h["feature_id"]:
            h["id"] = h["feature_id"]
        else:
            n += 1
            h["id"] = f"h{n}"
    return holes


def detect_pockets(shape: cq.Shape, faces: list[cq.Face], face_feature: dict[int, str], hole_face_idx: set[int] | None = None) -> list[dict[str, Any]]:
    bb = bbox(shape)
    emap = edge_face_map(shape)
    pockets: list[dict[str, Any]] = []
    for i, f in enumerate(faces):
        if surface_type(f) != "plane":
            continue
        n = f.normalAt(point_on_face(f))
        if n.z < 1 - 1e-6:
            continue
        z = f.Center().z
        if z > bb.zmax - TOL or z < bb.zmin + TOL:
            continue
        ow = f.outerWire()
        wb = bbox(ow)
        if wb.xmin <= bb.xmin + TOL or wb.xmax >= bb.xmax - TOL or wb.ymin <= bb.ymin + TOL or wb.ymax >= bb.ymax - TOL:
            continue  # açık basamak / omuz: cep değil
        # duvarların ulaştığı en yüksek Z = cebin başladığı seviye
        top = z
        neighbors: list[cq.Face] = []
        for e in ow.Edges():
            for nf in edge_neighbor_faces(e, emap):
                if not nf.isSame(f):
                    neighbors.append(nf)
                    top = max(top, bbox(nf).zmax)
        if top - z < TOL:
            continue
        hole_faces = [faces[j] for j in (hole_face_idx or ())]
        if neighbors and all(any(nf.isSame(hf) for hf in hole_faces) for nf in neighbors):
            continue  # kör delik tabanı: cep değil
        pockets.append(
            {
                "feature_id": face_feature.get(i),
                "floor_face_id": f"f{i}",
                "floor_z": round(z, 4),
                "top_z": round(top, 4),
                "depth": round(top - z, 4),
                "size_xy": [round(wb.xlen, 3), round(wb.ylen, 3)],
                "center_xy": [round((wb.xmin + wb.xmax) / 2, 3), round((wb.ymin + wb.ymax) / 2, 3)],
                "_face_idx": i,
            }
        )
    pockets.sort(key=lambda p: (p["center_xy"][0], p["center_xy"][1], -p["floor_z"]))
    k = 0
    for p in pockets:
        if p["feature_id"]:
            p["id"] = p["feature_id"]
        else:
            k += 1
            p["id"] = f"p{k}"
    return pockets


def outer_contour_wire(shape: cq.Shape, faces: list[cq.Face]) -> cq.Wire | None:
    """Alt yüzeyin (normal -Z, Z=zmin) dış teli: kontur frezeleme sınırı."""
    bb = bbox(shape)
    best, best_area = None, 0.0
    for f in faces:
        if surface_type(f) != "plane":
            continue
        if abs(f.Center().z - bb.zmin) > TOL:
            continue
        if f.normalAt(point_on_face(f)).z > -1 + 1e-6:
            continue
        if f.Area() > best_area:
            best, best_area = f, f.Area()
    return best.outerWire() if best is not None else None
