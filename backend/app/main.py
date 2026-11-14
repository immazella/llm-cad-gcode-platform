"""FastAPI uygulaması: REST API."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import logging  # noqa: E402

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import ValidationError, BaseModel  # noqa: E402

from .cad.features import CadError  # noqa: E402
from .cam.config import MachiningConfig  # noqa: E402
from .cam.interpreter import toolpath_json  # noqa: E402
from .cam.profiles import get_profile, list_profiles  # noqa: E402
from .cam.validator import rule_catalog  # noqa: E402
from . import audit, session  # noqa: E402
from .llm.agent import Agent  # noqa: E402
from .i18n import locale_scope, error_message, schema_errors, tr, language  # noqa: E402
from .presentation_i18n import present_profile
from .validation_i18n import present_rules  # noqa: E402
from .workspace import DATA_DIR, MODEL_TOOLS, TOOL_NAMES, ToolError, Workspace, run_tool  # noqa: E402

app = FastAPI(title="LLM Tabanlı CAD / G-Code Platformu", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ws = Workspace()
agent = Agent(ws)
lock = threading.Lock()  # tek kullanıcılı prototip: işlemleri sıraya koy
ui_state: dict[str, Any] = {}  # arayüzün sakladığı durum (sohbet mesajları, sekmeler)
session.load(ws, agent, ui_state)  # backend yeniden başlasa da oturum kaldığı yerden sürer

# MCP köprüsü: 3B görüntüleyicide yapılan yüzey seçimi ve harici (MCP) değişiklik sayacı.
# Web arayüzü sayacı yoklayarak Claude/ChatGPT gibi bir MCP istemcisinin yaptığı değişiklikleri görür.
selection_state: dict[str, Any] = {}
external = {"rev": 0, "last": None}
MCP_HEADER = "x-client"


def _save_session() -> None:
    with lock:
        try:
            session.save(ws, agent, ui_state)
        except Exception:
            logging.getLogger(__name__).exception("Oturum kaydedilemedi")


@app.middleware("http")
async def request_language(request: Request, call_next):
    with locale_scope(request.headers.get("Accept-Language")):
        return await call_next(request)


@app.exception_handler(RequestValidationError)
async def localized_schema_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": schema_errors(exc.errors())}))


@app.exception_handler(HTTPException)
async def localized_http_error(request: Request, exc: HTTPException):
    detail = error_message(exc.detail) if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


@app.middleware("http")
async def persist_session(request: Request, call_next):
    response = await call_next(request)
    # Durumu değiştirebilecek her API isteğinden sonra oturumu diske yaz
    if request.method != "GET" and request.url.path.startswith("/api/"):
        if request.headers.get(MCP_HEADER) == "mcp" and response.status_code < 400:
            with lock:
                external["rev"] += 1
                external["last"] = request.url.path.rsplit("/", 1)[-1]
        await run_in_threadpool(_save_session)
    return response


def guard(fn, *args, **kwargs):
    with lock:
        try:
            return fn(*args, **kwargs)
        except (CadError, ToolError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------- genel
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "llm_provider": agent.provider, "llm_model": agent.model_name}


@app.get("/api/state")
def state() -> dict[str, Any]:
    with lock:
        ui = {**ui_state, "messages": agent.active["messages"],
              "conversation": agent.conv_summary(agent.active, True)}
    return {**guard(ws.state), "llm": agent.info(), "ui": ui, "external": dict(external)}


@app.put("/api/ui")
def put_ui(body: dict[str, Any]) -> dict[str, Any]:
    """Arayüz durumu. Mesajlar, gönderildikleri sohbete yazılır (conversation_id); geçiş sırasında geciken bir
    kayıt yanlış sohbete düşmesin diye kimlik zorunlu tutulur, yoksa aktif sohbet kullanılır."""
    with lock:
        body = dict(body)
        msgs = body.pop("messages", None)
        cid = body.pop("conversation_id", None) or agent.active_id
        if msgs is not None and cid in agent.conversations:
            c = agent.conversations[cid]
            if len(msgs) != len(c["messages"]):
                c["updated"] = audit.now_iso()
            c["messages"] = msgs
        ui_state.update(body)
    return {"ok": True}


# ---------------------------------------------------------------- sohbetler
def _conv_payload(c: dict[str, Any]) -> dict[str, Any]:
    return {"conversation": agent.conv_summary(c, c["id"] == agent.active_id), "messages": c["messages"],
            "conversations": agent.list_conversations()}


@app.get("/api/conversations")
def conversations() -> dict[str, Any]:
    with lock:
        return {"conversations": agent.list_conversations(), "active_id": agent.active_id}


@app.post("/api/conversations")
def conversation_new() -> dict[str, Any]:
    with lock:
        return _conv_payload(agent.new_conversation())


@app.post("/api/conversations/{cid}/activate")
def conversation_activate(cid: str) -> dict[str, Any]:
    with lock:
        try:
            return _conv_payload(agent.activate(cid))
        except KeyError:
            raise HTTPException(404, "Sohbet bulunamadı.")


class RenameReq(BaseModel):
    title: str


@app.patch("/api/conversations/{cid}")
def conversation_rename(cid: str, req: RenameReq) -> dict[str, Any]:
    with lock:
        if cid not in agent.conversations:
            raise HTTPException(404, "Sohbet bulunamadı.")
        agent.rename_conversation(cid, req.title)
        return {"conversations": agent.list_conversations(), "active_id": agent.active_id}


@app.delete("/api/conversations/{cid}")
def conversation_delete(cid: str) -> dict[str, Any]:
    with lock:
        try:
            return _conv_payload(agent.delete_conversation(cid))
        except KeyError:
            raise HTTPException(404, "Sohbet bulunamadı.")


@app.post("/api/session/reset")
def session_reset() -> dict[str, Any]:
    """Yeni oturum: model, G-code'lar, sohbetler ve LLM audit kayıtları silinir; işleme ayarları ve LLM modu korunur."""
    with lock:
        ws.model, ws.gcodes, ws.current_gcode = None, {}, None
        agent.clear_conversations()
        ui_state.clear()
        selection_state.clear()
        removed = audit.clear()
    return {"ok": True, "audit_removed": removed}


