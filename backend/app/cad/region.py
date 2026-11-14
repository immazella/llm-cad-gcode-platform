"""Yüzey üzerinde seçilen bölgeler (⌘/Ctrl + çizim): r1, r2 …

Bölge modeli DEĞİŞTİRMEZ. Bir yüzeyin, kullanıcının çizdiği şekille kesişen parçasıdır ve
arayüzde/LLM bağlamında gerçek bir yüzey gibi davranır (kimlik, alan, normal, ölçüler).

- Düzlemsel yüzey ("plane" modu): şekil yüzeyin kendi düzleminde tanımlanır (gerçek mm ölçülü
  dikdörtgen / daire / çokgen), normal yönünde ince bir prizmaya çevrilip yüzeyle kesiştirilir.
- Eğri yüzey ("view" modu: silindir, koni, küre, B-spline …): ekrandaki şekil bakış yönünde
  yüzeye izdüşürülür. Kameradan çıkan ışınlar bir koni/prizma katısı oluşturur, yüzeyle
  kesiştirilir ve kameradan görünmeyen (arka) kısım atılır.

Her iki durumda kesişim BRepAlgoAPI_Common ile yapılır; sonuç yüzeyin eğriliğini ve sınırlarını
(kenarlar, delikler) birebir izleyen B-rep parçasıdır.

Bölge yüzey numarasına değil, geometrik tanımına (çapa noktası + şekil) bağlı saklanır. Model
yeniden kurulduğunda çapa noktasını içeren yüzey bulunup bölge yeniden hesaplanır.
"""
from __future__ import annotations

import math
from typing import Any

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_CompCurve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt, gp_Vec
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

from .features import CadError
from .occ_utils import bbox, face_details, mesh_tolerance, point_on_face, surface_type, vec

SHAPES = {"rect": "dikdörtgen", "circle": "daire", "lasso": "serbest çizim", "polygon": "çokgen"}
MAX_POINTS = 400
ANCHOR_TOL = 1e-3


def _r(x: float, d: int = 4) -> float:
    return round(float(x), d) + 0.0


def _V(p: Any) -> cq.Vector:
    return cq.Vector(float(p[0]), float(p[1]), float(p[2]))


def plane_frame(normal: cq.Vector) -> tuple[cq.Vector, cq.Vector]:
    """Düzlem eksenleri (u, v) — CadModel._placement ile aynı kural (add_pocket/add_hole x,y'si)."""
    n = normal.normalized()
    u = cq.Vector(1, 0, 0) if abs(abs(n.z) - 1) < 1e-6 else cq.Vector(0, 0, 1).cross(n).normalized()
    return u, n.cross(u).normalized()


def _project_on_surface(face: cq.Face, p: cq.Vector) -> cq.Vector:
    proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(p.x, p.y, p.z), BRep_Tool.Surface_s(face.wrapped))
    if proj.NbPoints() == 0:
        return p
    q = proj.NearestPoint()
    return cq.Vector(q.X(), q.Y(), q.Z())


def _dedupe(pts: list[cq.Vector], tol: float = 1e-4) -> list[cq.Vector]:
    out: list[cq.Vector] = []
    for p in pts:
        if not out or (p - out[-1]).Length > tol:
            out.append(p)
    while len(out) > 2 and (out[0] - out[-1]).Length <= tol:
        out.pop()
    return out


def _prism(face: cq.Face, v: cq.Vector) -> cq.Shape:
    return cq.Shape.cast(BRepPrimAPI_MakePrism(face.wrapped, gp_Vec(v.x, v.y, v.z)).Shape())


def _common(a: cq.Shape, b: cq.Shape) -> list[cq.Face]:
    op = BRepAlgoAPI_Common(a.wrapped, b.wrapped)
    op.SetRunParallel(False)
    op.Build()
    if not op.IsDone():
        raise CadError("Bölge hesaplanamadı (kesişim işlemi başarısız).")
    return [f for f in cq.Shape.cast(op.Shape()).Faces() if f.Area() > 1e-7]


