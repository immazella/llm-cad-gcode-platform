"""CAD modeli ve çalışma alanı.

Koordinat kuralı (tüm sistem için):
- Birim: mm
- Parça XY düzleminde merkezli (bounding box merkezi X=0, Y=0)
- Alt yüzey Z=0, üst yüzey Z=yükseklik
- G-code iş sıfırı: parça üst yüzeyinin merkezi (X0 Y0 Z0)
"""
from __future__ import annotations

import copy
import itertools
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import cadquery as cq
from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

from .features import CadError, apply_features, make_base
from .occ_utils import bbox, face_details, tessellate, vec
from .recognition import detect_holes, detect_pockets, map_faces_to_features, outer_contour_wire

PUBLIC_HOLE_KEYS = ("id", "feature_id", "diameter", "depth", "through", "top_point", "axis_dir", "face_ids")
PUBLIC_POCKET_KEYS = ("id", "feature_id", "floor_face_id", "floor_z", "top_z", "depth", "size_xy", "center_xy")
MAX_HISTORY = 30


def _r(x: float) -> float:
    return round(float(x), 4)


def public_feature(f: dict) -> dict:
    """Arayüz/LLM'ye giden özellik özeti: büyük ham veriler (SVG metni, yol noktaları) çıkarılır."""
    out = copy.deepcopy({k: v for k, v in f.items() if k not in ("svg", "local_paths")})
    if f["type"] == "engrave" and f["source"] == "path":
        out["path_count"] = len(f["local_paths"])
        out["point_count"] = sum(len(s) for s in f["local_paths"])
    return out


