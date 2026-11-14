import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AlarmResult, Checklist, GcodeRecord, LineState, ProgramDiff, WearResult } from "../types";

/**
 * Atölye sekmesi: programın makinede ilk denenmesi (prove-out) sırasında operatöre destek.
 * Her şey backend'deki deterministik araçlardan gelir; bu ekran makineye hiçbir şey göndermez.
 */

type Sev = "error" | "warning" | "info";
const SEV_TR: Record<Sev, string> = { error: "kritik", warning: "dikkat", info: "bilgi" };

function useTool<T>(onError: (m: string) => void) {
  const [data, setData] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (name: string, args: Record<string, unknown>) => {
    setBusy(true);
    try {
      const r = await api.tool<T>(name, args);
      setData(r);
      return r;
    } catch (e) {
      onError((e as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { data, setData, busy, run };
}

const xyz = (p: (number | null)[] | undefined) =>
  p ? p.map((c, i) => (c === null ? null : `${"XYZ"[i]}${+c.toFixed(3)}`)).filter(Boolean).join(" ") || "—" : "—";

/* ------------------------------------------------------------------ satır (tek blok) */
function LineCard({ gcode, line, setLine, onShow, onAsk, onError }: {
  gcode: GcodeRecord;
  line: number;
  setLine: (n: number) => void;
  onShow: (n: number) => void;
  onAsk: (text: string) => void;
  onError: (m: string) => void;
}) {
  const t = useTool<LineState>(onError);
  const [input, setInput] = useState(String(line));
  useEffect(() => setInput(String(line)), [line]);
  useEffect(() => {
    // takım yolu oynatılırken satır hızla değişir: kısa bir bekleme ile istek sayısını sınırla
    const h = window.setTimeout(() => void t.run("get_line_state", { line, gcode_id: gcode.gcode_id }), 180);
    return () => window.clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line, gcode.gcode_id]);
  const s = t.data;
  const max = gcode.line_count;
  const go = (n: number) => setLine(Math.min(Math.max(1, n), max));
  return (
    <section className="card shop-card">
      <h4>
        Satır ne yapacak?
        <span className="muted small">tek blok yardımcısı</span>
      </h4>
      <div className="line-nav">
        <button className="btn" onClick={() => go(line - 1)} disabled={line <= 1} title="Önceki satır">
          ◀
        </button>
        <input
          className="line-input mono"
          value={input}
          inputMode="numeric"
          onChange={(e) => setInput(e.target.value.replace(/[^0-9]/g, ""))}
          onKeyDown={(e) => e.key === "Enter" && input && go(Number(input))}
          onBlur={() => input && Number(input) !== line && go(Number(input))}
          aria-label="Satır numarası"
        />
        <button className="btn" onClick={() => go(line + 1)} disabled={line >= max} title="Sonraki satır (tek blok)">
          ▶
        </button>
        <span className="muted small">/ {max}</span>
      </div>
      {s && (
        <div className={`line-result ${t.busy ? "busy" : ""}`}>
          <div className="code-line mono">
            <span className="ln">{s.line}</span>
            {s.text || <span className="muted">(boş)</span>}
          </div>
          {s.warnings.map((w, i) => (
            <div key={i} className={`shop-alert ${/çarpışma|malzemenin içinden|altına iniyor/.test(w) ? "error" : "warning"}`}>
              {w}
            </div>
          ))}
          <p className="line-summary">{s.summary.replace(/^N\d+:\s*/, "").split(" DİKKAT:")[0]}</p>
          {s.move && (
            <table className="kv">
              <tbody>
                <tr><td>Hareket</td><td>{s.move.kind_tr}{s.move.feed_mm_min ? ` · F${s.move.feed_mm_min}` : ""}{s.move.length_mm ? ` · ${s.move.length_mm} mm` : ""}</td></tr>
                <tr><td>Bitiş (iş koord.)</td><td className="mono">{xyz(s.move.end_work)}</td></tr>
                <tr><td>Bitiş (makine)</td><td className="mono">{xyz(s.move.end_machine)}</td></tr>
                {s.move.end_above_top_mm !== null && (
                  <tr><td>Üst yüzeye göre</td><td className="mono">{s.move.end_above_top_mm > 0 ? "+" : ""}{s.move.end_above_top_mm} mm</td></tr>
                )}
              </tbody>
            </table>
          )}
          <table className="kv">
            <tbody>
              <tr><td>Takım</td><td>{s.after.tool !== null ? <><b className="mono">T{s.after.tool}</b> {s.after.tool_desc}</> : "—"}</td></tr>
              <tr><td>İş mili / soğutma</td><td>{s.after.spindle.on ? `açık S${s.after.spindle.rpm ?? "?"}` : "kapalı"} · {s.after.coolant ? "soğutma açık" : "soğutma kapalı"}</td></tr>
              <tr><td>Takım boyu</td><td className="mono">{s.after.tool_length_comp}</td></tr>
            </tbody>
          </table>
          {s.issues.map((i, k) => (
            <div key={k} className={`issue ${i.severity}`}>
              <span className="mono">{i.code}</span>
              <div>{i.message}</div>
            </div>
          ))}
          <div className="btn-row">
            <button className="btn small" onClick={() => onShow(line)}>
              Takım yolunda göster
            </button>
            <button className="btn small" onClick={() => onAsk(`${line}. satır ne yapacak? Operatöre kısaca açıkla.`)}>
              Toni Kross'a açıklat
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ prove-out */
function ChecklistCard({ gcode, onPick, onError }: { gcode: GcodeRecord; onPick: (n: number) => void; onError: (m: string) => void }) {
  const t = useTool<Checklist>(onError);
  const [done, setDone] = useState<Set<number>>(new Set());
  useEffect(() => {
    t.setData(null);
    setDone(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gcode.gcode_id]);
  const c = t.data;
  return (
    <section className="card shop-card">
      <h4>
        Prove-out kontrol listesi
        <button className="btn small primary" disabled={t.busy} onClick={() => void t.run("prove_out_checklist", { gcode_id: gcode.gcode_id })}>
          {c ? "Yenile" : "Listeyi çıkar"}
        </button>
      </h4>
      {!c && <div className="muted small">Programı makinede ilk kez denemeden önce: hazırlık adımları, her takımın ilk yaklaşması ve ilk kesişi, riskli hızlı hareketler.</div>}
      {c && (
        <>
          <div className="check-group">Hazırlık</div>
          {c.preparation.map((p, i) => (
            <label key={i} className="check-row">
              <input
                type="checkbox"
                checked={done.has(-1 - i)}
                onChange={() => setDone((d) => { const n = new Set(d); if (n.has(-1 - i)) n.delete(-1 - i); else n.add(-1 - i); return n; })}
              />
              <span>{p}</span>
            </label>
          ))}
          <div className="check-group">
            Satırlar <span className="muted small">{c.counts.error} kritik · {c.counts.warning} dikkat</span>
          </div>
          {c.items.map((it, i) => (
            <div key={i} className={`check-item ${it.severity} ${done.has(i) ? "done" : ""}`}>
              <input
                type="checkbox"
                checked={done.has(i)}
                onChange={() => setDone((d) => { const n = new Set(d); if (n.has(i)) n.delete(i); else n.add(i); return n; })}
                aria-label="Tamamlandı"
              />
              <button className="line-chip mono" onClick={() => onPick(it.line)} title="Bu satırı aç">
                {it.line}
              </button>
              <div>
                <div>{it.message}</div>
                <div className="check-action">→ {it.action}</div>
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ alarm */
function AlarmCard({ line, onError }: { line: number; onError: (m: string) => void }) {
  const t = useTool<AlarmResult>(onError);
  const [code, setCode] = useState("");
  const [withLine, setWithLine] = useState(true);
  const a = t.data;
  const ask = () => code.trim() && void t.run("explain_alarm", { code: code.trim(), ...(withLine ? { line } : {}) });
  return (
    <section className="card shop-card">
      <h4>Alarm açıkla</h4>
      <div className="inline-form">
        <input
          className="mono"
          placeholder="ör. PS0010, OT0501, error:20, ALARM:2"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
        />
        <button className="btn primary small" disabled={!code.trim() || t.busy} onClick={ask}>
          Açıkla
        </button>
      </div>
      <label className="check-row small">
        <input type="checkbox" checked={withLine} onChange={(e) => setWithLine(e.target.checked)} />
        <span>
          Alarm <b className="mono">{line}.</b> satırda oldu
        </span>
      </label>
      {a && !a.found && (
        <div className="shop-alert warning">
          {a.note}
          {a.found_in_other?.map((o) => (
            <div key={o.controller} className="small">
              Bu kod <b>{o.controller}</b> tablosunda var ({o.code} — {o.title}). Seçili makine profili doğru mu?
            </div>
          ))}
        </div>
      )}
      {a?.alarm && (
        <div className="alarm-result">
          <div className="alarm-head">
            <span className="alarm-code mono">{a.alarm.code}</span>
            <span className="alarm-title mono">{a.alarm.title}</span>
          </div>
          {a.alarm.meaning_tr && <p className="alarm-meaning">{a.alarm.meaning_tr}</p>}
          <blockquote className="alarm-en">{a.alarm.description_en}</blockquote>
          {a.alarm.actions.length > 0 && (
            <ul className="alarm-actions">
              {a.alarm.actions.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
          {a.matching_issues && a.matching_issues.length > 0 && (
            <div className="shop-alert warning small">Doğrulayıcı bu satırda da bulmuştu: {a.matching_issues.map((i) => `${i.code} ${i.message}`).join(" · ")}</div>
          )}
          {a.line_state && <div className="muted small">Satır {a.line_state.line}: {a.line_state.summary.replace(/^N\d+:\s*/, "").split(" DİKKAT:")[0]}</div>}
          {a.source && (
            <div className="alarm-source small">
              Kaynak:{" "}
              <a href={a.source.url} target="_blank" rel="noreferrer">
                {a.source.title}
              </a>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ ölçü düzeltme */
const FEATURES: [string, string][] = [
  ["pocket", "Cep (iç ölçü)"],
  ["slot", "Kanal (iç ölçü)"],
  ["bore", "Frezelenmiş delik"],
  ["contour", "Dış kontur"],
  ["depth", "Derinlik"],
  ["drilled_hole", "Matkap deliği"],
];

function WearCard({ onError }: { onError: (m: string) => void }) {
  const t = useTool<WearResult>(onError);
  const [f, setF] = useState({ feature: "pocket", nominal: "", measured: "", tolerance: "" });
  const r = t.data;
  const num = (s: string) => Number(s.replace(",", "."));
  const ok = f.nominal !== "" && f.measured !== "";
  return (
    <section className="card shop-card">
      <h4>Ölçü sapması</h4>
      <div className="wear-grid">
        <label>
          <span>Özellik</span>
          <select value={f.feature} onChange={(e) => setF({ ...f, feature: e.target.value })}>
            {FEATURES.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Resimdeki (mm)</span>
          <input className="mono" inputMode="decimal" value={f.nominal} onChange={(e) => setF({ ...f, nominal: e.target.value })} />
        </label>
        <label>
          <span>Ölçülen (mm)</span>
          <input className="mono" inputMode="decimal" value={f.measured} onChange={(e) => setF({ ...f, measured: e.target.value })} />
        </label>
        <label>
          <span>± Tolerans</span>
          <input className="mono" inputMode="decimal" placeholder="isteğe bağlı" value={f.tolerance} onChange={(e) => setF({ ...f, tolerance: e.target.value })} />
        </label>
      </div>
      <button
        className="btn primary small"
        disabled={!ok || t.busy}
        onClick={() =>
          void t.run("suggest_wear_offset", {
            feature: f.feature,
            nominal: num(f.nominal),
            measured: num(f.measured),
            ...(f.tolerance ? { tolerance: num(f.tolerance) } : {}),
          })
        }
      >
        Düzeltme öner
      </button>
      {r && (
        <div className={`shop-result ${r.action === "none" ? "ok" : ""}`}>
          <div className="mono big-num">{r.error_mm > 0 ? "+" : ""}{r.error_mm} mm</div>
          <div>{r.message}</div>
          {r.warning && <div className="shop-alert warning small">{r.warning}</div>}
          <div className="muted small">Öneridir; makinedeki ofseti operatör girer.</div>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ program farkı */
function DiffCard({ onLoaded, onError }: { onLoaded: (g: GcodeRecord) => void; onError: (m: string) => void }) {
  const t = useTool<ProgramDiff>(onError);
  const file = useRef<HTMLInputElement>(null);
  const d = t.data;
  return (
    <section className="card shop-card">
      <h4>Makinede değişen program</h4>
      <div className="muted small">Operatörün makinede düzelttiği programı yükle; son üretilen programla karşılaştırılır ve riskli değişiklikler işaretlenir.</div>
      <div className="btn-row">
        <button className="btn small" disabled={t.busy} onClick={() => file.current?.click()}>
          Programı yükle ve karşılaştır…
        </button>
        <input
          ref={file}
          type="file"
          hidden
          accept=".nc,.gcode,.ngc,.tap,.txt"
          onChange={async (e) => {
            const fl = e.target.files?.[0];
            e.target.value = "";
            if (!fl) return;
            try {
              const rec = await api.gcodeText(await fl.text(), `makineden:${fl.name}`);
              onLoaded(rec);
              await t.run("diff_programs", { edited_id: rec.gcode_id });
            } catch (err) {
              onError((err as Error).message);
            }
          }}
        />
      </div>
      {d && (
        <>
          <div className="diff-counts small">
            <b>{d.change_count}</b> değişiklik · <span className="sev error">{d.counts.error} kritik</span> ·{" "}
            <span className="sev warning">{d.counts.warning} dikkat</span> · <span className="sev info">{d.counts.info} bilgi</span>
          </div>
          <div className="diff-list">
            {d.changes.map((c, i) => (
              <div key={i} className={`diff-row ${c.severity}`}>
                <div className="diff-lines mono">
                  {c.old !== null && <div className="del">− {c.base_line}: {c.old}</div>}
                  {c.new !== null && <div className="add">+ {c.edited_line}: {c.new}</div>}
                </div>
                <div className="diff-risk small">
                  <span className={`sev ${c.severity}`}>{SEV_TR[c.severity as Sev]}</span> {c.risks.join("; ")}
                </div>
              </div>
            ))}
          </div>
          <div className="muted small">{d.note}</div>
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ sekme */
export default function ShopTab({
  gcode,
  activeLine,
  onShowLine,
  onAsk,
  onGcodeLoaded,
  onError,
}: {
  gcode: GcodeRecord | null;
  activeLine: number | null;
  onShowLine: (n: number) => void;
  onAsk: (text: string) => void;
  onGcodeLoaded: (g: GcodeRecord) => void;
  onError: (m: string) => void;
}) {
  const [line, setLine] = useState(1);
  useEffect(() => setLine(1), [gcode?.gcode_id]);
  // takım yolu oynatılırken satır takibi
  useEffect(() => {
    if (activeLine) setLine(activeLine);
  }, [activeLine]);
  if (!gcode) return <div className="muted pad">Atölye araçları için önce G-code üretin veya yükleyin.</div>;
  return (
    <div className="pad stack shop-tab">
      <div className="shop-banner small">
        <b>Atölye modu</b> · program <span className="mono">{gcode.gcode_id}</span> · {gcode.line_count} satır. Bu ekran yalnızca okur ve önerir; makineye hiçbir şey göndermez.
      </div>
      <LineCard gcode={gcode} line={line} setLine={setLine} onShow={onShowLine} onAsk={onAsk} onError={onError} />
      <ChecklistCard
        gcode={gcode}
        onPick={(n) => {
          setLine(n);
          onShowLine(n);
        }}
        onError={onError}
      />
      <AlarmCard line={line} onError={onError} />
      <WearCard onError={onError} />
      <DiffCard onLoaded={onGcodeLoaded} onError={onError} />
    </div>
  );
}
