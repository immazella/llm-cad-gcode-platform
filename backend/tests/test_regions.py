"""⌘/Ctrl ile yüzey üzerinde çizilen bölgeler (r1, r2 …): kesişim, kırpma, eğri yüzey, kalıcılık, LLM bağlamı."""
from __future__ import annotations

import math

import pytest

from app.cad.features import CadError
from app.cad.model import CadModel
from app.cad.occ_utils import face_details, surface_type


def _face(m: CadModel, normal: list[float]) -> str:
    return next(f"f{i}" for i, f in enumerate(m.faces) if face_details(f).get("normal") == normal)


def _rect(fid, pts):
    return {"shape": "rect", "face_id": fid, "anchor_point": pts[0], "points": pts}


@pytest.fixture
def box() -> CadModel:
    return CadModel("b", {"type": "box", "length": 60, "width": 40, "height": 10})


def test_rect_region_on_top_face_matches_drawn_shape(box):
    top = _face(box, [0, 0, 1])
    r = box.add_region(_rect(top, [[0, 0, 10], [20, 0, 10], [20, 12, 10], [0, 12, 10]]))
    assert r["region_id"] == "r1" and r["kind"] == "region" and r["face_id"] == top
    assert r["area"] == pytest.approx(240) and r["clipped"] is False
    assert r["placement"] == {"face_id": "r1", "x": 10, "y": 6, "length": 20, "width": 12, "axes": "x,y = parça X,Y"}
    assert len(r["outline_xy"]) == 4 and r["mesh"]["indices"] and box.version == 1  # model değişmedi


def test_region_is_clipped_by_face_boundary_and_holes(box):
    box.add_hole(10, 6, 4)
    top = _face(box, [0, 0, 1])
    r = box.add_region(_rect(top, [[0, 0, 10], [50, 0, 10], [50, 30, 10], [0, 30, 10]]))  # kenardan taşar
    assert r["clipped"] is True
    assert r["placement"]["length"] == pytest.approx(30) and r["placement"]["width"] == pytest.approx(20)
    assert r["area"] == pytest.approx(30 * 20 - math.pi * 4, rel=1e-3)
    assert r["holes"] == [box.holes[0]["id"]]


def test_circle_and_side_face_regions(box):
    top = _face(box, [0, 0, 1])
    c = box.add_region({"shape": "circle", "face_id": top, "anchor_point": [-15, 0, 10], "center": [-15, 0, 10], "radius": 5})
    assert c["circle"] == {"x": -15, "y": 0, "diameter": 10} and c["area"] == pytest.approx(math.pi * 25, rel=1e-3)
    side = _face(box, [0, -1, 0])
    s = box.add_region(_rect(side, [[0, -20, 5], [10, -20, 5], [10, -20, 8], [0, -20, 8]]))
    # yan yüzeyde x,y yüzey merkezine göre (add_pocket/add_hole face_id kuralı)
    assert s["placement"]["x"] == pytest.approx(5) and s["placement"]["y"] == pytest.approx(1.5)


def test_self_intersecting_lasso_is_rejected(box):
    top = _face(box, [0, 0, 1])
    with pytest.raises(CadError):
        box.add_region({"shape": "lasso", "face_id": top, "anchor_point": [0, 0, 10],
                        "points": [[0, 0, 10], [10, 5, 10], [0, 5, 10], [10, 0, 10]]})


def test_region_survives_rebuild_and_is_consumed_by_pocket(box):
    top = _face(box, [0, 0, 1])
    r1 = box.add_region(_rect(top, [[0, 0, 10], [20, 0, 10], [20, 12, 10], [0, 12, 10]]))
    box.add_region(_rect(top, [[-25, -15, 10], [-15, -15, 10], [-15, -5, 10], [-25, -5, 10]]))
    pl = r1["placement"]
    box.add_pocket(pl["x"], pl["y"], pl["length"], pl["width"], 3, face_id=pl["face_id"])
    regions, dropped = box.regions_info(mesh=False)
    assert dropped == ["r1"] and [r["region_id"] for r in regions] == ["r2"]


def test_view_projected_region_on_cylinder_keeps_visible_side():
    m = CadModel("d", {"type": "cylinder", "radius": 30, "height": 10})
    cyl = next(f"f{i}" for i, f in enumerate(m.faces) if surface_type(f) == "cylinder")
    cam = (0, -150, 5)
    pts = [(-50, 0, 2), (50, 0, 2), (50, 0, 8), (-50, 0, 8)]  # silindirden geniş: silüete kadar
    rays = [[*cam, *(p[k] - cam[k] for k in range(3))] for p in pts]
    r = m.add_region({"shape": "rect", "face_id": cyl, "anchor_point": [0, -30, 5], "rays": rays})
    assert r["mode"] == "view" and "placement" not in r
    assert r["bbox"][1][1] == pytest.approx(-6, abs=1e-2)  # silüet düzlemi y = -r²/d
    assert r["normal_at_center"][1] < 0  # kameraya bakan taraf


