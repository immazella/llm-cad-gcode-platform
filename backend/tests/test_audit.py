"""LLM audit günlüğü: kayıt içeriği, hash zinciri, MCP kaydı, dışa aktarım."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import audit
from app.workspace import Workspace


@pytest.fixture(autouse=True)
def fresh_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    yield


class FakeAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        usage = SimpleNamespace(input_tokens=100 * self.calls, output_tokens=10)
        if self.calls == 1:
            block = SimpleNamespace(model_dump=lambda **k: {"type": "tool_use", "id": "t1", "name": "create_box",
                                                            "input": {"length": 40, "width": 30, "height": 5}})
            return SimpleNamespace(id="msg_1", content=[block], stop_reason="tool_use", usage=usage)
        block = SimpleNamespace(model_dump=lambda **k: {"type": "text", "text": "Plaka oluşturuldu."})
        return SimpleNamespace(id="msg_2", content=[block], stop_reason="end_turn", usage=usage)


def test_llm_chat_is_fully_recorded():
    from app.llm.agent import Agent

    ws = Workspace()
    a = Agent(ws)
    a.provider, a.model_name, a._client = "anthropic", "test-model", FakeAnthropic()
    a.chat("40x30x5 plaka", None)
    [e] = audit.load()
    assert e["kind"] == "chat" and e["provider"] == "anthropic" and e["model"] == "test-model"
    assert e["input"]["message"] == "40x30x5 plaka" and e["input"]["context"].startswith("[BAĞLAM]")
    assert [r["stop_reason"] for r in e["rounds"]] == ["tool_use", "end_turn"]
    assert e["rounds"][0]["tool_requests"][0]["name"] == "create_box"
    assert e["usage"] == {"input_tokens": 300, "output_tokens": 20}
    assert e["tool_calls"][0]["ok"] and e["tool_calls"][0]["result"]["ok"]
    assert e["output"]["reply"] == "Plaka oluşturuldu."
    exp = audit.export()
    assert e["system_prompt_sha256"] in exp["system_prompts"]


def test_api_error_is_recorded():
    from app.llm.agent import Agent

    class Boom:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise RuntimeError("bağlantı yok")

    ws = Workspace()
    a = Agent(ws)
    a.provider, a.model_name, a._client = "anthropic", "m", Boom()
    a.chat("merhaba", None)
    assert "bağlantı yok" in audit.load()[0]["output"]["error"]


def test_chain_detects_tampering_and_deletion():
    for i in range(3):
        audit.record({"kind": "chat", "input": {"message": f"m{i}"}, "output": {"reply": "r"}})
    assert audit.verify()["ok"]
    p = audit.audit_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")  # ortadaki kayıt silindi
    v = audit.verify()
    assert not v["ok"] and v["broken_at_index"] == 2


def test_api_mcp_calls_logged_ui_calls_not_and_export():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    c.post("/api/session/reset")
    c.post("/api/llm/mode", json={"offline": True})
    c.post("/api/chat", json={"message": "60x40x10 plaka oluştur"})
    c.post("/api/tools/get_holes", json={}, headers={"x-client": "mcp"})
    c.post("/api/tools/get_holes", json={})  # arayüzün doğrudan çağrısı: LLM değil, kaydedilmez
    r = c.get("/api/audit").json()
    assert r["total"] == 2 and r["chain"]["ok"] and [e["kind"] for e in r["entries"]] == ["mcp_tool", "chat"]
    assert c.get("/api/audit", params={"kind": "chat"}).json()["total"] == 1
    one = r["entries"][1]["id"]
    ex = c.post("/api/audit/export", json={"ids": [one]})
    assert "attachment" in ex.headers["content-disposition"]
    data = json.loads(ex.content)
    assert data["count"] == 1 and data["entries"][0]["id"] == one and data["chain"]["ok"]
    assert c.post("/api/audit/export", json={}).json()["count"] == 2
    c.post("/api/conversations")
    assert c.get("/api/audit").json()["total"] == 2  # yeni sohbet audit'i silmez
    c.post("/api/session/reset")
    assert c.get("/api/audit").json()["total"] == 0  # yeni oturum siler


def test_session_reset_clears_audit():
    from fastapi.testclient import TestClient

    from app import audit
    from app.main import app

    audit.record({"kind": "chat", "input": {"message": "silinecek"}})
    assert audit.load()
    c = TestClient(app)
    r = c.post("/api/session/reset").json()
    assert r["ok"] and r["audit_removed"] >= 1
    assert audit.load() == [] and audit.verify()["ok"]
    e = audit.record({"kind": "chat", "input": {"message": "yeni"}})
    assert e["seq"] == 1 and e["prev_hash"] == audit.GENESIS  # zincir baştan başlar
