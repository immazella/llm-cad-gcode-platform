"""Kazıma (gravür) yolları: yazı, serbest çizgi/poligon ve SVG logo.

Kazıma bir "çizgi takip" işlemidir: takım merkezi verilen yolu sabit derinlikte izler.
Bu modül her kazıma özelliği için 2B vuruşları (stroke = açık/kapalı polyline) üretir:

- Yazı, tek çizgi (varsayılan): Hershey "futural" tek çizgi fontu. Harfler kontur değil
  orta çizgi olarak kazınır; gravürde standart yöntem budur. Hershey'de Türkçe harf yoktur:
  ç ş ğ ö ü ı İ temel harf + işaret çizgisiyle (çengel, kısa çizgi, nokta) oluşturulur.
- Yazı, kontur: sistemdeki TrueType font (CadQuery/OCC); harflerin dış hatları kazınır.
- Yol: kullanıcının/LLM'nin verdiği noktalar (açık çizgi veya kapalı şekil).
- SVG: logo dosyasındaki tüm path/şekil geometrisi (dönüşümler uygulanmış) çizgi olarak.

Vuruşlar özelliğin merkezine göre YEREL koordinatta saklanır/üretilir; yerleştirme
(origin + dönme) ortaktır. CAD tarafında oluk, vuruşların takım genişliği kadar
kalınlaştırılmış (shapely buffer) hâlinin derinlik kadar çıkarılmasıyla oluşur. Bu düz
tabanlı bir yaklaşımdır; V-uçla gerçek oluk V kesitlidir.
"""
from __future__ import annotations

import io
import math
from functools import lru_cache
from typing import Any

import cadquery as cq
from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

from .features import CadError

Pt = tuple[float, float]
Stroke = list[Pt]

MAX_POINTS = 20000  # sadeleştirme sonrası toplam nokta sınırı
MAX_SVG_BYTES = 2_000_000
FONTS = ("tek_cizgi", "kontur")

# --------------------------------------------------------------------------- Hershey tek çizgi yazı
# Türkçe harf → (temel harf, işaret)
TR_MAP = {
    "ç": ("c", "cedilla"), "Ç": ("C", "cedilla"), "ş": ("s", "cedilla"), "Ş": ("S", "cedilla"),
    "ğ": ("g", "breve"), "Ğ": ("G", "breve"), "ö": ("o", "diaeresis"), "Ö": ("O", "diaeresis"),
    "ü": ("u", "diaeresis"), "Ü": ("U", "diaeresis"), "ı": ("i", "dotless"), "İ": ("I", "dot"),
    "â": ("a", "circumflex"), "Â": ("A", "circumflex"), "î": ("i", "circumflex_dotless"), "û": ("u", "circumflex"),
}


@lru_cache(maxsize=1)
def _hershey():
    from HersheyFonts import HersheyFonts

    f = HersheyFonts()
    f.load_default_font("futural")
    return f