class LlmModeReq(BaseModel):
    offline: bool


@app.post("/api/llm/mode")
def llm_mode(req: LlmModeReq) -> dict[str, Any]:
    with lock:
        if not req.offline and not agent.llm_available:
            raise HTTPException(400, "Tanımlı bir LLM API anahtarı yok; backend/.env dosyasına anahtar ekleyip backend'i yeniden başlatın.")
        agent.set_offline(req.offline)
        return agent.info()


# ---------------------------------------------------------------- model
class PrimitiveReq(BaseModel):
    type: str
    length: float | None = None
    width: float | None = None
    height: float | None = None
    radius: float | None = None


@app.post("/api/models/primitive")
def create_primitive(req: PrimitiveReq) -> dict[str, Any]:
    if req.type == "box":
        return guard(ws.create_box, req.length or 60, req.width or 60, req.height or 10)
    if req.type == "cylinder":
        return guard(ws.create_cylinder, req.radius or 25, req.height or 10)
    raise HTTPException(400, "type: box | cylinder")


@app.post("/api/models/upload")
async def upload_step(file: UploadFile = File(...)) -> dict[str, Any]:
    name = file.filename or "model.step"
    if not name.lower().endswith((".step", ".stp")):
        raise HTTPException(400, "Yalnızca STEP (.step, .stp) dosyaları destekleniyor.")
    path = DATA_DIR / f"upload_{os.urandom(4).hex()}_{Path(name).name}"
    path.write_bytes(await file.read())
    return guard(ws.load_step_file, path, Path(name).stem)


class EngraveTextReq(BaseModel):
    text: str
    height: float = 6.0
    x: float = 0
    y: float = 0
    depth: float = 0.3
    width: float | None = None
    rotation: float = 0
    font: str = "tek_cizgi"
    face_id: str | None = None


@app.post("/api/models/current/engrave-text")
def engrave_text(req: EngraveTextReq) -> dict[str, Any]:
    res = guard(ws.add_engraving_text, **req.model_dump())
    ws.mark_stale()
    return res


@app.post("/api/models/current/engrave-svg")
async def engrave_svg(file: UploadFile = File(...), size: float = Form(30.0), x: float = Form(0.0), y: float = Form(0.0),
                      depth: float = Form(0.3), rotation: float = Form(0.0), width: float | None = Form(None),
                      face_id: str | None = Form(None)) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(400, "SVG dosyası çok büyük (en fazla 2 MB).")
    res = guard(ws.add_engraving_svg, raw.decode("utf-8", errors="replace"), size, x, y, depth, width, rotation, face_id or None,
                Path(file.filename or "logo.svg").stem)
    ws.mark_stale()
    return res


@app.get("/api/models/current/mesh")
def mesh() -> dict[str, Any]:
    return guard(lambda: ws.require_model().mesh())


@app.get("/api/models/current/faces/{face_id}")
def face(face_id: str, version: int | None = None) -> dict[str, Any]:
    m = guard(ws.require_model)
    if version is not None and version != m.version:
        raise HTTPException(409, "Model sürümü değişti; seçim geçersiz.")
    return guard(m.face_info, face_id)


