"""Request-local presentation: never mutate canonical machining records."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "session.pkl"))
    from app import main
    from app.llm.agent import Agent
    from app.workspace import Workspace
    workspace = Workspace()
    monkeypatch.setattr(main, "ws", workspace)
    monkeypatch.setattr(main, "agent", Agent(workspace))
    monkeypatch.setattr(main, "ui_state", {})
    monkeypatch.setattr(main, "_save_session", lambda: None)
    return TestClient(main.app)


def test_validation_presentation_and_canonical_data(client):
    from app import main
    text = "G21 G90\nG0 X0 Y0 Z5\nG0 Z-2\nG1 X10 F500\nM30\n"
    tr = client.post("/api/gcode/text", json={"text": text, "name": "Türkçe.nc"}).json()
    gid = tr["gcode_id"]
    stored = deepcopy(main.ws.gcodes[gid])
    en = client.get(f"/api/gcode/{gid}", headers={"Accept-Language": "en"}).json()
    assert en["text"] == tr["text"] == text
    assert en["source"] == "upload:Türkçe.nc"
    er = next(i for i in en["validation"]["issues"] if i["code"] == "E032")
    assert "Rapid" in er["message"] and "-2" in er["message"]
    assert "plunge" in er["rule"].lower()
    assert "G1" in er["suggestion"]
    for a, b in zip(tr["validation"]["issues"], en["validation"]["issues"]):
        for key in ("code", "data", "text", "line", "level", "severity"):
            assert a[key] == b[key]
    assert tr["validation"]["stats"] == en["validation"]["stats"]
    assert main.ws.gcodes[gid] == stored
    assert client.get(f"/api/gcode/{gid}").json() == tr


def test_chat_language_override_history_and_invalid_locale(client):
    from app import main
    old = [{"role": "assistant", "text": "Eski yanıt: G-code bulunamadı."}, {"role": "user", "text": "Merhaba"}]
    main.agent.active["messages"] = deepcopy(old)
    main.agent.active["title"] = "Kişisel başlığım"
    result = client.post("/api/chat", headers={"Accept-Language": "tr"}, json={"message": "hello", "language": "en"})
    assert "Offline" in result.json()["reply"]
    assert "limited" in result.json()["reply"]
    assert "Çevrimdışı" in client.post("/api/chat", headers={"Accept-Language": "en"}, json={"message": "hello", "language": "tr"}).json()["reply"]
    assert "Offline" in client.post("/api/chat", headers={"Accept-Language": "en"}, json={"message": "hello"}).json()["reply"]
    bad = client.post("/api/chat", json={"message": "hello", "language": "de"})
    assert bad.status_code == 422
    assert main.agent.active["messages"] == old
    state = client.get("/api/state", headers={"Accept-Language": "en"}).json()
    assert state["ui"]["messages"] == old
    assert state["ui"]["conversation"]["title"] == "Kişisel başlığım"
    assert state["llm"]["model"] == "kural-tabanlı"
    assert state["llm"]["model_label"] == "Rule-based"


def test_offline_english_model_and_validation(client):
    headers = {"Accept-Language": "en"}
    result = client.post("/api/chat", headers=headers, json={"message": "create box 60x40x10"}).json()
    assert "Created" in result["reply"]
    assert result["model_changed"] is True
    client.post("/api/gcode/text", json={"text": "G21 G90\nG0 X0 Y0 Z5\nG0 Z-2\nM30"})
    result = client.post("/api/chat", headers=headers, json={"message": "validate G-code"}).json()
    assert "Validation" in result["reply"] and "errors" in result["reply"]
    assert "Rapid" in result["reply"]
    assert "hata" not in result["reply"] and "Satır" not in result["reply"]


def test_offline_english_generation_reply_localizes_builtin_names(client):
    headers = {"Accept-Language": "en"}
    client.post("/api/chat", headers=headers, json={"message": "create box 60x60x10"})
    reply = client.post("/api/chat", headers=headers, json={"message": "generate g-code"}).json()["reply"]
    assert "Vertical machining center — Fanuc 0i-MF style (example)" in reply
    assert "outer contour" in reply
    assert "Dik işleme" not in reply and "dış kontur" not in reply


@pytest.mark.parametrize("provider", ["openai", "deepseek", "anthropic"])
def test_mocked_provider_receives_current_language_not_history(provider, monkeypatch):
    from types import SimpleNamespace as NS
    from app.llm.agent import Agent
    from app.workspace import Workspace
    from app.i18n import locale_scope
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    ag = Agent(Workspace())
    ag.provider = provider
    requests = []
    def create(**kwargs):
        requests.append(deepcopy(kwargs))
        if provider == "anthropic":
            block = NS(model_dump=lambda **kw: {"type": "text", "text": "Mock reply"})
            return NS(content=[block], stop_reason="end_turn")
        return NS(choices=[NS(message=NS(content="Mock reply", tool_calls=None))])
    ag._client = NS(chat=NS(completions=NS(create=create)), messages=NS(create=create))
    old = {"role": "user", "content": "Değiştirme: eski Türkçe içerik"}
    ag.history = [deepcopy(old)]
    monkeypatch.setattr(ag, "_audit", lambda *a: None)
    for lang, instruction in [("en", "Respond in English"), ("tr", "Respond in Turkish")]:
        with locale_scope(lang):
            assert ag.chat("test", None)["reply"] == "Mock reply"
        prompt = requests[-1].get("system") or requests[-1]["messages"][0]["content"]
        assert instruction in prompt
    assert ag.history[0] == old
    assert not any(m["role"] == "system" for m in ag.history)


def test_tool_reports_errors_summaries_and_names(client):
    headers = {"Accept-Language": "en"}
    r = client.post("/api/tools/create_box", headers=headers, json={"length": -1, "width": 30, "height": 10}).json()
    assert r["ok"] is False and r["error"] == "Box dimensions must be positive."
    r = client.post("/api/tools/create_box", headers=headers, json={"length": 40, "width": 30, "height": 10, "name": "Sohbet bulunamadı."}).json()
    assert r["message"] == "Box/plate created."
    assert r["model"]["name"] == "Sohbet bulunamadı."
    client.post("/api/gcode/text", headers=headers, json={"text": "G0 Z-2"})
    r = client.post("/api/tools/validate_gcode", headers=headers, json={}).json()
    assert "Rapid" in next(i for i in r["issues"] if i["code"] == "E032")["message"]
    missing = client.post("/api/tools/set_machine_profile", headers=headers, json={"profile_id": "özel_ad"}).json()
    assert "Unknown machine profile: özel_ad" in missing["error"]
    r = client.post("/api/models/primitive", headers=headers, json={"type": "box", "length": -1}).json()
    assert r["detail"] == "Box dimensions must be positive."


def test_generated_code_identical_across_languages(client):
    from app import main
    client.post("/api/models/primitive", json={"type": "box", "length": 40, "width": 30, "height": 10})
    en = client.post("/api/gcode/generate", headers={"Accept-Language": "en"}, json={}).json()
    tr = client.post("/api/gcode/generate", headers={"Accept-Language": "tr"}, json={}).json()
    assert en["text"] == tr["text"]
    assert en["plan"] == tr["plan"] and en["setup"] == tr["setup"]
    assert en["validation"]["stats"] == tr["validation"]["stats"]
    # Even English generation stores the canonical Turkish report, not UI prose.
    stored = main.ws.gcodes[en["gcode_id"]]["validation"]
    assert stored["disclaimer"].startswith("Bu rapor")
    assert en["validation"]["disclaimer"].startswith("This report")


def test_request_schema_errors_follow_locale(client):
    en = client.post("/api/chat", headers={"Accept-Language": "en"}, json={"language": "xx"})
    tr = client.post("/api/chat", headers={"Accept-Language": "tr"}, json={"language": "xx"})
    assert en.status_code == tr.status_code == 422
    a, b = en.json()["detail"], tr.json()["detail"]
    assert a[0]["type"] == b[0]["type"] == "missing"
    assert a[0]["msg"] == "Field required"
    assert b[0]["msg"] == "Bu alan zorunludur"
    assert a[1]["input"] == b[1]["input"] == "xx"
    assert "tr" in b[1]["msg"] and "en" in b[1]["msg"]


def test_all_rules_have_english_presentations_and_no_data_mutation():
    from app.cam.validator import RULES, validate
    from app.validation_i18n import RULE_EN, present_report
    from app.i18n import locale_scope
    assert set(RULES) == set(RULE_EN)
    samples = [
        "G21 G90\nG0 X0 Y0 Z5\nG1 X10 Z-2 F-1\nM30",
        "G21 G90\nG0 X0 Y0 Z5\nG1 X9999 F999999 S999999 M3\nM30",
        "G21 G90\nG0 X0 Y0 Z5\nG2 X10 Y0 R1 F100\nM30",
        "G21 G90\nG0 X0 Y0 Z5\nG2 X10 Y0 I1 J0 F100\nM30",
        "G21 G90\nG81 Z-5 R-1 F100\nG80\nM30",
        "G0 G1 X1 X2\nG999\nM6 T999\nM30",
    ]
    for code in samples:
        report = validate(code)
        saved = deepcopy(report)
        with locale_scope("en"):
            en = present_report(report)
        assert report == saved
        for tr_issue, en_issue in zip(report["issues"], en["issues"]):
            assert en_issue["message"] != tr_issue["message"]
            assert en_issue["rule"] != tr_issue["rule"]
            assert en_issue["data"] == tr_issue["data"]
            assert en_issue["text"] == tr_issue["text"]


def test_workshop_summaries_and_findings_follow_locale(client):
    headers = {"Accept-Language": "en"}
    a = client.post("/api/gcode/text", json={"text": "G21 G90\nG0 X0 Y0 Z5\nG0 Z-2\nM30"}).json()
    gid = a["gcode_id"]
    state = client.post("/api/tools/get_line_state", headers=headers, json={"line": 3}).json()
    assert "rapid" in state["summary"].lower()
    assert "WARNING" in state["summary"]
    assert state["after"]["distance_mode"] == "absolute (G90)"
    assert "Rapid" in next(i for i in state["issues"] if i["code"] == "E032")["message"]
    checklist = client.post("/api/tools/prove_out_checklist", headers=headers, json={}).json()
    assert "work offset" in checklist["preparation"][0].lower()
    assert any("Rapid" in i["message"] for i in checklist["items"])
    wear = client.post("/api/tools/suggest_wear_offset", headers=headers, json={"feature": "pocket", "nominal": 10, "measured": 9.9}).json()
    assert "G41/G42" in wear["message"] and "regenerate" in wear["message"]
    b = client.post("/api/gcode/text", json={"text": "G21 G90\nG0 X0 Y0 Z5\nG0 Z-3\nM30"}).json()
    diff = client.post("/api/tools/diff_programs", headers=headers, json={"base_id": gid, "edited_id": b["gcode_id"]}).json()
    assert "lower" in diff["changes"][0]["risks"][0]
    assert diff["changes"][0]["old"] == "G0 Z-2"


def test_profile_material_and_generation_presentation(client):
    from app import main
    from app.cam.profiles import get_profile
    en_h = {"Accept-Language": "en"}
    en = client.get("/api/profiles", headers=en_h).json()
    tr = client.get("/api/profiles").json()
    for a, b in zip(en, tr):
        assert a["id"] == b["id"] and a["name"] == b["name"]
        assert a["travel"] == b["travel"]
        if a["id"] == "fanuc_vmc":
            assert "tool changer" in a["description"]
            assert a["description"] != b["description"]
            assert get_profile(a["id"]).description == b["description"]
    mat = client.get("/api/materials", headers=en_h).json()
    assert "starting values" in mat["note"]
    rec = client.post("/api/cutting/recommend", headers=en_h, json={"diameter": 0.5, "tool_type": "endmill"}).json()
    assert "machine limit" in rec["notes"][0]
    client.post("/api/models/primitive", json={"type": "box"})
    out = client.post("/api/gcode/generate", headers=en_h, json={"operations": ["contour"]}).json()
    assert any("tabs" in w for w in out["warnings"])
    assert any("köprüsü" in w for w in main.ws.gcodes[out["gcode_id"]]["warnings"])


def test_alarm_presentation_preserves_source_codes(client):
    en = client.post("/api/tools/explain_alarm", headers={"Accept-Language": "en"}, json={"controller": "grbl", "code": "error:22"}).json()
    tr = client.post("/api/tools/explain_alarm", json={"controller": "grbl", "code": "error:22"}).json()
    assert en["alarm"]["code"] == tr["alarm"]["code"] == "error:22"
    assert en["alarm"]["meaning_tr"] == en["alarm"]["description_en"]
    assert "Set F" in en["alarm"]["actions"][0]
    assert en["source"]["url"] == tr["source"]["url"]
    missing = client.post("/api/tools/explain_alarm", headers={"Accept-Language": "en"}, json={"code": "UNKNOWN"}).json()
    assert "Do not guess" in missing["note"] and missing["found"] is False


def test_request_local_rules_and_errors(client):
    def request(lang):
        headers = {"Accept-Language": lang}
        rules = client.get("/api/rules", headers=headers).json()
        error = client.get("/api/gcode/does-not-exist", headers=headers).json()
        return rules[0], error
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(request, ["en", "tr"] * 12))
    for n, (rule, error) in enumerate(results):
        assert rule["code"] == "E001"
        assert rule["title"] == ("Syntax error" if n % 2 == 0 else "Sözdizimi hatası")
        assert error["detail"] == ("G-code not found." if n % 2 == 0 else "G-code bulunamadı.")
    assert client.get("/api/rules", headers={"Accept-Language": "invalid"}).json()[0]["title"] == "Sözdizimi hatası"
    assert client.get("/api/rules", headers={"Accept-Language": "en-US,en;q=0.9"}).json()[0]["title"] == "Syntax error"


def test_default_model_names_follow_creation_language(client):
    en_box = client.post("/api/tools/create_box", json={"length": 60, "width": 40, "height": 10}, headers={"Accept-Language": "en"}).json()
    assert en_box["model"]["name"] == "Box 60x40x10"
    tr_cyl = client.post("/api/tools/create_cylinder", json={"radius": 25, "height": 8}, headers={"Accept-Language": "tr"}).json()
    assert tr_cyl["model"]["name"] == "Silindir R25 H8"
    en_cyl = client.post("/api/tools/create_cylinder", json={"radius": 25, "height": 8}, headers={"Accept-Language": "en"}).json()
    assert en_cyl["model"]["name"] == "Cylinder R25 H8"
    named = client.post("/api/tools/create_box", json={"length": 5, "width": 5, "height": 5, "name": "Flanş A"}, headers={"Accept-Language": "en"}).json()
    assert named["model"]["name"] == "Flanş A"
