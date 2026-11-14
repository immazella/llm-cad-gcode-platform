"""Oturum kalıcılığı: backend yeniden başlasa da (ör. --reload) model, geri alma geçmişi,
G-code kayıtları, işleme ayarları, LLM sohbet geçmişi, LLM modu ve arayüz durumu (sohbet
mesajları, açık sekmeler) kaldığı yerden devam eder.

Kayıt: backend/data/session.pkl. CAD şekilleri (OCP nesneleri) doğrudan pickle'lanamadığından
yalnızca içe aktarılmış/pişirilmiş taban şekiller BREP baytları olarak saklanır; parametrik
modeller taban + özellik listesinden yeniden kurulur.
"""
from __future__ import annotations

import copy
import io
import logging
import os
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cadquery as cq

from .cad.model import CadModel

if TYPE_CHECKING:
    from .llm.agent import Agent
    from .workspace import Workspace

log = logging.getLogger(__name__)
SESSION_VERSION = 1


def session_path() -> Path:
    from .workspace import DATA_DIR

    return Path(os.getenv("SESSION_FILE") or DATA_DIR / "session.pkl")


# ---------------------------------------------------------------- CAD modeli
def _shape_to_bytes(shape: cq.Shape | None, cache: dict[int, bytes]) -> bytes | None:
    if shape is None:
        return None
    key = id(shape)
    if key not in cache:
        buf = io.BytesIO()
        shape.exportBrep(buf)
        cache[key] = buf.getvalue()
    return cache[key]


def _shape_from_bytes(data: bytes | None, cache: dict[bytes, cq.Shape]) -> cq.Shape | None:
    if data is None:
        return None
    if data not in cache:
        cache[data] = cq.Shape.importBrep(io.BytesIO(data))
    return cache[data]


def model_to_state(m: CadModel) -> dict[str, Any]:
    shapes: dict[int, bytes] = {}
    return {
        "id": m.id, "name": m.name, "kind": m.kind, "version": m.version,
        "base": copy.deepcopy(m.base), "features": copy.deepcopy(m.features),
        "base_shape": _shape_to_bytes(m.base_shape, shapes),
        "log": list(m.log),
        "regions": copy.deepcopy(getattr(m, "regions", {})), "region_seq": getattr(m, "region_seq", 0),
        "history": [(copy.deepcopy(b), copy.deepcopy(f), _shape_to_bytes(s, shapes), k) for b, f, s, k in m.history],
    }


def model_from_state(st: dict[str, Any]) -> CadModel:
    shapes: dict[bytes, cq.Shape] = {}
    m = CadModel.__new__(CadModel)
    m.id, m.name, m.kind = st["id"], st["name"], st["kind"]
    m.base, m.features = st["base"], st["features"]
    m.base_shape = _shape_from_bytes(st["base_shape"], shapes)
    m.counters, m.log, m.version = {}, list(st["log"]), 0
    m.regions, m.region_seq = dict(st.get("regions") or {}), int(st.get("region_seq", 0))
    m.history = [(b, f, _shape_from_bytes(s, shapes), k) for b, f, s, k in st["history"]]
    m.rebuild()
    # Sürüm numarası korunur ki eski G-code kayıtlarının "güncel mi" kontrolü doğru çalışsın
    m.version = st["version"]
    return m


# ---------------------------------------------------------------- kaydet / yükle
def save(ws: Workspace, agent: Agent, ui: dict[str, Any]) -> None:
    if hasattr(agent, "stash"):
        agent.stash()
    data = {
        "v": SESSION_VERSION,
        "model": model_to_state(ws.model) if ws.model else None,
        "gcodes": ws.gcodes,
        "current_gcode": ws.current_gcode,
        "config": ws.config.model_dump(),
        "agent": {"history": agent.history, "offline": agent.provider == "offline" and agent.llm_available,
                  "conversations": getattr(agent, "conversations", None), "active_id": getattr(agent, "active_id", None)},
        "ui": ui,
    }
    path = session_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)  # yarım yazılmış dosya kalmasın


def load(ws: Workspace, agent: Agent, ui: dict[str, Any]) -> bool:
    """Kayıtlı oturumu yükler; dosya yok/bozuksa boş oturumla devam eder."""
    from .cam.config import MachiningConfig

    path = session_path()
    if not path.exists():
        return False
    try:
        with open(path, "rb") as fh:
            data = pickle.load(fh)
        if data.get("v") != SESSION_VERSION:
            return False
        ws.config = MachiningConfig.model_validate(data["config"])
        ws.gcodes = data["gcodes"]
        ws.current_gcode = data["current_gcode"]
        ws.model = model_from_state(data["model"]) if data["model"] else None
        agent.history = data["agent"]["history"]
        if data["agent"]["offline"]:
            agent.set_offline(True)
        ui.clear()
        ui.update(data.get("ui") or {})
        convs, active = data["agent"].get("conversations"), data["agent"].get("active_id")
        old_messages = ui.pop("messages", None)
        if convs:
            agent.conversations, agent.active_id = convs, active
            if active in convs:
                agent.history = convs[active]["history"]
        elif hasattr(agent, "ensure_conversation"):
            # eski oturum biçimi: tek sohbet → ilk sohbet kaydı
            agent.conversations, agent.active_id = {}, None
            agent.ensure_conversation()
            agent.active["messages"] = old_messages or []
            first = next((m["text"] for m in agent.active["messages"] if m.get("role") == "user"), None)
            if first:
                agent.active["title"] = agent.title_from(first)
        return True
    except Exception:  # bozuk / uyumsuz kayıt: uygulamayı kilitlemesin
        log.exception("Oturum yüklenemedi; boş oturumla başlanıyor (%s)", path)
        ws.model, ws.gcodes, ws.current_gcode = None, {}, None
        agent.history = []
        if hasattr(agent, "clear_conversations"):
            agent.clear_conversations()
        ui.clear()
        return False


def clear() -> None:
    session_path().unlink(missing_ok=True)
