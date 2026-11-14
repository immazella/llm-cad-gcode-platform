"""Open CASCADE yardımcıları: yüzey bazlı tessellation, yüzey bilgisi, kenar örnekleme.

Bu modül CadQuery'nin üst seviye API'sinin yetmediği yerlerde doğrudan OCP kullanır.
"""
from __future__ import annotations

import math
from typing import Any

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import (
    GeomAbs_BSplineSurface,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

SURFACE_NAMES = {
    GeomAbs_Plane: "plane",
    GeomAbs_Cylinder: "cylinder",
    GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere",
    GeomAbs_Torus: "torus",
    GeomAbs_BSplineSurface: "bspline",
}


def bbox(shape: cq.Shape) -> cq.BoundBox:
    """Kesin (üçgenlemeden etkilenmeyen) sınır kutusu."""
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, b, False, False)
    return cq.BoundBox(b)


def vec(v: Any) -> list[float]:
    """cq.Vector / gp_Pnt / gp_Dir -> [x, y, z] (yuvarlanmış)."""
    if isinstance(v, cq.Vector):
        return [round(v.x, 4) + 0.0, round(v.y, 4) + 0.0, round(v.z, 4) + 0.0]
    return [round(v.X(), 4) + 0.0, round(v.Y(), 4) + 0.0, round(v.Z(), 4) + 0.0]


def surface_type(face: cq.Face) -> str:
    return SURFACE_NAMES.get(BRepAdaptor_Surface(face.wrapped).GetType(), "other")


def point_on_face(face: cq.Face) -> cq.Vector:
    """Yüzeyin *üzerinde* (deliklerin içine düşmeyen) gerçek bir nokta döndürür.

    Yüzeyin en büyük üçgeninin ağırlık merkezi alınıp yüzeye izdüşürülür. Bu,
    kimlik eşleştirmesinde (yüzey hangi özelliğe ait?) kullanılır.
    """
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
    if tri is None:
        BRepMesh_IncrementalMesh(face.wrapped, 0.05, False, 0.3, True)
        tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
    if tri is None or tri.NbTriangles() == 0:
        return face.Center()
    trsf = loc.Transformation()
    best, best_area = None, -1.0
    for i in range(1, tri.NbTriangles() + 1):
        a, b, c = (tri.Node(k).Transformed(trsf) for k in tri.Triangle(i).Get())
        va = cq.Vector(a.X(), a.Y(), a.Z())
        vb = cq.Vector(b.X(), b.Y(), b.Z())
        vc = cq.Vector(c.X(), c.Y(), c.Z())
        area = (vb - va).cross(vc - va).Length
        if area > best_area:
            best_area, best = area, (va + vb + vc) / 3.0
    surf = BRep_Tool.Surface_s(face.wrapped)
    proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(best.x, best.y, best.z), surf)
    if proj.NbPoints() > 0:
        p = proj.NearestPoint()
        return cq.Vector(p.X(), p.Y(), p.Z())
    return best


def face_details(face: cq.Face) -> dict[str, Any]:
    """Bir yüzeyin geometrik tanımı (LLM'ye ve arayüze gönderilir)."""
    stype = surface_type(face)
    info: dict[str, Any] = {
        "surface": stype,
        "area": round(face.Area(), 3),
        "center": vec(face.Center()),
    }
    ad = BRepAdaptor_Surface(face.wrapped)
    if stype == "plane":
        p = point_on_face(face)
        info["normal"] = vec(face.normalAt(p))
    elif stype == "cylinder":
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        info["radius"] = round(cyl.Radius(), 4)
        info["diameter"] = round(cyl.Radius() * 2, 4)
        info["axis_point"] = vec(ax.Location())
        info["axis_dir"] = vec(ax.Direction())
        umin, umax, _, _ = BRepTools.UVBounds_s(face.wrapped)
        info["angle_deg"] = round(math.degrees(umax - umin), 2)
    elif stype == "cone":
        cone = ad.Cone()
        info["half_angle_deg"] = round(math.degrees(cone.SemiAngle()), 3)
        info["axis_dir"] = vec(cone.Axis().Direction())
    elif stype == "torus":
        tor = ad.Torus()
        info["minor_radius"] = round(tor.MinorRadius(), 4)
    bb = bbox(face)
    info["bbox"] = [vec(cq.Vector(bb.xmin, bb.ymin, bb.zmin)), vec(cq.Vector(bb.xmax, bb.ymax, bb.zmax))]
    return info


def edge_face_map(shape: cq.Shape) -> TopTools_IndexedDataMapOfShapeListOfShape:
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape.wrapped, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def edge_neighbor_faces(edge: cq.Edge, emap: TopTools_IndexedDataMapOfShapeListOfShape) -> list[cq.Face]:
    if not emap.Contains(edge.wrapped):
        return []
    faces: list[cq.Face] = []
    for f in emap.FindFromKey(edge.wrapped):
        cf = cq.Face(f)
        if not any(cf.isSame(x) for x in faces):
            faces.append(cf)
    return faces


def is_seam(edge: cq.Edge, emap) -> bool:
    """Silindir dikiş kenarı mı? (Aynı yüzey kenarın iki tarafında.)"""
    return len(edge_neighbor_faces(edge, emap)) < 2


def mesh_tolerance(shape: cq.Shape) -> float:
    bb = bbox(shape)
    return max(0.01, min(0.2, bb.DiagonalLength * 0.0008))


def tessellate(shape: cq.Shape, faces: list[cq.Face]) -> tuple[list[dict], list[list[float]]]:
    """Her yüzeyi ayrı ayrı üçgenler. Frontend her yüzeyi ayrı mesh olarak çizer,
    böylece fareyle üzerine gelinen yüzey kimliğiyle (face_id) tanınır."""
    tol = mesh_tolerance(shape)
    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, 0.25, True)
    out: list[dict] = []
    for idx, face in enumerate(faces):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        verts: list[float] = []
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            verts.extend((round(p.X(), 4), round(p.Y(), 4), round(p.Z(), 4)))
        rev = face.wrapped.Orientation() == TopAbs_REVERSED
        idxs: list[int] = []
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            if rev:
                b, c = c, b
            idxs.extend((a - 1, b - 1, c - 1))
        out.append({"id": f"f{idx}", "vertices": verts, "indices": idxs})

    # Kenar çizgileri (dikiş kenarları hariç)
    emap = edge_face_map(shape)
    edges: list[list[float]] = []
    for e in shape.Edges():
        if is_seam(e, emap):
            continue
        n = 2 if e.geomType() == "LINE" else 32
        pts: list[float] = []
        for k in range(n):
            p = e.positionAt(k / (n - 1))
            pts.extend((round(p.x, 4), round(p.y, 4), round(p.z, 4)))
        edges.append(pts)
    return out, edges