# ---------------------------------------------------------------------------- tanım
def new_region_def(model: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Arayüzden gelen isteği doğrular ve saklanacak bölge tanımına çevirir."""
    shape = req.get("shape")
    if shape not in SHAPES:
        raise CadError(f"shape: {' | '.join(SHAPES)}")
    idx, face = model.face_by_id(req["face_id"])
    stype = surface_type(face)
    if req.get("anchor_point") is None:
        raise CadError("anchor_point gerekli.")
    anchor = _project_on_surface(face, _V(req["anchor_point"]))
    if face.distance(cq.Vertex.makeVertex(*anchor.toTuple())) > max(0.5, mesh_tolerance(model.shape) * 4):
        raise CadError("Çizimin başlangıç noktası seçilen yüzeyin üzerinde değil.")
    defn: dict[str, Any] = {"shape": shape, "face_id": f"f{idx}", "surface": stype, "anchor_point": vec(anchor)}
    if stype == "plane":
        defn["mode"] = "plane"
        defn["normal"] = face_details(face)["normal"]
        if shape == "circle":
            if req.get("center") is None or not req.get("radius"):
                raise CadError("Daire için center ve radius gerekli.")
            if float(req["radius"]) < 0.05:
                raise CadError("Daire çok küçük (yarıçap < 0.05 mm).")
            defn["center"], defn["radius"] = [float(c) for c in req["center"]], float(req["radius"])
        else:
            pts = req.get("points") or []
            if len(pts) < 3 or len(pts) > MAX_POINTS:
                raise CadError(f"Şekil 3–{MAX_POINTS} nokta içermeli.")
            defn["points"] = [[float(c) for c in p] for p in pts]
    else:
        defn["mode"] = "view"
        rays = req.get("rays") or []
        if len(rays) < 3 or len(rays) > MAX_POINTS:
            raise CadError(f"Eğri yüzeyde seçim için 3–{MAX_POINTS} ışın gerekli.")
        defn["rays"] = [[float(c) for c in r] for r in rays]
    return defn


def find_anchor(model: Any, defn: dict[str, Any]) -> tuple[int, cq.Face]:
    """Bölgenin üzerinde durduğu yüzeyi (çapa noktasını içeren, aynı tipte yüzey) bulur."""
    p = cq.Vertex.makeVertex(*defn["anchor_point"])
    want_n = cq.Vector(*defn["normal"]) if defn.get("normal") else None
    pv = cq.Vector(*defn["anchor_point"])

    def ok(face: cq.Face) -> bool:
        if surface_type(face) != defn["surface"]:
            return False
        fb = bbox(face)
        if not (fb.xmin - 1e-2 <= pv.x <= fb.xmax + 1e-2 and fb.ymin - 1e-2 <= pv.y <= fb.ymax + 1e-2
                and fb.zmin - 1e-2 <= pv.z <= fb.zmax + 1e-2):
            return False
        if face.distance(p) > ANCHOR_TOL:
            return False
        if want_n is not None and (cq.Vector(*face_details(face)["normal"]) - want_n).Length > 1e-4:
            return False
        return True

    try:
        idx = int(defn["face_id"].lstrip("f"))
        if idx < len(model.faces) and ok(model.faces[idx]):
            return idx, model.faces[idx]
    except ValueError:
        pass
    for i, f in enumerate(model.faces):
        if ok(f):
            return i, f
    raise CadError("Bölgenin üzerinde durduğu yüzey artık yok (model değişti).")


# ---------------------------------------------------------------------------- hesap
def _plane_pieces(face: cq.Face, defn: dict[str, Any]) -> tuple[list[cq.Face], float]:
    n = cq.Vector(*defn["normal"]).normalized()
    o = cq.Vector(*defn["anchor_point"])
    flat = lambda q: q - n * (q - o).dot(n)  # noqa: E731  — düzleme izdüşüm
    if defn["shape"] == "circle":
        wire = cq.Wire.makeCircle(defn["radius"], flat(_V(defn["center"])), n)
    else:
        pts = _dedupe([flat(_V(q)) for q in defn["points"]])
        if len(pts) < 3:
            raise CadError("Çizilen şekil çok küçük.")
        wire = cq.Wire.makePolygon(pts, close=True)
    try:
        sketch = cq.Face.makeFromWires(wire)
    except Exception as exc:
        raise CadError("Çizilen şekilden yüzey oluşturulamadı; daha sade bir şekil çizin.") from exc
    if not sketch.isValid() or sketch.Area() < 1e-4:
        raise CadError("Çizilen şekil kendini kesiyor veya çok küçük; daha sade bir şekil çizin.")
    tool = _prism(sketch.translate(-n * 1.0), n * 2.0)
    return _common(face, tool), sketch.Area()


def _view_pieces(model: Any, face: cq.Face, defn: dict[str, Any]) -> list[cq.Face]:
    rays = [(_V(r[:3]), _V(r[3:]).normalized()) for r in defn["rays"]]
    m = sum((d for _, d in rays), cq.Vector()).normalized()
    o = sum((p for p, _ in rays), cq.Vector()) / len(rays)
    if any(d.dot(m) < 0.05 for _, d in rays):
        raise CadError("Şekil görüş açısına göre çok geniş.")
    bb = model.bbox
    c = cq.Vector(bb.center.x, bb.center.y, bb.center.z)
    R = bb.DiagonalLength / 2 + 1.0
    s_mid = (c - o).dot(m)
    s_near, s_far = max(s_mid - R, 1e-2), s_mid + R
    # Aynı derinlik düzlemine (m'ye dik) oturan uçlar → düzlemsel kapaklı, düzgün bir koni/prizma
    near = _dedupe([p + d * ((s_near - (p - o).dot(m)) / d.dot(m)) for p, d in rays])
    far = _dedupe([p + d * ((s_far - (p - o).dot(m)) / d.dot(m)) for p, d in rays])
    if len(near) < 3 or len(near) != len(far):
        raise CadError("Çizilen şekil çok küçük.")
    try:
        tool = cq.Solid.makeLoft([cq.Wire.makePolygon(near, close=True), cq.Wire.makePolygon(far, close=True)], ruled=True)
    except Exception as exc:
        raise CadError("Çizilen şekilden seçim hacmi oluşturulamadı; daha sade bir şekil çizin.") from exc
    if not tool.isValid():
        raise CadError("Çizilen şekil kendini kesiyor; daha sade bir şekil çizin.")
    pieces = _common(face, tool)

    # Dönel yüzeylerde ışınlar yüzeyin arka tarafına da değer: görünür yarıyı (siluete kadar) ayır
    anchor = cq.Vector(*defn["anchor_point"])
    info = face_details(face)
    cut_plane: tuple[cq.Vector, cq.Vector] | None = None
    if info["surface"] in ("cylinder", "cone"):
        P, A = cq.Vector(*info["axis_point"]), cq.Vector(*info["axis_dir"]).normalized()
        w = (o - P) - A * (o - P).dot(A)
        if w.Length > 1e-6:
            off = (info["radius"] ** 2 / w.Length) if info["surface"] == "cylinder" and w.Length > info["radius"] else 0.0
            cut_plane = (P + w.normalized() * off, w.normalized())
    elif info["surface"] == "sphere":
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        sph = BRepAdaptor_Surface(face.wrapped).Sphere()
        P, r = cq.Vector(*vec(sph.Location())), sph.Radius()
        w = o - P
        if w.Length > r:
            cut_plane = (P + w.normalized() * (r * r / w.Length), w.normalized())
    if cut_plane and pieces:
        q, nn = cut_plane
        side = 1.0 if (anchor - q).dot(nn) >= 0 else -1.0
        a = cq.Vector(1, 0, 0) if abs(nn.x) < 0.9 else cq.Vector(0, 1, 0)
        a = nn.cross(a).normalized()
        b = nn.cross(a).normalized()
        L = bb.DiagonalLength * 4 + 10
        sq = cq.Face.makeFromWires(cq.Wire.makePolygon([q + a * L + b * L, q - a * L + b * L, q - a * L - b * L, q + a * L - b * L], close=True))
        half = _prism(sq, nn * (side * L))
        pieces = [p for piece in pieces for p in _common(piece, half)]

    # Kameraya arkasını dönen parçaları at
    kept = []
    for p in pieces:
        pt = point_on_face(p)
        if p.normalAt(pt).dot(o - pt) > 0:
            kept.append(p)
    return kept


def _polyline(wire: cq.Wire, defl: float) -> list[cq.Vector]:
    curve = BRepAdaptor_CompCurve(wire.wrapped)
    disc = GCPnts_QuasiUniformDeflection(curve, defl)
    if not disc.IsDone():
        return [cq.Vector(*v.toTuple()) for v in wire.Vertices()]
    pts = [disc.Value(i) for i in range(1, disc.NbPoints() + 1)]
    return [cq.Vector(p.X(), p.Y(), p.Z()) for p in pts]


def _simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas–Peucker (kapalı çokgen; ilk nokta sabit)."""
    if len(pts) < 4:
        return pts

    def rdp(seg: list[tuple[float, float]]) -> list[tuple[float, float]]:
        (ax, ay), (bx, by) = seg[0], seg[-1]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-12
        imax, dmax = 0, 0.0
        for i in range(1, len(seg) - 1):
            d = abs(dy * (seg[i][0] - ax) - dx * (seg[i][1] - ay)) / L
            if d > dmax:
                imax, dmax = i, d
        if dmax <= tol:
            return [seg[0], seg[-1]]
        return rdp(seg[: imax + 1])[:-1] + rdp(seg[imax:])

    far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    a = rdp(pts[: far + 1])
    b = rdp(pts[far:] + [pts[0]])
    return a[:-1] + b[:-1]


def _tessellate(pieces: list[cq.Face], tol: float) -> dict[str, list]:
    verts: list[float] = []
    idxs: list[int] = []
    for f in pieces:
        BRepMesh_IncrementalMesh(f.wrapped, tol, False, 0.25, True)
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f.wrapped, loc)
        if tri is None:
            continue
        base = len(verts) // 3
        trsf = loc.Transformation()
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            verts.extend((_r(p.X()), _r(p.Y()), _r(p.Z())))
        rev = f.wrapped.Orientation() == TopAbs_REVERSED
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            if rev:
                b, c = c, b
            idxs.extend((base + a - 1, base + b - 1, base + c - 1))
    return {"vertices": verts, "indices": idxs}


