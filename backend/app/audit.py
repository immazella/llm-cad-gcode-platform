"""LLM audit günlüğü: asistanla yapılan her etkileşimin değiştirilemez kaydı.

Ne kaydedilir
- Web sohbeti (kind="chat"): kullanıcı mesajı, seçili elemanlar, LLM'e giden [BAĞLAM] bloğu, sağlayıcı/model,
  her LLM turunun cevabı (metin, araç çağrıları, stop_reason, token kullanımı, gecikme), çalıştırılan her aracın
  argümanları + sonucu + süresi, son cevap ve hata.
- MCP istemcilerinin (Claude Desktop, Cursor, ChatGPT…) çalıştırdığı araçlar (kind="mcp_tool").
Sistem istemi her kayıtta tekrarlanmaz; SHA-256 özeti yazılır, tam metni bir kez `audit_prompts.json`'a konur
ve dışa aktarımda eklenir.

Bütünlük
- Dosya yalnızca sonuna eklenir (JSON Lines: `backend/data/audit.jsonl`). "Yeni sohbet" onu silmez; "Yeni oturum"
  (POST /api/session/reset) tüm kayıtları ve sistem istemi arşivini siler (clear()), zincir baştan başlar.
- Her kayıt bir önceki kaydın özetini (prev_hash) taşır ve kendi içeriğinin SHA-256 özetiyle (hash) kapanır.
  Arada bir satır değiştirilir veya silinirse zincir kırılır; `verify()` hangi kayıtta kırıldığını söyler.
- Ekleme sırasında dosya kilitlenir (POSIX'te fcntl); web backend'i ve yerel MCP süreci aynı dosyaya güvenle yazar.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Windows'ta fcntl yok: o zaman yalnızca süreç içi kilit kullanılır
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

GENESIS = "0" * 64
PREVIEW_CHARS = 4000
_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "entries": []}


def audit_path() -> Path:
    from .workspace import DATA_DIR

    return Path(os.getenv("AUDIT_FILE") or DATA_DIR / "audit.jsonl")


def _prompts_path() -> Path:
    return audit_path().with_name(audit_path().stem + "_prompts.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def preview(obj: Any, limit: int = PREVIEW_CHARS) -> Any:
    """Büyük araç sonuçlarını kısaltır (ör. mesh); küçükleri olduğu gibi bırakır."""
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    if len(text) <= limit:
        return obj
    return {"_kısaltıldı": True, "boyut": len(text), "baş": text[:limit]}


def register_prompt(text: str) -> str:
    """Sistem istemini özetine göre bir kez saklar; özeti döndürür."""
    h = sha(text)
    p = _prompts_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    if h not in data:
        data[h] = {"first_seen": now_iso(), "text": text}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    return h


def _last_line(fh) -> str | None:
    fh.seek(0, os.SEEK_END)
    end = fh.tell()
    if end == 0:
        return None
    pos, buf = end, b""
    while pos > 0:
        step = min(4096, pos)
        pos -= step
        fh.seek(pos)
        buf = fh.read(step) + buf
        if buf.rstrip(b"\n").count(b"\n") >= 1:
            break
    lines = [l for l in buf.split(b"\n") if l.strip()]
    return lines[-1].decode("utf-8") if lines else None


def record(entry: dict[str, Any]) -> dict[str, Any]:
    """Kaydı zincire ekler. Hata olursa uygulamayı durdurmaz (audit en iyi çaba ile; hata loglanır)."""
    entry = {"id": uuid.uuid4().hex[:12], "ts": now_iso(), **entry}
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(path, "a+b") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                last = _last_line(fh)
                prev = json.loads(last) if last else None
                entry["seq"] = (prev["seq"] + 1) if prev else 1
                entry["prev_hash"] = prev["hash"] if prev else GENESIS
                entry["hash"] = sha(_canonical(entry))
                fh.seek(0, os.SEEK_END)
                fh.write((json.dumps(entry, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return entry


def clear() -> int:
    """Tüm audit kayıtlarını siler (Yeni oturum). Silinen kayıt sayısını döndürür.

    Dosya silinmek yerine kilit altında sıfırlanır: aynı anda yazan bir MCP süreci yarım satır bırakamaz.
    """
    path = audit_path()
    n = 0
    with _lock:
        if path.exists():
            with open(path, "r+b") as fh:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    n = sum(1 for line in fh if line.strip())
                    fh.seek(0)
                    fh.truncate(0)
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        _prompts_path().unlink(missing_ok=True)
        _cache.update(key=None, entries=[])
    return n


def safe_record(entry: dict[str, Any]) -> dict[str, Any] | None:
    import logging

    try:
        return record(entry)
    except Exception:  # audit yazılamasa da sohbet çalışmaya devam etsin
        logging.getLogger(__name__).exception("Audit kaydı yazılamadı")
        return None


def load() -> list[dict[str, Any]]:
    path = audit_path()
    if not path.exists():
        return []
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _cache["key"] == key:
        return _cache["entries"]
    entries = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                entries.append({"id": f"bozuk-satır-{i}", "seq": None, "kind": "corrupt", "raw": line[:500]})
    _cache.update(key=key, entries=entries)
    return entries


def verify(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    entries = load() if entries is None else entries
    prev = GENESIS
    for i, e in enumerate(entries):
        if e.get("kind") == "corrupt" or e.get("prev_hash") != prev or sha(_canonical(e)) != e.get("hash"):
            return {"ok": False, "count": len(entries), "broken_at_seq": e.get("seq"), "broken_at_index": i + 1,
                    "message": f"Zincir {i + 1}. kayıtta kırık: kayıt değiştirilmiş, silinmiş veya araya eklenmiş."}
        prev = e["hash"]
    return {"ok": True, "count": len(entries), "last_hash": prev, "message": "Kayıt zinciri bozulmamış."}


def summary(e: dict[str, Any]) -> dict[str, Any]:
    inp, out = e.get("input") or {}, e.get("output") or {}
    calls = e.get("tool_calls") or []
    return {
        "id": e.get("id"), "seq": e.get("seq"), "ts": e.get("ts"), "kind": e.get("kind"), "client": e.get("client"),
        "provider": e.get("provider"), "model": e.get("model"),
        "message": (inp.get("message") or e.get("tool") or "")[:240],
        "reply": (out.get("reply") or "")[:240], "error": out.get("error"),
        "tool_count": len(calls), "tool_fail": sum(1 for c in calls if not c.get("ok")),
        "tools": [c.get("name") for c in calls][:12],
        "duration_ms": e.get("duration_ms"), "usage": e.get("usage"), "rounds": len(e.get("rounds") or []),
    }


def query(q: str = "", kind: str | None = None, offset: int = 0, limit: int = 100, ids_only: bool = False) -> dict[str, Any]:
    entries = load()
    rows = list(reversed(entries))
    if kind:
        rows = [e for e in rows if e.get("kind") == kind]
    if q:
        ql = q.lower()
        rows = [e for e in rows if ql in json.dumps(e, ensure_ascii=False, default=str).lower()]
    total = len(rows)
    if ids_only:  # "filtreye uyanların hepsini seç" için
        return {"total": total, "ids": [e.get("id") for e in rows]}
    return {"total": total, "all": len(entries), "offset": offset,
            "entries": [summary(e) for e in rows[offset: offset + limit]], "chain": verify(entries)}


def get(entry_id: str) -> dict[str, Any] | None:
    return next((e for e in load() if e.get("id") == entry_id), None)


def export(ids: list[str] | None = None) -> dict[str, Any]:
    entries = load()
    chain = verify(entries)
    chosen = entries if not ids else [e for e in entries if e.get("id") in set(ids)]
    prompts_used = {e.get("system_prompt_sha256") for e in chosen} - {None}
    try:
        prompts = json.loads(_prompts_path().read_text(encoding="utf-8")) if _prompts_path().exists() else {}
    except (OSError, ValueError):
        prompts = {}
    return {
        "format": "busproje-llm-audit/1",
        "exported_at": now_iso(),
        "selection": "seçili kayıtlar" if ids else "tüm kayıtlar",
        "count": len(chosen),
        "chain": chain,
        "note": ("Her kaydın 'hash' alanı, 'hash' dışındaki alanların sıralı-anahtarlı kompakt JSON'unun SHA-256 özetidir; "
                 "'prev_hash' bir önceki kaydın hash'idir. Seçili dışa aktarımda araya giren kayıtlar olmadığı için zincir "
                 "yalnızca tüm kayıtlarla doğrulanabilir; 'chain' alanı dışa aktarma anındaki tam doğrulama sonucudur."),
        "system_prompts": {h: prompts[h] for h in prompts_used if h in prompts},
        "entries": chosen,
    }


def mcp_tool(w: Any, name: str, args: dict[str, Any], res: dict[str, Any], ms: int, via: str) -> None:
    """MCP istemcisinin (Claude Desktop, Cursor, ChatGPT…) çalıştırdığı aracı kaydeder. via: http | local"""
    ok = res.get("ok", True) is not False
    m = w.model
    safe_record({
        "kind": "mcp_tool", "client": f"mcp:{via}", "provider": "mcp-istemcisi", "model": None, "tool": name,
        "state": {"model_id": m.id if m else None, "model_version": m.version if m else None, "gcode_id": w.current_gcode,
                  "machine_profile": w.config.profile_id},
        "input": {"message": f"{name}({', '.join(f'{k}={v!r}' for k, v in (args or {}).items())})"[:2000]},
        "tool_calls": [{"name": name, "args": args, "ok": ok, "error": None if ok else res.get("error"), "duration_ms": ms,
                        "result": preview(res)}],
        "output": {"reply": None, "error": None if ok else res.get("error")},
        "duration_ms": ms,
    })


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)
