import { tx, useLocale, LanguageSelector } from "./i18n";
import { enumLabel, selectionLabel } from "./presentation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import ChatPanel from "./components/ChatPanel";
import Inspector from "./components/Inspector";
import ModelViewer from "./components/ModelViewer";
import ToolpathViewer from "./components/ToolpathViewer";
import AuditView from "./components/AuditView";
import ConversationDrawer from "./components/ConversationDrawer";
import type { AppState, ChatMessage, ConvPayload, ConvSummary, GcodeRecord, MachineProfile, MachiningConfig, MeshData, RegionInfo, RegionRequest, Selection, Toolpath } from "./types";
import { isRegion, selectionId } from "./types";

export default function App() {
  const { locale } = useLocale();
  const [llm, setLlm] = useState<AppState["llm"] | null>(null);
  const [mesh, setMesh] = useState<MeshData | null>(null);
  const [config, setConfig] = useState<MachiningConfig | null>(null);
  const [profiles, setProfiles] = useState<MachineProfile[]>([]);
  const [gcode, setGcode] = useState<GcodeRecord | null>(null);
  const [toolpath, setToolpath] = useState<Toolpath | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  // Seçim: Shift+tık ile birden fazla yüzey/bölge; son eleman "birincil" seçimdir
  const [selected, setSelection] = useState<Selection[]>([]);
  const [regions, setRegions] = useState<RegionInfo[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"model" | "toolpath" | "audit">("model");
  const [auditRev, setAuditRev] = useState(0); // Yeni oturum audit kayıtlarını siler: açık audit ekranı yenilensin
  const [tab, setTab] = useState<"model" | "gcode" | "shop" | "settings">("model");
  // Atölye sekmesinden "takım yolunda göster": takım yolu görünümü o satıra atlar
  const [seek, setSeek] = useState<{ line: number; n: number } | null>(null);
  const showLine = useCallback((line: number) => {
    setView("toolpath");
    setSeek({ line, n: Date.now() });
  }, []);
  const [activeLine, setActiveLine] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const stepInput = useRef<HTMLInputElement>(null);
  // Oturum backend'den geri yüklenmeden arayüz durumunu kaydetme (boş durum kaydı ezmesin)
  const uiRestored = useRef(false);
  // Sohbetler: aktif sohbetin kimliği, liste ve sol çekmece
  const [convId, setConvId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConvSummary[]>([]);
  const [drawer, setDrawer] = useState(false);
  const convRef = useRef<string | null>(null);
  convRef.current = convId;
  // MCP istemcisinin yaptığı değişiklikleri izlemek için: son görülen sayaç ve güncel ekran durumu
  const extRev = useRef<number | null>(null);
  const meshRef = useRef<MeshData | null>(null);
  meshRef.current = mesh;

  const model = mesh?.summary ?? null;
  const selectedIds = useMemo(() => new Set(selected.map(selectionId)), [selected]);
  const notify = useCallback((m: string) => {
    setToast(m);
    window.setTimeout(() => setToast((t) => (t === m ? null : t)), 5000);
  }, []);

  // Bölgeler model değişince backend'de yeniden yerleştirilir; yerleştirilemeyenler düşer
  const refreshRegions = useCallback(async () => {
    try {
      const r = await api.regions();
      setRegions(r.regions);
      if (r.dropped.length) notify(tx("Model değiştiği için {0} bölgesi kaldırıldı.", {"0": r.dropped.join(", ")}));
    } catch {
      setRegions([]);
    }
  }, [notify]);

  const refreshModel = useCallback(async () => {
    try {
      const m = await api.mesh();
      setMesh(m);
      setSelection([]);
      setHovered(null);
      await refreshRegions();
    } catch {
      setMesh(null);
      setRegions([]);
    }
  }, [refreshRegions]);

  const loadGcode = useCallback(async (g: GcodeRecord | string) => {
    const rec = typeof g === "string" ? await api.gcode(g) : g;
    setGcode(rec);
    setToolpath(await api.toolpath(rec.gcode_id));
  }, []);

  // Refresh presentation only: preserve CAD state, code bytes, history and drafts.
  const previousLocale = useRef(locale);
  useEffect(() => {
    if (previousLocale.current === locale) return;
    previousLocale.current = locale;
    if (!gcode) return;
    let active = true;
    void api.gcode(gcode.gcode_id).then((localized) => {
      if (active) setGcode((current) => current?.gcode_id === localized.gcode_id
        ? { ...current, validation: localized.validation, warnings: localized.warnings, plan: localized.plan }
        : current);
    }).catch(() => { /* Keep the existing report when offline. */ });
    return () => { active = false; };
  }, [locale, gcode?.gcode_id]);

  useEffect(() => {
    api
      .state()
      .then(async (s) => {
        setLlm(s.llm);
        setConfig(s.config);
        if (s.ui?.messages) setMessages(s.ui.messages);
        if (s.ui?.conversation) setConvId(s.ui.conversation.id);
        api.conversations().then((r) => setConversations(r.conversations), () => {});
        if (s.ui?.view) setView(s.ui.view);
        if (s.ui?.tab) setTab(s.ui.tab);
        uiRestored.current = true;
        extRev.current = s.external?.rev ?? 0;
        setProfiles(await api.profiles());
        if (s.model) await refreshModel();
        if (s.gcode) await loadGcode(s.gcode.gcode_id);
      })
      .catch(() => notify(tx("Backend'e bağlanılamadı. FastAPI sunucusunun (port 8000) çalıştığından emin olun.")));
  }, [refreshModel, loadGcode, notify]);

  // Sohbet mesajları ve açık sekmeler backend oturumuna kaydedilir (sayfa yenilense de kalır)
  useEffect(() => {
    if (!uiRestored.current) return;
    const cid = convRef.current ?? undefined;
    const t = window.setTimeout(() => void api.putUi({ messages, view, tab, conversation_id: cid }).catch(() => {}), 400);
    return () => window.clearTimeout(t);
  }, [messages, view, tab]);

  // Backend modeli bellekte tutar; yeniden başlarsa (ör. --reload) ekrandaki model eskide kalır.
  // Ekrandaki durumu backend'dekiyle eşitler; bir fark bulduysa true döner.
  const resync = useCallback(async (): Promise<boolean> => {
    let s: AppState;
    try {
      s = await api.state();
    } catch {
      return false;
    }
    setLlm(s.llm);
    if (!s.model) {
      if (!mesh) return false;
      setMesh(null);
      setSelection([]);
      setRegions([]);
      setHovered(null);
      if (!s.gcode) {
        setGcode(null);
        setToolpath(null);
      }
      notify(tx("Backend yeniden başlatıldığı için model bellekten silindi. Modeli yeniden oluşturun veya STEP dosyasını tekrar yükleyin."));
      return true;
    }
    if (!mesh || s.model.model_id !== mesh.model_id || s.model.version !== mesh.version) {
      await refreshModel();
      notify(tx("Model backend ile yeniden eşitlendi. Seçimi tekrar yapın."));
      return true;
    }
    return false;
  }, [mesh, refreshModel, notify]);

  useEffect(() => {
    const onFocus = () => void resync();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [resync]);

  // MCP (Claude Desktop, Cursor, ChatGPT…) üzerinden yapılan değişiklikleri izle: backend'in harici
  // değişiklik sayacı artınca modeli, G-code'u ve ayarları yeniden yükle.
  useEffect(() => {
    const t = window.setInterval(async () => {
      if (busy || extRev.current === null) return;
      let s: AppState;
      try {
        s = await api.state();
      } catch {
        return;
      }
      const rev = s.external?.rev ?? 0;
      if (rev === extRev.current) return;
      extRev.current = rev;
      setConfig(s.config);
      const m = meshRef.current;
      if (!s.model) {
        setMesh(null);
        setSelection([]);
        setRegions([]);
      } else if (!m || s.model.model_id !== m.model_id || s.model.version !== m.version) {
        await refreshModel();
      }
      if (!s.gcode) {
        setGcode(null);
        setToolpath(null);
      } else {
        await loadGcode(s.gcode.gcode_id);
      }
      notify(tx("Harici istemci (MCP) değişiklik yaptı{0}.", {"0": s.external?.last ? `: ${s.external.last}` : ""}));
    }, 1500);
    return () => window.clearInterval(t);
  }, [busy, refreshModel, loadGcode, notify]);

  // Seçimi ekrana ve backend'e (MCP get_selection) bildirir
  const commitSelection = useCallback(
    (list: Selection[], version: number | null) => {
      setSelection(list);
      const last = list.length ? list[list.length - 1] : null;
      void api
        .putSelection(last && !isRegion(last) ? last.face_id : null, version, isRegion(last) ? last.region_id : null, list.map(selectionId))
        .catch(() => {});
    },
    [],
  );

  /** add=true (Shift+tık): seçime ekle / zaten seçiliyse çıkar */
  const toggleOrReplace = useCallback(
    (item: Selection, add: boolean, version: number | null) => {
      const id = selectionId(item);
      if (!add) return commitSelection([item], version);
      setSelection((cur) => {
        const next = cur.some((x) => selectionId(x) === id) ? cur.filter((x) => selectionId(x) !== id) : [...cur, item];
        const last = next.length ? next[next.length - 1] : null;
        void api
          .putSelection(last && !isRegion(last) ? last.face_id : null, version, isRegion(last) ? last.region_id : null, next.map(selectionId))
          .catch(() => {});
        return next;
      });
    },
    [commitSelection],
  );

  const selectFace = useCallback(
    async (id: string | null, add = false) => {
      if (!id || !mesh) {
        if (!add) commitSelection([], null);
        return;
      }
      if (add && selected.some((x) => !isRegion(x) && x.face_id === id)) {
        // zaten seçili: bilgiyi yeniden çekmeden çıkar
        return toggleOrReplace(selected.find((x) => !isRegion(x) && x.face_id === id)!, true, mesh.version);
      }
      try {
        toggleOrReplace(await api.face(id, mesh.version), add, mesh.version);
      } catch (e) {
        if (!(await resync())) notify((e as Error).message);
      }
    },
    [mesh, selected, notify, resync, commitSelection, toggleOrReplace],
  );

  const selectRegion = useCallback(
    (id: string | null, add = false) => {
      const r = id ? regions.find((x) => x.region_id === id) ?? null : null;
      if (!r) return add ? undefined : commitSelection([], mesh?.version ?? null);
      toggleOrReplace(r, add, mesh?.version ?? null);
    },
    [regions, mesh, commitSelection, toggleOrReplace],
  );

  const deselect = useCallback(
    (id: string) => commitSelection(selected.filter((x) => selectionId(x) !== id), mesh?.version ?? null),
    [selected, mesh, commitSelection],
  );

  const createRegion = useCallback(
    async (req: RegionRequest) => {
      try {
        const r = await api.createRegion(req);
        setRegions((rs) => [...rs.filter((x) => x.region_id !== r.region_id), r]);
        commitSelection([r], r.model_version);
      } catch (e) {
        if (!(await resync())) notify((e as Error).message);
      }
    },
    [notify, resync, commitSelection],
  );

  const deleteRegions = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      const gone: string[] = [];
      for (const id of ids) {
        try {
          await api.deleteRegion(id);
          gone.push(id);
        } catch (e) {
          notify((e as Error).message);
        }
      }
      setRegions((rs) => rs.filter((x) => !gone.includes(x.region_id)));
      setSelection((cur) => cur.filter((x) => !(isRegion(x) && gone.includes(x.region_id))));
      if (gone.length > 1) notify(tx("{0} bölge silindi.", {"0": gone.length}));
    },
    [notify],
  );

  // Klavye: Esc seçimi bırakır, Delete / ⌫ seçili bölgeleri siler (yazı alanındayken değil)
  useEffect(() => {
    const kd = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || t.isContentEditable)) return;
      if (e.key === "Escape" && selected.length) commitSelection([], mesh?.version ?? null);
      if ((e.key === "Delete" || e.key === "Backspace") && !e.metaKey && !e.ctrlKey) {
        const ids = selected.filter(isRegion).map((r) => r.region_id);
        if (!ids.length) return;
        e.preventDefault();
        void deleteRegions(ids);
      }
    };
    window.addEventListener("keydown", kd);
    return () => window.removeEventListener("keydown", kd);
  }, [selected, mesh, commitSelection, deleteRegions]);

  // Sohbetteki kimlik çipleri: r2 / f5 / hole_1 / pocket_2 … tıklanınca modelde seçilir
  const selectEntity = useCallback(
    (id: string) => {
      if (!mesh) return;
      setView("model");
      if (/^r\d+$/.test(id)) return selectRegion(id);
      if (/^f\d+$/.test(id)) return void selectFace(id);
      const s = mesh.summary;
      const h = s.holes.find((x) => x.id === id || x.feature_id === id);
      const p = s.pockets.find((x) => x.id === id || x.feature_id === id);
      const f = h?.face_ids[0] ?? p?.floor_face_id ?? mesh.faces.find((x) => x.feature_id === id)?.id;
      if (f) void selectFace(f);
      else notify(tx("{0} şu anki modelde bulunamadı.", {"0": id}));
    },
    [mesh, selectFace, selectRegion, notify],
  );

  const send = async (text: string) => {
    const selText =
      selected.length === 0 ? null : selected.length === 1 ? `${selectionId(selected[0])} — ${selectionLabel(selected[0])}` : tx("{0} eleman: {1}", {"0": selected.length, "1": selected.map(selectionId).join(", ")});
    setMessages((m) => [...m, { role: "user", text, selection: selText }]);
    setBusy(true);
    try {
      const r = await api.chat(text, null, mesh?.version ?? null, null, selected.map(selectionId), convId);
      setMessages((m) => [...m, { role: "assistant", text: r.reply || tx("(boş yanıt)"), actions: r.actions }]);
      api.conversations().then((c) => setConversations(c.conversations), () => {}); // başlık / sıralama
      if (r.state.config) setConfig(r.state.config);
      if (r.model_changed || (r.state.model && r.state.model.model_id !== mesh?.model_id)) {
        await refreshModel();
        setView("model");
      }
      if (r.gcode_changed && r.state.gcode) {
        await loadGcode(r.state.gcode.gcode_id);
        setTab("gcode");
      } else if (r.state.gcode && gcode && r.state.gcode.stale !== gcode.stale) {
        setGcode({ ...gcode, stale: r.state.gcode.stale });
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "system", text: tx("Hata: {0}", {"0": (e as Error).message}) }]);
    } finally {
      setBusy(false);
    }
  };

  // ---- sohbetler
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  /** Geçişten önce aktif sohbetin son mesajlarını hemen kaydet (gecikmeli kayıt iptal olabilir) */
  const flushUi = async () => {
    if (convRef.current) await api.putUi({ messages: messagesRef.current, conversation_id: convRef.current }).catch(() => {});
  };
  const applyConv = (r: ConvPayload) => {
    setConvId(r.conversation.id);
    setMessages(r.messages);
    setConversations(r.conversations);
  };
  const newConversation = () =>
    run(async () => {
      await flushUi();
      applyConv(await api.newConversation());
    });
  const switchConversation = (id: string) =>
    run(async () => {
      await flushUi();
      applyConv(await api.activateConversation(id));
    });
  const renameConversation = (id: string, title: string) =>
    run(async () => setConversations((await api.renameConversation(id, title)).conversations));
  const deleteConversation = (id: string) =>
    run(async () => {
      if (id !== convId) await flushUi();
      applyConv(await api.deleteConversation(id));
    });
  const currentConversation = conversations.find((c) => c.id === convId);
  const convTitle = currentConversation?.titled ? currentConversation.title : tx("Yeni sohbet");
  // Aktif sohbetin mesaj sayısı backend'e gecikmeli yazılır; listede ekrandaki hâli göster
  const convList = useMemo(
    () =>
      conversations.map((c) =>
        c.id === convId ? { ...c, active: true, user_count: messages.filter((m) => m.role === "user").length } : { ...c, active: false },
      ),
    [conversations, convId, messages],
  );

  const run = async (fn: () => Promise<unknown>, after?: () => Promise<void> | void) => {
    try {
      await fn();
      await after?.();
    } catch (e) {
      notify((e as Error).message);
    }
  };

  const changeProfile = (id: string) =>
    run(async () => {
      const r = await api.setProfile(id);
      setConfig(r.config);
      if (gcode) await loadGcode(gcode.gcode_id);
      const rv = r.revalidation;
      notify(
        tx("Makine: {0}.", {"0": r.profile.name}) +
          (rv ? tx(" Program yeniden doğrulandı: {0} hata, {1} uyarı.", {"0": rv.counts.error, "1": rv.counts.warning}) : "") +
          (r.note ? ` ${r.note}` : ""),
      );
    });

  const markStale = () => gcode && model && gcode.model_id === model.model_id && setGcode({ ...gcode, stale: true });

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◢</span>
          <div>
            <b>LLM CAD · G-Code</b>
            <small>{tx("Büyük Dil Modeli Temelli G-Kodu Geliştirme ve Kontrolü Sistemi")}</small>
          </div>
        </div>
        <div className="top-actions">
          <LanguageSelector />
          <button className="btn" onClick={() => run(() => api.primitive({ type: "box", length: 60, width: 60, height: 10 }), refreshModel)}>
             {tx("+ Plaka")} </button>
          <button className="btn" onClick={() => run(() => api.primitive({ type: "cylinder", radius: 30, height: 10 }), refreshModel)}>
            + Disk
          </button>
          <button className="btn" onClick={() => stepInput.current?.click()}>
             {tx("STEP yükle")} </button>
          <input
            ref={stepInput}
            type="file"
            accept=".step,.stp"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (!f) return;
              await run(
                async () => {
                  const r = await api.uploadStep(f);
                  setMessages((m) => [...m, { role: "system", text: r.message }]);
                },
                refreshModel,
              );
            }}
          />
          <button className="btn" disabled={!model} onClick={() => run(() => api.download('/api/models/current/export.step', 'model.step'))}>
             {tx("STEP indir")}          </button>
          <button className="btn" disabled={!model?.can_undo} onClick={() => run(api.undo, async () => { await refreshModel(); markStale(); })}>
             {tx("↶ Geri al")} </button>
          <button
            className={`btn audit-btn ${view === "audit" ? "on" : ""}`}
            title={tx("LLM audit kayıtlarını aç: her sohbet isteği ve MCP araç çağrısı")}
            onClick={() => setView("audit")}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden>
              <path d="M8 1.5l5.5 2v4.2c0 3.2-2.3 5.7-5.5 6.8-3.2-1.1-5.5-3.6-5.5-6.8V3.5z" />
              <path d="M5.5 8l1.8 1.8L10.8 6.3" />
            </svg>
            Audit
          </button>
          <button
            className="btn"
            title={tx("Modeli, G-code'ları, sohbetleri ve audit kayıtlarını silip boş bir oturum başlatır (işleme ayarları korunur)")}
            onClick={() => {
              if (!window.confirm(tx("Model, G-code'lar, bütün sohbetler ve LLM audit kayıtları silinecek. Bu geri alınamaz. Yeni oturum başlatılsın mı?"))) return;
              void run(api.sessionReset, () => {
                setAuditRev((n) => n + 1);
                setMesh(null);
                setSelection([]);
                setRegions([]);
                setHovered(null);
                setGcode(null);
                setToolpath(null);
                setMessages([]);
                void api.state().then((st) => setConvId(st.ui?.conversation?.id ?? null));
                void api.conversations().then((r) => setConversations(r.conversations));
              });
            }}
          >
             {tx("Yeni oturum")} </button>
          {llm && (
            <button
              className={`llm-badge llm-toggle ${llm.provider}`}
              disabled={!llm.available}
              title={
                !llm.available
                  ? tx("API anahtarı yok: yalnızca kural tabanlı mod kullanılabilir")
                  : llm.provider === "offline"
                    ? tx("Tıklayın: {0} · {1} moduna geç", {"0": llm.configured_provider, "1": llm.configured_model})
                    : tx("Tıklayın: çevrimdışı (kural tabanlı) moda geç")
              }
              onClick={() =>
                run(
                  () => api.setLlmMode(llm.provider !== "offline"),
                  async () => {
                    const s = await api.state();
                    setLlm(s.llm);
                    setMessages((m) => [
                      ...m,
                      {
                        role: "system",
                        text: s.llm.provider === "offline" ? tx("Çevrimdışı moda geçildi (kural tabanlı).") : tx("LLM moduna geçildi: {0} · {1}", {"0": s.llm.provider, "1": s.llm.model}),
                      },
                    ]);
                  },
                )
              }
            >
              <span className={`llm-switch ${llm.provider === "offline" ? "" : "on"}`} aria-hidden />
              {llm.provider === "offline" ? tx("Çevrimdışı mod") : `${llm.provider} · ${llm.model}`}
            </button>
          )}
        </div>
      </header>

      <main className="layout">
        <ChatPanel
          messages={messages}
          busy={busy}
          selection={selected}
          onRemove={deselect}
          onClearSelection={() => commitSelection([], mesh?.version ?? null)}
          onSend={send}
          onReset={() => void newConversation()}
          title={convTitle}
          conversationCount={convList.length}
          onOpenDrawer={() => setDrawer(true)}
          onEntity={selectEntity}
          onLine={showLine}
        />

        <section className="viewport">
          <div className="view-tabs">
            <button className={view === "model" ? "on" : ""} onClick={() => setView("model")}>
              3D Model
            </button>
            <button className={view === "toolpath" ? "on" : ""} onClick={() => setView("toolpath")}>
               {tx("Takım yolu")} {toolpath ? "" : "·"}
            </button>
            <button className={view === "audit" ? "on" : ""} onClick={() => setView("audit")}>
              Audit
            </button>
            {view === "model" && hovered && mesh && (
              <span className="hover-info mono">
                {(() => {
                  const rg = regions.find((x) => x.region_id === hovered);
                  if (rg) return tx("{0} · {1} üzerinde {2} · {3} mm²", {"0": rg.region_id, "1": rg.face_id, "2": enumLabel(rg.shape), "3": rg.area});
                  const f = mesh.faces.find((x) => x.id === hovered);
                  return f ? `${f.id} · ${enumLabel(f.surface)}${f.hole_id ? ` · ${f.hole_id}` : ""}${f.pocket_id ? ` · ${f.pocket_id}` : ""}` : "";
                })()}
              </span>
            )}
          </div>
          <div className="view-body">
            {view === "model" ? (
              <>
                <ModelViewer
                  mesh={mesh}
                  hovered={hovered}
                  selectedIds={selectedIds}
                  regions={regions}
                  onHover={setHovered}
                  onSelect={selectFace}
                  onSelectRegion={selectRegion}
                  onCreateRegion={createRegion}
                />
                {!mesh && (
                  <div className="empty-overlay">
                    <p>{tx("Henüz model yok.")}</p>
                    <p className="muted">{tx("Soldaki sohbetten bir parça tarif edin, üstten hazır bir plaka ekleyin veya STEP dosyası yükleyin.")}</p>
                  </div>
                )}
                {mesh && <div className="view-hint muted small">{tx("Sol tık: seç · sürükle: döndür · sağ tık: kaydır · tekerlek: yakınlaştır · ⇧+tık: çoklu seçim · ⌘/Ctrl basılı tut: bölge çiz · Delete: seçili bölgeleri sil · Esc: seçimi bırak")}</div>}
              </>
            ) : view === "audit" ? (
              <AuditView refreshKey={messages.length + auditRev * 100000} onError={notify} />
            ) : (
              <ToolpathViewer
                seek={seek}
                toolpath={toolpath}
                mesh={gcode?.model_id && gcode.model_id === mesh?.model_id ? mesh : null}
                origin={gcode?.work_origin_model ?? null}
                toolDiameter={config?.tools.find((t) => t.number === config.mill_tool)?.diameter ?? 6}
                onLine={setActiveLine}
              />
            )}
          </div>
        </section>

        <Inspector
          tab={tab}
          setTab={setTab}
          model={model}
          selection={selected}
          gcode={gcode}
          config={config}
          profiles={profiles}
          onProfileChange={changeProfile}
          activeLine={view === "toolpath" ? activeLine : null}
          onAsk={(t) => void send(t)}
          onShowLine={showLine}
          onDeleteFeature={(id) => run(() => api.deleteFeature(id), async () => { await refreshModel(); markStale(); })}
          onSelectFace={(id, add) => selectFace(id, add)}
          regions={regions}
          onSelectRegion={selectRegion}
          onDeselect={deselect}
          onClearSelection={() => commitSelection([], mesh?.version ?? null)}
          onDeleteRegions={deleteRegions}
          onEngraved={async (msg) => {
            await refreshModel();
            markStale();
            setView("model");
            notify(tx("Kazıma eklendi — {0}", {"0": msg}));
          }}
          onGenerate={(ops) =>
            run(async () => {
              await loadGcode(await api.generate(ops));
              setView("toolpath");
            })
          }
          onGcodeLoaded={(g) => run(() => loadGcode(g), () => setView("toolpath"))}
          onConfigSaved={(c) => {
            setConfig(c);
            if (gcode) void loadGcode(gcode.gcode_id); // bağlama değişti: doğrulama sonucu güncellendi
            notify(tx("İşleme ayarları kaydedildi."));
          }}
          onProfilesChanged={(ps, c, msg) => {
            setProfiles(ps);
            setConfig(c);
            if (gcode) void loadGcode(gcode.gcode_id);
            notify(msg);
          }}
          onError={notify}
        />
      </main>
      <ConversationDrawer
        open={drawer}
        conversations={convList}
        busy={busy}
        onClose={() => setDrawer(false)}
        onNew={() => void newConversation()}
        onSelect={(id) => void switchConversation(id)}
        onRename={(id, t) => void renameConversation(id, t)}
        onDelete={(id) => void deleteConversation(id)}
      />
      {toast && <div className="toast" onClick={() => setToast(null)}>{toast}</div>}
    </div>
  );
}
