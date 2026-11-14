"""Temel uçtan uca testler: CAD motoru, özellik tanıma, G-code üretimi ve doğrulama, LLM protokolü."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cad.features import CadError
from app.cad.model import CadModel, load_step
from app.cam.config import MachiningConfig
from app.cam.generator import generate_gcode
from app.cam.interpreter import toolpath_json
from app.cam.validator import validate
from app.workspace import Workspace


@pytest.fixture
def plate() -> CadModel:
    m = CadModel("test", {"type": "box", "length": 60, "width": 60, "height": 10})
    m.add_hole(0, 0, 20)
    return m


def test_hole_recognized_with_feature_id(plate):
    h = plate.holes[0]
    assert h["id"] == "hole_1" and h["diameter"] == 20 and h["through"]
    face = plate.face_info(h["face_ids"][0])
    assert face["hole_id"] == "hole_1" and face["surface"] == "cylinder"


def test_modify_hole_and_undo(plate):
    plate.modify_hole("hole_1", diameter=30)
    assert plate.holes[0]["diameter"] == 30
    plate.undo()
    assert plate.holes[0]["diameter"] == 20


def test_hole_outside_part_is_rejected(plate):
    with pytest.raises(CadError):
        plate.add_hole(100, 100, 5)
    with pytest.raises(CadError):
        plate.add_hole(28, 0, 10)  # kenarı taşar → tam delik olmaz
    assert len(plate.features) == 1  # model geri alındı


def test_blind_hole_bottom_not_pocket(plate):
    plate.add_hole(20, 20, 6, depth=4)
    assert not plate.pockets
    blind = next(h for h in plate.holes if h["id"] == "hole_2")
    assert not blind["through"] and abs(blind["depth"] - 4) < 1e-6


def test_pocket_and_base_resize(plate):
    plate.add_pocket(15, 15, 16, 12, 3, corner_radius=3)
    assert plate.pockets[0]["id"] == "pocket_1" and abs(plate.pockets[0]["depth"] - 3) < 1e-6
    plate.modify_base(height=15)
    assert plate.summary()["bbox"]["size"][2] == 15
    assert abs(plate.pockets[0]["depth"] - 3) < 1e-6  # cep yeni üst yüzeye taşındı


def test_step_roundtrip_and_defeature(plate, tmp_path: Path):
    plate.add_hole(-20, -20, 8)
    path = tmp_path / "p.step"
    plate.export_step(path)
    m = load_step(path, "imp")
    assert {h["diameter"] for h in m.holes} == {8.0, 20.0}
    hid = next(h["id"] for h in m.holes if h["diameter"] == 8)
    m.modify_hole(hid, diameter=10)
    assert sorted(h["diameter"] for h in m.holes) == [10.0, 20.0]
    assert m.shape.isValid()


def test_generated_gcode_is_valid(plate):
    plate.add_hole(-20, -20, 6)
    plate.add_pocket(15, 15, 16, 12, 3, corner_radius=3)
    res = generate_gcode(plate, MachiningConfig())
    ops = {p["op"] for p in res["plan"]}
    assert ops == {"drill", "helical", "pocket", "contour"}
    rep = validate(res["text"], stock_height=res["stock_height"])
    assert rep["valid"], rep["issues"]
    tp = toolpath_json(res["text"])
    assert tp["stats"]["bbox"]["min"][2] < -10  # kontur parçanın altına iner


@pytest.mark.parametrize(
    "code,expected",
    [
        ("G21 G90\nS1000 M3\nG0 Z-2\nG0 X10\nM30\n", "E031"),  # malzeme içinde G0
        ("G21 G90\nG0 X0 Y0 Z5\nG1 Z-1 F100\nM30\n", "E030"),  # iş mili kapalı kesme
        ("G21 G90\nS1000 M3\nG0 X0 Y0 Z5\nG2 X10 Y0 I3 J0 F100\nM30\n", "E053"),  # tutarsız yay
        ("G21 G90\nS1000 M3\nG0 X900 Y0 Z5\nM30\n", "E040"),  # makine sınırı
        ("G21 G90\nS1000 M3\nG0 X0 Y0 Z5\nG83 X0 Y0 Z-5 R2 F80\nM30\n", "E023"),  # Q eksik
        ("G21 G90\nG65 P9000\nM30\n", "E002"),  # desteklenmeyen
        ("G21 G90\nG0 G1 X1\nM30\n", "E003"),  # modal çakışma
        ("G21 G90\nG1 X1 Y$2\nM30\n", "E001"),  # sözdizimi
    ],
)
def test_validator_rules(code, expected):
    codes = {i["code"] for i in validate(code)["issues"]}
    assert expected in codes, codes


# ------------------------------------------------------------------ LLM protokolü (sahte istemci)
class FakeAnthropic:
    """İlk çağrıda araç kullanır, ikinci çağrıda metin döndürür."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        assert kw["tools"] and kw["system"]
        if self.calls == 1:
            blk = SimpleNamespace(model_dump=lambda **_: {"type": "tool_use", "id": "tu1", "name": "create_box",
                                                          "input": {"length": 40, "width": 30, "height": 5}})
            return SimpleNamespace(content=[blk], stop_reason="tool_use")
        last = kw["messages"][-1]
        assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"
        assert json.loads(last["content"][0]["content"])["ok"] is True
        blk = SimpleNamespace(model_dump=lambda **_: {"type": "text", "text": "Plaka oluşturuldu."})
        return SimpleNamespace(content=[blk], stop_reason="end_turn")