def _dot(cx: float, cy: float, r: float = 1.0) -> Stroke:
    return [(cx - r, cy), (cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]


def _accent(kind: str, cx: float, upper: bool) -> list[Stroke]:
    """Hershey ham birimlerinde (y aşağı, taban çizgisi 9, büyük harf üstü -12) işaret çizgileri."""
    top = -15.5 if upper else -9.5  # işaretin oturduğu yükseklik
    if kind == "cedilla":
        return [[(cx, 9), (cx, 11), (cx + 2, 11.5), (cx + 2, 13.5), (cx, 14.5), (cx - 2, 14)]]
    if kind == "breve":
        return [[(cx - 3.5, top - 2.5), (cx - 2.5, top - 0.5), (cx, top + 0.5), (cx + 2.5, top - 0.5), (cx + 3.5, top - 2.5)]]
    if kind == "diaeresis":
        return [_dot(cx - 3, top - 1, 0.8), _dot(cx + 3, top - 1, 0.8)]
    if kind == "dot":
        return [_dot(cx, top - 1, 0.9)]
    if kind in ("circumflex", "circumflex_dotless"):
        return [[(cx - 3.5, top), (cx, top - 3.5), (cx + 3.5, top)]]
    return []


def hershey_text(text: str, height: float, spacing: float = 1.0) -> list[Stroke]:
    """Tek çizgi yazı vuruşları; büyük harf yüksekliği = height (mm), yerel koordinat, y yukarı."""
    f = _hershey()
    strokes: list[Stroke] = []
    cursor = 0.0
    missing: set[str] = set()
    for ch in text:
        base, accent = TR_MAP.get(ch, (ch, None))
        glyphs = list(f.glyphs_for_text(base))
        if not glyphs:
            missing.add(ch)
            continue
        g = glyphs[0]
        lines = [list(s) for s in g.strokes]
        if accent in ("dotless", "circumflex_dotless"):
            lines = [s for s in lines if min(p[1] for p in s) > -8]  # i'nin noktasını at
        (dx0, dy0), (dx1, dy1) = g.draw_box
        cx = (dx0 + dx1) / 2
        if accent:
            lines += _accent(accent, cx, base.isupper())
        off = cursor - g.left_offset
        for s in lines:
            if len(s) >= 2:
                strokes.append([(off + x, -y) for x, y in s])
        cursor += g.char_width * spacing
    if missing:
        raise CadError("Tek çizgi fontta olmayan karakter(ler): " + " ".join(sorted(missing)) + ". 'kontur' fontunu deneyin.")
    cap = 21.0  # taban çizgisi (9) ile büyük harf üstü (-12) arası
    k = height / cap
    return [[(x * k, (y + 9) * k) for x, y in s] for s in strokes]


# --------------------------------------------------------------------------- kontur (TrueType) yazı
def outline_text(text: str, height: float) -> list[Stroke]:
    try:
        ref = cq.Workplane().text("H", 10, 1).val().BoundingBox().ylen
        size = 10 * height / ref
        solid = cq.Workplane().text(text, size, 1, halign="left", valign="baseline").val()
    except Exception as exc:
        raise CadError(f"Kontur font ile yazı oluşturulamadı: {exc}") from exc
    strokes: list[Stroke] = []
    top_z = solid.BoundingBox().zmax
    for face in solid.Faces():
        if abs(face.Center().z - top_z) > 1e-6:
            continue
        for wire in [face.outerWire(), *face.innerWires()]:
            strokes.append(_wire_points(wire, closed=True))
    return strokes


def _wire_points(wire: cq.Wire, closed: bool, tol: float = 0.02) -> Stroke:
    from OCP.BRepAdaptor import BRepAdaptor_CompCurve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    curve = BRepAdaptor_CompCurve(wire.wrapped)
    disc = GCPnts_QuasiUniformDeflection(curve, tol)
    pts = [(curve.Value(disc.Parameter(i)).X(), curve.Value(disc.Parameter(i)).Y()) for i in range(1, disc.NbPoints() + 1)]
    if closed and pts and math.dist(pts[0], pts[-1]) > 1e-6:
        pts.append(pts[0])
    return pts


# --------------------------------------------------------------------------- SVG
def svg_strokes(svg_text: str, size: float) -> list[Stroke]:
    """SVG'deki tüm şekilleri vuruşlara çevirir; en uzun kenar = size (mm), merkez (0,0)."""
    if len(svg_text.encode("utf-8")) > MAX_SVG_BYTES:
        raise CadError("SVG dosyası çok büyük (en fazla 2 MB).")
    try:
        from svgelements import SVG, Path, Shape

        doc = SVG.parse(io.StringIO(svg_text), reify=True)
    except Exception as exc:
        raise CadError(f"SVG okunamadı: {exc}") from exc
    raw: list[Stroke] = []
    for el in doc.elements():
        if not isinstance(el, Shape):
            continue
        path = Path(el)
        if len(path) == 0:
            continue
        for sub in path.as_subpaths():
            sp = Path(sub)
            length = sp.length(error=1e-3) if len(sp) else 0
            if not length:
                continue
            n = max(2, min(2000, int(length / 0.5) + 2))
            pts = [(p.x, -p.y) for p in (sp.point(i / (n - 1)) for i in range(n)) if p is not None]
            if len(pts) >= 2:
                raw.append(pts)
    if not raw:
        raise CadError("SVG'de kazınacak çizgi/şekil bulunamadı (yazı öğeleri desteklenmez; yazıyı yola çevirin).")
    xs = [p[0] for s in raw for p in s]
    ys = [p[1] for s in raw for p in s]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span <= 0:
        raise CadError("SVG geometrisinin boyutu sıfır.")
    k = size / span
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [[((x - cx) * k, (y - cy) * k) for x, y in s] for s in raw]


# --------------------------------------------------------------------------- ortak
def _center(strokes: list[Stroke]) -> list[Stroke]:
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [[(x - cx, y - cy) for x, y in s] for s in strokes]


def local_strokes(feat: dict[str, Any]) -> list[Stroke]:
    src = feat["source"]
    if src == "text":
        if not str(feat.get("text", "")).strip():
            raise CadError("Kazınacak yazı boş.")
        if feat["height"] <= 0:
            raise CadError("Yazı yüksekliği pozitif olmalı.")
        s = hershey_text(feat["text"], feat["height"]) if feat.get("font", "tek_cizgi") == "tek_cizgi" else outline_text(feat["text"], feat["height"])
        return _center(s)
    if src == "path":
        return [[tuple(p) for p in s] for s in feat["local_paths"]]
    if src == "svg":
        return svg_strokes(feat["svg"], feat["size"])
    raise CadError(f"Bilinmeyen kazıma kaynağı: {src}")


def placed_strokes(feat: dict[str, Any]) -> list[Stroke]:
    """Parça koordinatında (XY) vuruşlar: yerel → dönme → origin'e taşıma → sadeleştirme."""
    ox, oy = feat["origin"][0], feat["origin"][1]
    a = math.radians(feat.get("rotation", 0) or 0)
    ca, sa = math.cos(a), math.sin(a)
    out: list[Stroke] = []
    total = 0
    for s in local_strokes(feat):
        pts = [(ox + x * ca - y * sa, oy + x * sa + y * ca) for x, y in s]
        closed = len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6
        if len(pts) > 2:
            simp = list(LineString(pts).simplify(0.005).coords)
            if closed and math.dist(simp[0], simp[-1]) > 1e-6:
                simp.append(simp[0])
            pts = simp
        if len(pts) >= 2:
            out.append([(round(x, 4), round(y, 4)) for x, y in pts])
            total += len(pts)
    if not out:
        raise CadError("Kazınacak yol yok.")
    if total > MAX_POINTS:
        raise CadError(f"Kazıma çok karmaşık ({total} nokta; en fazla {MAX_POINTS}). Logoyu sadeleştirin veya küçültün.")
    return out


def groove_region(strokes: list[Stroke], width: float):
    if width <= 0:
        raise CadError("Kazıma çizgi genişliği pozitif olmalı.")
    parts = [LineString(s).buffer(width / 2, quad_segs=6) for s in strokes]
    return unary_union(parts).simplify(0.005)


def _polys(geom) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def support_region(shape: cq.Shape, z: float):
    """z yüksekliğindeki +Z yönlü düzlemsel yüzeylerin (kazımanın yapılacağı yüzey) birleşimi."""
    from .occ_utils import face_details

    regions = []
    for face in shape.Faces():
        try:
            info = face_details(face)
        except Exception:
            continue
        if info["surface"] != "plane" or info.get("normal") != [0, 0, 1] or abs(face.Center().z - z) > 1e-4:
            continue
        holes = [_wire_points(w, True, 0.01) for w in face.innerWires()]
        regions.append(make_valid(Polygon(_wire_points(face.outerWire(), True, 0.01), holes)))
    return unary_union(regions) if regions else None


def groove_tool(shape: cq.Shape, feat: dict[str, Any], prior=None) -> tuple[cq.Shape, Any]:
    """Kazıma oluğunun 'takım katısı' (parçadan çıkarılacak hacim) ve 2B bölgesi.

    prior: aynı yüzeyde daha önce kazınmış bölgeler; yeni kazıma bunlarla kesişebilir
    (yüzeyde açılmış olukları "boşluk" saymamak için desteğe eklenir).
    """
    if feat.get("normal") != [0, 0, 1]:
        raise CadError("Kazıma yalnızca yukarı bakan yatay yüzeylere yapılabilir.")
    depth, width = float(feat["depth"]), float(feat["width"])
    if depth <= 0:
        raise CadError("Kazıma derinliği pozitif olmalı.")
    z = feat["origin"][2]
    region = groove_region(placed_strokes(feat), width)
    support = support_region(shape, z)
    if support is not None and prior is not None:
        support = unary_union([support, prior])
    if support is None:
        raise CadError("Kazıma yüzeyi bulunamadı (Z=%.3f'te yukarı bakan düz yüzey yok)." % z)
    if not region.within(support.buffer(1e-3)):
        outside = region.difference(support)
        b = outside.bounds
        raise CadError(f"Kazıma yüzeyin sınırlarını aşıyor ya da bir deliğe/boşluğa taşıyor "
                       f"(taşan bölge yaklaşık X {b[0]:.1f}…{b[2]:.1f}, Y {b[1]:.1f}…{b[3]:.1f}). Konumu veya boyutu değiştirin.")
    solids = []
    for poly in _polys(region):
        outer = cq.Wire.makePolygon([cq.Vector(x, y, z + 1.0) for x, y in poly.exterior.coords[:-1]], close=True)
        inner = [cq.Wire.makePolygon([cq.Vector(x, y, z + 1.0) for x, y in r.coords[:-1]], close=True) for r in poly.interiors]
        face = cq.Face.makeFromWires(outer, inner)
        solids.append(cq.Solid.extrudeLinear(face, cq.Vector(0, 0, -(depth + 1.0))))
    tool = solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)
    # Yüzey→özellik eşlemesi için hızlı 2B test bilgisi (binlerce yüzeyde 3B mesafe hesabı çok yavaş)
    tool._engrave = {"test": prep(region.buffer(1e-3)), "z": z, "depth": depth}
    return tool, region


def face_in_groove(tool: cq.Shape, p: cq.Vector) -> bool | None:
    """Yüzey noktası bu kazıma oluğuna mı ait? Kazıma takımı değilse None."""
    info = getattr(tool, "_engrave", None)
    if info is None:
        return None
    z = info["z"]
    return z - info["depth"] - 1e-3 <= p.z < z - 1e-4 and info["test"].contains(Point(p.x, p.y))
