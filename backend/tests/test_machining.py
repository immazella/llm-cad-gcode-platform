"""Takım kütüphanesi, kesme verisi, kurulum (ham parça / iş sıfırı / G54–G59), operasyon ayarları, makine düzenleyici ve post seçenekleri."""
from __future__ import annotations

import math
import re

import pytest

from app.cad.model import CadModel
from app.cam.config import MachiningConfig
from app.cam.generator import generate_gcode
from app.cam.materials import recommend
from app.cam.profiles import delete_profile, get_profile, list_profiles, save_profile
from app.cam.validator import validate
from app.workspace import Workspace, run_tool


@pytest.fixture
def part() -> CadModel:
    m = CadModel("t", {"type": "box", "length": 60, "width": 40, "height": 10})
    m.add_hole(-20, -10, 6)
    m.add_pocket(12, 4, 20, 14, 3, corner_radius=3)
    return m


def gen(m, **cfg):
    c = MachiningConfig.model_validate(cfg)
    r = generate_gcode(m, c)
    v = validate(r["text"], c.profile, c.setup, r["stock_height"], c.tool_lengths())
    return r, v, c


def moves(text: str) -> list[dict[str, float]]:
    out = []
    for ln in text.splitlines():
        if ln.startswith("(") or not re.search(r"[XYZ]-?\d", ln):
            continue
        out.append({a: float(v) for a, v in re.findall(r"([XYZ])(-?[\d.]+)", ln)})
    return out


# ---------------------------------------------------------------- kesme verisi
def test_recommend_follows_formula_and_machine_limit():
    r = recommend("endmill", 6, 3, "carbide", "steel_mild")
    assert r["rpm"] == pytest.approx(1000 * r["vc"] / (math.pi * 6), abs=1)
    assert r["feed"] == pytest.approx(r["rpm"] * 3 * r["fz"], rel=0.01)
    lim = recommend("endmill", 6, 3, "carbide", "al6061", max_rpm=10000)
    assert lim["rpm"] == 10000 and lim["notes"]
    d = recommend("drill", 8, 2, "hss", "al6061")
    assert d["peck"] and d["stepdown"] is None


def test_priority_override_then_preset_then_material(part):
    r, v, _ = gen(part)
    pocket = next(p for p in r["plan"] if p["op"] == "pocket")
    assert "preset" in pocket["source"] and pocket["rpm"] == 10000  # T1 alüminyum preseti, makine sınırına kırpıldı
    r2, _, _ = gen(part, setup={"material": "stainless"})
    p2 = next(p for p in r2["plan"] if p["op"] == "pocket")
    assert "malzeme tablosu" in p2["source"] and p2["rpm"] < 5000
    r3, _, _ = gen(part, op_settings={"pocket": {"rpm": 4000, "feed": 500}})
    p3 = next(p for p in r3["plan"] if p["op"] == "pocket")
    assert (p3["rpm"], p3["feed"]) == (4000, 500) and "operasyon ayarı" in p3["source"]
    assert "S4000" in r3["text"]
    r4, _, _ = gen(part, cutting_mode="manual", cutting={"spindle_rpm": 7000, "feed_rate": 700})
    p4 = next(p for p in r4["plan"] if p["op"] == "pocket")
    assert (p4["rpm"], p4["feed"], p4["source"]) == (7000, 700, "elle ayarlar")


# ---------------------------------------------------------------- kurulum
def test_origin_front_left_and_stock(part):
    r, v, _ = gen(part, setup={"origin_xy": "front_left", "stock_side": 2, "stock_top": 1.5, "wcs": 55})
    assert v["valid"], v["issues"][:3]
    assert r["work_origin_model"] == [-32.0, -22.0, 11.5]
    assert r["plan"][0]["op"] == "face"
    assert "\nG55\n" in r["text"] and "\nG54\n" not in r["text"]
    xy = [m for m in moves(r["text"]) if "Z" not in m]
    # sol ön köşe sıfır: parçanın tüm noktaları X, Y ≥ 0 (takım yarıçapı kadar taşma hariç)
    assert min(m.get("X", 99) for m in xy) > -8 and min(m.get("Y", 99) for m in xy) > -8
    assert r["stock_height"] == 11.5


def test_face_skipped_warning_when_disabled(part):
    r, _, _ = gen(part, setup={"stock_top": 1}, op_settings={"face": {"enabled": False}})
    assert not any(p["op"] == "face" for p in r["plan"])
    assert any("üst payı" in w for w in r["warnings"])


# ---------------------------------------------------------------- operasyon ayarları
def test_ramp_entry_moves_xyz_together(part):
    r, v, _ = gen(part, operations=["pocket"], op_settings={"pocket": {"entry": "ramp", "ramp_angle": 3}})
    assert v["valid"]
    def sloped(text):  # XY ile Z aynı satırda değişen kesme hareketleri
        return [ln for ln in text.splitlines() if not ln.startswith("G0") and re.search(r"[XY]-?\d", ln) and re.search(r"Z-\d", ln)]

    assert len(sloped(r["text"])) > 5
    r2, _, _ = gen(part, operations=["pocket"], op_settings={"pocket": {"entry": "plunge"}})
    assert not sloped(r2["text"])


def test_finish_pass_and_stock_to_leave(part):
    r, v, _ = gen(part, operations=["pocket"], op_settings={"pocket": {"finish_pass": True, "finish_allowance": 0.3, "entry": "plunge"}})
    assert v["valid"] and "son paso" in r["text"]
    z = [m["Z"] for m in moves(r["text"]) if "Z" in m]
    assert min(z) == pytest.approx(-3, abs=1e-3)
    r2, _, _ = gen(part, operations=["pocket"], op_settings={"pocket": {"stock_z": 0.5, "entry": "plunge"}})
    assert min(m["Z"] for m in moves(r2["text"]) if "Z" in m) == pytest.approx(-2.5, abs=1e-3)


