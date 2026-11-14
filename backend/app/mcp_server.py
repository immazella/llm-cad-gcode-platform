"""MCP sunucusu: platformun araçlarını Claude Desktop, Claude Code, Cursor, ChatGPT gibi
MCP istemcilerine açar ("ikinci arayüz").

Web uygulamasıyla aynı çekirdeği kullanır: araç şemaları `llm/tool_schemas.py`'den, araç
uygulamaları `workspace.py`'den gelir. Orkestrasyonu (hangi aracın çağrılacağı) bu modda
istemcinin kendi LLM'i yapar; `llm/agent.py` kullanılmaz.

Çalışma modları (MCP_MODE ortam değişkeni):
- auto  (varsayılan): web backend'i (CAD_API_URL, varsayılan http://127.0.0.1:8000) açıksa ona
        bağlanır; değişiklikler tarayıcıdaki 3B görünümde anında görünür ve görüntüleyicide
        seçilen yüzey get_selection ile okunabilir. Backend kapalıysa aynı çekirdeği bu süreç
        içinde çalıştırır ve oturumu backend/data/session.pkl'ye yazar; web uygulaması sonradan
        açıldığında model kaldığı yerden görünür.
- http : yalnızca web backend'i üzerinden çalışır.
- local: yalnızca süreç içi çalışır.

Başlatma:  cd backend && ../.venv/bin/python -m app.mcp_server     (stdio aktarımı)
Uzak:      ../.venv/bin/python -m app.mcp_server --http          (Streamable HTTP; run_mcp_remote.sh
           bunu Cloudflare Quick Tunnel ile birlikte başlatır). Uzak modda adres gizli bir anahtar
           içerir (/mcp/<MCP_TOKEN>) ve bilgisayardaki dosyalara dokunan araçlar kapalıdır.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import secrets
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

# stdout MCP protokolüne ait: kayıtlar yalnızca stderr'e
logging.basicConfig(stream=sys.stderr, level=os.getenv("MCP_LOG_LEVEL", "WARNING"),
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("cad-mcp")

from .llm.prompts import SYSTEM_PROMPT  # noqa: E402
from .llm.tool_schemas import TOOL_SCHEMAS  # noqa: E402

SERVER_NAME = "busproje-cad"
MAX_TEXT = 24000  # bir araç sonucunun metin üst sınırı (karakter)


class BackendError(Exception):
    """Kullanıcıya/LLM'ye iletilecek, beklenen hata."""


@dataclass
class UiResult:
    """Arayüze bağlı araç sonucu: text LLM'ye, structured hem LLM'ye hem arayüze, meta yalnızca arayüze gider."""

    text: Any
    structured: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


