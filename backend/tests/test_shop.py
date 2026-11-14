"""Atölye (prove-out) araçları: satır durumu, kontrol listesi, alarm bilgi tabanı, ölçü düzeltme, program farkı."""
from __future__ import annotations

import pytest

from app.workspace import Workspace, run_tool


@pytest.fixture
def ws() -> Workspace:
    w = Workspace()
    w.create_box(60, 40, 10)
    w.add_hole(8, x=10, y=0)
    w.add_pocket(20, 12, 3, x=-12, y=0)
    w.generate_gcode()
    return w


def _line(ws, pred) -> int:
    lines = ws.gcodes[ws.current_gcode]["text"].splitlines()
    return next(i for i, l in enumerate(lines, 1) if pred(l))


def test_line_state_reports_tool_change_and_machine_coords(ws):
    n = _line(ws, lambda l: "G43" in l and "H1" in l)
    st = run_tool(ws, "get_line_state", {"line": n})
    assert st["after"]["tool"] == 1 and "G43 H1" in st["after"]["tool_length_comp"]
    off = ws.config.setup.offset_for(ws.config.profile)
    assert st["move"]["end_machine"][2] == pytest.approx(st["move"]["end_work"][2] + off[2])  # takım boyu bilinmiyor → 0
    assert any("H1" in w for w in st["warnings"])
    assert st["summary"].startswith(f"N{n}:")


def test_line_out_of_range_is_tool_error(ws):
    assert run_tool(ws, "get_line_state", {"line": 99999})["ok"] is False


def test_checklist_lists_first_approach_and_first_cut_per_tool(ws):
    cl = run_tool(ws, "prove_out_checklist", {})
    kinds = [(i["kind"], i["message"].split()[0]) for i in cl["items"]]
    for t in ("T7", "T1"):
        assert ("first_approach", t) in kinds and ("first_cut", t) in kinds
    assert cl["counts"]["error"] == 0


def test_rapid_into_part_is_flagged(ws):
    text = "%\nO1000\nG21 G90 G17\nT1 M6\nS1000 M3\nG0 G43 H1 Z5\nX0 Y0\nG0 Z-5\nM30\n%"
    ws.upload_gcode(text, "hatali")
    cl = run_tool(ws, "prove_out_checklist", {})
    assert any(i["severity"] == "error" and i["line"] == 8 for i in cl["items"])
    st = run_tool(ws, "get_line_state", {"line": 8})
    assert st["move"]["passes_through_finished_part"] and st["warnings"]


def test_alarm_kb_is_sourced_and_never_guesses(ws):
    r = run_tool(ws, "explain_alarm", {"code": "PS10"})
    assert r["found"] and r["alarm"]["code"] == "PS0010" and "B-64305EN" in r["source"]["title"]
    r = run_tool(ws, "explain_alarm", {"code": "ALARM:2", "controller": "grbl"})
    assert r["found"] and "E040" in r["alarm"]["related_rules"] and "github.com/gnea/grbl" in r["source"]["url"]
    r = run_tool(ws, "explain_alarm", {"code": "error:20"})  # aktif profil Fanuc
    assert not r["found"] and r["found_in_other"][0]["controller"] == "grbl"
    assert not run_tool(ws, "explain_alarm", {"code": "PS9999"})["found"]


def test_wear_offset_directions(ws):
    p = run_tool(ws, "suggest_wear_offset", {"feature": "pocket", "nominal": 12, "measured": 11.96})
    assert p["radial_wear_mm"] == pytest.approx(-0.02) and p["action"] == "cam" and p["effective_diameter"] == pytest.approx(5.96)
    c = run_tool(ws, "suggest_wear_offset", {"feature": "contour", "nominal": 50, "measured": 50.04})
    assert c["radial_wear_mm"] == pytest.approx(-0.02)
    d = run_tool(ws, "suggest_wear_offset", {"feature": "depth", "nominal": 3, "measured": 2.95})
    assert d["action"] == "length_wear" and d["wear_mm"] == pytest.approx(-0.05)
    assert run_tool(ws, "suggest_wear_offset", {"feature": "drilled_hole", "nominal": 8, "measured": 8.05})["action"] == "tool"
    assert run_tool(ws, "suggest_wear_offset", {"feature": "pocket", "nominal": 12, "measured": 11.99, "tolerance": 0.02})["action"] == "none"


def test_diff_flags_lowered_rapid_and_removed_g43(ws):
    base = ws.current_gcode
    lines = ws.gcodes[base]["text"].splitlines()
    i = _line(ws, lambda l: l.strip() == "Z1") - 1
    j = _line(ws, lambda l: "G43" in l and "H7" in l) - 1
    lines[i] = "Z0.2"
    lines[j] = lines[j].replace("G43 H7 ", "")
    ws.upload_gcode("\n".join(lines), "makineden")
    d = run_tool(ws, "diff_programs", {})
    assert d["base_id"] == base and d["counts"]["error"] >= 1
    risks = " ".join(r for c in d["changes"] for r in c["risks"])
    assert "G43" in risks and "Z 1 → 0.2" in risks


def test_offline_shop_sentences(ws):
    from app.llm.agent import Agent

    ag = Agent(ws)
    ag.set_offline(True)
    assert ag.chat("20. satır ne yapacak?", None)["reply"].startswith("N20:")
    assert "IMPROPER G-CODE" in ag.chat("PS0010 alarmı verdi", None)["reply"]
    assert "Hazırlık" in ag.chat("İlk deneme için kontrol listesi", None)["reply"]
    assert "Ø5.960" in ag.chat("cep 12 olmalıydı 11.96 çıktı", None)["reply"]