class CadModel:
    def __init__(self, name: str, base: dict, base_shape: cq.Shape | None = None):
        self.id = uuid.uuid4().hex[:8]
        self.name = name
        self.kind = "imported" if base_shape is not None else "parametric"
        self.base = base
        self.base_shape = base_shape
        self.features: list[dict] = []
        self.counters: dict[str, itertools.count] = {}
        self.history: list[tuple] = []
        self.version = 0
        self.log: list[str] = []
        self.regions: dict[str, dict] = {}  # ⌘/Ctrl ile yüzeyler üzerinde seçilen bölgeler (model geometrisinin parçası değil)
        self.region_seq = 0
        self.rebuild()

    # ------------------------------------------------------------------ çekirdek
    def _next_id(self, kind: str) -> str:
        used = {f["id"] for f in self.features}
        i = 1
        while f"{kind}_{i}" in used:
            i += 1
        return f"{kind}_{i}"

    def rebuild(self) -> None:
        base = self.base_shape if self.kind == "imported" else make_base(self.base)
        shape, tools = apply_features(base, self.features)
        faces = shape.Faces()
        face_feature = map_faces_to_features(faces, tools)
        self.shape = shape
        self.faces = faces
        self.face_feature = face_feature
        # Kazıma oluklarının yüzeyleri delik/cep olarak tanınmamalı (yoksa CAM onları ayrıca boşaltır)
        engrave_ids = {f["id"] for f in self.features if f["type"] == "engrave"}
        engrave_faces = {i for i, fid in face_feature.items() if fid in engrave_ids}
        self.holes = [h for h in detect_holes(shape, faces, face_feature) if not set(h["_face_idx"]) & engrave_faces]
        self.pockets = [p for p in detect_pockets(shape, faces, face_feature, {i for h in self.holes for i in h['_face_idx']})
                        if p["_face_idx"] not in engrave_faces]
        self.face_hole = {i: h["id"] for h in self.holes for i in h["_face_idx"]}
        self.face_pocket = {p["_face_idx"]: p["id"] for p in self.pockets}
        self.version += 1
        self._mesh_cache: dict | None = None
        self._region_cache: dict[str, dict] = {}  # bölge kimliği → bu sürüm için hesaplanmış bölge

    def _snapshot(self) -> tuple:
        return (copy.deepcopy(self.base), copy.deepcopy(self.features), self.base_shape, self.kind)

    def _restore(self, snap: tuple) -> None:
        self.base, self.features, self.base_shape, self.kind = copy.deepcopy(snap[0]), copy.deepcopy(snap[1]), snap[2], snap[3]

    @contextmanager
    def edit(self, description: str, hole_check: str | None = None):
        """Atomik düzenleme: hata olursa model eski hâline döner.
        hole_check: verilen kimlikli deliğin sonuçta tam bir delik olarak tanınması zorunlu."""
        snap = self._snapshot()
        try:
            yield
            self.rebuild()
            if hole_check and not any(h["feature_id"] == hole_check for h in self.holes):
                raise CadError("Delik parçanın sınırlarını aşıyor veya başka bir özellikle kesişiyor; tam bir delik oluşmadı. Konumu/çapı kontrol edin.")
        except Exception:
            self._restore(snap)
            self.rebuild()
            raise
        self.history.append(snap)
        del self.history[:-MAX_HISTORY]
        self.log.append(description)

    def undo(self) -> str:
        if not self.history:
            raise CadError("Geri alınacak işlem yok.")
        self._restore(self.history.pop())
        self.rebuild()
        return self.log.pop() if self.log else "son işlem"

    # ------------------------------------------------------------------ yardımcılar
    @property
    def bbox(self):
        return bbox(self.shape)

    def face_by_id(self, face_id: str) -> tuple[int, cq.Face]:
        # Bölge kimliği (r1 …) verilirse bölgenin o anki sürümde üzerinde durduğu yüzey kullanılır;
        # böylece aynı mesajda model birkaç kez değişse de bölgeye yapılan işlemler doğru yüzeye gider.
        if str(face_id).startswith("r"):
            fid = self._region(str(face_id))["face_id"]
            return int(fid[1:]), self.faces[int(fid[1:])]
        try:
            idx = int(str(face_id).lstrip("f"))
            return idx, self.faces[idx]
        except (ValueError, IndexError):
            raise CadError(f"'{face_id}' kimlikli yüzey bulunamadı (model sürümü değişmiş olabilir).")

    def hole_by_id(self, hole_id: str) -> dict:
        for h in self.holes:
            if h["id"] == hole_id or h["feature_id"] == hole_id:
                return h
        ids = ", ".join(h["id"] for h in self.holes) or "yok"
        raise CadError(f"'{hole_id}' kimlikli delik yok. Mevcut delikler: {ids}")

    def feature_by_id(self, fid: str) -> dict:
        for f in self.features:
            if f["id"] == fid:
                return f
        raise CadError(f"'{fid}' kimlikli parametrik özellik yok.")

    def _placement(self, x: float, y: float, face_id: str | None) -> tuple[list, list, list]:
        """Delik/cep için yerleşim düzlemi: (origin, normal, xdir)."""
        if face_id is None:
            bb = self.bbox
            origin = cq.Vector(x, y, bb.zmax)
            normal, xdir = cq.Vector(0, 0, 1), cq.Vector(1, 0, 0)
        else:
            _, face = self.face_by_id(face_id)
            info = face_details(face)
            if info["surface"] != "plane":
                raise CadError("Bu işlem için düzlemsel bir yüzey seçilmeli (seçilen yüzey: " + info["surface"] + ").")
            normal = cq.Vector(*info["normal"])
            if abs(abs(normal.z) - 1) < 1e-6:
                # yatay yüzeyde x,y parça koordinatıdır
                origin = cq.Vector(x, y, face.Center().z)
                xdir = cq.Vector(1, 0, 0)
            else:
                # yan yüzeyde x: yatay yüzey ekseni, y: dikey yüzey ekseni (yüzey merkezine göre)
                u = cq.Vector(0, 0, 1).cross(normal).normalized()
                v = normal.cross(u).normalized()
                origin = face.Center() + u * x + v * y
                xdir = u
            if face.distance(cq.Vertex.makeVertex(*origin.toTuple())) > 1e-3:
                raise CadError("Verilen konum seçili yüzeyin sınırları dışında kalıyor.")
        if face_id is None and self.shape.distance(cq.Vertex.makeVertex(*origin.toTuple())) > 1e-3:
            raise CadError(f"({x}, {y}) noktası parçanın üst yüzeyinin dışında kalıyor.")
        return [_r(origin.x), _r(origin.y), _r(origin.z)], vec(normal), vec(xdir)

    # ------------------------------------------------------------------ işlemler
    def add_hole(self, x: float, y: float, diameter: float, depth: float | None = None, face_id: str | None = None) -> str:
        origin, normal, _ = self._placement(x, y, face_id)
        fid = self._next_id("hole")
        with self.edit(f"{fid} eklendi (Ø{diameter})", hole_check=fid):
            self.features.append(
                {"id": fid, "type": "hole", "origin": origin, "normal": normal, "diameter": float(diameter), "depth": float(depth) if depth else None}
            )
        return fid

    def add_pocket(self, x: float, y: float, length: float, width: float, depth: float, corner_radius: float = 0, face_id: str | None = None) -> str:
        origin, normal, xdir = self._placement(x, y, face_id)
        fid = self._next_id("pocket")
        with self.edit(f"{fid} eklendi ({length}x{width}x{depth})"):
            self.features.append(
                {"id": fid, "type": "pocket", "origin": origin, "normal": normal, "xdir": xdir,
                 "length": float(length), "width": float(width), "depth": float(depth), "corner_radius": float(corner_radius or 0)}
            )
        return fid

    def add_engraving(self, source: str, x: float, y: float, depth: float, width: float, rotation: float = 0.0,
                      face_id: str | None = None, **params: Any) -> str:
        """Kazıma özelliği ekler. source: text (text, height, font) | path (paths, absolute XY) | svg (svg, size)."""
        from .engrave import FONTS

        if source == "path":
            paths = params.pop("paths")
            pts = [p for s in paths for p in s]
            if not pts or any(len(s) < 2 for s in paths):
                raise CadError("Her yol en az iki nokta içermeli: [[x, y], [x, y], ...].")
            xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
            x, y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            local = []
            for s, closed in zip(paths, params.pop("closed")):
                q = [[_r(float(p[0]) - x), _r(float(p[1]) - y)] for p in s]
                if closed and q[0] != q[-1]:
                    q.append(list(q[0]))
                local.append(q)
            params["local_paths"] = local
        if source == "text" and params.get("font", "tek_cizgi") not in FONTS:
            raise CadError(f"font: {' | '.join(FONTS)}")
        origin, normal, _ = self._placement(x, y, face_id)
        if normal != [0, 0, 1]:
            raise CadError("Kazıma yalnızca yukarı bakan yatay yüzeylere yapılabilir (üst yüzey veya cep tabanı).")
        fid = self._next_id("engrave")
        label = {"text": f"yazı '{params.get('text', '')}'", "path": f"{len(params.get('local_paths', []))} yol", "svg": "SVG logo"}[source]
        with self.edit(f"{fid} eklendi (kazıma: {label}, derinlik {depth})"):
            self.features.append({"id": fid, "type": "engrave", "source": source, "origin": origin, "normal": normal,
                                  "depth": float(depth), "width": float(width), "rotation": float(rotation or 0), **params})
        return fid

    def add_edge_feature(self, kind: str, size: float, edges: str) -> str:
        fid = self._next_id(kind)
        key = "radius" if kind == "fillet" else "size"
        with self.edit(f"{fid} eklendi ({edges}, {size} mm)"):
            self.features.append({"id": fid, "type": kind, key: float(size), "edges": edges})
        return fid

    def modify_base(self, **dims: float | None) -> None:
        if self.kind != "parametric":
            raise CadError("Yüklenen STEP modelinin ana gövde ölçüleri değiştirilemez (yalnızca özellik eklenebilir/düzenlenebilir).")
        dims = {k: float(v) for k, v in dims.items() if v is not None}
        allowed = {"box": {"length", "width", "height"}, "cylinder": {"radius", "height"}}[self.base["type"]]
        bad = set(dims) - allowed
        if bad:
            raise CadError(f"{self.base['type']} için geçersiz ölçü(ler): {', '.join(bad)}")
        old = dict(self.base)
        with self.edit("ana gövde ölçüleri değişti: " + ", ".join(f"{k}={v}" for k, v in dims.items())):
            self.base.update(dims)
            # Yüzeylere bağlı özellikleri yeni yüzey konumlarına taşı
            for f in self.features:
                if "origin" not in f:
                    continue
                o, n = f["origin"], f["normal"]
                if n == [0, 0, 1] and abs(o[2] - old["height"]) < 1e-6:
                    o[2] = self.base["height"]
                if self.base["type"] == "box":
                    if abs(abs(n[0]) - 1) < 1e-6 and abs(abs(o[0]) - old["length"] / 2) < 1e-6:
                        o[0] = n[0] * self.base["length"] / 2
                    if abs(abs(n[1]) - 1) < 1e-6 and abs(abs(o[1]) - old["width"] / 2) < 1e-6:
                        o[1] = n[1] * self.base["width"] / 2

    def modify_hole(self, hole_id: str, diameter: float | None = None, x: float | None = None, y: float | None = None,
                    depth: float | None = None, through: bool | None = None) -> str:
        hole = self.hole_by_id(hole_id)
        if hole["feature_id"]:
            feat = self.feature_by_id(hole["feature_id"])
            with self.edit(f"{feat['id']} düzenlendi", hole_check=feat["id"]):
                if diameter is not None:
                    feat["diameter"] = float(diameter)
                if feat["normal"] == [0, 0, 1]:
                    if x is not None:
                        feat["origin"][0] = float(x)
                    if y is not None:
                        feat["origin"][1] = float(y)
                elif x is not None or y is not None:
                    raise CadError("Yan yüzeydeki deliğin konumu bu sürümde değiştirilemez; silip yeniden ekleyin.")
                if through:
                    feat["depth"] = None
                elif depth is not None:
                    feat["depth"] = float(depth)
            return feat["id"]
        # Tanınan (parametrik olmayan) delik: defeaturing ile kaldır, yenisini kes
        top = cq.Vector(*hole["top_point"])
        axis = cq.Vector(*hole["axis_dir"])
        if (x is not None or y is not None) and abs(abs(axis.z) - 1) > 1e-6:
            raise CadError("Yalnızca dikey deliklerin konumu değiştirilebilir.")
        new_top = cq.Vector(x if x is not None else top.x, y if y is not None else top.y, top.z)
        new_depth = None if (through or (through is None and depth is None and hole["through"])) else float(depth or hole["depth"])
        new_d = float(diameter or hole["diameter"])
        fid = self._next_id("hole")
        with self.edit(f"tanınan delik {hole_id} yeniden oluşturuldu → {fid}", hole_check=fid):
            self._bake_without_faces(self._hole_removal_faces(hole))
            self.features.append({"id": fid, "type": "hole", "origin": vec(new_top), "normal": vec(axis), "diameter": new_d, "depth": new_depth})
        return fid

    def _hole_removal_faces(self, hole: dict) -> list[cq.Face]:
        """Deliği oluşturan yüzeyler + delik içinde kalan taban/uç yüzeyleri."""
        from .occ_utils import edge_face_map, edge_neighbor_faces
        idxs = set(hole["_face_idx"])
        sel = [self.faces[i] for i in idxs]
        emap = edge_face_map(self.shape)
        axis_p = cq.Vector(*hole["top_point"])
        axis_d = cq.Vector(*hole["axis_dir"])
        r = hole["radius"]
        extra: list[cq.Face] = []
        for f in sel:
            for e in f.Edges():
                for nf in edge_neighbor_faces(e, emap):
                    if any(nf.isSame(s) for s in sel + extra):
                        continue
                    pts = [cq.Vector(*v.toTuple()) for v in nf.Vertices()] or [nf.Center()]
                    ok = True
                    for p in pts:
                        rel = p - axis_p
                        radial = rel - axis_d * rel.dot(axis_d)
                        if radial.Length > r + 1e-3:
                            ok = False
                            break
                    if ok:
                        extra.append(nf)
        return sel + extra

    def _bake_without_faces(self, faces: list[cq.Face]) -> None:
        df = BRepAlgoAPI_Defeaturing()
        df.SetShape(self.shape.wrapped)
        for f in faces:
            df.AddFaceToRemove(f.wrapped)
        df.SetRunParallel(False)
        df.Build()
        if not df.IsDone():
            raise CadError("Delik geometriden kaldırılamadı (defeaturing başarısız).")
        new_shape = cq.Shape.cast(df.Shape())
        if not new_shape.isValid():
            raise CadError("Delik kaldırma geçersiz geometri üretti.")
        # Mevcut özellikler gövdeye 'pişirilir'; model bundan sonra bu gövde üzerinde çalışır
        self.base_shape = new_shape
        self.kind = "imported"
        self.base = {"type": "baked", "from": self.base}
        self.features = []

    def modify_feature(self, feature_id: str, **params: Any) -> None:
        feat = self.feature_by_id(feature_id)
        editable = {
            "hole": {"diameter", "depth", "x", "y"},
            "pocket": {"length", "width", "depth", "corner_radius", "x", "y"},
            "fillet": {"radius", "edges"},
            "chamfer": {"size", "edges"},
            "engrave": {"x", "y", "depth", "width", "rotation", "text", "height", "font", "size"},
        }[feat["type"]]
        if feat["type"] == "engrave":
            only = {"text": {"text", "height", "font"}, "svg": {"size"}, "path": set()}[feat["source"]]
            wrong = (set(params) & {"text", "height", "font", "size"}) - only
            if wrong:
                raise CadError(f"Bu kazıma ({feat['source']}) için geçersiz parametre(ler): {', '.join(wrong)}")
        params = {k: v for k, v in params.items() if v is not None}
        bad = set(params) - editable
        if bad:
            raise CadError(f"{feat['type']} için düzenlenemeyen parametre(ler): {', '.join(bad)}")
        with self.edit(f"{feature_id} düzenlendi: " + ", ".join(f"{k}={v}" for k, v in params.items())):
            for k, v in params.items():
                if k in ("x", "y"):
                    if feat["normal"] != [0, 0, 1]:
                        raise CadError("Yalnızca üst yüzeydeki özelliklerin konumu değiştirilebilir.")
                    feat["origin"][0 if k == "x" else 1] = float(v)
                else:
                    feat[k] = v if k in ("edges", "text", "font") else float(v)

    def delete_feature(self, feature_id: str) -> None:
        if any(f["id"] == feature_id for f in self.features):
            with self.edit(f"{feature_id} silindi"):
                self.features = [f for f in self.features if f["id"] != feature_id]
            return
        hole = self.hole_by_id(feature_id)  # tanınan delik
        with self.edit(f"tanınan delik {feature_id} kaldırıldı"):
            self._bake_without_faces(self._hole_removal_faces(hole))

    # ------------------------------------------------------------------ sorgular
    def face_info(self, face_id: str) -> dict[str, Any]:
        idx, face = self.face_by_id(face_id)
        info = face_details(face)
        info["face_id"] = f"f{idx}"
        info["feature_id"] = self.face_feature.get(idx)
        info["hole_id"] = self.face_hole.get(idx)
        info["pocket_id"] = self.face_pocket.get(idx)
        if info["hole_id"]:
            h = self.hole_by_id(info["hole_id"])
            info["hole"] = {k: h[k] for k in PUBLIC_HOLE_KEYS}
            info["role"] = f"{h['id']} deliğinin iç yüzeyi (Ø{h['diameter']} mm)"
        elif info["pocket_id"]:
            info["role"] = f"{info['pocket_id']} cebinin tabanı"
        elif info["feature_id"]:
            feat = self.feature_by_id(info["feature_id"])
            info["role"] = f"{feat['id']} ({feat['type']}) özelliğine ait yüzey"
        else:
            bb = self.bbox
            n = info.get("normal")
            if n == [0, 0, 1] and abs(info["center"][2] - bb.zmax) < 1e-3:
                info["role"] = "parçanın üst yüzeyi"
            elif n == [0, 0, -1] and abs(info["center"][2] - bb.zmin) < 1e-3:
                info["role"] = "parçanın alt yüzeyi"
            elif info["surface"] == "plane":
                info["role"] = "yan / iç düzlemsel yüzey"
            else:
                info["role"] = f"{info['surface']} yüzey"
        if info["feature_id"]:
            info["feature"] = public_feature(self.feature_by_id(info["feature_id"]))
        return info

    # ------------------------------------------------------------------ bölgeler
    def add_region(self, req: dict[str, Any]) -> dict[str, Any]:
        """Bir yüzeyin üzerinde çizilen şekli bölge (r1, r2 …) olarak kaydeder. Modeli değiştirmez."""
        from .region import compute_region, new_region_def, public

        defn = new_region_def(self, req)
        defn["id"] = f"r{self.region_seq + 1}"
        info = compute_region(self, defn)
        # Çapa: bölgenin içinden sağlam bir nokta (ilk tık sınırda kalabilir; kenara yapılan kazıma onu yüzeyden düşürmesin)
        from .occ_utils import point_on_face, vec
        defn["anchor_point"] = vec(point_on_face(max(info["_pieces"], key=lambda f: f.Area())))
        self.region_seq += 1
        self.regions[defn["id"]] = defn
        self._region_cache[defn["id"]] = info
        return public(info)

    def _region(self, region_id: str) -> dict[str, Any]:
        from .region import compute_region

        if region_id not in self.regions:
            ids = ", ".join(self.regions) or "yok"
            raise CadError(f"'{region_id}' kimlikli bölge yok. Mevcut bölgeler: {ids}")
        info = self._region_cache.get(region_id)
        if info is None:
            try:
                info = compute_region(self, self.regions[region_id])
            except CadError:
                del self.regions[region_id]  # model değişti, bölge artık yerleştirilemiyor
                raise CadError(f"{region_id} bölgesi model değiştiği için geçersiz oldu ve kaldırıldı.")
            self._region_cache[region_id] = info
        return info

    def region_info(self, region_id: str, mesh: bool = True) -> dict[str, Any]:
        from .region import public

        return public(self._region(region_id), mesh)

    def regions_info(self, mesh: bool = True) -> tuple[list[dict[str, Any]], list[str]]:
        """Geçerli bölgeler ve (model değiştiği için) düşürülen bölge kimlikleri."""
        out, dropped = [], []
        for rid in list(self.regions):
            try:
                out.append(self.region_info(rid, mesh))
            except CadError:
                dropped.append(rid)
        return out, dropped

    def delete_region(self, region_id: str) -> None:
        self.regions.pop(region_id, None)
        self._region_cache.pop(region_id, None)

    def summary(self) -> dict[str, Any]:
        bb = self.bbox
        return {
            "model_id": self.id,
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "base": self.base,
            "bbox": {"min": [_r(bb.xmin), _r(bb.ymin), _r(bb.zmin)], "max": [_r(bb.xmax), _r(bb.ymax), _r(bb.zmax)],
                     "size": [_r(bb.xlen), _r(bb.ylen), _r(bb.zlen)]},
            "volume_mm3": _r(self.shape.Volume()),
            "face_count": len(self.faces),
            "features": [public_feature(f) for f in self.features],
            "holes": [{k: h[k] for k in PUBLIC_HOLE_KEYS} for h in self.holes],
            "pockets": [{k: p[k] for k in PUBLIC_POCKET_KEYS} for p in self.pockets],
            "can_undo": bool(self.history),
            "log": self.log[-10:],
        }

    def mesh(self) -> dict[str, Any]:
        if self._mesh_cache is None:
            faces, edges = tessellate(self.shape, self.faces)
            from .occ_utils import surface_type
            for fm in faces:
                i = int(fm["id"][1:])
                fm["surface"] = surface_type(self.faces[i])
                fm["feature_id"] = self.face_feature.get(i)
                fm["hole_id"] = self.face_hole.get(i)
                fm["pocket_id"] = self.face_pocket.get(i)
            self._mesh_cache = {"model_id": self.id, "version": self.version, "faces": faces, "edges": edges,
                                "summary": self.summary()}
        return self._mesh_cache

    def contour_wire(self) -> cq.Wire | None:
        return outer_contour_wire(self.shape, self.faces)

    def export_step(self, path: Path) -> Path:
        self.shape.exportStep(str(path))
        return path


def load_step(path: Path, name: str) -> CadModel:
    try:
        wp = cq.importers.importStep(str(path))
    except Exception as exc:
        raise CadError(f"STEP dosyası okunamadı: {exc}") from exc
    vals = [v for v in wp.vals() if isinstance(v, cq.Shape)]
    if not vals:
        raise CadError("STEP dosyasında katı geometri bulunamadı.")
    shape = vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    if not shape.Solids():
        raise CadError("STEP dosyasında katı (solid) yok; yalnızca yüzey/kabuk modelleri desteklenmiyor.")
    bb = bbox(shape)
    # Koordinat kuralına getir: XY merkezli, alt yüzey Z=0
    shape = shape.translate(cq.Vector(-(bb.xmin + bb.xmax) / 2, -(bb.ymin + bb.ymax) / 2, -bb.zmin))
    return CadModel(name, {"type": "step", "file": path.name}, base_shape=shape)