# ====================================================================== arka uçlar
class _OfflineAgentStub:
    """session.save/load web uygulamasının sohbet geçmişini de saklar; MCP süreci sohbete
    dokunmaz ama oturum dosyasındaki geçmişi bozmamak için aynen taşır."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self._offline = False

    def set_offline(self, offline: bool) -> None:
        self._offline = offline

    @property
    def provider(self) -> str:
        return "offline" if self._offline else "?"

    @property
    def llm_available(self) -> bool:
        return self._offline


class LocalBackend:
    """Çekirdeği bu süreç içinde çalıştırır."""

    name = "local"

    def __init__(self) -> None:
        self.ws = None
        self._agent = _OfflineAgentStub()
        self._ui: dict[str, Any] = {}
        self._sel: dict[str, Any] = {}

    def reload(self) -> None:
        """Oturumu diskten (web backend'inin son yazdığı hâliyle) yükler."""
        from . import session
        from .workspace import Workspace

        self.ws = Workspace()
        self._agent = _OfflineAgentStub()
        self._ui = {}
        session.load(self.ws, self._agent, self._ui)

    def _w(self):
        if self.ws is None:
            self.reload()
        return self.ws

    def _save(self) -> None:
        from . import session

        try:
            session.save(self._w(), self._agent, self._ui)
        except Exception:  # kalıcılık yardımcı; işlem başarısız sayılmaz
            log.exception("Oturum kaydedilemedi")

    def _call(self, fn, *args, **kwargs):
        from .cad.features import CadError
        from .workspace import ToolError

        try:
            return fn(*args, **kwargs)
        except (CadError, ToolError) as exc:
            raise BackendError(str(exc)) from exc

    # -------------------------------------------------------------- arayüz
    def tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        from .workspace import MODEL_TOOLS, run_tool

        from . import audit

        ws = self._w()
        t = audit.Timer()
        res = run_tool(ws, name, args)
        if res.get("ok", True) is not False:
            if name in MODEL_TOOLS:
                ws.mark_stale()
            self._save()
        audit.mcp_tool(ws, name, args, res, t.ms(), "local")
        return res

    def context(self) -> str:
        return self._w().context(self.selection())

    def selection(self) -> dict[str, Any] | None:
        """MCP görüntüleyicisinde (show_model_3d) seçilen yüzey; model değiştiyse None."""
        from .cad.features import CadError

        m = self._w().model
        sel = self._sel
        if not sel or m is None or sel.get("model_id") != m.id or sel.get("model_version") != m.version:
            return None
        try:
            return m.face_info(sel["face_id"])
        except CadError:
            return None

    def set_selection(self, face_id: str | None, model_version: int | None) -> dict[str, Any] | None:
        m = self._w().model
        self._sel = {}
        if face_id and m is not None:
            self._sel = {"face_id": face_id, "model_version": model_version, "model_id": m.id}
        return self.selection()

    def current_gcode_id(self) -> str | None:
        return self._w().current_gcode

    def load_step(self, path: Path) -> dict[str, Any]:
        from .workspace import DATA_DIR

        dest = DATA_DIR / f"upload_{os.urandom(4).hex()}_{path.name}"
        shutil.copyfile(path, dest)
        res = self._call(self._w().load_step_file, dest, path.stem)
        self._save()
        return res

    def upload_gcode(self, text: str, name: str) -> dict[str, Any]:
        ws = self._w()
        rec = self._call(ws.upload_gcode, text, name)
        self._save()
        return ws.gcode_meta(rec)

    def gcode(self, gcode_id: str) -> dict[str, Any]:
        ws = self._w()
        rec = ws.gcodes.get(gcode_id)
        if rec is None:
            raise BackendError(f"G-code bulunamadı: {gcode_id}")
        return {**ws.gcode_meta(rec), "text": rec["text"]}

    def engrave_svg(self, svg: str, params: dict[str, Any]) -> dict[str, Any]:
        ws = self._w()
        res = self._call(ws.add_engraving_svg, svg, **params)
        ws.mark_stale()
        self._save()
        return res

    def mesh(self) -> dict[str, Any]:
        return self._call(lambda: self._w().require_model().mesh())

    def toolpath(self, gcode_id: str) -> dict[str, Any]:
        from .cam.interpreter import toolpath_json

        rec = self.gcode(gcode_id)
        return toolpath_json(rec["text"], self._w().config.profile.rapid_feed)

    def rules(self) -> list[dict[str, Any]]:
        from .cam.validator import rule_catalog

        return rule_catalog()


class HttpBackend:
    """Çalışan web backend'ine (FastAPI) REST üzerinden bağlanır."""

    name = "http"

    def __init__(self, url: str) -> None:
        import httpx

        self.url = url.rstrip("/")
        # x-client başlığı, web arayüzünün harici değişikliği fark edip ekranı yenilemesini sağlar
        self.client = httpx.Client(base_url=self.url, timeout=180.0, headers={"x-client": "mcp"})

    def alive(self) -> bool:
        try:
            return self.client.get("/api/health", timeout=0.5).status_code == 200
        except Exception:
            return False

    def _req(self, method: str, path: str, **kw) -> Any:
        import httpx

        try:
            r = self.client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise BackendError(f"Web backend'ine ulaşılamadı ({self.url}): {exc}") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text[:300]
            raise BackendError(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
        return r.json()

    def tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", f"/api/tools/{name}", json=args)

    def context(self) -> str:
        return self._req("GET", "/api/context")["text"]

    def selection(self) -> dict[str, Any] | None:
        return self._req("GET", "/api/selection")["selection"]

    def selections(self) -> list[dict[str, Any]]:
        return self._req("GET", "/api/selection").get("selections") or []

    def set_selection(self, face_id: str | None, model_version: int | None) -> dict[str, Any] | None:
        self._req("PUT", "/api/selection", json={"face_id": face_id, "model_version": model_version})
        return self.selection()

    def current_gcode_id(self) -> str | None:
        g = self._req("GET", "/api/state").get("gcode")
        return g["gcode_id"] if g else None

    def load_step(self, path: Path) -> dict[str, Any]:
        with open(path, "rb") as fh:
            return self._req("POST", "/api/models/upload", files={"file": (path.name, fh, "application/step")})

    def upload_gcode(self, text: str, name: str) -> dict[str, Any]:
        rec = self._req("POST", "/api/gcode/text", json={"text": text, "name": name})
        rec.pop("text", None)
        return rec

    def gcode(self, gcode_id: str) -> dict[str, Any]:
        return self._req("GET", f"/api/gcode/{gcode_id}")

    def engrave_svg(self, svg: str, params: dict[str, Any]) -> dict[str, Any]:
        name = params.pop("name", "logo")
        data = {k: str(v) for k, v in params.items() if v is not None}
        return self._req("POST", "/api/models/current/engrave-svg", data=data,
                         files={"file": (f"{name}.svg", svg.encode("utf-8"), "image/svg+xml")})

    def mesh(self) -> dict[str, Any]:
        return self._req("GET", "/api/models/current/mesh")

    def toolpath(self, gcode_id: str) -> dict[str, Any]:
        return self._req("GET", f"/api/gcode/{gcode_id}/toolpath")

    def rules(self) -> list[dict[str, Any]]:
        return self._req("GET", "/api/rules")


class Router:
    """MCP_MODE'a göre arka ucu seçer. auto modda web backend'i açılıp kapandıkça geçiş yapar."""

    def __init__(self, mode: str | None = None, url: str | None = None, remote: bool = False) -> None:
        self.remote = remote
        self.mode = (mode or os.getenv("MCP_MODE", "auto")).strip().lower()
        if self.mode not in ("auto", "http", "local"):
            raise ValueError("MCP_MODE auto, http veya local olmalı")
        self.http = HttpBackend(url or os.getenv("CAD_API_URL", "http://127.0.0.1:8000"))
        self.local = LocalBackend()
        self._active: LocalBackend | HttpBackend | None = None
        self._checked = 0.0

    def get(self) -> LocalBackend | HttpBackend:
        if self.mode == "local":
            return self.local
        if self.mode == "http":
            return self.http
        now = time.monotonic()
        if self._active is not None and now - self._checked < 2.0:
            return self._active
        new = self.http if self.http.alive() else self.local
        if new is self.local and self._active is not self.local:
            # Backend kapandı (ya da hiç açılmadı): onun son kaydettiği oturumdan devam et
            self.local.reload()
        self._active, self._checked = new, now
        return new

    def describe(self, b: LocalBackend | HttpBackend) -> str:
        if b is self.http:
            return (f"Bağlantı: web backend'i ({self.http.url}). Değişiklikler tarayıcıdaki 3B görünümde anında görünür; "
                    "kullanıcının görüntüleyicide seçtiği yüzey get_selection ile okunur.")
        return ("Bağlantı: yerel mod (web arayüzü kapalı). Kullanıcı modeli show_model_3d görüntüleyicisinde görür ve orada "
                "tıklayarak yüzey seçebilir (get_selection). Oturum diske kaydedilir, web uygulaması açılınca aynı model görünür.")


# ====================================================================== görselleştirme
_C_FACE = (0.72, 0.75, 0.80)
_C_HOLE = (0.95, 0.55, 0.15)
_C_POCKET = (0.25, 0.55, 0.95)
_C_FEATURE = (0.62, 0.40, 0.85)
_C_SELECT = (0.15, 0.75, 0.35)
VIEWS = {"iso": (28, -58, "persp"), "top": (90, -90, "ortho"), "front": (0, -90, "ortho"), "right": (0, 0, "ortho")}


def _png(fig) -> bytes:
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _equal_3d(ax, lo, hi) -> None:
    import numpy as np

    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    span = np.maximum(hi - lo, 1e-6)
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(tuple(np.maximum(span, span.max() * 0.04)))


def render_model_png(mesh: dict[str, Any], view: str = "iso", selected_face: str | None = None, labels: bool = True) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

    elev, azim, proj = VIEWS.get(view, VIEWS["iso"])
    light = np.array([0.35, -0.55, 0.75])
    light /= np.linalg.norm(light)
    polys, colors = [], []
    for f in mesh["faces"]:
        v = np.asarray(f["vertices"], float).reshape(-1, 3)
        idx = np.asarray(f["indices"], int).reshape(-1, 3)
        if not len(idx):
            continue
        tri = v[idx]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        shade = 0.45 + 0.55 * np.abs(n @ light)
        if f["id"] == selected_face:
            base = _C_SELECT
        elif f.get("hole_id"):
            base = _C_HOLE
        elif f.get("pocket_id"):
            base = _C_POCKET
        elif f.get("feature_id"):
            base = _C_FEATURE
        else:
            base = _C_FACE
        polys.extend(tri)
        colors.extend(np.clip(np.asarray(base)[None, :] * shade[:, None], 0, 1))
    if not polys:
        raise BackendError("Modelin görüntülenecek yüzeyi yok.")
    pts = np.concatenate([p for p in polys])
    lo, hi = pts.min(axis=0), pts.max(axis=0)

    fig = plt.figure(figsize=(6.4, 5.2))
    ax = fig.add_subplot(projection="3d")
    ax.set_proj_type("ortho" if proj == "ortho" else "persp")
    ax.add_collection3d(Poly3DCollection(polys, facecolors=colors, edgecolors="none"))
    segs = []
    for e in mesh.get("edges", []):
        p = np.asarray(e, float).reshape(-1, 3)
        segs.extend(zip(p[:-1], p[1:]))
    if segs:
        ax.add_collection3d(Line3DCollection(segs, colors=(0.12, 0.12, 0.15), linewidths=0.6))
    s = mesh.get("summary") or {}
    if labels:
        for h in s.get("holes", []):
            x, y, z = h["top_point"]
            ax.text(x, y, z + 0.02 * (hi[2] - lo[2] + 1), f"{h['id']} Ø{h['diameter']:g}", fontsize=7, color="#9a3412")
        for p in s.get("pockets", []):
            (x, y), z = p["center_xy"], p.get("top_z", hi[2])
            ax.text(x, y, z, p["id"], fontsize=7, color="#1e40af")
    _equal_3d(ax, lo, hi)
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlabel("X", fontsize=8)
    ax.set_ylabel("Y", fontsize=8)
    ax.set_zlabel("Z", fontsize=8)
    ax.tick_params(labelsize=7)
    hidden = {"top": "z", "front": "y", "right": "x"}.get(view)
    if hidden:  # bakış yönündeki eksen tek nokta gibi görünür; etiketleri üst üste binmesin
        getattr(ax, f"set_{hidden}ticks")([])
        getattr(ax, f"set_{hidden}label")("")
    size = " × ".join(f"{d:g}" for d in (s.get("bbox") or {}).get("size", []))
    ax.set_title(f"{s.get('name', 'model')}  ({size} mm, sürüm {s.get('version', '?')}) — {view}", fontsize=9)
    return _png(fig)


def render_toolpath_png(tp: dict[str, Any], mesh: dict[str, Any] | None = None, title: str = "") -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    from mpl_toolkits.mplot3d.art3d import Line3DCollection

    cut, rapid = [], []
    for s in tp["segments"]:
        p = np.asarray(s["points"], float)
        pairs = list(zip(p[:-1], p[1:]))
        (rapid if s["kind"] == "rapid" else cut).extend(pairs)
    if not cut and not rapid:
        raise BackendError("G-code'da çizilecek hareket yok.")
    allp = np.array([q for pair in cut + rapid for q in pair])

    fig = plt.figure(figsize=(10, 4.6))
    a2 = fig.add_subplot(1, 2, 1)
    a3 = fig.add_subplot(1, 2, 2, projection="3d")
    model_segs = []
    if mesh:
        top = max(max(f["vertices"][2::3]) for f in mesh["faces"] if f["vertices"])
        for e in mesh.get("edges", []):
            p = np.asarray(e, float).reshape(-1, 3)
            p[:, 2] -= top  # iş sıfırı parça üst yüzeyi
            model_segs.extend(zip(p[:-1], p[1:]))
        a2.add_collection(LineCollection([(a[:2], b[:2]) for a, b in model_segs], colors="#9ca3af", linewidths=0.6))
        a3.add_collection3d(Line3DCollection(model_segs, colors="#9ca3af", linewidths=0.5))
    a2.add_collection(LineCollection([(a[:2], b[:2]) for a, b in rapid], colors="#dc2626", linewidths=0.6, linestyles="dashed", alpha=0.55))
    a2.add_collection(LineCollection([(a[:2], b[:2]) for a, b in cut], colors="#2563eb", linewidths=0.9))
    ext = allp if not model_segs else np.vstack([allp, np.array([q for pair in model_segs for q in pair])])
    lo, hi = ext.min(axis=0), ext.max(axis=0)
    pad = 0.05 * max(hi[0] - lo[0], hi[1] - lo[1], 1)
    a2.set_xlim(lo[0] - pad, hi[0] + pad)
    a2.set_ylim(lo[1] - pad, hi[1] + pad)
    a2.set_aspect("equal")
    a2.set_title("Üstten (XY)", fontsize=9)
    a2.tick_params(labelsize=7)
    a2.grid(alpha=0.2)
    a3.add_collection3d(Line3DCollection(rapid, colors="#dc2626", linewidths=0.5, linestyles="dashed", alpha=0.5))
    a3.add_collection3d(Line3DCollection(cut, colors="#2563eb", linewidths=0.7))
    _equal_3d(a3, lo, hi)
    a3.view_init(elev=28, azim=-58)
    a3.tick_params(labelsize=7)
    a3.set_title("İzometrik (iş koordinatı)", fontsize=9)
    fig.legend(handles=[Line2D([], [], color="#2563eb", label="kesme (G1/G2/G3)"),
                        Line2D([], [], color="#dc2626", ls="--", label="hızlı (G0)")],
               loc="lower center", ncol=2, fontsize=8, frameon=False)
    st = tp.get("stats", {})
    fig.suptitle(f"{title}  —  {st.get('segment_count', 0)} segment, kesme {st.get('cut_length_mm', 0)} mm, "
                 f"tahmini {st.get('estimated_time_s', 0)} s", fontsize=9)
    return _png(fig)


# ====================================================================== etkileşimli görüntüleyici (MCP Apps)
# İstemci MCP Apps'i destekliyorsa (ChatGPT, Claude, VS Code…) show_model_3d'nin sonucu sohbetin içinde
# döndürülebilir bir 3B görüntüleyici olarak açılır. Arayüz, mcp_ui/ projesinden tek dosya olarak derlenen
# backend/app/mcp_ui/index.html'dir ve ui:// kaynağı olarak sunulur.
APPS_EXTENSION = "io.modelcontextprotocol/ui"
APP_MIME = "text/html;profile=mcp-app"
VIEWER_URI = "ui://busproje/viewer.html"
VIEWER_FILE = Path(__file__).resolve().parent / "mcp_ui" / "index.html"
MESH_META_KEY = "busproje/mesh"  # sonucun _meta'sı: yalnızca arayüze gider, LLM bağlamını şişirmez

_viewer_cache: dict[str, Any] = {}
_mesh_cache: dict[tuple[Any, Any], dict[str, Any]] = {}


def viewer_html() -> str:
    try:
        mtime = VIEWER_FILE.stat().st_mtime
    except FileNotFoundError:
        return ("<!doctype html><meta charset='utf-8'><body style='font-family:sans-serif;padding:16px'>"
                "Görüntüleyici derlenmemiş. <code>cd mcp_ui && npm install && npm run build</code> çalıştırın.</body>")
    if _viewer_cache.get("mtime") != mtime:
        _viewer_cache.update(mtime=mtime, html=VIEWER_FILE.read_text(encoding="utf-8"))
    return _viewer_cache["html"]


def _b64(arr) -> str:
    return base64.b64encode(arr.tobytes()).decode("ascii")


def compact_mesh(mesh: dict[str, Any]) -> dict[str, Any]:
    """Web görüntüleyicisinin mesh'ini (sayı listeleri) base64 Float32/Uint16-32'ye çevirir; tarayıcı doğrudan
    typed array olarak okur. Aynı model sürümü için sonuç önbellekten gelir."""
    import numpy as np

    s = mesh.get("summary") or {}
    key = (mesh.get("model_id"), mesh.get("version"))
    if key in _mesh_cache:
        return _mesh_cache[key]
    faces = []
    for f in mesh["faces"]:
        if not f["indices"]:
            continue
        idx = np.asarray(f["indices"])
        faces.append({"id": f["id"], "v": _b64(np.asarray(f["vertices"], dtype="<f4")),
                      "i": _b64(idx.astype("<u2" if idx.max() < 65536 else "<u4")),
                      "i32": bool(idx.max() >= 65536),
                      "hole_id": f.get("hole_id"), "pocket_id": f.get("pocket_id"), "feature_id": f.get("feature_id")})
    segs = []
    for e in mesh.get("edges", []):
        p = np.asarray(e, dtype="<f4").reshape(-1, 3)
        if len(p) > 1:
            segs.append(np.stack([p[:-1], p[1:]], axis=1).reshape(-1, 3))
    edges = _b64(np.concatenate(segs).astype("<f4")) if segs else ""
    out = {"model_id": mesh.get("model_id"), "version": mesh.get("version"), "faces": faces, "edges": edges,
           "summary": {"name": s.get("name"), "version": s.get("version"), "bbox": s.get("bbox"),
                       "holes": [{"id": h["id"], "diameter": h.get("diameter"), "top_point": h.get("top_point")}
                                 for h in s.get("holes", [])],
                       "pockets": [{"id": p["id"]} for p in s.get("pockets", [])],
                       "features": [{"id": ft.get("id"), "type": ft.get("type")} for ft in s.get("features", [])
                                    if isinstance(ft, dict)]}}
    if len(_mesh_cache) > 8:
        _mesh_cache.clear()
    _mesh_cache[key] = out
    return out


def _model_brief(s: dict[str, Any], sel: dict[str, Any] | None) -> dict[str, Any]:
    return {"model": s.get("name"), "version": s.get("version"),
            "size_mm": (s.get("bbox") or {}).get("size"),
            "holes": [h["id"] for h in s.get("holes", [])], "pockets": [p["id"] for p in s.get("pockets", [])],
            "selected_face": sel["face_id"] if sel else None}


# ====================================================================== araç tanımları
READ_ONLY = {"get_geometry_info", "get_holes", "get_face_info", "list_machine_profiles", "validate_gcode",
             "get_context", "get_selection", "get_gcode", "render_model", "render_toolpath", "list_validation_rules",
             "show_model_3d"}
# Arayüze bağlı araçlar: _meta.ui.resourceUri (MCP Apps) + eski ChatGPT Apps SDK anahtarları
UI_TOOL_META = {
    "show_model_3d": {"ui": {"resourceUri": VIEWER_URI}, "openai/outputTemplate": VIEWER_URI,
                      "openai/widgetAccessible": True,
                      "openai/toolInvocation/invoking": "3B görünüm hazırlanıyor…",
                      "openai/toolInvocation/invoked": "3B görünüm hazır"},
    # Görüntüleyici tıklamada bunu çağırır; model de çağırabilir. resourceUri yok: kendi başına arayüz açmaz.
    "set_selection": {"ui": {"visibility": ["model", "app"]}, "openai/widgetAccessible": True},
}
PATH = {"type": "string", "description": "Kullanıcının bilgisayarındaki mutlak dosya yolu (~ kullanılabilir)"}

EXTRA_TOOLS: list[dict[str, Any]] = [
    {"name": "show_model_3d",
     "description": ("Aktif modeli kullanıcıya sohbetin içinde ETKİLEŞİMLİ 3B görüntüleyiciyle gösterir (döndürme, yakınlaştırma, "
                     "yüzeye tıklayıp seçme). Kullanıcı modeli görmek/göstermek/3B istediğinde ve bir değişiklikten sonra sonucu "
                     "göstermek için bunu kullan. Seçilen yüzey get_selection ile okunur. İstemci arayüz gösteremiyorsa render_model'e geç."),
     "parameters": {"type": "object", "properties": {}}},
    {"name": "set_selection",
     "description": ("Bir yüzeyi seçili yapar (genelde görüntüleyici kullanıcı tıklayınca çağırır). face_id null ise seçim kaldırılır. "
                     "model_version, yüzeyin ait olduğu model sürümüdür; eski sürüme ait seçim geçersiz sayılır."),
     "parameters": {"type": "object", "properties": {
         "face_id": {"type": ["string", "null"], "description": "Yüzey kimliği, ör. f7"},
         "model_version": {"type": ["integer", "null"], "description": "Seçimin yapıldığı model sürümü"}}}},
    {"name": "get_context",
     "description": ("Aktif modelin özeti (ölçüler, özellikler, delik/cep kimlikleri), seçili yüzey, makine profili ve mevcut G-code "
                     "durumu. Bir isteğe başlamadan önce ve kullanıcı arayüzde bir şey değiştirmiş olabileceğinde çağır."),
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_selection",
     "description": ("Kullanıcının 3B görüntüleyicide (web arayüzü veya show_model_3d) tıklayarak seçtiği yüzey (face_id, rol, hole_id/pocket_id, merkez, normal). "
                     "'Seçtiğim delik', 'bu yüzey' gibi ifadelerde çağır. Seçim yoksa null döner."),
     "parameters": {"type": "object", "properties": {}}},
    {"name": "render_model",
     "description": ("Aktif modelin görüntüsünü (PNG) döndürür. Delik yüzeyleri turuncu, cepler mavi, kazıma/diğer özellikler mor, "
                     "seçili yüzey yeşil; delik ve cep kimlikleri etiketlenir. Görüntü SANA gelir; bazı istemciler (ör. ChatGPT) kullanıcıya göstermez. "
                     "Kendi görsel kontrolün için kullan; kullanıcıya göstermek için show_model_3d tercih et."),
     "parameters": {"type": "object", "properties": {
         "view": {"type": "string", "enum": list(VIEWS), "description": "iso (varsayılan), top, front, right"},
         "labels": {"type": "boolean", "description": "Delik/cep etiketleri (varsayılan true)"}}}},
    {"name": "render_toolpath",
     "description": "G-code takım yolunun görüntüsü (PNG): üstten ve izometrik; kesmeler mavi, hızlı hareketler kesikli kırmızı, parça kenarları gri.",
     "parameters": {"type": "object", "properties": {"gcode_id": {"type": "string", "description": "Verilmezse mevcut program"}}}},
    {"name": "get_gcode",
     "description": ("G-code program metnini satır numaralarıyla döndürür (doğrulama raporundaki satır numaralarıyla aynı). "
                     "Uzun programlarda start_line/max_lines ile sayfala. Programı kendin yazma; generate_gcode ile üret."),
     "parameters": {"type": "object", "properties": {
         "gcode_id": {"type": "string", "description": "Verilmezse mevcut program"},
         "start_line": {"type": "integer", "minimum": 1, "description": "Varsayılan 1"},
         "max_lines": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Varsayılan 200"}}}},
    {"name": "load_step",
     "description": "Bilgisayardaki bir STEP (.step/.stp) dosyasını yükleyip aktif model yapar; delik ve cepler otomatik tanınır.",
     "parameters": {"type": "object", "properties": {"path": PATH}, "required": ["path"]}},
    {"name": "upload_gcode",
     "description": ("Dışarıdan bir G-code programını sisteme alır ve seçili makine profiline göre doğrular. Dosya yolu (path) "
                     "ya da metin (text) ver. Sonuçta doğrulama özeti döner; ayrıntı için validate_gcode."),
     "parameters": {"type": "object", "properties": {
         "path": PATH, "text": {"type": "string", "description": "Program metni (path yerine)"},
         "name": {"type": "string"}}}},
    {"name": "save_gcode",
     "description": "Mevcut (veya verilen) G-code programını bilgisayardaki bir dosyaya yazar.",
     "parameters": {"type": "object", "properties": {
         "path": PATH, "gcode_id": {"type": "string"},
         "overwrite": {"type": "boolean", "description": "Dosya varsa üzerine yaz (varsayılan false)"}}, "required": ["path"]}},
    {"name": "add_engraving_svg",
     "description": ("Bilgisayardaki bir SVG logosunu parçanın üst yüzeyine (veya yukarı bakan bir yüzeye) kazır. size: logonun "
                     "büyük kenarı (mm). Sonra modify_feature ile taşınabilir/döndürülebilir."),
     "parameters": {"type": "object", "properties": {
         "path": PATH, "size": {"type": "number"}, "x": {"type": "number"}, "y": {"type": "number"},
         "depth": {"type": "number"}, "width": {"type": "number"}, "rotation": {"type": "number"},
         "face_id": {"type": "string"}}, "required": ["path"]}},
    {"name": "list_validation_rules",
     "description": "G-code doğrulayıcısının kural listesi (kod, seviye, önem, başlık).",
     "parameters": {"type": "object", "properties": {}}},
]


def _export_step_schema() -> dict[str, Any]:
    base = next(t for t in TOOL_SCHEMAS if t["name"] == "export_step")
    params = json.loads(json.dumps(base["parameters"]))
    params.setdefault("properties", {})
    params["properties"]["path"] = {**PATH, "description": "Yazılacak .step dosyasının yolu (verilmezse backend/data içinde kalır)"}
    params["properties"]["overwrite"] = {"type": "boolean", "description": "Dosya varsa üzerine yaz (varsayılan false)"}
    return {**base, "description": base["description"] + " path verilirse dosya oraya kopyalanır.", "parameters": params}


# Uzak (HTTP/tünel) modda bilgisayardaki dosyaları okuyan/yazan araçlar sunulmaz: link ele geçirilse
# bile kimse dosya sistemine erişemesin. G-code metin olarak yüklenebilir; STEP web arayüzünden yüklenir.
REMOTE_HIDDEN = {"load_step", "save_gcode", "add_engraving_svg"}


def _remote_upload_schema() -> dict[str, Any]:
    t = next(t for t in EXTRA_TOOLS if t["name"] == "upload_gcode")
    props = {k: v for k, v in t["parameters"]["properties"].items() if k != "path"}
    return {**t, "description": "Dışarıdan bir G-code programını METİN olarak sisteme alır ve seçili makine profiline göre doğrular.",
            "parameters": {"type": "object", "properties": props, "required": ["text"]}}


def tool_definitions(remote: bool = False) -> list[dict[str, Any]]:
    if remote:
        core = list(TOOL_SCHEMAS)  # export_step path parametresi yok
        extra = [_remote_upload_schema() if t["name"] == "upload_gcode" else t for t in EXTRA_TOOLS if t["name"] not in REMOTE_HIDDEN]
        return core + extra
    core = [_export_step_schema() if t["name"] == "export_step" else t for t in TOOL_SCHEMAS]
    return core + EXTRA_TOOLS


TOOL_DEFS = {t["name"]: t for t in tool_definitions()}


# ====================================================================== araç yürütme
def _out_path(path: str, overwrite: bool, suffixes: tuple[str, ...]) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        raise BackendError("Mutlak bir dosya yolu verin (ör. /Users/ad/Desktop/parca.step).")
    if p.suffix.lower() not in suffixes:
        p = p.with_suffix(suffixes[0])
    if p.exists() and not overwrite:
        raise BackendError(f"{p} zaten var. Üzerine yazmak için overwrite=true verin.")
    if not p.parent.exists():
        raise BackendError(f"Klasör yok: {p.parent}")
    return p


def _in_path(path: str, suffixes: tuple[str, ...] | None = None, max_bytes: int = 50_000_000) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_file():
        raise BackendError(f"Dosya bulunamadı: {p}")
    if suffixes and p.suffix.lower() not in suffixes:
        raise BackendError(f"Desteklenen uzantılar: {', '.join(suffixes)}")
    if p.stat().st_size > max_bytes:
        raise BackendError("Dosya çok büyük.")
    return p


def _gcode_id(b, gcode_id: str | None) -> str:
    gid = gcode_id or b.current_gcode_id()
    if not gid:
        raise BackendError("Henüz G-code yok. Önce generate_gcode ile üretin veya upload_gcode ile yükleyin.")
    return gid


def execute(router: Router, name: str, args: dict[str, Any]) -> tuple[Any, bytes | None]:
    """Aracı çalıştırır. (JSON'a çevrilecek sonuç, isteğe bağlı PNG) döndürür; hata BackendError'dur."""
    if router.remote and (name in REMOTE_HIDDEN or args.get("path")):
        raise BackendError("Uzak bağlantıda bilgisayardaki dosyalara erişen araçlar kapalıdır. "
                           "G-code'u metin olarak verin; STEP dosyasını web arayüzünden yükleyin.")
    b = router.get()
    if name == "get_context":
        return b.context() + "\n" + router.describe(b), None
    if name == "get_selection":
        sel = b.selection()
        many = b.selections() if hasattr(b, "selections") else []
        if len(many) > 1:  # web arayüzünde Shift ile birden fazla eleman seçilmiş
            return {"selection": sel, "selections": many, "note": f"{len(many)} eleman seçili; 'selection' son seçilendir."}, None
        return {"selection": sel, "note": None if sel else ("Seçili yüzey yok." if b is router.http else
                                                            "Yerel modda yüzey seçimi yok (web arayüzü kapalı).")}, None
    if name == "show_model_3d":
        mesh = b.mesh()
        sel = b.selection()
        s = mesh["summary"]
        brief = _model_brief(s, sel)
        size = " × ".join(f"{d:g}" for d in (brief["size_mm"] or []))
        text = (f"3B görüntüleyici açıldı: {s.get('name')} ({size} mm, sürüm {s.get('version')}); "
                f"delikler: {', '.join(brief['holes']) or 'yok'}; cepler: {', '.join(brief['pockets']) or 'yok'}. "
                "Kullanıcı görüntüleyicide yüzeye tıklayarak seçim yapabilir (get_selection). Görünümü metinle tekrar anlatma.")
        return UiResult(text, brief, {MESH_META_KEY: compact_mesh(mesh), "busproje/selection": sel}), None
    if name == "set_selection":
        if not hasattr(b, "set_selection"):
            raise BackendError("Bu bağlantıda seçim desteklenmiyor.")
        fid, ver = args.get("face_id"), args.get("model_version")
        sel = b.set_selection(fid, ver)
        if fid and sel is None:
            raise BackendError(f"{fid} seçilemedi: yüzey yok ya da model değişti (görüntüleyiciyi yenileyin).")
        return {"ok": True, "selection": sel}, None
    if name == "render_model":
        mesh = b.mesh()
        sel = b.selection()
        view = args.get("view") or "iso"
        png = render_model_png(mesh, view, sel["face_id"] if sel else None, args.get("labels", True))
        s = mesh["summary"]
        return {"model": s["name"], "version": s["version"], "view": view,
                "holes": [h["id"] for h in s["holes"]], "pockets": [p["id"] for p in s["pockets"]],
                "selected_face": sel["face_id"] if sel else None}, png
    if name == "render_toolpath":
        gid = _gcode_id(b, args.get("gcode_id"))
        tp = b.toolpath(gid)
        try:
            mesh = b.mesh()
        except BackendError:
            mesh = None
        return {"gcode_id": gid, "stats": tp["stats"]}, render_toolpath_png(tp, mesh, f"Program {gid}")
    if name == "get_gcode":
        gid = _gcode_id(b, args.get("gcode_id"))
        rec = b.gcode(gid)
        lines = rec["text"].splitlines()
        start = max(1, int(args.get("start_line") or 1))
        n = min(1000, max(1, int(args.get("max_lines") or 200)))
        chunk = lines[start - 1:start - 1 + n]
        v = rec.get("validation") or {}
        meta = {"gcode_id": gid, "source": rec.get("source"), "line_count": len(lines),
                "generated_for": rec.get("generated_for"), "stale": rec.get("stale"),
                "validation_counts": v.get("counts"), "shown": [start, start + len(chunk) - 1]}
        body = "\n".join(f"{start + i:>5}: {ln}" for i, ln in enumerate(chunk))
        more = f"\n… {len(lines) - (start - 1 + len(chunk))} satır daha (start_line={start + len(chunk)})" if start - 1 + len(chunk) < len(lines) else ""
        return json.dumps(meta, ensure_ascii=False) + "\n" + body + more, None
    if name == "load_step":
        return b.load_step(_in_path(args["path"], (".step", ".stp"))), None
    if name == "upload_gcode":
        if args.get("path"):
            p = _in_path(args["path"], max_bytes=5_000_000)
            text, default_name = p.read_text(encoding="utf-8", errors="replace"), p.name
        elif args.get("text"):
            text, default_name = args["text"], "mcp"
        else:
            raise BackendError("path veya text verin.")
        rec = b.upload_gcode(text, args.get("name") or default_name)
        v = rec.get("validation") or {}
        return {"ok": True, "gcode_id": rec["gcode_id"], "source": rec.get("source"), "line_count": rec.get("line_count"),
                "validation_summary": {"valid": v.get("valid"), "counts": v.get("counts"),
                                       "estimated_time_s": (v.get("stats") or {}).get("estimated_time_s")},
                "next": "Ayrıntılı bulgular için validate_gcode, satırlar için get_gcode."}, None
    if name == "save_gcode":
        gid = _gcode_id(b, args.get("gcode_id"))
        p = _out_path(args["path"], bool(args.get("overwrite")), (".nc", ".ngc", ".gcode", ".tap", ".txt", ".cnc"))
        rec = b.gcode(gid)
        p.write_text(rec["text"], encoding="utf-8")
        return {"ok": True, "gcode_id": gid, "path": str(p), "line_count": len(rec["text"].splitlines())}, None
    if name == "add_engraving_svg":
        p = _in_path(args["path"], (".svg",), max_bytes=2_000_000)
        params = {k: args[k] for k in ("size", "x", "y", "depth", "width", "rotation", "face_id") if args.get(k) is not None}
        params["name"] = p.stem
        return b.engrave_svg(p.read_text(encoding="utf-8", errors="replace"), params), None
    if name == "list_validation_rules":
        return b.rules(), None
    if name == "export_step":
        path, overwrite = args.get("path"), bool(args.get("overwrite"))
        dest = _out_path(path, overwrite, (".step", ".stp")) if path else None
        res = b.tool("export_step", {})
        if res.get("ok") is False:
            return res, None
        from .workspace import DATA_DIR

        src = DATA_DIR / res["file"]
        res = {"ok": True, "file": str(src)}
        if dest:
            shutil.copyfile(src, dest)
            res["path"] = str(dest)
        return res, None
    if name in TOOL_DEFS:
        return b.tool(name, args), None
    raise BackendError(f"Bilinmeyen araç: {name}")


# ====================================================================== MCP sunucusu
MCP_PREAMBLE = """Bu MCP sunucusu "Büyük Dil Modeli Temelli G-Kodu Geliştirme ve Kontrolü Sistemi" platformunun araçlarını sunar.
MCP'YE ÖZGÜ KURALLAR (aşağıdaki genel yönergelerden önce gelir):
- [BAĞLAM] bloğu otomatik gelmez. İşe başlarken, kullanıcı arayüzde bir şey değiştirmiş olabileceğinde ve birden fazla adımlı
  işlerde araya get_context çağrısı koy. Bağlam metni hangi modda çalıştığını (web backend'i / yerel) da söyler.
- "Seçtiğim yüzey/delik" gibi ifadelerde get_selection çağır; seçim 3B görüntüleyicide (web arayüzü veya show_model_3d) yapılır.
- Modeli kullanıcıya göstermek için show_model_3d kullan (etkileşimli 3B görüntüleyici sohbette açılır; görüntüyü metinle uzun uzun
  anlatma). render_model görüntüsü sana gelir, kendi kontrolün içindir. Takım yolu için render_toolpath. G-code'u get_gcode ile oku; asla kendin yazma.
- Dosya araçları (load_step, upload_gcode, save_gcode, export_step path, add_engraving_svg) kullanıcının bilgisayarındaki
  mutlak yollarla çalışır. SVG logo add_engraving_svg ile yüklenebilir.
- Araç hata döndürürse (isError) mesajı kullanıcıya sade dille açıkla; aynı parametrelerle körlemesine tekrar deneme.

GENEL YÖNERGELER (web sohbetiyle aynı):
"""
INSTRUCTIONS = MCP_PREAMBLE + SYSTEM_PROMPT
REMOTE_NOTE = """UZAK BAĞLANTI: Bu oturum bir tünel üzerinden uzaktan bağlı. Bilgisayardaki dosyalara erişen araçlar (load_step,
save_gcode, add_engraving_svg, dosya yolları) kapalıdır. G-code'u upload_gcode ile METİN olarak al; STEP yüklemek isteyen
kullanıcıya web arayüzünü kullanmasını söyle. Programı kullanıcıya vermek için get_gcode ile metni göster.

"""


def _dump(obj: Any) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n… (çıktı {len(text)} karakterden kısaltıldı)"
    return text


def build_server(router: Router | None = None) -> Server:
    router = router or Router()
    lock = threading.Lock()  # çekirdek tek kullanıcılı: çağrıları sıraya koy
    defs = {t["name"]: t for t in tool_definitions(router.remote)}
    instructions = (REMOTE_NOTE + INSTRUCTIONS) if router.remote else INSTRUCTIONS
    tools = [types.Tool(name=t["name"], description=t["description"], input_schema=t["parameters"],
                        annotations=types.ToolAnnotations(read_only_hint=t["name"] in READ_ONLY,
                                                          destructive_hint=False if t["name"] in READ_ONLY else None),
                        meta=UI_TOOL_META.get(t["name"]))
             for t in defs.values()]
    viewer_meta = {"ui": {"prefersBorder": True}}
    viewer_res = types.Resource(uri=VIEWER_URI, name="busproje-viewer", title="3B model görüntüleyici",
                                description="Aktif CAD modelinin etkileşimli 3B görünümü (yüzey seçimi)",
                                mime_type=APP_MIME, meta=viewer_meta)

    async def on_list_resources(ctx, params) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[viewer_res])

    async def on_read_resource(ctx, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        if str(params.uri) != VIEWER_URI:
            raise ValueError(f"Bilinmeyen kaynak: {params.uri}")
        return types.ReadResourceResult(contents=[types.TextResourceContents(
            uri=VIEWER_URI, mime_type=APP_MIME, text=viewer_html(), meta=viewer_meta)])

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    def _run(name: str, args: dict[str, Any]) -> types.CallToolResult:
        t0 = time.perf_counter()
        try:
            return _run_inner(name, args)
        finally:  # MCP_LOG_LEVEL=INFO ile her aracın süresi stderr'e yazılır (yavaşlık teşhisi)
            log.info("araç %s %.0f ms", name, (time.perf_counter() - t0) * 1000)

    def _run_inner(name: str, args: dict[str, Any]) -> types.CallToolResult:
        with lock:
            try:
                result, png = execute(router, name, args)
            except BackendError as exc:
                return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], is_error=True)
            except Exception as exc:  # beklenmeyen: istemciye ayrıntıyla dön, sunucu çökmesin
                log.exception("Araç hatası: %s", name)
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"İşlem başarısız: {type(exc).__name__}: {exc}")],
                                            is_error=True)
        if isinstance(result, UiResult):
            return types.CallToolResult(content=[types.TextContent(type="text", text=_dump(result.text))],
                                        structured_content=result.structured, meta=result.meta)
        content: list[Any] = [types.TextContent(type="text", text=_dump(result))]
        if png:
            content.append(types.ImageContent(type="image", data=base64.b64encode(png).decode(), mime_type="image/png"))
        failed = isinstance(result, dict) and result.get("ok") is False
        return types.CallToolResult(content=content, is_error=failed)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        if params.name not in defs:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Bilinmeyen araç: {params.name}")], is_error=True)
        return await anyio.to_thread.run_sync(_run, params.name, dict(params.arguments or {}))

    prompt = types.Prompt(name="cad_asistani", title="CAD/CAM asistanı",
                          description="Platformun kullanım kurallarını (koordinatlar, araçlar, doğrulama) sohbete ekler.")

    async def on_list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[prompt])

    async def on_get_prompt(ctx, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        if params.name != prompt.name:
            raise ValueError(f"Bilinmeyen prompt: {params.name}")
        return types.GetPromptResult(description=prompt.description, messages=[
            types.PromptMessage(role="user", content=types.TextContent(type="text", text=instructions))])

    server = Server(SERVER_NAME, version="0.2.0", title="LLM-Based CAD and G-Code Platform",
                    description="Natural-language CAD modeling, CNC G-code generation, and machine-profile-based validation",
                    instructions=instructions, on_list_tools=on_list_tools, on_call_tool=on_call_tool,
                    on_list_resources=on_list_resources, on_read_resource=on_read_resource,
                    on_list_prompts=on_list_prompts, on_get_prompt=on_get_prompt)
    server.extensions[APPS_EXTENSION] = {}  # MCP Apps desteğini ilan et
    return server


def http_app(token: str):
    """Uzak bağlantı için Streamable HTTP uygulaması. Cloudflare Quick Tunnel SSE desteklemediği için
    yanıtlar düz JSON'dur (json_response) ve oturumsuz çalışır (stateless_http)."""
    from mcp.server.transport_security import TransportSecuritySettings

    if len(token) < 16:
        raise ValueError("MCP_TOKEN en az 16 karakter olmalı")
    server = build_server(Router(remote=True))
    # Tünel adresi (xxx.trycloudflare.com) önceden bilinmediği için Host denetimi kapalı; erişimi
    # yoldaki gizli anahtar sınırlar ve sunucu yalnızca 127.0.0.1'i dinler.
    return server.streamable_http_app(streamable_http_path=f"/mcp/{token}", json_response=True, stateless_http=True,
                                      host="127.0.0.1",
                                      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


def serve_http() -> None:
    import uvicorn

    port = int(os.getenv("MCP_HTTP_PORT", "8765"))
    token = os.getenv("MCP_TOKEN") or secrets.token_urlsafe(24)
    print(f"MCP (HTTP) dinleniyor: http://127.0.0.1:{port}/mcp/{token}", file=sys.stderr, flush=True)
    uvicorn.run(http_app(token), host="127.0.0.1", port=port, log_level=os.getenv("MCP_HTTP_LOG", "warning"))


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def client_config() -> dict[str, Any]:
    """Bu bilgisayardaki mutlak yollarla MCP istemci yapılandırması (Claude Desktop / Cursor biçimi)."""
    backend = str(Path(__file__).resolve().parent.parent)
    return {"mcpServers": {SERVER_NAME: {"command": sys.executable, "args": ["-m", "app.mcp_server"],
                                         "env": {"PYTHONPATH": backend, "MCP_MODE": "auto"}}}}


def print_config() -> None:
    cfg = client_config()
    srv = cfg["mcpServers"][SERVER_NAME]
    print("# Claude Desktop (claude_desktop_config.json) ve Cursor (~/.cursor/mcp.json) için:")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print("\n# Claude Code için:")
    print(f"claude mcp add {SERVER_NAME} --env PYTHONPATH='{srv['env']['PYTHONPATH']}' --env MCP_MODE=auto "
          f"-- '{srv['command']}' -m app.mcp_server")


def main() -> None:
    if "--print-config" in sys.argv:
        print_config()
        return
    # .env (ör. CAD_API_URL, MCP_MODE) okunsun; LLM anahtarları bu modda kullanılmaz
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass
    if "--http" in sys.argv:
        serve_http()
        return
    anyio.run(_serve)


if __name__ == "__main__":
    main()