def test_session_keeps_regions(box):
    from app import session
    from app.llm.agent import Agent
    from app.workspace import Workspace

    ws = Workspace()
    ws.model = box
    top = _face(box, [0, 0, 1])
    box.add_region(_rect(top, [[0, 0, 10], [20, 0, 10], [20, 12, 10], [0, 12, 10]]))
    session.save(ws, Agent(ws), {})
    ws2 = Workspace()
    assert session.load(ws2, Agent(ws2), {})
    regs, dropped = ws2.model.regions_info(mesh=False)
    assert not dropped and regs[0]["area"] == pytest.approx(240)
    assert ws2.model.add_region(_rect(top, [[1, 1, 10], [2, 1, 10], [2, 2, 10]]))["region_id"] == "r2"
    session.clear()


def test_region_context_and_offline_pocket(box):
    from app.llm.agent import Agent
    from app.workspace import Workspace

    ws = Workspace()
    ws.model = box
    ag = Agent(ws)
    ag.set_offline(True)
    top = _face(box, [0, 0, 1])
    box.add_region(_rect(top, [[0, 0, 10], [20, 0, 10], [20, 12, 10], [0, 12, 10]]))
    sel = box.region_info("r1", mesh=False)
    ctx = ws.context(sel)
    assert "Seçili bölge" in ctx and '"placement"' in ctx and "mesh" not in ctx
    res = ag.chat("Seçtiğim bölgeye 3 mm cep aç", sel)
    assert res["model_changed"] and box.pockets[0]["size_xy"] == pytest.approx([20, 12])


def test_region_id_works_as_face_id_after_model_changes(box):
    top = _face(box, [0, 0, 1])
    box.add_region(_rect(top, [[-25, -15, 10], [-15, -15, 10], [-15, -5, 10], [-25, -5, 10]]))
    box.add_pocket(15, 10, 10, 8, 2)  # model değişti, yüzey numaraları kaydı
    fid = box.add_hole(-20, -10, 4, face_id="r1")
    assert any(h["feature_id"] == fid for h in box.holes)


def test_multi_selection_context_and_offline_loop(box):
    from app.llm.agent import Agent
    from app.workspace import Workspace

    ws = Workspace()
    ws.model = box
    ag = Agent(ws)
    ag.set_offline(True)
    top = _face(box, [0, 0, 1])
    box.add_region(_rect(top, [[-25, -15, 10], [-15, -15, 10], [-15, -5, 10], [-25, -5, 10]]))
    box.add_region(_rect(top, [[5, 5, 10], [20, 5, 10], [20, 12, 10], [5, 12, 10]]))
    sels = [box.region_info("r1", mesh=False), box.region_info("r2", mesh=False)]
    ctx = ws.context(sels)
    assert "Seçili elemanlar (2" in ctx and "  1. bölge (" in ctx and "  2. bölge (" in ctx
    res = ag.chat("Seçtiğim bölgelere 2 mm cep aç", sels)
    assert sum(a["tool"] == "add_pocket" and a["ok"] for a in res["actions"]) == 2
    assert sorted(p["size_xy"] for p in box.pockets) == [[10, 10], [15, 7]]


def test_chat_api_with_multiple_ids():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    c.post("/api/session/reset")
    c.post("/api/llm/mode", json={"offline": True})
    c.post("/api/models/primitive", json={"type": "box", "length": 60, "width": 60, "height": 10})
    c.post("/api/tools/add_hole", json={"diameter": 6, "x": -10, "y": 0})
    c.post("/api/tools/add_hole", json={"diameter": 6, "x": 10, "y": 0})
    mesh = c.get("/api/models/current/mesh").json()
    hole_faces = {}
    for f in mesh["faces"]:
        if f["hole_id"]:
            hole_faces.setdefault(f["hole_id"], f["id"])
    ids = list(hole_faces.values())
    c.put("/api/selection", json={"ids": ids, "model_version": mesh["version"]})
    assert len(c.get("/api/selection").json()["selections"]) == 2
    r = c.post("/api/chat", json={"message": "Seçtiğim deliklerin çapını 8 mm yap", "selected_ids": ids,
                                  "model_version": mesh["version"]}).json()
    assert [a["tool"] for a in r["actions"]] == ["modify_hole", "modify_hole"] and all(a["ok"] for a in r["actions"])
    assert all(h["diameter"] == 8 for h in r["state"]["model"]["holes"])
    c.post("/api/session/reset")
