"""Birden fazla sohbet: ayrı LLM geçmişi ve mesajlar, geçiş, silme, oturum kalıcılığı."""
from __future__ import annotations

from fastapi.testclient import TestClient


def client():
    from app.main import app

    c = TestClient(app)
    c.post("/api/session/reset")
    c.post("/api/llm/mode", json={"offline": True})
    return c


def test_conversations_keep_separate_history_and_messages():
    from app.main import agent

    c = client()
    first = c.get("/api/state").json()["ui"]["conversation"]["id"]
    c.post("/api/chat", json={"message": "60x40x10 plaka oluştur", "conversation_id": first})
    c.put("/api/ui", json={"messages": [{"role": "user", "text": "60x40x10 plaka oluştur"}, {"role": "assistant", "text": "tamam"}],
                           "conversation_id": first})
    agent.history.append({"role": "user", "content": "A sohbeti"})  # LLM geçmişi işareti
    r = c.post("/api/conversations").json()
    second = r["conversation"]["id"]
    assert second != first and r["messages"] == [] and agent.history == []
    # boş sohbet açıkken "yeni sohbet" çoğaltmaz
    assert c.post("/api/conversations").json()["conversation"]["id"] == second
    c.post("/api/chat", json={"message": "G-code üret", "conversation_id": second})
    lst = c.get("/api/conversations").json()["conversations"]
    assert [x["id"] for x in lst] == [second, first] and lst[1]["title"] == "60x40x10 plaka oluştur"
    back = c.post(f"/api/conversations/{first}/activate").json()
    assert len(back["messages"]) == 2 and {"role": "user", "content": "A sohbeti"} in agent.history


def test_put_ui_goes_to_named_conversation_even_after_switch():
    c = client()
    a = c.get("/api/state").json()["ui"]["conversation"]["id"]
    c.post("/api/chat", json={"message": "merhaba", "conversation_id": a})
    b = c.post("/api/conversations").json()["conversation"]["id"]
    c.put("/api/ui", json={"messages": [{"role": "user", "text": "geç kalan"}], "conversation_id": a})  # gecikmiş kayıt
    assert c.get("/api/state").json()["ui"]["messages"] == []  # aktif (b) etkilenmedi
    assert c.post(f"/api/conversations/{a}/activate").json()["messages"][0]["text"] == "geç kalan"
    assert c.get("/api/state").json()["ui"]["conversation"]["id"] == a
    del b


def test_rename_delete_and_chat_switches_to_given_conversation():
    from app.main import agent

    c = client()
    a = c.get("/api/state").json()["ui"]["conversation"]["id"]
    c.post("/api/chat", json={"message": "bir", "conversation_id": a})
    b = c.post("/api/conversations").json()["conversation"]["id"]
    c.patch(f"/api/conversations/{a}", json={"title": "  Flanş   işi "})
    assert next(x for x in c.get("/api/conversations").json()["conversations"] if x["id"] == a)["title"] == "Flanş işi"
    c.post("/api/chat", json={"message": "iki", "conversation_id": a})  # başka sekmeden a'ya yazıldı
    assert agent.active_id == a
    r = c.delete(f"/api/conversations/{a}").json()
    assert r["conversation"]["id"] == b and [x["id"] for x in r["conversations"]] == [b]
    r = c.delete(f"/api/conversations/{b}").json()  # sonuncusu silinince boş yeni sohbet
    assert r["messages"] == [] and len(r["conversations"]) == 1
    assert c.delete("/api/conversations/yok").status_code == 404


def test_conversations_survive_session_roundtrip():
    from app import session
    from app.llm.agent import Agent
    from app.workspace import Workspace

    ws = Workspace()
    ag = Agent(ws)
    ag.active["messages"] = [{"role": "user", "text": "ilk"}]
    ag.history.append({"role": "user", "content": "ilk"})
    ag.new_conversation()
    ag.history.append({"role": "user", "content": "ikinci"})
    session.save(ws, ag, {})
    ws2 = Workspace()
    ag2 = Agent(ws2)
    assert session.load(ws2, ag2, {})
    assert len(ag2.conversations) == 2 and ag2.history == [{"role": "user", "content": "ikinci"}]
    other = next(cid for cid in ag2.conversations if cid != ag2.active_id)
    ag2.activate(other)
    assert ag2.history == [{"role": "user", "content": "ilk"}]
    session.clear()


def test_old_single_chat_session_is_migrated(tmp_path, monkeypatch):
    import pickle

    from app import session
    from app.llm.agent import Agent
    from app.workspace import Workspace

    ws = Workspace()
    data = {"v": session.SESSION_VERSION, "model": None, "gcodes": {}, "current_gcode": None, "config": ws.config.model_dump(),
            "agent": {"history": [{"role": "user", "content": "eski"}], "offline": False},
            "ui": {"messages": [{"role": "user", "text": "eski mesaj"}], "tab": "model"}}
    p = tmp_path / "s.pkl"
    p.write_bytes(pickle.dumps(data))
    monkeypatch.setenv("SESSION_FILE", str(p))
    ag = Agent(ws)
    ui: dict = {}
    assert session.load(ws, ag, ui)
    assert "messages" not in ui and ag.active["messages"][0]["text"] == "eski mesaj"
    assert ag.active["title"] == "eski mesaj" and ag.history[0]["content"] == "eski"
