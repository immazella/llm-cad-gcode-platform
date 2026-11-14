"""MCP sunucusu testleri: gerçek stdio bağlantısıyla araç listesi, uçtan uca akış, hata
dönüşü, görseller ve web backend'i (HTTP köprüsü) üzerinden çalışma."""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
EXAMPLES = BACKEND.parent / "examples"
PNG_MAGIC = b"\x89PNG"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _session_run(env: dict[str, str], steps):
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
                                   env={**os.environ, **env}, cwd=str(BACKEND))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await steps(s)


def run(env, steps):
    return anyio.run(_session_run, env, steps)


def text(res) -> str:
    return next(c.text for c in res.content if c.type == "text")


def image(res) -> bytes | None:
    im = next((c for c in res.content if c.type == "image"), None)
    return base64.b64decode(im.data) if im else None


@pytest.fixture
def local_env(tmp_path):
    return {"MCP_MODE": "local", "SESSION_FILE": str(tmp_path / "session.pkl")}


def test_tools_and_prompt_listed(local_env):
    async def steps(s):
        tools = {t.name: t for t in (await s.list_tools()).tools}
        prompt = await s.get_prompt("cad_asistani")
        return tools, prompt

    tools, prompt = run(local_env, steps)
    from app.llm.tool_schemas import TOOL_SCHEMAS

    # web sohbetindeki her araç MCP'de de var (tek kaynak) + MCP'ye özgü araçlar
    assert {t["name"] for t in TOOL_SCHEMAS} <= set(tools)
    assert {"get_context", "get_selection", "render_model", "render_toolpath", "get_gcode", "load_step"} <= set(tools)
    assert "path" in tools["export_step"].input_schema["properties"]
    assert tools["get_holes"].annotations.read_only_hint is True
    assert "get_context" in prompt.messages[0].content.text


def test_end_to_end_local(local_env, tmp_path):
    out = tmp_path / "parca.nc"

    async def steps(s):
        r = {}
        r["box"] = await s.call_tool("create_box", {"length": 60, "width": 60, "height": 10})
        r["hole"] = await s.call_tool("add_hole", {"diameter": 20})
        r["mod"] = await s.call_tool("modify_hole", {"hole_id": "hole_1", "new_diameter": 30})
        r["ctx"] = await s.call_tool("get_context", {})
        r["img"] = await s.call_tool("render_model", {"view": "top"})
        r["gen"] = await s.call_tool("generate_gcode", {})
        r["val"] = await s.call_tool("validate_gcode", {})
        r["lines"] = await s.call_tool("get_gcode", {"start_line": 1, "max_lines": 5})
        r["tp"] = await s.call_tool("render_toolpath", {})
        r["save"] = await s.call_tool("save_gcode", {"path": str(out)})
        r["save2"] = await s.call_tool("save_gcode", {"path": str(out)})  # var olan dosya: overwrite yok
        return r

    r = run(local_env, steps)
    assert not r["box"].is_error and not r["mod"].is_error
    assert json.loads(text(r["mod"]))["holes"][0]["diameter"] == 30
    assert "hole_1" in text(r["ctx"]) and "yerel mod" in text(r["ctx"])
    assert image(r["img"]).startswith(PNG_MAGIC)
    gen = json.loads(text(r["gen"]))
    assert gen["ok"] and gen["validation_summary"]["counts"]["error"] == 0
    assert json.loads(text(r["val"]))["valid"] is True
    assert "    1: %" in text(r["lines"]) and "satır daha" in text(r["lines"])
    assert image(r["tp"]).startswith(PNG_MAGIC)
    assert not r["save"].is_error and out.read_text().startswith("%")
    assert r["save2"].is_error and "overwrite" in text(r["save2"])


def test_errors_are_flagged(local_env):
    async def steps(s):
        no_model = await s.call_tool("add_hole", {"diameter": 5})
        missing = await s.call_tool("load_step", {"path": "/yok/boyle/bir/dosya.step"})
        return no_model, missing

    no_model, missing = run(local_env, steps)
    assert no_model.is_error and "model" in text(no_model).lower()
    assert missing.is_error and "bulunamadı" in text(missing)


def test_step_and_gcode_upload(local_env):
    async def steps(s):
        step = await s.call_tool("load_step", {"path": str(EXAMPLES / "ornek_flans.step")})
        nc = await s.call_tool("upload_gcode", {"path": str(EXAMPLES / "hatali_ornek.nc")})
        return step, nc

    step, nc = run(local_env, steps)
    assert len(json.loads(text(step))["model"]["holes"]) >= 1
    v = json.loads(text(nc))["validation_summary"]
    assert v["valid"] is False and v["counts"]["error"] > 0


def test_local_session_persists(local_env):
    """Yerel modda oturum diske yazılır; yeni süreç (ya da web backend'i) aynı modeli görür."""
    run(local_env, lambda s: s.call_tool("create_cylinder", {"radius": 25, "height": 8}))
    res = run(local_env, lambda s: s.call_tool("get_context", {}))
    assert "Silindir R25 H8" in text(res)