def test_anthropic_tool_loop(monkeypatch):
    from app.llm.agent import Agent

    ws = Workspace()
    a = Agent(ws)
    a.provider, a.model_name, a._client = "anthropic", "test", FakeAnthropic()
    r = a.chat("40x30x5 plaka", None)
    assert r["reply"] == "Plaka oluşturuldu." and r["model_changed"]
    assert ws.model and ws.model.summary()["bbox"]["size"] == [40, 30, 5]


class FakeOpenAI:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="create_cylinder", arguments='{"radius": 10, "height": 4}'))
            msg = SimpleNamespace(content=None, tool_calls=[tc])
        else:
            assert kw["messages"][-1]["role"] == "tool"
            msg = SimpleNamespace(content="Silindir hazır.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_openai_tool_loop():
    from app.llm.agent import Agent

    ws = Workspace()
    a = Agent(ws)
    a.provider, a.model_name, a._client = "openai", "test", FakeOpenAI()
    r = a.chat("silindir", None)
    assert r["reply"] == "Silindir hazır." and ws.model.base["type"] == "cylinder"


def test_offline_toggle(monkeypatch):
    from app.llm.agent import Agent
    from app.workspace import Workspace

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    a = Agent(Workspace())
    assert (a.provider, a.llm_available) == ("deepseek", True)
    a.set_offline(True)
    assert a.provider == "offline" and a.info()["configured_provider"] == "deepseek"
    a.set_offline(False)
    assert (a.provider, a.model_name) == ("deepseek", "deepseek-chat")

    monkeypatch.setenv("LLM_PROVIDER", "offline")
    b = Agent(Workspace())
    b.set_offline(False)
    assert b.provider == "offline" and not b.llm_available


# ------------------------------------------------------------------ makine profilleri ve post-processor
from app.cam.profiles import Setup, get_profile, list_profiles  # noqa: E402

PROFILE_IDS = ["fanuc_vmc", "linuxcnc_mill", "grbl_router"]


def test_profiles_load():
    ids = {p.id for p in list_profiles()}
    assert set(PROFILE_IDS) <= ids
    assert not get_profile("grbl_router").canned_cycles and not get_profile("grbl_router").tool_changer


@pytest.mark.parametrize("pid", PROFILE_IDS)
def test_generated_gcode_valid_on_own_profile(plate, pid):
    plate.add_hole(-20, -20, 6)
    plate.add_pocket(15, 15, 16, 12, 3, corner_radius=3)
    res = generate_gcode(plate, MachiningConfig(profile_id=pid))
    assert res["profile_id"] == pid and res["post"] == get_profile(pid).post
    rep = validate(res["text"], get_profile(pid), Setup(), res["stock_height"])
    assert rep["valid"] and rep["counts"]["warning"] == 0, rep["issues"]
    assert rep["profile"]["id"] == pid and set(rep["levels"]) == {"1", "2", "3"}


def test_dialect_differences(plate):
    plate.add_hole(-20, -20, 6)  # matkapla delinir → delme döngüsü
    fanuc = generate_gcode(plate, MachiningConfig(profile_id="fanuc_vmc"))["text"]
    grbl = generate_gcode(plate, MachiningConfig(profile_id="grbl_router"))
    assert "G81" in fanuc and "M6" in fanuc and "G43 H" in fanuc and fanuc.splitlines()[1].startswith("O")
    assert "G81" not in grbl["text"] and "G83" not in grbl["text"] and "M6" not in grbl["text"] and "%" not in grbl["text"]
    assert "M0 (TAKIM DEGISTIR" in grbl["text"]
    assert any("G0/G1" in w for w in grbl["warnings"])
    # Fanuc programı GRBL'de: desteklenmeyen döngü ve takım değişimi
    codes = {i["code"] for i in validate(fanuc, get_profile("grbl_router"))["issues"]}
    assert {"E015", "E018"} <= codes


def test_spindle_clamped_to_profile(plate):
    res = generate_gcode(plate, MachiningConfig(profile_id="linuxcnc_mill"))
    assert "S10000" not in res["text"] and "S6000" in res["text"]
    assert any("6000" in w for w in res["warnings"])
    # aynı programın Fanuc'a göre üretilmiş hâli LinuxCNC'de devir hatası verir
    fanuc = generate_gcode(plate, MachiningConfig(profile_id="fanuc_vmc"))["text"]
    assert "E012" in {i["code"] for i in validate(fanuc, get_profile("linuxcnc_mill"))["issues"]}


def test_travel_checked_in_machine_coordinates():
    thick = CadModel("kalin", {"type": "box", "length": 60, "width": 60, "height": 25})
    res = generate_gcode(thick, MachiningConfig(profile_id="grbl_router"))
    rep = validate(res["text"], get_profile("grbl_router"), Setup(), res["stock_height"])
    e040 = [i for i in rep["issues"] if i["code"] == "E040"]
    assert e040 and e040[0]["data"]["axis"] == "Z" and e040[0]["data"]["frame"] == "machine"
    assert e040[0]["data"]["machine"] < get_profile("grbl_router").travel["Z"].min
    # iş sıfırı 10 mm aşağı alınınca (parça daha alçak bağlanınca) aynı program kursa sığar
    rep2 = validate(res["text"], get_profile("grbl_router"), Setup(work_offset=(-200, -150, -50)), res["stock_height"])
    assert "E040" not in {i["code"] for i in rep2["issues"]}
    # aynı parça Fanuc tezgâhında sorunsuz
    fres = generate_gcode(thick, MachiningConfig(profile_id="fanuc_vmc"))
    assert validate(fres["text"], get_profile("fanuc_vmc"), Setup(), fres["stock_height"])["valid"]


def test_tool_length_comp_rule_and_offset():
    prog = "%\nO1\nG21 G90\nT1 M6\nS1000 M3\nG0 X0 Y0 Z5\nG1 Z-1 F100\nM30\n%\n"
    codes = {i["code"] for i in validate(prog, get_profile("fanuc_vmc"))["issues"]}
    assert "W017" in codes and "E002" not in codes
    fixed = prog.replace("G0 X0 Y0 Z5", "G0 G43 H1 X0 Y0 Z5")
    assert "W017" not in {i["code"] for i in validate(fixed, get_profile("fanuc_vmc"))["issues"]}
    # takım boyu Z'ye eklenir: 400 mm'lik bir takım Z kursunu (max 0) aşar
    rep = validate(fixed, get_profile("fanuc_vmc"), Setup(), tool_lengths={1: 400})
    assert any(i["code"] == "E040" and i["data"]["tool_length"] == 400 for i in rep["issues"])
    assert "I071" not in {i["code"] for i in rep["issues"]}
    # boy girilmemişse bunu raporda söyler (sessizce 0 varsaymaz)
    unknown = validate(fixed, get_profile("fanuc_vmc"))
    assert any(i["code"] == "I071" and i["data"]["H"] == 1 for i in unknown["issues"])


def test_tool_length_lives_on_tool():
    """Boy takım kaydında durur; takım listesi değişse de doğru takıma uygulanır."""
    ws = Workspace()
    ws.create_box(60, 60, 10)
    ws.add_hole(10, x=0, y=0)
    for t in ws.config.tools:
        t.length = 60.0
    ws.generate_gcode()
    v = ws.gcodes[ws.current_gcode]["validation"]
    assert v["valid"] and "I071" not in {i["code"] for i in v["issues"]}
    assert v["setup"]["tool_lengths"]["1"] == 60.0
    # Ø10 matkaba (T9) Z kursunu aşacak kadar uzun bir boy verilince yalnızca o takımın hareketleri hata verir
    next(t for t in ws.config.tools if t.diameter == 10).length = 400.0
    v = ws.revalidate_current()
    e040 = [i for i in v["issues"] if i["code"] == "E040"]
    assert e040 and e040[0]["data"]["tool_length"] == 400.0


def test_issue_has_structured_fields():
    i = validate("G21 G90\nS1000 M3\nG0 X900 Y0 Z5\nM30\n")["issues"][0]
    assert {"code", "level", "severity", "rule", "line", "data", "message"} <= set(i)
    assert i["code"] == "E040" and i["level"] == 2


def test_workspace_profile_switch_revalidates():
    ws = Workspace()
    ws.create_box(60, 60, 10)
    ws.add_hole(10, x=0, y=0)
    ws.generate_gcode()
    assert ws.gcodes[ws.current_gcode]["validation"]["valid"]
    r = ws.set_machine_profile("grbl_router")
    assert r["revalidation"]["valid"] is False and "note" in r
    ws.generate_gcode()
    rec = ws.gcodes[ws.current_gcode]
    assert rec["generated_for"] == "grbl_router" and rec["validation"]["valid"]


def test_hole_in_pocket_floor_has_safe_r_plane():
    """Cep tabanındaki delik cepten önce delinir: R düzlemi malzemenin içinde olmamalı (her profilde)."""
    m = CadModel("cep-delik", {"type": "box", "length": 60, "width": 60, "height": 15})
    m.add_pocket(0, 0, 30, 30, 5, corner_radius=3)
    m.add_hole(0, 0, 10, depth=4, face_id=m.pockets[0]["floor_face_id"])
    assert m.holes[0]["top_point"][2] < 15
    for pid in PROFILE_IDS:
        res = generate_gcode(m, MachiningConfig(profile_id=pid))
        rep = validate(res["text"], get_profile(pid), Setup(), res["stock_height"])
        assert rep["valid"], (pid, rep["issues"])
    # doğrulayıcı R düzlemi malzeme içinde olan döngüyü yakalar
    bad = "G21 G90\nS1000 M3\nG0 X0 Y0 Z5\nG98 G81 X0 Y0 Z-9 R-3 F100\nG80\nM30\n"
    assert "E032" in {i["code"] for i in validate(bad)["issues"]}


# ------------------------------------------------------------------ kazıma (gravür)
from app.cad.engrave import hershey_text, placed_strokes  # noqa: E402
from app.cam.generator import order_strokes  # noqa: E402
from app.workspace import run_tool  # noqa: E402

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
       '<circle cx="50" cy="50" r="40" fill="none" stroke="black"/>'
       '<path d="M30 60 Q50 20 70 60" fill="none" stroke="black"/>'
       '<rect x="40" y="65" width="20" height="10" transform="rotate(10 50 70)"/></svg>')