def compute_region(model: Any, defn: dict[str, Any]) -> dict[str, Any]:
    """Bölgeyi mevcut model üzerinde hesaplar. Dönen sözlükte '_pieces' (B-rep parçaları) da vardır."""
    idx, face = find_anchor(model, defn)
    nominal = None
    if defn["mode"] == "plane":
        pieces, nominal = _plane_pieces(face, defn)
    else:
        pieces = _view_pieces(model, face, defn)
    if not pieces:
        raise CadError("Çizilen şekil seçilen yüzeyle kesişmiyor (yüzeyin görünen kısmında bir alan çizin).")

    comp = cq.Compound.makeCompound(pieces)
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(comp.wrapped, props)
    area = props.Mass()
    cm = props.CentreOfMass()
    rb = bbox(comp)
    tol = mesh_tolerance(model.shape)
    eps = max(0.01, model.bbox.DiagonalLength * 2e-4)  # çizim için yüzeyin hafif önüne

    face_info = model.face_info(f"f{idx}")
    info: dict[str, Any] = {
        "kind": "region",
        "region_id": defn["id"],
        "shape": defn["shape"],
        "mode": defn["mode"],
        "face_id": f"f{idx}",
        "surface": defn["surface"],
        "face_role": face_info["role"],
        "area": _r(area, 3),
        "center": [_r(cm.X()), _r(cm.Y()), _r(cm.Z())],
        "bbox": [vec(cq.Vector(rb.xmin, rb.ymin, rb.zmin)), vec(cq.Vector(rb.xmax, rb.ymax, rb.zmax))],
        "pieces": len(pieces),
        "clipped": None if nominal is None else bool(area < nominal * (1 - 1e-4) - 1e-6),
        "model_version": model.version,
    }
    for k in ("feature_id", "hole_id", "pocket_id"):
        info[k] = face_info.get(k)

    # Sınır çizgileri (çizim için yüzeyin önüne itilmiş) + düzlemde 2B ölçüler
    defl = max(tol * 0.5, 0.005)
    outline: list[list[float]] = []
    n = cq.Vector(*defn["normal"]) if defn["mode"] == "plane" else None
    for p in pieces:
        for w in p.Wires():
            pts = _polyline(w, defl)
            line: list[float] = []
            for q in pts:
                nq = n if n is not None else p.normalAt(q)
                q2 = q + nq * eps
                line.extend((_r(q2.x), _r(q2.y), _r(q2.z)))
            if len(line) >= 6:
                outline.append(line)

    if defn["mode"] == "plane":
        info["normal"] = defn["normal"]
        horizontal = abs(abs(n.z) - 1) < 1e-6
        u, v = plane_frame(n)
        origin = cq.Vector(0, 0, 0) if horizontal else face.Center()
        uv = lambda q: ((q - origin).dot(u), (q - origin).dot(v)) if not horizontal else (q.x, q.y)  # noqa: E731
        pts2 = [uv(q) for p in pieces for w in p.Wires() for q in _polyline(w, defl)]
        us, vs = [a for a, _ in pts2], [b for _, b in pts2]
        info["placement"] = {
            "face_id": defn["id"],  # bölge kimliği: model değişse de doğru yüzeye çözülür
            "x": _r((min(us) + max(us)) / 2, 3), "y": _r((min(vs) + max(vs)) / 2, 3),
            "length": _r(max(us) - min(us), 3), "width": _r(max(vs) - min(vs), 3),
            "axes": "x,y = parça X,Y" if horizontal else "x,y = yüzey merkezine göre (yatay, dikey)",
        }
        if defn["shape"] == "circle":
            cx, cy = uv(_V(defn["center"]))
            info["circle"] = {"x": _r(cx, 3), "y": _r(cy, 3), "diameter": _r(defn["radius"] * 2, 3)}
        if horizontal:
            main = max(pieces, key=lambda f: f.Area())
            ring = [(q.x, q.y) for q in _polyline(main.outerWire(), defl)]
            if len(ring) > 1 and math.hypot(ring[0][0] - ring[-1][0], ring[0][1] - ring[-1][1]) < 1e-6:
                ring = ring[:-1]
            ring = _simplify(ring, max(0.02, defl))
            if len(ring) > 64:
                ring = _simplify(ring, max(info["placement"]["length"], info["placement"]["width"]) * 0.005)
            info["outline_xy"] = [[_r(x, 3), _r(y, 3)] for x, y in ring]
    else:
        pt = point_on_face(pieces[0])
        info["normal_at_center"] = vec(pieces[0].normalAt(pt))
        for k in ("radius", "diameter", "axis_dir", "axis_point"):
            if k in face_info:
                info[k] = face_info[k]

    # Bölgeye değen delikler
    hits = []
    for h in model.holes:
        if any(model.faces[i].distance(comp) < 1e-3 for i in h["_face_idx"]):
            hits.append(h["id"])
    info["holes"] = hits

    shape_tr = SHAPES[defn["shape"]]
    dims = ""
    if info.get("circle"):
        dims = f", Ø{info['circle']['diameter']:g} mm"
    elif info.get("placement"):
        dims = f", {info['placement']['length']:g} × {info['placement']['width']:g} mm"
    info["role"] = f"{info['face_id']} yüzeyi ({face_info['role']}) üzerinde seçilmiş bölge ({shape_tr}{dims})"
    info["mesh"] = _tessellate(pieces, tol)
    info["outline"] = outline
    info["_pieces"] = pieces
    return info


def public(info: dict[str, Any], mesh: bool = True) -> dict[str, Any]:
    out = {k: v for k, v in info.items() if not k.startswith("_")}
    if not mesh:
        out.pop("mesh", None)
        out.pop("outline", None)
    return out
