import { tx, useLocale, getLocale } from "../i18n";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { AuditEntry, AuditList, AuditSummary } from "../types";
import Markdown from "./Markdown";

/**
 * LLM audit kayıtları: web sohbetinde ve MCP istemcilerinde LLM'in yaptığı her şey.
 * Kayıtlar backend'de yalnızca eklenen, hash zincirli bir günlükte durur; bu ekran yalnızca okur ve dışa aktarır.
 */

const PAGE = 100;
const KINDS = (): [string, string][] => ([
  ["", tx("Tümü")],
  ["chat", tx("Web sohbeti")],
  ["mcp_tool", "MCP"],
]);

const fmtTime = (iso: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  const today = new Date().toDateString() === d.toDateString();
  return `${today ? tx("bugün") : `${p(d.getDate())}.${p(d.getMonth() + 1)}`} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
/** Liste önizlemesinde markdown işaretlerini at */
const plain = (s: string) => s.replace(/\*\*|`|^#+\s|\n+/g, (m) => (m.startsWith("\n") ? " · " : ""));
const fmtMs = (ms: number | null | undefined) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : tx("{0} sn", {"0": (ms / 1000).toFixed(1)}));
const json = (v: unknown) => JSON.stringify(v, null, 2);

function Detail({ id, onError }: { id: string; onError: (m: string) => void }) {
  useLocale();
  const [e, setE] = useState<AuditEntry | null>(null);
  useEffect(() => {
    api.auditEntry(id).then(setE, (err: Error) => onError(err.message));
  }, [id, onError]);
  if (!e) return <div className="audit-detail muted small">{tx("Yükleniyor…")}</div>;
  return (
    <div className="audit-detail">
      <section>
        <h5>{tx("Girdi")}</h5>
        <div className="audit-msg">{e.input?.message || <span className="muted">—</span>}</div>
        {e.input?.selection && e.input.selection.length > 0 && (
          <div className="small">
             {tx("Seçim:")} {e.input.selection.map((s) => <span key={s} className="md-entity">{s}</span>)}
          </div>
        )}
        {e.input?.context && (
          <details>
            <summary className="small">{tx("LLM'e giden bağlam ([BAĞLAM] bloğu)")}</summary>
            <pre className="audit-pre">{e.input.context}</pre>
          </details>
        )}
        <div className="audit-meta small mono">
           {tx("durum: model")} {e.state?.model_id ?? "—"} v{e.state?.model_version ?? "—"} · G-code {e.state?.gcode_id ?? "—"}  {tx("· makine")} {e.state?.machine_profile ?? "—"}
        </div>
      </section>

      {e.rounds && e.rounds.length > 0 && (
        <section>
          <h5>{tx("LLM turları (")}{e.rounds.length})</h5>
          {e.rounds.map((r) => (
            <div key={r.round} className="audit-round">
              <div className="audit-round-head mono small">
                <b>#{r.round}</b> · {fmtMs(r.latency_ms)} · {r.stop_reason ?? "—"}
                {r.usage && ` · ${r.usage.input_tokens ?? "?"} → ${r.usage.output_tokens ?? "?"} token`}
                {r.response_id && <span className="muted"> · {r.response_id}</span>}
              </div>
              {r.text && <div className="audit-round-text">{r.text}</div>}
              {r.reasoning && (
                <details>
                  <summary className="small">{tx("Akıl yürütme (reasoning)")}</summary>
                  <pre className="audit-pre">{r.reasoning}</pre>
                </details>
              )}
              {r.tool_requests.map((t, i) => (
                <div key={i} className="mono small audit-req">
                  → <span className="fn-name">{t.name}</span>
                  <span className="fn-punc">(</span>
                  <span className="fn-val obj">{typeof t.input === "string" ? t.input : JSON.stringify(t.input)}</span>
                  <span className="fn-punc">)</span>
                </div>
              ))}
            </div>
          ))}
        </section>
      )}

      {e.tool_calls && e.tool_calls.length > 0 && (
        <section>
          <h5>{tx("Çalıştırılan araçlar (")}{e.tool_calls.length})</h5>
          {e.tool_calls.map((c, i) => (
            <details key={i} className={`audit-call ${c.ok ? "ok" : "fail"}`}>
              <summary className="mono small">
                <span className="fn-status">{c.ok ? "✓" : "✕"}</span> <span className="fn-name">{c.name}</span>
                <span className="fn-punc">(</span>
                <span className="fn-val obj">{JSON.stringify(c.args)}</span>
                <span className="fn-punc">)</span> <span className="muted">· {fmtMs(c.duration_ms)}</span>
              </summary>
              {c.error && <div className="fn-err">{c.error}</div>}
              <pre className="audit-pre">{json(c.result)}</pre>
            </details>
          ))}
        </section>
      )}

      <section>
        <h5>{tx("Çıktı")}</h5>
        {e.output?.error && <div className="shop-alert error small">{e.output.error}</div>}
        {e.output?.reply ? <Markdown text={e.output.reply} /> : <div className="muted small">—</div>}
      </section>

      <section className="audit-integrity mono small">
        <div>seq {e.seq} · id {e.id} · {e.ts}</div>
        <div>hash {e.hash}</div>
        <div>{tx("önceki")} {e.prev_hash}</div>
        {e.system_prompt_sha256 && <div>{tx("sistem istemi sha256")} {e.system_prompt_sha256}</div>}
        {e.tools_sha256 && <div>{tx("araç şemaları sha256")} {e.tools_sha256}…</div>}
      </section>
      <div className="btn-row">
        <button className="btn small" onClick={() => void api.auditDownload([e.id]).catch((err: Error) => onError(err.message))}>
           {tx("Bu kaydı indir (JSON)")} </button>
      </div>
    </div>
  );
}