# ---------------------------------------------------------------- bölgeler (⌘/Ctrl + çizim)
class RegionReq(BaseModel):
    shape: str
    face_id: str
    model_version: int | None = None
    anchor_point: list[float]
    points: list[list[float]] | None = None  # düzlemsel yüzey: yüzey düzlemindeki köşeler (dünya koordinatı)
    center: list[float] | None = None  # düzlemsel yüzeyde daire
    radius: float | None = None
    rays: list[list[float]] | None = None  # eğri yüzey: kameradan çıkan ışınlar [ox, oy, oz, dx, dy, dz]


def _check_version(m, version: int | None) -> None:
    if version is not None and version != m.version:
        raise HTTPException(409, "Model sürümü değişti; çizimi tekrar yapın.")


@app.post("/api/models/current/regions")
def region_create(req: RegionReq) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        m = ws.require_model()
        _check_version(m, req.model_version)
        return m.add_region(req.model_dump(exclude={"model_version"}))

    return guard(run)


@app.get("/api/models/current/regions")
def region_list() -> dict[str, Any]:
    with lock:
        if ws.model is None:
            return {"regions": [], "dropped": [], "model_version": None}
        regions, dropped = ws.model.regions_info()
        return {"regions": regions, "dropped": dropped, "model_version": ws.model.version}


@app.delete("/api/models/current/regions/{region_id}")
def region_delete(region_id: str) -> dict[str, Any]:
    def run() -> None:
        ws.require_model().delete_region(region_id)
        if selection_state.get("region_id") == region_id:
            selection_state.clear()
        if region_id in (selection_state.get("ids") or []):
            selection_state["ids"] = [i for i in selection_state["ids"] if i != region_id]

    guard(run)
    return {"ok": True}


@app.post("/api/models/current/undo")
def undo() -> dict[str, Any]:
    res = guard(ws.undo)
    ws.mark_stale()
    return res


@app.delete("/api/models/current/features/{feature_id}")
def delete_feature(feature_id: str) -> dict[str, Any]:
    res = guard(ws.delete_feature, feature_id)
    ws.mark_stale()
    return res


@app.get("/api/models/current/export.step")
def export_step() -> FileResponse:
    res = guard(ws.export_step)
    m = ws.require_model()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in m.name) or "model"
    return FileResponse(DATA_DIR / res["file"], filename=f"{safe}.step", media_type="application/step")


# ---------------------------------------------------------------- sohbet
class ChatReq(BaseModel):
    message: str
    language: Literal["tr", "en"] | None = None
    selected_face_id: str | None = None
    selected_region_id: str | None = None
    selected_ids: list[str] | None = None  # çoklu seçim: yüzey (f3) ve bölge (r1) kimlikleri, seçim sırasıyla
    model_version: int | None = None
    conversation_id: str | None = None  # mesajın ait olduğu sohbet (başka sekmede sohbet değiştiyse bile doğru geçmiş)