def test_turkish_single_line_text():
    plain = hershey_text("cgsou", 6)
    tr = hershey_text("çğşöü", 6)
    assert len(tr) == len(plain) + 7  # çengel ×2 (ç, ş), kısa yay ×1 (ğ), iki nokta ×2 (ö, ü)
    dotted, dotless = hershey_text("i", 6), hershey_text("ı", 6)
    assert len(dotless) == len(dotted) - 1
    cap = max(p[1] for s in hershey_text("H", 6) for p in s)
    assert abs(cap - 6) < 1e-6  # büyük harf yüksekliği = height


def test_engraving_not_recognized_as_holes_or_pockets(plate):
    plate.add_engraving("text", 0, 20, 0.3, 0.35, text="İTÜ 2026", height=5)
    plate.add_engraving("svg", 0, -20, 0.3, 0.35, svg=SVG, size=14)
    assert [h["id"] for h in plate.holes] == ["hole_1"] and plate.pockets == []
    assert plate.shape.isValid()
    feats = plate.summary()["features"]
    assert "svg" not in feats[2] and feats[1]["text"] == "İTÜ 2026"


def test_engraving_must_fit_surface(plate):
    with pytest.raises(CadError):
        plate.add_engraving("text", 0, 20, 0.3, 0.35, text="ÇOK UZUN BİR KAZIMA YAZISI", height=8)  # parçadan taşar
    with pytest.raises(CadError):
        plate.add_engraving("text", 0, 0, 0.3, 0.35, text="ORTA", height=5)  # Ø20 deliğin üstüne düşer
    assert len(plate.features) == 1


