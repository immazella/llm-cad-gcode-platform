import { App, type McpUiHostContext } from "@modelcontextprotocol/ext-apps/app-with-deps";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { decodeEdges, decodeFaces, type CompactMesh, type SelectionInfo } from "./mesh";
import { createLocalePreference, describeSelection, translate, type Locale, type MessageKey } from "./i18n";
import Viewer, { DARK, LIGHT } from "./Viewer";
import "./styles.css";

const MESH_KEY = "busproje/mesh";
const SEL_KEY = "busproje/selection";
const preference = createLocalePreference();

type ToolResult = {
  content?: { type: string; text?: string }[];
  structuredContent?: unknown;
  isError?: boolean;
  _meta?: Record<string, unknown>;
};
// Keep local errors as catalog keys so an existing error follows a language switch.
// Server/user text and exception diagnostics are opaque: never rewrite them.
type ViewerError = { key: MessageKey; detail?: string } | { external: string };

function textOf(r: ToolResult): string {
  return (r.content ?? []).filter((c) => c.type === "text").map((c) => c.text ?? "").join("\n");
}

function Root() {
  const [locale, setLocale] = useState<Locale>(() => preference.read());
  const localeRef = useRef(locale);
  localeRef.current = locale;
  const t = (key: MessageKey) => translate(locale, key);
  const changeLocale = (next: Locale) => {
    preference.write(next);
    setLocale(next);
  };
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = translate(locale, "title");
  }, [locale]);

  const appRef = useRef<App | null>(null);
  const [connected, setConnected] = useState<"wait" | "ok" | "none">("wait");
  const [ctx, setCtx] = useState<McpUiHostContext | undefined>();
  const [mesh, setMesh] = useState<CompactMesh | null>(null);
  const [selection, setSelection] = useState<SelectionInfo | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ViewerError | null>(null);
  const errorText = error && ("external" in error ? error.external : translate(locale, error.key, { detail: error.detail ?? "" }));

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
      if (r.isError) setError(textOf(r) ? { external: textOf(r) } : { key: "modelError" });
      else if (!accept(r)) setError({ key: "missingMesh" });
    } catch (e) {
      setError({ key: "modelErrorDetail", detail: (e as Error).message });
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
        setError(textOf(res) ? { external: textOf(res) } : { key: "toolError" });
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
          setError(textOf(r) ? { external: textOf(r) } : { key: "selectionToolError" });
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
        // Read current locale after the async response, without reconnecting the host.
        app
          .updateModelContext({
            content: [{ type: "text", text: describeSelection(localeRef.current, sel, mesh.version) }],
          })
          .catch(() => {});
      } catch (e) {
        setSelection(prev);
        setError({ key: "selectionError", detail: (e as Error).message });
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
            {connected === "none" ? t("hostRequired") : errorText ?? t("loading")}
          </div>
        )}

        {s && (
          <div className="panel head">
            <div className="title">{s.name}</div>
            <div className="sub">{size} mm · {t("version")} {s.version}</div>
          </div>
        )}

        <div className="tools">
          <label className="language-control" htmlFor="viewer-language">
            <span>{t("language")}</span>
            <select id="viewer-language" aria-label={t("language")} value={locale} onChange={(e) => changeLocale(e.target.value === "en" ? "en" : "tr")}>
              <option value="tr" lang="tr">Türkçe</option>
              <option value="en" lang="en">English</option>
            </select>
          </label>
          <button onClick={refresh} disabled={busy || connected !== "ok"} title={t("refreshTitle")}>
            {busy ? "…" : "↻"} {t("refresh")}
          </button>
          {canFullscreen && (
            <button onClick={toggleFullscreen} title={t(fullscreen ? "exitFullscreen" : "fullscreen")}>
              {t(fullscreen ? "exitFullscreen" : "fullscreen")}
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
                <div className="sub">{selection.role ?? t("selected")}</div>
                <div className="hint">{t("selectionHint")}</div>
              </>
            ) : (
              <>
                <div className="legend">
                  <span><i className="dot hole" />{t("hole")}</span>
                  <span><i className="dot pocket" />{t("pocket")}</span>
                  <span><i className="dot feature" />{t("feature")}</span>
                </div>
                <div className="hint">{t("controlsHint")}</div>
              </>
            )}
          </div>
        )}

        {errorText && mesh && <button className="toast" title={t("dismissError")} onClick={() => setError(null)}>{errorText}</button>}
      </div>
    </div>
  );
}

// StrictMode yok: geliştirmede efekt iki kez çalışır ve host'a iki kez bağlanmaya çalışır
createRoot(document.getElementById("root")!).render(<Root />);