export default function AuditView({ refreshKey, onError }: { refreshKey: number; onError: (m: string) => void }) {
  useLocale();
  const [q, setQ] = useState("");
  const [qDeb, setQDeb] = useState("");
  const [kind, setKind] = useState("");
  const [data, setData] = useState<AuditList | null>(null);
  const [rows, setRows] = useState<AuditSummary[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reqId = useRef(0);

  useEffect(() => {
    const t = window.setTimeout(() => setQDeb(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  const load = useCallback(
    async (offset = 0) => {
      const my = ++reqId.current;
      setBusy(true);
      try {
        const r = await api.auditList({ q: qDeb, kind, offset, limit: PAGE });
        if (my !== reqId.current) return;
        setData(r);
        setRows((prev) => (offset === 0 ? r.entries : [...prev, ...r.entries]));
      } catch (e) {
        onError((e as Error).message);
      } finally {
        if (my === reqId.current) setBusy(false);
      }
    },
    [qDeb, kind, onError],
  );

  useEffect(() => {
    void load(0);
  }, [load, refreshKey]);

  const visibleIds = useMemo(() => rows.map((r) => r.id), [rows]);
  const allVisiblePicked = visibleIds.length > 0 && visibleIds.every((id) => picked.has(id));
  const toggle = (id: string) =>
    setPicked((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const filtered = !!(qDeb || kind);

  const download = async (ids: string[] | null) => {
    try {
      await api.auditDownload(ids);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const chain = data?.chain;
  return (
    <div className="audit-view">
      <header className="audit-head">
        <div>
          <h3>{tx("LLM audit kayıtları")}</h3>
          <div className="muted small">
             {tx("Web sohbetinde ve MCP istemcilerinde LLM'in aldığı girdi, bağlam, her tur, çalıştırdığı araçlar ve cevabı. Kayıtlar yalnızca eklenir; silinemez.")} </div>
        </div>
        {chain && (
          <div className={`chain-badge ${chain.ok ? "ok" : "bad"}`} title={chain.ok ? tx("Son hash: {0}", {"0": chain.last_hash}) : chain.message}>
            {chain.ok ? tx("✓ Zincir sağlam") : tx("✕ Zincir {0}. kayıtta kırık", {"0": chain.broken_at_index})}
            <span className="muted"> · {chain.count}  {tx("kayıt")}</span>
          </div>
        )}
      </header>

      <div className="audit-controls">
        <input className="audit-search" placeholder={tx("Kayıtlarda ara (mesaj, araç, cevap, hata…)")} value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          {KINDS().map(([k, l]) => (
            <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>
              {l}
            </button>
          ))}
        </div>
        <button className="btn small" onClick={() => void load(0)} disabled={busy} title={tx("Yenile")}>
          ⟳
        </button>
      </div>

      <div className="audit-actions">
        <label className="check-row small">
          <input
            type="checkbox"
            checked={allVisiblePicked}
            onChange={() =>
              setPicked((p) => {
                const n = new Set(p);
                if (allVisiblePicked) visibleIds.forEach((id) => n.delete(id));
                else visibleIds.forEach((id) => n.add(id));
                return n;
              })
            }
          />
          <span>{tx("Görünenlerin hepsi")}</span>
        </label>
        {filtered && data && data.total > rows.length && (
          <button
            className="btn ghost small"
            onClick={async () => {
              try {
                const r = await api.auditIds({ q: qDeb, kind });
                setPicked((p) => new Set([...p, ...r.ids]));
              } catch (e) {
                onError((e as Error).message);
              }
            }}
          >
             {tx("Filtreye uyan")} {data.total}  {tx("kaydı seç")} </button>
        )}
        <span className="audit-picked small">{picked.size}  {tx("seçili")}</span>
        {picked.size > 0 && (
          <button className="btn ghost small" onClick={() => setPicked(new Set())}>
             {tx("Seçimi temizle")} </button>
        )}
        <span className="grow" />
        <button className="btn small primary" disabled={picked.size === 0} onClick={() => void download([...picked])}>
           {tx("Seçilenleri indir (")}{picked.size})
        </button>
        <button className="btn small" disabled={!data?.all} onClick={() => void download(null)} title={tx("Bütün kayıtlar, filtreden bağımsız")}>
           {tx("Tümünü indir (JSON)")} </button>
      </div>

      <div className="audit-table">
        <div className="audit-row audit-th small">
          <span />
          <span>#</span>
          <span>{tx("Zaman")}</span>
          <span>{tx("Kaynak")}</span>
          <span>{tx("Girdi / cevap")}</span>
          <span>{tx("Araç")}</span>
          <span>{tx("Süre")}</span>
          <span>Token</span>
        </div>
        {rows.length === 0 && !busy && (
          <div className="muted pad small">{filtered ? tx("Filtreye uyan kayıt yok.") : tx("Henüz kayıt yok. Sohbette bir istek gönderdiğinde burada görünecek.")}</div>
        )}
        {rows.map((r) => (
          <div key={r.id} className={`audit-item ${open === r.id ? "open" : ""} ${picked.has(r.id) ? "picked" : ""}`}>
            <div className="audit-row" onClick={() => setOpen((o) => (o === r.id ? null : r.id))}>
              <span onClick={(e) => e.stopPropagation()}>
                <input type="checkbox" checked={picked.has(r.id)} onChange={() => toggle(r.id)} aria-label={tx("Kayıt {0} seç", {"0": r.seq})} />
              </span>
              <span className="mono muted">{r.seq}</span>
              <span className="mono small" title={new Date(r.ts).toLocaleString(getLocale() === "en" ? "en-US" : "tr-TR")}>{fmtTime(r.ts)}</span>
              <span>
                <span className={`kind-chip ${r.kind}`}>{r.kind === "chat" ? tx("sohbet") : r.kind === "mcp_tool" ? "MCP" : r.kind}</span>
                <div className="muted tinyline">{r.kind === "chat" ? `${r.provider} · ${r.model}` : r.client}</div>
              </span>
              <span className="audit-text">
                <span className="audit-in">{r.message || "—"}</span>
                {(r.reply || r.error) && <span className={`audit-out ${r.error ? "err" : ""}`}>{r.error ?? plain(r.reply)}</span>}
              </span>
              <span className="mono small">
                {r.tool_count}
                {r.tool_fail > 0 && <span className="sev error"> ✕{r.tool_fail}</span>}
              </span>
              <span className="mono small">{fmtMs(r.duration_ms)}</span>
              <span className="mono small">{r.usage ? `${r.usage.input_tokens ?? 0}/${r.usage.output_tokens ?? 0}` : "—"}</span>
            </div>
            {open === r.id && <Detail id={r.id} onError={onError} />}
          </div>
        ))}
        {data && rows.length < data.total && (
          <div className="pad">
            <button className="btn small" disabled={busy} onClick={() => void load(rows.length)}>
               {tx("Daha fazla yükle (")}{rows.length} / {data.total})
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