def test_engraving_on_pocket_floor_and_cam_order():
    m = CadModel("gravur", {"type": "box", "length": 80, "width": 40, "height": 10})
    m.add_hole(-30, 0, 6)
    m.add_engraving("text", 5, 8, 0.3, 0.35, text="İTÜ Çağ", height=6)
    m.add_pocket(5, -8, 50, 14, 3, corner_radius=3)
    m.add_engraving("text", 5, -8, 0.3, 0.35, text="şöğü", height=5, face_id=m.pockets[0]["floor_face_id"])
    assert len(m.pockets) == 1
    for pid in PROFILE_IDS:
        res = generate_gcode(m, MachiningConfig(profile_id=pid))
        ops = [(p["op"], p["feature"]) for p in res["plan"]]
        # matkap → üst yüzey kazıması → cep → cep tabanı kazıması → kontur
        assert ops == [("drill", "hole_1"), ("engrave", "engrave_1"), ("pocket", "pocket_1"), ("engrave", "engrave_2"), ("contour", "dış kontur")]
        rep = validate(res["text"], get_profile(pid), Setup(), res["stock_height"])
        assert rep["valid"] and rep["counts"]["warning"] == 0, (pid, rep["issues"])
        assert "Z-3.3" in res["text"]  # cep tabanı (-3) + 0.3 mm kazıma