@pytest.fixture
def web_backend(tmp_path):
    port = _free_port()
    env = {**os.environ, "SESSION_FILE": str(tmp_path / "web.pkl"), "LLM_PROVIDER": "offline"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=str(BACKEND), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import httpx

    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(url + "/api/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("web backend başlamadı")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


def test_http_bridge_shares_state_and_selection(web_backend, tmp_path):
    import httpx

    env = {"MCP_MODE": "http", "CAD_API_URL": web_backend, "SESSION_FILE": str(tmp_path / "unused.pkl")}
    run(env, lambda s: s.call_tool("create_box", {"length": 50, "width": 40, "height": 10}))
    run(env, lambda s: s.call_tool("add_hole", {"diameter": 8, "x": 10}))

    # web arayüzü değişikliği görür (model + harici değişiklik sayacı)
    st = httpx.get(web_backend + "/api/state").json()
    assert st["model"]["name"].startswith("Kutu 50x40x10") and len(st["model"]["holes"]) == 1
    assert st["external"]["rev"] == 2

    # kullanıcı görüntüleyicide deliğin iç yüzeyini seçer → MCP get_selection bunu görür
    mesh = httpx.get(web_backend + "/api/models/current/mesh").json()
    face = next(f["id"] for f in mesh["faces"] if f["hole_id"])
    httpx.put(web_backend + "/api/selection", json={"face_id": face, "model_version": mesh["version"]})

    async def steps(s):
        sel = await s.call_tool("get_selection", {})
        ctx = await s.call_tool("get_context", {})
        mod = await s.call_tool("modify_hole", {"hole_id": json.loads(text(sel))["selection"]["hole_id"], "new_diameter": 12})
        sel_after = await s.call_tool("get_selection", {})
        return sel, ctx, mod, sel_after

    sel, ctx, mod, sel_after = run(env, steps)
    assert json.loads(text(sel))["selection"]["hole_id"] == "hole_1"
    assert "Seçili yüzey: {" in text(ctx) and "web backend" in text(ctx)
    assert json.loads(text(mod))["holes"][0]["diameter"] == 12
    # model sürümü değişti: eski seçim geçersiz
    assert json.loads(text(sel_after))["selection"] is None


# ---------------------------------------------------------------- uzak (HTTP / tünel) mod
@pytest.fixture
def remote_server(tmp_path):
    """--http ile başlatılan sunucu; Host başlığı tünel adresi gibi davranır."""
    port = _free_port()
    token = "test-anahtar-0123456789abcdef"  # gitleaks:allow -- local test fixture, not a real credential
    env = {**os.environ, "MCP_HTTP_PORT": str(port), "MCP_TOKEN": token, "MCP_MODE": "local",
           "SESSION_FILE": str(tmp_path / "remote.pkl")}
    proc = subprocess.Popen([sys.executable, "-m", "app.mcp_server", "--http"], cwd=str(BACKEND), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import httpx

    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            httpx.get(base + "/", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("HTTP MCP sunucusu başlamadı")
    yield base, token
    proc.terminate()
    proc.wait(timeout=10)


def _rpc(base: str, path: str, method: str, params: dict | None = None, host: str = "abc-def.trycloudflare.com"):
    """SSE'siz düz JSON yanıtı (Cloudflare Quick Tunnel uyumu) bekleyen ham JSON-RPC çağrısı."""
    import httpx

    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return httpx.post(base + path, json=body, timeout=120,
                      headers={"Host": host, "Accept": "application/json, text/event-stream",
                               "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"})


def test_remote_http_mode(remote_server):
    from mcp.client.streamable_http import streamable_http_client

    base, token = remote_server
    url = f"{base}/mcp/{token}"

    # yanlış anahtar: uç nokta yok
    assert _rpc(base, "/mcp/yanlis-anahtar", "tools/list").status_code == 404

    async def steps():
        async with streamable_http_client(url) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                box = await s.call_tool("create_box", {"length": 40, "width": 30, "height": 8})
                img = await s.call_tool("render_model", {})
                up = await s.call_tool("upload_gcode", {"text": "G21 G90\nG0 X0 Y0\nM30\n"})
                blocked = await s.call_tool("export_step", {"path": "/tmp/x.step"})
                hidden = await s.call_tool("save_gcode", {"path": "/tmp/x.nc"})
                return names, box, img, up, blocked, hidden

    names, box, img, up, blocked, hidden = anyio.run(steps)
    assert not ({"load_step", "save_gcode", "add_engraving_svg"} & names)
    assert "get_context" in names and "generate_gcode" in names
    assert not box.is_error and image(img).startswith(PNG_MAGIC)
    assert not up.is_error and json.loads(text(up))["ok"]
    assert blocked.is_error and "dosya" in text(blocked)
    assert hidden.is_error

    # tünelden gelen istek: Host başlığı farklı, yanıt SSE değil düz JSON olmalı
    r = _rpc(base, f"/mcp/{token}", "tools/list")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert any(t["name"] == "create_box" for t in r.json()["result"]["tools"])


# ---------------------------------------------------------------- MCP Apps: etkileşimli 3B görüntüleyici
def test_apps_viewer_tool_and_resource(local_env):
    """show_model_3d arayüze bağlı (ui:// kaynağı), mesh yalnızca _meta'da, seçim görüntüleyiciden yapılır."""
    import numpy as np

    from app.mcp_server import APP_MIME, MESH_META_KEY, VIEWER_URI

    async def steps(s):
        r = {}
        r["tools"] = {t.name: t for t in (await s.list_tools()).tools}
        r["res"] = (await s.list_resources()).resources
        r["read"] = await s.read_resource(VIEWER_URI)
        r["empty"] = await s.call_tool("show_model_3d", {})
        await s.call_tool("create_box", {"length": 60, "width": 60, "height": 10})
        await s.call_tool("add_hole", {"diameter": 20})
        r["show"] = await s.call_tool("show_model_3d", {})
        mesh = r["show"].meta[MESH_META_KEY]
        face = next(f["id"] for f in mesh["faces"] if f["hole_id"])
        r["sel"] = await s.call_tool("set_selection", {"face_id": face, "model_version": mesh["version"]})
        r["get"] = await s.call_tool("get_selection", {})
        r["ctx"] = await s.call_tool("get_context", {})
        r["stale"] = await s.call_tool("set_selection", {"face_id": face, "model_version": mesh["version"] - 1})
        r["clear"] = await s.call_tool("set_selection", {"face_id": None, "model_version": mesh["version"]})
        return r

    r = run(local_env, steps)
    t = r["tools"]["show_model_3d"]
    assert t.meta["ui"]["resourceUri"] == VIEWER_URI and t.meta["openai/outputTemplate"] == VIEWER_URI
    assert "resourceUri" not in r["tools"]["set_selection"].meta["ui"]  # kendi başına arayüz açmaz
    assert r["tools"]["get_holes"].meta is None

    assert [x.uri for x in r["res"]] == [VIEWER_URI] and r["res"][0].mime_type == APP_MIME
    html = r["read"].contents[0]
    assert html.mime_type == APP_MIME and "<html" in html.text.lower()

    assert r["empty"].is_error  # model yokken anlaşılır hata
    show = r["show"]
    assert not show.is_error
    assert show.structured_content["holes"] == ["hole_1"] and show.structured_content["version"] >= 2
    assert "hole_1" in text(show) and len(text(show)) < 600  # LLM'ye giden kısa özet; mesh metinde yok
    mesh = show.meta[MESH_META_KEY]
    f0 = mesh["faces"][0]
    verts = np.frombuffer(base64.b64decode(f0["v"]), dtype="<f4")
    idx = np.frombuffer(base64.b64decode(f0["i"]), dtype="<u4" if f0["i32"] else "<u2")
    assert len(verts) % 3 == 0 and len(idx) % 3 == 0 and idx.max() < len(verts) // 3
    assert len(base64.b64decode(mesh["edges"])) % 24 == 0  # segment başına 2 nokta × 3 × float32

    sel = json.loads(text(r["sel"]))["selection"]
    assert sel["hole_id"] == "hole_1"
    assert json.loads(text(r["get"]))["selection"]["face_id"] == sel["face_id"]
    assert "Seçili yüzey: {" in text(r["ctx"])
    assert r["stale"].is_error and "model değişti" in text(r["stale"])
    assert json.loads(text(r["clear"]))["selection"] is None


def test_apps_viewer_over_http_bridge(web_backend, tmp_path):
    """Web backend açıkken görüntüleyicideki seçim web arayüzünün seçimiyle aynı yere yazılır."""
    import httpx

    env = {"MCP_MODE": "http", "CAD_API_URL": web_backend, "SESSION_FILE": str(tmp_path / "unused.pkl")}

    async def steps(s):
        await s.call_tool("create_box", {"length": 50, "width": 40, "height": 10})
        await s.call_tool("add_pocket", {"length": 20, "width": 10, "depth": 3})
        show = await s.call_tool("show_model_3d", {})
        mesh = show.meta["busproje/mesh"]
        face = next(f["id"] for f in mesh["faces"] if f["pocket_id"])
        sel = await s.call_tool("set_selection", {"face_id": face, "model_version": mesh["version"]})
        return show, sel

    show, sel = run(env, steps)
    assert not show.is_error and show.structured_content["pockets"] == ["pocket_1"]
    assert json.loads(text(sel))["selection"]["pocket_id"] == "pocket_1"
    assert httpx.get(web_backend + "/api/selection").json()["selection"]["pocket_id"] == "pocket_1"
