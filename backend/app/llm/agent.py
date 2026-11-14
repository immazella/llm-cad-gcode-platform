"""LLM orkestratörü: tool calling döngüsü.

Sağlayıcılar:
- anthropic : Claude (ANTHROPIC_API_KEY)
- openai    : OpenAI ve OpenAI uyumlu uç noktalar (OPENAI_API_KEY, isteğe bağlı OPENAI_BASE_URL:
              Gemini, Groq, OpenRouter, yerel Ollama vb.)
- deepseek  : DeepSeek (DEEPSEEK_API_KEY, OpenAI uyumlu uç nokta https://api.deepseek.com)
- offline   : API anahtarı yoksa basit kural tabanlı Türkçe ayrıştırıcı (demo / test için)
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from .. import audit
from ..i18n import tr, language
from ..workspace import GCODE_TOOLS, MODEL_TOOLS, Workspace, run_tool
from .offline import OfflineInterpreter
from .prompts import SYSTEM_PROMPT
from .tool_schemas import TOOL_SCHEMAS

def response_system_prompt() -> str:
    # Replace the legacy language policy, not the CAD/CAM instructions. Never store
    # a language instruction in conversation history: each new turn can switch.
    base = SYSTEM_PROMPT.replace("Kullanıcıyla Türkçe konuş (kullanıcı başka dilde yazarsa o dilde yanıt ver).", "")
    instruction = "Respond in English" if language() == "en" else "Respond in Turkish"
    return base + "\n\nRESPONSE LANGUAGE: " + instruction + (
        " for this turn, regardless of the language of earlier messages or tool descriptions. "
        "Explain validation findings in this language. Preserve G-code, identifiers, numeric values, "
        "filenames, user-supplied names and quoted source content exactly. Do not rewrite prior conversation history.")


MAX_TOOL_ROUNDS = 8
MAX_HISTORY = 40


def detect_provider() -> tuple[str, str]:
    p = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not p:
        if os.getenv("ANTHROPIC_API_KEY"):
            p = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            p = "openai"
        elif os.getenv("DEEPSEEK_API_KEY"):
            p = "deepseek"
        else:
            p = "offline"
    model = {
        "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        "openai": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "offline": "kural-tabanlı",
    }.get(p, "?")
    return p, model


class Agent:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.provider, self.model_name = detect_provider()
        # .env'den gelen sağlayıcı; çevrimdışı mod kapatılınca buna geri dönülür
        self.configured_provider, self.configured_model = self.provider, self.model_name
        self.history: list[dict[str, Any]] = []
        self._client = None
        self._offline = OfflineInterpreter()
        self._rounds: list[dict[str, Any]] = []  # audit: o anki sohbet isteğinin LLM turları
        # Birden fazla sohbet: her birinin ekranda görünen mesajları ve LLM geçmişi ayrı tutulur.
        # self.history her zaman AKTİF sohbetin geçmişidir.
        self.conversations: dict[str, dict[str, Any]] = {}
        self.active_id: str | None = None
        self.ensure_conversation()

    @property
    def llm_available(self) -> bool:
        return self.configured_provider != "offline"

    def set_offline(self, offline: bool) -> None:
        """Çevrimdışı (kural tabanlı) modu çalışırken açar/kapatır."""
        if offline or not self.llm_available:
            self.provider, self.model_name = "offline", "kural-tabanlı"
        else:
            self.provider, self.model_name = self.configured_provider, self.configured_model

    def info(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model_name, "available": self.llm_available,
                "configured_provider": self.configured_provider, "configured_model": self.configured_model,
                "model_label": tr("Kural tabanlı", "Rule-based") if self.provider == "offline" else self.model_name,
                "status_label": tr("Çevrimdışı", "Offline") if self.provider == "offline" else tr("Çevrimiçi", "Online")}

    def reset(self) -> None:
        """Aktif sohbeti boşaltır (mesajlar + LLM geçmişi)."""
        self.history = []
        self.ensure_conversation()
        c = self.active
        c["history"], c["messages"], c["title"] = self.history, [], None

    # ------------------------------------------------------------------ sohbetler
    @staticmethod
    def _new_conv() -> dict[str, Any]:
        now = audit.now_iso()
        return {"id": uuid.uuid4().hex[:10], "title": None, "created": now, "updated": now, "messages": [], "history": []}

    def ensure_conversation(self) -> None:
        if self.active_id not in self.conversations:
            c = self._new_conv()
            c["history"] = self.history
            self.conversations[c["id"]] = c
            self.active_id = c["id"]

    @property
    def active(self) -> dict[str, Any]:
        self.ensure_conversation()
        return self.conversations[self.active_id]

    def stash(self) -> None:
        """Aktif sohbetin LLM geçmişini sohbet kaydına yazar (geçiş ve kayıt öncesi)."""
        self.active["history"] = self.history

    @staticmethod
    def conv_summary(c: dict[str, Any], active: bool = False) -> dict[str, Any]:
        users = [m for m in c["messages"] if m.get("role") == "user"]
        return {"id": c["id"], "title": c["title"] or tr("Yeni sohbet", "New conversation"), "titled": bool(c["title"]), "created": c["created"],
                "updated": c["updated"], "count": len(c["messages"]), "user_count": len(users), "active": active,
                "preview": (users[-1]["text"][:80] if users else "")}

    def list_conversations(self) -> list[dict[str, Any]]:
        self.ensure_conversation()
        cs = sorted(self.conversations.values(), key=lambda c: c["updated"], reverse=True)
        return [self.conv_summary(c, c["id"] == self.active_id) for c in cs]

    def new_conversation(self) -> dict[str, Any]:
        self.stash()
        cur = self.active
        if not cur["messages"] and not cur["history"] and not cur["title"]:
            return cur  # zaten boş bir sohbet açık: yenisini çoğaltma
        c = self._new_conv()
        self.conversations[c["id"]] = c
        self.active_id, self.history = c["id"], c["history"]
        return c

    def activate(self, cid: str) -> dict[str, Any]:
        if cid not in self.conversations:
            raise KeyError(cid)
        self.stash()
        self.active_id = cid
        self.history = self.conversations[cid]["history"]
        return self.conversations[cid]

    def rename_conversation(self, cid: str, title: str) -> dict[str, Any]:
        c = self.conversations[cid]
        c["title"] = " ".join(title.split())[:80] or None
        return c

    def delete_conversation(self, cid: str) -> dict[str, Any]:
        """Sohbeti siler; aktifse en son güncellenen diğerine (yoksa yeni boş sohbete) geçer. Yeni aktif sohbeti döndürür."""
        if cid not in self.conversations:
            raise KeyError(cid)
        was_active = cid == self.active_id
        del self.conversations[cid]
        if was_active:
            rest = sorted(self.conversations.values(), key=lambda c: c["updated"], reverse=True)
            if rest:
                self.active_id, self.history = rest[0]["id"], rest[0]["history"]
            else:
                self.active_id, self.history = None, []
                self.ensure_conversation()
        return self.active

    def clear_conversations(self) -> None:
        self.conversations, self.active_id, self.history = {}, None, []
        self.ensure_conversation()

    @staticmethod
    def title_from(message: str) -> str:
        t = re.sub(r"\s+", " ", message).strip()
        if len(t) <= 48:
            return t
        cut = t[:48].rsplit(" ", 1)[0]
        return (cut if len(cut) > 20 else t[:48]) + "…"

    # ------------------------------------------------------------------ ortak
    def _exec(self, name: str, args: dict, actions: list[dict]) -> dict:
        t = audit.Timer()
        result = run_tool(self.ws, name, args)
        ok = result.get("ok", True) is not False
        actions.append({"tool": name, "args": args, "ok": ok, "error": None if ok else result.get("error"), "result": result,
                        "duration_ms": t.ms()})
        return result

    def _trim(self) -> None:
        if len(self.history) <= MAX_HISTORY:
            return
        h = self.history[-MAX_HISTORY:]
        # geçmiş, araç sonucu ile başlamamalı (tool_use / tool_result çiftleri bozulmasın)
        while h and not self._is_plain_user(h[0]):
            h.pop(0)
        self.history = h

    @staticmethod
    def _is_plain_user(msg: dict) -> bool:
        if msg.get("role") != "user":
            return False
        c = msg.get("content")
        return isinstance(c, str) or (isinstance(c, list) and all(b.get("type") == "text" for b in c))

    def chat(self, message: str, selection: dict | list[dict] | None) -> dict[str, Any]:
        actions: list[dict] = []
        ctx = self.ws.context(selection)
        timer = audit.Timer()
        self._rounds = []
        provider, model_name = self.provider, self.model_name
        error = None
        try:
            if self.provider == "anthropic":
                reply = self._chat_anthropic(ctx + message, actions)
            elif self.provider in ("openai", "deepseek"):
                reply = self._chat_openai(ctx + message, actions)
            else:
                reply = self._offline.run(message, selection, lambda n, a: self._exec(n, a, actions), self.ws)
        except Exception as exc:  # API hatası: kullanıcıya göster
            error = f"{type(exc).__name__}: {exc}"
            reply = tr(f"LLM servisine erişilemedi ({error}). .env dosyasındaki API anahtarını ve model adını kontrol edin.",
                       f"Could not reach the LLM service ({error}). Check the API key and model name in .env.")
        c = self.active
        if not c["title"]:
            c["title"] = self.title_from(message)
        c["updated"] = audit.now_iso()
        self._audit(message, selection, ctx, provider, model_name, actions, reply, error, timer.ms())
        self._trim()
        c["history"] = self.history  # _trim listeyi yeniden oluşturabilir
        model_changed = any(a["ok"] and a["tool"] in MODEL_TOOLS for a in actions)
        if model_changed:
            self.ws.mark_stale()
        return {
            "reply": reply,
            "actions": [{k: v for k, v in a.items() if k != "result"} for a in actions],
            "model_changed": model_changed,
            "gcode_changed": any(a["ok"] and a["tool"] in GCODE_TOOLS for a in actions),
        }

    def _audit(self, message, selection, ctx, provider, model_name, actions, reply, error, ms) -> None:
        sels = selection if isinstance(selection, list) else [selection] if selection else []
        usage: dict[str, int] = {}
        for r in self._rounds:
            for k, v in (r.get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
        m = self.ws.model
        audit.safe_record({
            "kind": "chat", "client": "web", "provider": provider, "model": model_name,
            "conversation": {"id": self.active_id, "title": self.active.get("title")},
            "system_prompt_sha256": audit.register_prompt(response_system_prompt()) if provider != "offline" else None,
            "tools_sha256": audit.sha(json.dumps(TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False))[:16],
            "state": {"model_id": m.id if m else None, "model_version": m.version if m else None,
                      "gcode_id": self.ws.current_gcode, "machine_profile": self.ws.config.profile_id},
            "input": {"message": message,
                      "selection": [s.get("region_id") or s.get("face_id") for s in sels if s],
                      "context": ctx},
            "rounds": self._rounds,
            "tool_calls": [{"name": a["tool"], "args": a["args"], "ok": a["ok"], "error": a["error"],
                            "duration_ms": a.get("duration_ms"), "result": audit.preview(a.get("result"))} for a in actions],
            "output": {"reply": reply, "error": error},
            "duration_ms": ms, "usage": usage or None,
        })

    # ------------------------------------------------------------------ Anthropic
    def _chat_anthropic(self, text: str, actions: list[dict]) -> str:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOL_SCHEMAS]
        self.history.append({"role": "user", "content": text})
        final_text = ""
        for i in range(MAX_TOOL_ROUNDS):
            t = audit.Timer()
            resp = self._client.messages.create(model=self.model_name, max_tokens=2048, system=response_system_prompt(),
                                                tools=tools, messages=self.history)
            content = [b.model_dump(exclude_none=True) for b in resp.content]
            content = [{k: v for k, v in b.items() if k in ("type", "text", "id", "name", "input")} for b in content]
            u = getattr(resp, "usage", None)
            self._rounds.append({
                "round": i + 1, "latency_ms": t.ms(), "stop_reason": getattr(resp, "stop_reason", None),
                "response_id": getattr(resp, "id", None),
                "usage": {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None)} if u else None,
                "text": "\n".join(b["text"] for b in content if b["type"] == "text"),
                "tool_requests": [{"id": b.get("id"), "name": b.get("name"), "input": b.get("input")} for b in content if b["type"] == "tool_use"],
            })
            self.history.append({"role": "assistant", "content": content})
            texts = [b["text"] for b in content if b["type"] == "text"]
            if texts:
                final_text = "\n".join(texts)
            uses = [b for b in content if b["type"] == "tool_use"]
            if resp.stop_reason != "tool_use" or not uses:
                return final_text
            results = []
            for u in uses:
                r = self._exec(u["name"], u.get("input") or {}, actions)
                results.append({"type": "tool_result", "tool_use_id": u["id"],
                                "content": json.dumps(r, ensure_ascii=False, default=str)[:12000],
                                **({"is_error": True} if r.get("ok") is False else {})})
            self.history.append({"role": "user", "content": results})
        return final_text or tr("İşlem çok fazla adım gerektirdi; lütfen isteği parçalara bölün.", "This operation required too many steps; please split the request into smaller parts.")

    # ------------------------------------------------------------------ OpenAI uyumlu
    def _chat_openai(self, text: str, actions: list[dict]) -> str:
        import openai

        if self._client is None:
            if self.provider == "deepseek":
                self._client = openai.OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                                             base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
            else:
                self._client = openai.OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
        tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in TOOL_SCHEMAS]
        # Düşünme modundaki modeller (ör. deepseek-reasoner) reasoning_content'i yalnızca aynı tur içindeki
        # araç çağrılarında geri ister; önceki turlardakini göndermiyoruz.
        for m in self.history:
            m.pop("reasoning_content", None)
        self.history.append({"role": "user", "content": text})
        for i in range(MAX_TOOL_ROUNDS):
            t = audit.Timer()
            resp = self._client.chat.completions.create(model=self.model_name, tools=tools,
                                                        messages=[{"role": "system", "content": response_system_prompt()}, *self.history])
            msg = resp.choices[0].message
            u = getattr(resp, "usage", None)
            self._rounds.append({
                "round": i + 1, "latency_ms": t.ms(), "stop_reason": getattr(resp.choices[0], "finish_reason", None),
                "response_id": getattr(resp, "id", None),
                "usage": {"input_tokens": getattr(u, "prompt_tokens", None), "output_tokens": getattr(u, "completion_tokens", None)} if u else None,
                "text": msg.content or "",
                "reasoning": (getattr(msg, "reasoning_content", None) or None),
                "tool_requests": [{"id": tc.id, "name": tc.function.name, "input": tc.function.arguments} for tc in (msg.tool_calls or [])],
            })
            entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                entry["reasoning_content"] = reasoning
            if msg.tool_calls:
                entry["tool_calls"] = [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]
            self.history.append(entry)
            if not msg.tool_calls:
                return msg.content or ""
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                r = self._exec(tc.function.name, args, actions)
                self.history.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(r, ensure_ascii=False, default=str)[:12000]})
        return tr("İşlem çok fazla adım gerektirdi; lütfen isteği parçalara bölün.", "This operation required too many steps; please split the request into smaller parts.")