def test_engraving_tool_selection_and_width_warning(plate):
    plate.add_engraving("path", 0, 0, 0.3, 2.0, paths=[[[-25, -25], [25, -25]]], closed=[False])
    res = generate_gcode(plate, MachiningConfig())
    eng = next(p for p in res["plan"] if p["op"] == "engrave")
    assert MachiningConfig().tool(eng["tool"]).type == "vbit"
    assert any("V-uç" in w and "genişlik" in w for w in res["warnings"])  # 60° uç 0.3 mm'de ~0.35 mm keser, 2 mm değil
    cfg = MachiningConfig()
    cfg.tools = [t for t in cfg.tools if t.type != "vbit"]
    res2 = generate_gcode(plate, cfg)
    assert next(p for p in res2["plan"] if p["op"] == "engrave")["tool"] == cfg.mill_tool


def test_order_strokes_reverses_and_rotates():
    a = [(10.0, 0.0), (20.0, 0.0)]
    b = [(1.0, 0.0), (5.0, 0.0)]
    ring = [(30.0, 0.0), (30.0, 5.0), (25.0, 5.0), (25.0, 0.0), (30.0, 0.0)]
    out = order_strokes([a, b[::-1], ring])
    assert out[0] == b and out[1] == a  # en yakın uçtan, gerekirse ters
    assert out[2][0] == (25.0, 0.0) and out[2][-1] == (25.0, 0.0)  # kapalı yol en yakın köşeden


def test_engraving_tools_via_workspace():
    ws = Workspace()
    ws.create_box(80, 40, 10)
    r = run_tool(ws, "add_engraving_text", {"text": "ÖRNEK", "height": 6, "y": 8})
    assert r["ok"] and r["width"] == pytest.approx(0.346, abs=1e-3)  # 60° V-uç, 0.3 mm
    r = run_tool(ws, "add_engraving_path", {"paths": [[[-30, -10], [0, -15], [30, -10]]], "closed": False})
    assert r["ok"] and r["stroke_count"] == 1
    r = run_tool(ws, "modify_feature", {"feature_id": "engrave_1", "params": {"text": "İTÜ", "height": 8}})
    assert r["ok"] and ws.model.feature_by_id("engrave_1")["text"] == "İTÜ"
    bad = run_tool(ws, "modify_feature", {"feature_id": "engrave_2", "params": {"text": "x"}})
    assert bad["ok"] is False
    ws.add_engraving_svg(SVG, size=12, x=25, y=5)
    g = ws.generate_gcode()
    assert g["ok"] and g["validation_summary"]["valid"] and len(placed_strokes(ws.model.feature_by_id("engrave_3"))) >= 3


def test_session_roundtrip():
    from app import session
    from app.llm.agent import Agent
    from app.workspace import Workspace

    ws = Workspace()
    ag = Agent(ws)
    ws.create_box(60, 60, 10)
    ws.add_hole(10)
    ws.generate_gcode()
    ag.history = [{"role": "user", "content": "merhaba"}]
    ws.config.cutting.feed_rate = 777
    summary, gid = ws.model.summary(), ws.current_gcode
    ag.active["messages"] = [{"role": "user", "text": "merhaba"}]  # sohbet mesajları sohbet kaydında
    session.save(ws, ag, {"tab": "gcode"})

    ws2 = Workspace()
    ag2 = Agent(ws2)
    ui: dict = {}
    assert session.load(ws2, ag2, ui)
    assert ws2.model.summary() == summary
    assert ws2.current_gcode == gid and not ws2.gcodes[gid]["stale"]
    assert ws2.config.cutting.feed_rate == 777
    assert ag2.history == ag.history and ag2.active["messages"][0]["text"] == "merhaba" and ui["tab"] == "gcode"
    ws2.undo()  # geri alma geçmişi de korunur
    assert not ws2.model.holes
    session.clear()