def _resolve(ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Seçim kimliklerini (f3 / r1) bilgiye çevirir; çözülemeyenler için not döndürür."""
    out, notes = [], []
    for i in ids:
        try:
            out.append(ws.model.region_info(i, mesh=False) if i.startswith("r") else ws.model.face_info(i))
        except CadError as exc:
            if i.startswith("r"):
                notes.append(str(exc))
    return out, notes


@app.post("/api/chat")
def chat(req: ChatReq) -> dict[str, Any]:
    with locale_scope(req.language or language()), lock:
        if req.conversation_id and req.conversation_id in agent.conversations and req.conversation_id != agent.active_id:
            agent.activate(req.conversation_id)
        selection: dict | list | None = None
        note = None
        ids = list(req.selected_ids or []) or [i for i in (req.selected_region_id or req.selected_face_id,) if i]
        if ids and ws.model:
            if req.model_version is not None and req.model_version != ws.model.version:
                note = tr("Seçim eski model sürümüne ait olduğu için yok sayıldı.", "Selection ignored because it belongs to an older model version.")
            else:
                sels, notes = _resolve(ids)
                note = " ".join(notes) or None
                selection = sels if len(sels) > 1 else (sels[0] if sels else None)
        res = agent.chat(req.message, selection)
        if note:
            res["reply"] = f"({note})\n" + res["reply"]
        res["state"] = ws.state()
        return res


@app.post("/api/chat/reset")
def chat_reset() -> dict[str, Any]:
    with lock:
        agent.reset()
    return {"ok": True}


# ---------------------------------------------------------------- MCP köprüsü
def _current_selections() -> list[dict[str, Any]]:
    """Web görüntüleyicide seçili yüzeyler/bölgeler (seçim sırasıyla); model değiştiyse yüzeyler düşer."""
    m = ws.model
    if m is None or selection_state.get("model_id") != m.id:
        return []
    ids = selection_state.get("ids") or [i for i in (selection_state.get("region_id") or selection_state.get("face_id"),) if i]
    same = selection_state.get("model_version") == m.version
    out = []
    for i in ids:
        if not i.startswith("r") and not same:
            continue  # yüzey numaraları eski sürüme ait; bölgeler ise yeniden yerleştirilir
        try:
            out.append(m.region_info(i, mesh=False) if i.startswith("r") else m.face_info(i))
        except CadError:
            pass
    return out


def _current_selection() -> dict[str, Any] | None:
    """Birincil (son seçilen) eleman."""
    sels = _current_selections()
    return sels[-1] if sels else None


class SelectionReq(BaseModel):
    face_id: str | None = None
    region_id: str | None = None
    ids: list[str] | None = None  # çoklu seçim (f3, r1 …)
    model_version: int | None = None


@app.put("/api/selection")
def put_selection(req: SelectionReq) -> dict[str, Any]:
    with lock:
        selection_state.clear()
        if (req.face_id or req.region_id or req.ids) and ws.model is not None:
            selection_state.update(face_id=req.face_id, region_id=req.region_id, ids=req.ids, model_version=req.model_version,
                                   model_id=ws.model.id)
    return {"ok": True}


@app.get("/api/selection")
def get_selection() -> dict[str, Any]:
    with lock:
        sels = _current_selections()
        return {"selection": sels[-1] if sels else None, "selections": sels}


@app.get("/api/context")
def get_context() -> dict[str, Any]:
    """LLM bağlam bloğu (web sohbetindeki [BAĞLAM] ile aynı), seçili yüzey dahil."""
    with lock:
        sels = _current_selections()
        return {"text": ws.context(sels if len(sels) > 1 else (sels[0] if sels else None))}


@app.post("/api/tools/{name}")
def call_tool(name: str, request: Request, args: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """LLM araçlarını doğrudan çalıştırır (MCP sunucusu bunu kullanır). Sohbetteki araç çağrısıyla aynı yol.
    MCP'den gelen çağrılar audit günlüğüne yazılır; arayüzün Atölye sekmesi gibi doğrudan kullanımlar LLM olmadığı için yazılmaz."""
    if name not in TOOL_NAMES:
        raise HTTPException(404, f"Bilinmeyen araç: {name}")
    with lock:
        t = audit.Timer()
        res = run_tool(ws, name, args or {})
        if res.get("ok", True) is not False and name in MODEL_TOOLS:
            ws.mark_stale()
        if request.headers.get(MCP_HEADER) == "mcp":
            audit.mcp_tool(ws, name, args or {}, res, t.ms(), "http")
        return res


# ---------------------------------------------------------------- LLM audit günlüğü
@app.get("/api/audit")
def audit_list(q: str = "", kind: str | None = None, offset: int = 0, limit: int = 100, ids_only: bool = False) -> dict[str, Any]:
    return audit.query(q, kind or None, max(offset, 0), min(max(limit, 1), 500), ids_only)


@app.get("/api/audit/{entry_id}")
def audit_get(entry_id: str) -> dict[str, Any]:
    e = audit.get(entry_id)
    if e is None:
        raise HTTPException(404, "Audit kaydı bulunamadı.")
    return e


class AuditExportReq(BaseModel):
    ids: list[str] | None = None  # None → tüm kayıtlar


@app.post("/api/audit/export")
def audit_export(req: AuditExportReq) -> JSONResponse:
    data = audit.export(req.ids)
    stamp = data["exported_at"][:16].replace("-", "").replace(":", "").replace("T", "-")
    name = f"llm-audit-{stamp}{'-secili' if req.ids else ''}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- işleme ayarları
@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return ws.config.model_dump()


@app.put("/api/config")
def put_config(cfg: MachiningConfig) -> dict[str, Any]:
    try:
        get_profile(cfg.profile_id)
    except KeyError as exc:
        raise HTTPException(400, str(exc.args[0]))
    with lock:
        ws.config = cfg
        ws.revalidate_current()  # makine/bağlama değişti: mevcut programı yeniden doğrula
    return ws.config.model_dump()


# ---------------------------------------------------------------- makine profilleri
@app.get("/api/profiles")
def profiles() -> list[dict[str, Any]]:
    return [present_profile(p.model_dump()) for p in list_profiles()]


class ProfileReq(BaseModel):
    profile_id: str
    work_offset: list[float] | None = None


@app.post("/api/profile")
def set_profile(req: ProfileReq) -> dict[str, Any]:
    res = guard(ws.set_machine_profile, req.profile_id, req.work_offset)
    return {**res, "config": ws.config.model_dump()}


class MachineReq(BaseModel):
    profile: dict[str, Any]
    activate: bool = False


@app.post("/api/profiles")
def save_machine(req: MachineReq) -> dict[str, Any]:
    """Kullanıcı makinesini kaydeder (yeni ya da mevcut kullanıcı makinesini günceller)."""
    from .cam.profiles import save_profile

    with lock:
        try:
            p = save_profile(req.profile)
        except ValidationError as exc:
            raise HTTPException(400, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in schema_errors(exc.errors())[:4]))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc.args[0] if exc.args else exc))
        if req.activate or ws.config.profile_id == p.id:
            ws.set_machine_profile(p.id)  # aynı makine: yalnızca yeniden doğrular, kurulum korunur
        return {"profile": present_profile(p.model_dump()), "profiles": [present_profile(x.model_dump()) for x in list_profiles()], "config": ws.config.model_dump()}