def test_drill_cycle_forced_and_peck(part):
    r, _, _ = gen(part, operations=["drill"], op_settings={"drill": {"cycle": "G83", "peck": 1.25}})
    assert "G83" in r["text"] and "Q1.25" in r["text"]
    r2, _, _ = gen(part, operations=["drill"], op_settings={"drill": {"cycle": "G81"}})
    assert "G81" in r2["text"] and "G83" not in r2["text"]


def test_op_tool_choice_and_flute_length_warning(part):
    cfg = MachiningConfig()
    cfg.tools.append(cfg.tools[0].model_copy(update={"number": 30, "diameter": 4, "flute_length": 2, "description": "Ø4 kısa", "presets": []}))
    cfg.op_settings["pocket"].tool = 30
    r = generate_gcode(part, cfg)
    assert next(p for p in r["plan"] if p["op"] == "pocket")["tool"] == 30
    assert any("ağız boyu" in w for w in r["warnings"])


# ---------------------------------------------------------------- makine ve post
def custom(**kw):
    d = get_profile("fanuc_vmc").model_dump()
    d.update(id="test_vmc", name="Test VMC", **kw)
    return save_profile(d)


def test_custom_machine_crud_and_builtin_protected():
    p = custom(coolant_modes={"flood": "M8", "mist": "m7"})
    assert not p.builtin and 7 in p.supported_m and p.coolant_modes["mist"] == "M7"
    assert any(x.id == "test_vmc" for x in list_profiles())
    with pytest.raises(ValueError):
        save_profile({**get_profile("fanuc_vmc").model_dump()})
    with pytest.raises(ValueError):
        delete_profile("fanuc_vmc")
    delete_profile("test_vmc")
    assert all(x.id != "test_vmc" for x in list_profiles())
    # silinmiş makineye işaret eden oturum varsayılana döner
    assert MachiningConfig.model_validate({"profile_id": "test_vmc"}).profile_id == "fanuc_vmc"


def test_post_options_and_coolant_mode(part):
    custom(post_options={"line_numbers": True, "line_increment": 5, "comments": False, "home_at_end": "G28", "arc_format": "r"},
           coolant_modes={"flood": "M8", "mist": "M7"})
    try:
        cfg = MachiningConfig.model_validate({"profile_id": "test_vmc", "cutting": {"coolant": "mist"}})
        part.add_hole(15, -12, 12)  # matkabı yoksa helisel → yay
        cfg.tools = [t for t in cfg.tools if not (t.type == "drill" and t.diameter == 12)]
        r = generate_gcode(part, cfg)
        lines = r["text"].splitlines()
        assert not any("(" in l for l in lines)
        body = [l for l in lines if l not in ("%",) and not l.startswith("O")]
        assert all(re.match(r"N\d+ ", l) for l in body) and body[0].startswith("N5 ")
        assert any(" M7" in l for l in lines) and not any(l.endswith(" M8") for l in lines)
        assert any(re.search(r"G[23] .*R\d", l) for l in lines) and not any(" I" in l for l in lines)
        assert any("G91 G28 Z0" in l for l in lines)
        v = validate(r["text"], cfg.profile, cfg.setup, r["stock_height"], cfg.tool_lengths())
        assert v["valid"], [i for i in v["issues"] if i["severity"] == "error"][:3]
    finally:
        delete_profile("test_vmc")


def test_unsupported_coolant_falls_back_off(part):
    r, _, _ = gen(part, cutting={"coolant": "air"})
    assert any("soğutma kapatıldı" in w for w in r["warnings"]) and "M8" not in r["text"]


# ---------------------------------------------------------------- LLM araçları
def test_workspace_tools_roundtrip():
    ws = Workspace()
    ws.create_box(60, 40, 10)
    ws.add_hole(6, x=-20, y=-10)
    assert run_tool(ws, "set_setup", {"material": "paslanmaz", "origin_xy": "back_right", "stock_top": 1})["setup"]["material"] == "stainless"
    assert run_tool(ws, "set_setup", {"wcs": 60})["ok"] is False
    rec = run_tool(ws, "recommend_cutting", {"tool_number": 1})
    assert rec["material"] == "stainless" and rec["rpm"] < 6000
    t = run_tool(ws, "upsert_tool", {"number": 40, "type": "bullmill", "diameter": 10, "corner_radius": 1, "flutes": 4})
    assert t["created"] and t["tool"]["corner_radius"] == 1
    assert run_tool(ws, "set_operation", {"op": "pocket", "tool": 40, "finish_pass": True})["settings"]["tool"] == 40
    assert run_tool(ws, "set_operation", {"op": "pocket", "tool": 99})["ok"] is False
    assert run_tool(ws, "delete_tool", {"number": 40})["ok"]
    assert ws.config.op_settings["pocket"].tool is None
    g = run_tool(ws, "generate_gcode", {"spindle_rpm": 3000})
    assert g["ok"] and all(p["rpm"] == 3000 for p in g["plan"] if p["op"] != "drill")
    rec_ = ws.gcodes[ws.current_gcode]
    assert rec_["work_origin_model"] == [30.0, 20.0, 11.0] and rec_["setup"]["origin_xy"] == "back_right"
    # profil değişince kurulum (malzeme, sıfır) korunur
    run_tool(ws, "set_machine_profile", {"profile_id": "grbl_router"})
    assert ws.config.setup.material == "stainless" and ws.config.setup.origin_xy == "back_right"
