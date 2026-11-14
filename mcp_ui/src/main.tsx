import { App, type McpUiHostContext } from "@modelcontextprotocol/ext-apps/app-with-deps";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { decodeEdges, decodeFaces, type CompactMesh, type SelectionInfo } from "./mesh";
import Viewer, { DARK, LIGHT } from "./Viewer";
import "./styles.css";

const MESH_KEY = "busproje/mesh";
const SEL_KEY = "busproje/selection";

type ToolResult = {
  content?: { type: string; text?: string }[];
  structuredContent?: unknown;
  isError?: boolean;
  _meta?: Record<string, unknown>;
};

function textOf(r: ToolResult): string {
  return (r.content ?? []).filter((c) => c.type === "text").map((c) => c.text ?? "").join("\n");
}

function describe(sel: SelectionInfo, version: number): string {
  const what = sel.role ?? sel.face_id;
  return (
    `Kullanıcı 3B görüntüleyicide ${sel.face_id} yüzeyini seçti: ${what} (model sürümü ${version}). ` +
    `"Seçtiğim yüzey/delik" derse bunu kastediyor; gerekirse get_selection ile doğrula.`
  );
}

function Root() {
  const appRef = useRef<App | null>(null);
  const [connected, setConnected] = useState<"wait" | "ok" | "none">("wait");
  const [ctx, setCtx] = useState<McpUiHostContext | undefined>();
  const [mesh, setMesh] = useState<CompactMesh | null>(null);
  const [selection, setSelection] = useState<SelectionInfo | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = useCallback((r: ToolResult): boolean => {
    const m = r._meta?.[MESH_KEY] as CompactMesh | undefined;
    if (!m) return false;
    setMesh(m);
    setSelection((r._meta?.[SEL_KEY] as SelectionInfo | null) ?? null);
    setError(null);
    return true;
  }, []);

  const refresh = useCallback(async () => {
    const app = appRef.current;
    if (!app) return;
    setBusy(true);
    try {
      const r = (await app.callServerTool({ name: "show_model_3d", arguments: {} })) as ToolResult;
      if (r.isError) setError(textOf(r) || "Model alınamadı.");
      else if (!accept(r)) setError("Sunucu mesh verisi göndermedi.");
    } catch (e) {
      setError(`Model alınamadı: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [accept]);

  useEffect(() => {
    const app = new App({ name: "busproje-viewer", version: "0.2.0" }, {}, { autoResize: true });
    appRef.current = app;
    // İşleyiciler connect'ten ÖNCE kurulmalı: sonuç el sıkışmadan hemen sonra gelir
    app.ontoolresult = (r) => {
      const res = r as ToolResult;
      if (res.isError) {
        setError(textOf(res) || "Araç hata döndürdü.");
        return;
      }
      // Bazı istemciler sonucun _meta'sını iletmeyebilir: o durumda veriyi kendimiz isteriz
      if (!accept(res)) void refresh();
    };
    app.onhostcontextchanged = (c) => setCtx((prev) => ({ ...(prev ?? {}), ...c }));
    const timer = window.setTimeout(() => setConnected((s) => (s === "wait" ? "none" : s)), 4000);
    app
      .connect()
      .then(() => {
        setConnected("ok");
        setCtx(app.getHostContext());
      })
      .catch(() => setConnected("none"));
    return () => window.clearTimeout(timer);
  }, [accept, refresh]);

  const theme = ctx?.theme === "light" ? "light" : "dark";
  const palette = theme === "light" ? LIGHT : DARK;
  const fullscreen = ctx?.displayMode === "fullscreen";
  const canFullscreen = !!ctx?.availableDisplayModes?.includes("fullscreen");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const faces = useMemo(() => (mesh ? decodeFaces(mesh) : []), [mesh]);
  const edges = useMemo(() => (mesh ? decodeEdges(mesh) : new Float32Array(0)), [mesh]);
  const featureTypes = useMemo(
    () => Object.fromEntries((mesh?.summary.features ?? []).map((f) => [f.id, f.type])),
    [mesh],
  );

  const select = useCallback(
    async (faceId: string | null) => {
      const app = appRef.current;
      if (!app || !mesh) return;
      const prev = selection;
      setSelection(faceId ? { face_id: faceId } : null); // anında vurgula
      try {
        const r = (await app.callServerTool({
          name: "set_selection",
          arguments: { face_id: faceId, model_version: mesh.version },
        })) as ToolResult;
        if (r.isError) {
          setSelection(prev);
          setError(textOf(r));
          return;
        }
        let sel: SelectionInfo | null = null;
        try {
          sel = (JSON.parse(textOf(r)).selection as SelectionInfo | null) ?? null;
        } catch {
          sel = faceId ? { face_id: faceId } : null;
        }
        setSelection(sel);
        setError(null);
        // Modelin bir sonraki turda seçimi bilmesi için (destekleyen istemcilerde)
        app
          .updateModelContext({
            content: [{ type: "text", text: sel ? describe(sel, mesh.version) : "Kullanıcı görüntüleyicide seçimi kaldırdı." }],
          })
          .catch(() => {});
      } catch (e) {
        setSelection(prev);
        setError(`Seçim gönderilemedi: ${(e as Error).message}`);
      }
    },
    [mesh, selection],
  );

  const toggleFullscreen = async () => {
    const app = appRef.current;
    if (!app) return;
    try {
      const r = await app.requestDisplayMode({ mode: fullscreen ? "inline" : "fullscreen" });
      setCtx((c) => ({ ...(c ?? {}), displayMode: r.mode }));
    } catch {
      /* istemci desteklemiyor */
    }
  };

  const s = mesh?.summary;
  const size = s?.bbox?.size?.map((d) => +d.toFixed(2)).join(" × ");

  return (
    <div className={`shell ${fullscreen ? "full" : ""}`}>
      <div className="stage">
        {mesh ? (
          <Viewer
            faces={faces}
            featureTypes={featureTypes}
            edges={edges}
            bbox={s?.bbox ?? null}
            fitKey={`${mesh.model_id}-${mesh.version}`}
            palette={palette}
            hovered={hovered}
            selected={selection?.face_id ?? null}
            onHover={setHovered}
            onSelect={select}
          />
        ) : (
          <div className="empty">
            {connected === "none"
              ? "Bu sayfa bir MCP istemcisinin (ChatGPT, Claude…) içinde açılmalı."
              : error ?? "Model yükleniyor…"}
          </div>
        )}

        {s && (
          <div className="panel head">
            <div className="title">{s.name}</div>
            <div className="sub">
              {size} mm · sürüm {s.version}
            </div>
          </div>
        )}

        <div className="tools">
          <button onClick={refresh} disabled={busy || connected !== "ok"} title="Modeli yeniden al">
            {busy ? "…" : "↻"} Yenile
          </button>
          {canFullscreen && (
            <button onClick={toggleFullscreen} title="Tam ekran">
              {fullscreen ? "Küçült" : "Tam ekran"}
            </button>
          )}
        </div>

        {mesh && (
          <div className="panel foot">
            {selection ? (
              <>
                <div className="title">
                  <span className="dot sel" /> {selection.face_id}
                  {selection.hole_id ? ` · ${selection.hole_id}` : selection.pocket_id ? ` · ${selection.pocket_id}` : ""}
                </div>
                <div className="sub">{selection.role ?? "seçildi"}</div>
                <div className="hint">Sohbette "seçtiğim yüzey" diyerek bu yüzey üzerinde işlem isteyebilirsiniz.</div>
              </>
            ) : (
              <>
                <div className="legend">
                  <span><i className="dot hole" />delik</span>
                  <span><i className="dot pocket" />cep</span>
                  <span><i className="dot feature" />kazıma / özellik</span>
                </div>
                <div className="hint">Döndür: sürükle · Yakınlaş: tekerlek · Seç: yüzeye tıkla</div>
              </>
            )}
          </div>
        )}

        {error && mesh && <div className="toast" onClick={() => setError(null)}>{error}</div>}
      </div>
    </div>
  );
}

// StrictMode yok: geliştirmede efekt iki kez çalışır ve host'a iki kez bağlanmaya çalışır
createRoot(document.getElementById("root")!).render(<Root />);