@app.delete("/api/profiles/{profile_id}")
def delete_machine(profile_id: str) -> dict[str, Any]:
    from .cam.profiles import DEFAULT_PROFILE_ID, delete_profile

    with lock:
        try:
            delete_profile(profile_id)
        except KeyError:
            raise HTTPException(404, f"Makine bulunamadı: {profile_id}")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if ws.config.profile_id == profile_id:
            ws.set_machine_profile(DEFAULT_PROFILE_ID)
        return {"profiles": [present_profile(x.model_dump()) for x in list_profiles()], "config": ws.config.model_dump()}


@app.get("/api/materials")
def materials() -> dict[str, Any]:
    return ws.list_materials()


class RecommendReq(BaseModel):
    tool_number: int | None = None
    tool_type: str | None = None
    diameter: float | None = None
    flutes: int | None = None
    tool_material: str | None = None
    material: str | None = None


@app.post("/api/cutting/recommend")
def cutting_recommend(req: RecommendReq) -> dict[str, Any]:
    return guard(ws.recommend_cutting, **req.model_dump())


@app.get("/api/rules")
def rules() -> list[dict[str, Any]]:
    return present_rules(rule_catalog())


# ---------------------------------------------------------------- G-code
class GenerateReq(BaseModel):
    operations: list[str] | None = None


@app.post("/api/gcode/generate")
def gcode_generate(req: GenerateReq) -> dict[str, Any]:
    guard(ws.generate_gcode, req.operations)
    return gcode_get(ws.current_gcode)


class GcodeText(BaseModel):
    text: str
    name: str = "yapıştırılan"


@app.post("/api/gcode/text")
def gcode_text(req: GcodeText) -> dict[str, Any]:
    rec = guard(ws.upload_gcode, req.text, req.name)
    return gcode_get(rec["gcode_id"])


@app.post("/api/gcode/upload")
async def gcode_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(400, "Dosya çok büyük (en fazla 5 MB).")
    rec = guard(ws.upload_gcode, raw.decode("utf-8", errors="replace"), file.filename or "dosya")
    return gcode_get(rec["gcode_id"])


@app.post("/api/gcode/{gcode_id}/validate")
def gcode_validate(gcode_id: str) -> dict[str, Any]:
    guard(ws.validate_gcode, gcode_id)
    return gcode_get(gcode_id)


@app.get("/api/gcode/{gcode_id}")
def gcode_get(gcode_id: str) -> dict[str, Any]:
    rec = ws.gcodes.get(gcode_id)
    if not rec:
        raise HTTPException(404, "G-code bulunamadı.")
    return {**ws.gcode_meta(rec), "text": rec["text"]}


@app.get("/api/gcode/{gcode_id}/toolpath")
def gcode_toolpath(gcode_id: str) -> dict[str, Any]:
    rec = ws.gcodes.get(gcode_id)
    if not rec:
        raise HTTPException(404, "G-code bulunamadı.")
    return toolpath_json(rec["text"], ws.config.profile.rapid_feed)


@app.get("/api/gcode/{gcode_id}/download")
def gcode_download(gcode_id: str) -> PlainTextResponse:
    rec = ws.gcodes.get(gcode_id)
    if not rec:
        raise HTTPException(404, "G-code bulunamadı.")
    return PlainTextResponse(rec["text"], headers={"Content-Disposition": f'attachment; filename="program_{gcode_id}.nc"'})


# ---------------------------------------------------------------- derlenmiş frontend (isteğe bağlı)
DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
