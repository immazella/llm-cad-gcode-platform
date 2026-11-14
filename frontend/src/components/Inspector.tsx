import { tx, useLocale } from "../i18n";
import { enumLabel, selectionLabel, profileLabel, materialLabel, roleLabel } from "../presentation";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { GcodeRecord, MachineProfile, MachiningConfig, ModelSummary, RegionInfo, Selection } from "../types";
import { isRegion, selectionId } from "../types";
import ShopTab from "./ShopTab";
import MachiningSettings from "./MachiningSettings";
import type { SettingsSub } from "./MachiningSettings";

function NumField({ label, value, onChange, unit }: { label: string; value: number; onChange: (v: number) => void; unit?: string }) {
  useLocale();
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" value={value} step="any" onChange={(e) => onChange(Number(e.target.value))} />
      {unit && <em>{unit}</em>}
    </label>
  );
}

const n = (v: number, d = 2) => (Math.round(v * 10 ** d) / 10 ** d).toString();

/* ------------------------------------------------------------------ Kazıma (gravür) */
function EngraveCard({
  selection,
  onDone,
  onError,
}: {
  selection: Selection | null;
  onDone: (msg: string) => void;
  onError: (m: string) => void;
}) {
  useLocale();
  const [mode, setMode] = useState<"text" | "svg">("text");
  const [text, setText] = useState("İTÜ");
  const [height, setHeight] = useState(6);
  const [font, setFont] = useState("tek_cizgi");
  const [size, setSize] = useState(30);
  const [pos, setPos] = useState({ x: 0, y: 0, rotation: 0, depth: 0.3 });
  const [onFace, setOnFace] = useState(false);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<HTMLInputElement>(null);
  // seçili yüzey yukarı bakan düzlemse (ör. cep tabanı) kazıma oraya yapılabilir
  const faceOk = !!selection && selection.surface === "plane" && selection.normal?.[2] === 1;
  const faceId = onFace && faceOk ? selection!.face_id : null;
  const setP = (k: keyof typeof pos, v: number) => setPos({ ...pos, [k]: v });

  const run = async (fn: () => Promise<{ feature_id: string; stroke_count: number; width: number }>) => {
    setBusy(true);
    try {
      const r = await fn();
      onDone(tx("{0}: {1} vuruş, çizgi {2} mm.", {"0": r.feature_id, "1": r.stroke_count, "2": r.width}));
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h4>{tx("Kazıma (gravür)")}</h4>
      <div className="seg">
        <button className={mode === "text" ? "on" : ""} onClick={() => setMode("text")}>
           {tx("Yazı")} </button>
        <button className={mode === "svg" ? "on" : ""} onClick={() => setMode("svg")}>
          SVG logo
        </button>
      </div>
      {mode === "text" ? (
        <>
          <label className="field col">
            <span>{tx("Yazı")}</span>
            <input value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <NumField label={tx("Harf yüksekliği")} unit="mm" value={height} onChange={setHeight} />
          <label className="field">
            <span>Font</span>
            <select className="select" value={font} onChange={(e) => setFont(e.target.value)}>
              <option value="tek_cizgi">{tx("Tek çizgi (gravür)")}</option>
              <option value="kontur">{tx("Kontur (harf hatları)")}</option>
            </select>
          </label>
        </>
      ) : (
        <NumField label={tx("Logo boyutu (uzun kenar)")} unit="mm" value={size} onChange={setSize} />
      )}
      <div className="grid2">
        <NumField label={tx("Merkez X")} unit="mm" value={pos.x} onChange={(v) => setP("x", v)} />
        <NumField label={tx("Merkez Y")} unit="mm" value={pos.y} onChange={(v) => setP("y", v)} />
        <NumField label={tx("Açı")} unit="°" value={pos.rotation} onChange={(v) => setP("rotation", v)} />
        <NumField label={tx("Derinlik")} unit="mm" value={pos.depth} onChange={(v) => setP("depth", v)} />
      </div>
      <label className="field small" title={faceOk ? "" : tx("3B görünümde yukarı bakan düz bir yüzey (ör. cep tabanı) seçin")}>
        <span>{tx("Seçili yüzeye")} {faceOk ? `(${selection!.face_id})` : tx("(yatay yüzey seçili değil)")}</span>
        <input type="checkbox" disabled={!faceOk} checked={onFace && faceOk} onChange={(e) => setOnFace(e.target.checked)} />
      </label>
      <div className="btn-row">
        {mode === "text" ? (
          <button
            className="btn primary"
            disabled={busy || !text.trim()}
            onClick={() => run(() => api.engraveText({ text, height, font, ...pos, face_id: faceId }))}
          >
            {busy ? tx("Kazınıyor…") : tx("Yazıyı kazı")}
          </button>
        ) : (
          <>
            <button className="btn primary" disabled={busy} onClick={() => svgRef.current?.click()}>
              {busy ? tx("Kazınıyor…") : tx("SVG seç ve kazı…")}
            </button>
            <input
              ref={svgRef}
              type="file"
              accept=".svg,image/svg+xml"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) run(() => api.engraveSvg(f, { size, ...pos, face_id: faceId }));
              }}
            />
          </>
        )}
      </div>
      <div className="muted tiny">
         {tx("Takım (V-uç) yolu sabit derinlikte izler; alan boşaltılmaz. SVG'de yazı öğeleri yerine yola çevrilmiş şekiller kullanın.")} </div>
    </section>
  );
}

/* ------------------------------------------------------------------ Model sekmesi */
const SHAPE_TR = (): Record<RegionInfo["shape"], string> => ({ rect: tx("Dikdörtgen"), circle: tx("Daire"), lasso: tx("Serbest çizim"), polygon: tx("Çokgen") });

function RegionCard({ r, onDelete, onSelectFace }: { r: RegionInfo; onDelete: (id: string) => void; onSelectFace: (id: string) => void }) {
  useLocale();
  return (
    <section className="card accent region-card">
      <h4>
         {tx("Seçili bölge ·")} {r.region_id}
        <button className="icon-btn" title={tx("Bölgeyi sil")} onClick={() => onDelete(r.region_id)}>
          🗑
        </button>
      </h4>
      <div className="kv">
        <span>{tx("Şekil")}</span>
        <b>
          {SHAPE_TR()[r.shape]}
          {r.clipped ? tx(" · yüzey sınırında kırpıldı") : ""}
          {r.pieces > 1 ? tx(" · {0} parça", {"0": r.pieces}) : ""}
        </b>
        <span>{tx("Yüzey")}</span>
        <b>
          <a className="link" onClick={() => onSelectFace(r.face_id)}>
            {r.face_id}
          </a>{" "}
          · {enumLabel(r.surface)} · <span className="muted">{roleLabel(r.face_role)}</span>
        </b>
        <span>{tx("Alan")}</span>
        <b>{n(r.area)} mm²</b>
        {r.circle ? (
          <>
            <span>{tx("Çap")}</span>
            <b className="mono">Ø{n(r.circle.diameter, 3)} mm</b>
          </>
        ) : (
          r.placement && (
            <>
              <span>{tx("Boyut")}</span>
              <b className="mono">
                {n(r.placement.length)} × {n(r.placement.width)} mm
              </b>
            </>
          )
        )}
        {r.placement && (
          <>
            <span>{tx("Konum")}</span>
            <b className="mono" title={r.placement.axes}>
              x {n(r.placement.x)} · y {n(r.placement.y)}
            </b>
          </>
        )}
        {(r.normal ?? r.normal_at_center) && (
          <>
            <span>Normal</span>
            <b className="mono">[{(r.normal ?? r.normal_at_center)!.map((v) => n(v, 2)).join(", ")}]</b>
          </>
        )}
        <span>{tx("Merkez")}</span>
        <b className="mono">[{r.center.map((v) => n(v, 2)).join(", ")}]</b>
        {r.holes.length > 0 && (
          <>
            <span>{tx("Delikler")}</span>
            <b className="mono">{r.holes.join(", ")}</b>
          </>
        )}
      </div>
      <div className="muted small">
        {r.mode === "plane" ? tx("Sohbette “seçtiğim bölgeye 3 mm cep aç” gibi komutlar ölçüyü bu bölgeden alır.") : tx("Eğri yüzey bölgesi: bakış yönünde izdüşürüldü (cep/kazıma bu sürümde yalnız düzlemlerde).")}
      </div>
    </section>
  );
}

function ModelTab({
  model,
  selected,
  regions,
  onDelete,
  onSelectFace,
  onSelectRegion,
  onDeselect,
  onClearSelection,
  onDeleteRegions,
  onEngraved,
  onError,
}: {
  model: ModelSummary | null;
  selected: Selection[];
  regions: RegionInfo[];
  onDelete: (id: string) => void;
  onSelectFace: (id: string, add?: boolean) => void;
  onSelectRegion: (id: string, add: boolean) => void;
  onDeselect: (id: string) => void;
  onClearSelection: () => void;
  onDeleteRegions: (ids: string[]) => void;
  onEngraved: (msg: string) => void;
  onError: (m: string) => void;
}) {
  useLocale();
  if (!model) return <div className="muted pad">{tx("Aktif model yok.")}</div>;
  const s = model.bbox.size;
  const selection = selected.length ? selected[selected.length - 1] : null; // birincil (son seçilen)
  const selRegionIds = selected.filter(isRegion).map((r) => r.region_id);
  const isSel = (id: string) => selected.some((x) => selectionId(x) === id);
  return (
    <div className="pad stack">
      {selected.length > 1 && (
        <section className="card accent">
          <h4>
             {tx("Seçim ·")} {selected.length}  {tx("eleman")} <button className="btn ghost small" onClick={onClearSelection} title={tx("Seçimi bırak (Esc)")}>
               {tx("Bırak")} </button>
          </h4>
          {selected.map((x) => (
            <div key={selectionId(x)} className="row-item">
              <span className="mono">{selectionId(x)}</span>
              <span className="muted small grow">{selectionLabel(x)}</span>
              <button className="icon-btn" title={tx("Seçimden çıkar")} onClick={() => onDeselect(selectionId(x))}>
                ✕
              </button>
            </div>
          ))}
          {selRegionIds.length > 0 && (
            <button className="btn small danger-ghost" onClick={() => onDeleteRegions(selRegionIds)}>
               {tx("Seçili")} {selRegionIds.length}  {tx("bölgeyi sil")} </button>
          )}
          <div className="muted small">{tx("Sohbetteki istek seçili elemanların hepsine uygulanır. Ayrıntılar son seçilen için aşağıda.")}</div>
        </section>
      )}
      {isRegion(selection) && <RegionCard r={selection} onDelete={(id) => onDeleteRegions([id])} onSelectFace={onSelectFace} />}
      {selection && !isRegion(selection) && (
        <section className="card accent">
          <h4>{tx("Seçili yüzey ·")} {selection.face_id}</h4>
          <div className="kv">
            <span>{tx("Rol")}</span>
            <b>{selectionLabel(selection)}</b>
            <span>{tx("Yüzey tipi")}</span>
            <b>{enumLabel(selection.surface)}</b>
            <span>{tx("Alan")}</span>
            <b>{n(selection.area)} mm²</b>
            {selection.diameter !== undefined && (
              <>
                <span>{tx("Çap")}</span>
                <b>Ø{n(selection.diameter, 3)} mm</b>
              </>
            )}
            {selection.normal && (
              <>
                <span>Normal</span>
                <b className="mono">[{selection.normal.map((v) => n(v, 2)).join(", ")}]</b>
              </>
            )}
            <span>{tx("Merkez")}</span>
            <b className="mono">[{selection.center.map((v) => n(v, 2)).join(", ")}]</b>
          </div>
        </section>
      )}
      <section className="card">
        <h4>{model.name}</h4>
        <div className="kv">
          <span>{tx("Tür")}</span>
          <b>{model.kind === "parametric" ? tx("Parametrik") : tx("İçe aktarılan / pişirilmiş")}</b>
          <span>{tx("Boyut")}</span>
          <b className="mono">
            {n(s[0])} × {n(s[1])} × {n(s[2])} mm
          </b>
          <span>{tx("Hacim")}</span>
          <b className="mono">{n(model.volume_mm3 / 1000, 2)} cm³</b>
          <span>{tx("Yüzey sayısı")}</span>
          <b>{model.face_count}</b>
          <span>{tx("Sürüm")}</span>
          <b>v{model.version}</b>
        </div>
      </section>
      <section className="card">
        <h4>{tx("Özellik ağacı")}</h4>
        {model.features.length === 0 && <div className="muted small">{tx("Parametrik özellik yok.")}</div>}
        {model.features.map((f) => (
          <div key={f.id} className="row-item">
            <span className="mono">{f.id}</span>
            <span className="muted small grow">
              {f.type === "hole" && `Ø${f.diameter} ${f.depth ? `derinlik ${f.depth}` : tx("boydan boya")}`}
              {f.type === "pocket" && `${f.length}×${f.width}×${f.depth}${f.corner_radius ? ` R${f.corner_radius}` : ""}`}
              {f.type === "fillet" && `R${f.radius} · ${f.edges}`}
              {f.type === "chamfer" && `${f.size} mm · ${f.edges}`}
              {f.type === "engrave" &&
                tx("kazıma · {0} · derinlik {1}", {"0": f.source === "text" ? `"${f.text}" ${f.height} mm${f.font === "kontur" ? ` ${tx("Kontur")}` : ""}` : f.source === "svg" ? `SVG ${f.name ?? ""} ${f.size} mm` : tx("{0} yol", {0:f.path_count}), "1": f.depth})}
            </span>
            <button className="icon-btn" title={tx("Sil")} onClick={() => onDelete(f.id)}>
              🗑
            </button>
          </div>
        ))}
      </section>
      <section className="card">
        <h4>
           {tx("Bölgeler (")}{regions.length})
          {regions.length > 1 && (
            <button className="btn ghost small" title={tx("Bütün bölgeleri sil")} onClick={() => onDeleteRegions(regions.map((r) => r.region_id))}>
               {tx("Tümünü sil")} </button>
          )}
        </h4>
        {regions.length === 0 && <div className="muted small">{tx("⌘/Ctrl basılı tutup modelin bir yüzeyinde dikdörtgen, daire, serbest şekil veya çokgen çizin.")}</div>}
        {regions.map((r) => (
          <div
            key={r.region_id}
            className={`row-item clickable ${isSel(r.region_id) ? "on" : ""}`}
            onClick={(e) => onSelectRegion(r.region_id, e.shiftKey)}
            title={tx("Tık: seç · ⇧+tık: seçime ekle/çıkar")}
          >
            <span className="mono">{r.region_id}</span>
            <span className="muted small grow">
              {SHAPE_TR()[r.shape]} · {r.face_id} · {r.circle ? `Ø${n(r.circle.diameter)}` : r.placement ? `${n(r.placement.length)}×${n(r.placement.width)}` : `${n(r.area)} mm²`}
            </span>
            <button
              className="icon-btn"
              title={tx("Sil")}
              onClick={(e) => {
                e.stopPropagation();
                onDeleteRegions([r.region_id]);
              }}
            >
              🗑
            </button>
          </div>
        ))}
      </section>
      <EngraveCard selection={selection} onDone={onEngraved} onError={onError} />
      <section className="card">
        <h4>{tx("Tanınan delikler (")}{model.holes.length})</h4>
        {model.holes.map((h) => (
          <div key={h.id} className="row-item clickable" onClick={(e) => onSelectFace(h.face_ids[0], e.shiftKey)}>
            <span className="mono">{h.id}</span>
            <span className="muted small grow">
              Ø{n(h.diameter)} · {h.through ? tx("boydan boya") : tx("kör {0} mm", {"0": n(h.depth)})} · ({n(h.top_point[0], 1)}, {n(h.top_point[1], 1)})
            </span>
          </div>
        ))}
      </section>
      {model.pockets.length > 0 && (
        <section className="card">
          <h4>{tx("Tanınan cepler (")}{model.pockets.length})</h4>
          {model.pockets.map((p) => (
            <div key={p.id} className="row-item clickable" onClick={(e) => onSelectFace(p.floor_face_id, e.shiftKey)}>
              <span className="mono">{p.id}</span>
              <span className="muted small grow">
                {n(p.size_xy[0])}×{n(p.size_xy[1])}  {tx("· derinlik")} {n(p.depth)}
              </span>
            </div>
          ))}
        </section>
      )}
      {model.log.length > 0 && (
        <section className="card">
          <h4>{tx("İşlem geçmişi")}</h4>
          <ol className="log small">
            {model.log.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ Makine profili */
const travelText = (p: MachineProfile) => (["X", "Y", "Z"] as const).map((a) => `${a} ${p.travel[a].min}…${p.travel[a].max}`).join(" · ");

const ORIGIN_TR = (): Record<string, string> => ({ center: tx("merkez"), front_left: tx("sol ön"), front_right: tx("sağ ön"), back_left: tx("sol arka"), back_right: tx("sağ arka") });

function MachineCard({
  profiles,
  config,
  onChange,
  onEdit,
}: {
  profiles: MachineProfile[];
  config: MachiningConfig | null;
  onChange: (id: string) => void;
  onEdit: (sub: SettingsSub) => void;
}) {
  useLocale();
  const p = profiles.find((x) => x.id === config?.profile_id);
  const mine = profiles.filter((x) => !x.builtin);
  const [mats, setMats] = useState<Record<string, string>>({});
  useEffect(() => {
    api.materials().then((m) => setMats(Object.fromEntries(m.materials.map((x) => [x.id, x.name]))), () => {});
  }, []);
  const st = config?.setup;
  return (
    <section className="card">
      <h4>
         {tx("Makine ve kurulum")} <button className="btn small ghost" onClick={() => onEdit("machine")}>
           {tx("Makineyi düzenle")} </button>
      </h4>
      <select className="select" value={config?.profile_id ?? ""} onChange={(e) => onChange(e.target.value)} disabled={!profiles.length}>
        {mine.length > 0 && (
          <optgroup label={tx("Makinelerim")}>
            {mine.map((x) => (
              <option key={x.id} value={x.id}>
                {profileLabel(x)}
              </option>
            ))}
          </optgroup>
        )}
        <optgroup label={tx("Hazır kütüphane")}>
          {profiles
            .filter((x) => x.builtin)
            .map((x) => (
              <option key={x.id} value={x.id}>
                {profileLabel(x)}
              </option>
            ))}
        </optgroup>
      </select>
      {st && config && (
        <button className="setup-line small" onClick={() => onEdit("setup")} title={tx("Kurulumu düzenle")}>
          <span className="mono">G{st.wcs}</span>  {tx("· sıfır")} {ORIGIN_TR()[st.origin_xy]} · {materialLabel({id: st.material, name: mats[st.material] ?? st.material})}
          {st.stock_side || st.stock_top ? tx(" · pay {0}/{1} mm", {"0": st.stock_side, "1": st.stock_top}) : ""}  {tx("· kesme")} {config.cutting_mode === "auto" ? tx("otomatik") : tx("elle")}
        </button>
      )}
      {p && (
        <>
          <div className="chips">
            <span className="chip">{p.controller}</span>
            <span className={`chip ${p.tool_changer ? "" : "off"}`}>{p.tool_changer ? tx("M6 · {0} takım", {"0": p.max_tools}) : tx("M6 yok (elle değişim)")}</span>
            <span className={`chip ${p.canned_cycles ? "" : "off"}`}>{p.canned_cycles ? tx("G81/G83 var") : tx("Delme döngüsü yok")}</span>
            <span className="chip">S ≤ {p.max_spindle}</span>
            <span className="chip">F ≤ {p.max_feed}</span>
          </div>
          <div className="muted small">{tx("Kurs (makine koord.):")} {travelText(p)}</div>
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ G-code sekmesi */
function GcodeTab({
  hasModel,
  gcode,
  activeLine,
  profiles,
  config,
  onProfileChange,
  onEditSettings,
  onGenerate,
  onLoaded,
  onError,
}: {
  onEditSettings: (sub: SettingsSub) => void;
  hasModel: boolean;
  gcode: GcodeRecord | null;
  activeLine: number | null;
  profiles: MachineProfile[];
  config: MachiningConfig | null;
  onProfileChange: (id: string) => void;
  onGenerate: (ops: string[]) => void;
  onLoaded: (g: GcodeRecord) => void;
  onError: (m: string) => void;
}) {
  useLocale();
  const [ops, setOps] = useState<string[]>(["face", "drill", "pocket", "engrave", "contour"]);
  const [focusLine, setFocusLine] = useState<number | null>(null);
  const codeRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const line = focusLine ?? activeLine;

  useEffect(() => {
    if (!line || !codeRef.current) return;
    const el = codeRef.current.querySelector(`[data-line="${line}"]`) as HTMLElement | null;
    if (el) codeRef.current.scrollTop = el.offsetTop - codeRef.current.clientHeight / 2;
  }, [line]);
  useEffect(() => setFocusLine(null), [activeLine]);

  const toggle = (o: string) => setOps((p) => (p.includes(o) ? p.filter((x) => x !== o) : [...p, o]));
  const v = gcode?.validation;
  const issueLines = new Map<number, string>();
  v?.issues.forEach((i) => i.line && !issueLines.has(i.line) && issueLines.set(i.line, i.severity));

  const profileName = (id: string | null | undefined) => { const p = profiles.find((p) => p.id === id); return p ? profileLabel(p) : id ?? ""; };
  const mismatch = gcode?.generated_for && config && gcode.generated_for !== config.profile_id;

  return (
    <div className="pad stack gcode-tab">
      <MachineCard profiles={profiles} config={config} onChange={onProfileChange} onEdit={onEditSettings} />
      <section className="card">
        <h4>{tx("G-code üret")}</h4>
        <div className="ops">
          {[
            ["face", tx("Alın")],
            ["drill", tx("Delme")],
            ["pocket", tx("Cep boşaltma")],
            ["engrave", tx("Kazıma")],
            ["contour", tx("Dış kontur")],
          ].map(([k, l]) => (
            <label key={k}>
              <input type="checkbox" checked={ops.includes(k)} onChange={() => toggle(k)} /> {l}
            </label>
          ))}
        </div>
        <div className="btn-row">
          <button className="btn primary" disabled={!hasModel || ops.length === 0} onClick={() => onGenerate(ops)}>
             {tx("Üret")} </button>
          <button className="btn" onClick={() => fileRef.current?.click()}>
             {tx("G-code yükle…")} </button>
          <input
            ref={fileRef}
            type="file"
            accept=".nc,.gcode,.ngc,.tap,.txt"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (!f) return;
              try {
                onLoaded(await api.gcodeText(await f.text(), f.name));
              } catch (err) {
                onError((err as Error).message);
              }
            }}
          />
          {gcode && (
            <button className="btn" onClick={() => void api.download(`/api/gcode/${gcode.gcode_id}/download`, `${gcode.gcode_id}.nc`).catch((err: Error) => onError(err.message))}>
               {tx("İndir (.nc)")} </button>
          )}
        </div>
      </section>
      {gcode && (
        <>
          {gcode.stale && <div className="banner warn">{tx("Model bu G-code üretildikten sonra değişti. Yeniden üretmeniz önerilir.")}</div>}
          {mismatch && (
            <div className="banner warn">
               {tx("Bu program")} <b>{profileName(gcode.generated_for)}</b>  {tx("için üretildi; şu an")} <b>{profileName(config?.profile_id)}</b>  {tx("makinesine göre doğrulanıyor. Bu makine için yeniden üretin.")} </div>
          )}
          {gcode.plan.length > 0 && (
            <section className="card">
              <h4>{tx("Üretim planı")}</h4>
              <ol className="plan small">
                {gcode.plan.map((p, i) => (
                  <li key={i}>
                    <span className="mono">T{p.tool}</span>{" "}
                    {({ face: tx("Alın"), drill: tx("Delme"), helical: tx("Helisel delik"), pocket: tx("Cep"), engrave: tx("Kazıma"), contour: tx("Kontur") } as Record<string, string>)[p.op] ?? p.op} · {p.feature}
                    {p.cycle ? ` (${p.cycle})` : ""}
                    {p.rpm != null && (
                      <span className="plan-cd mono" title={p.source}>
                        S{p.rpm} F{p.feed}
                        {p.stepdown != null ? ` ap${p.stepdown}` : ""}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
              {gcode.warnings.length > 0 && (
                <ul className="warnings small">
                  {gcode.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              )}
            </section>
          )}
          {v && (
            <section className="card">
              <h4>
                 {tx("Doğrulama")}{" "}
                <span className={`badge ${v.valid ? "ok" : "err"}`}>{v.valid ? tx("Hata yok") : tx("Hatalı")}</span>
              </h4>
              <div className="muted small">
                {profileName(v.profile.id) || v.profile.name} · G{gcode.setup?.wcs ?? config?.setup.wcs ?? 54} = ({v.setup.work_offset.map((x) => n(x, 1)).join(", ")})
              </div>
              <div className="levels">
                {Object.entries(v.levels).map(([lv, L]) => (
                  <div key={lv} className={`level ${L.counts.error ? "err" : L.counts.warning ? "warn" : "ok"}`}>
                    <span className="mono">S{lv}</span>
                    <span>{L.name}</span>
                    <span className="small">
                      {L.counts.error ? <span className="sev error">{L.counts.error}  {tx("hata")} </span> : null}
                      {L.counts.warning ? <span className="sev warning">{L.counts.warning}  {tx("uyarı")} </span> : null}
                      {!L.counts.error && !L.counts.warning ? <span className="sev ok">✓</span> : null}
                    </span>
                  </div>
                ))}
              </div>
              <div className="counts small">
                <span className="sev error">{v.counts.error}  {tx("hata")}</span>
                <span className="sev warning">{v.counts.warning}  {tx("uyarı")}</span>
                <span className="sev info">{v.counts.info}  {tx("bilgi")}</span>
                <span className="muted">
                  {v.stats.line_count}  {tx("satır · ~")}{Math.round(v.stats.estimated_time_s)}  {tx("sn")} {v.stats.tool_change_time_s ? tx(" (takım değişimi {0} sn dahil)", {"0": Math.round(v.stats.tool_change_time_s)}) : ""} · {v.stats.tool_changes}  {tx("takım değişimi")} </span>
              </div>
              <div className="issues">
                {v.issues.map((i, k) => (
                  <div key={k} className={`issue ${i.severity}`} onClick={() => i.line && setFocusLine(i.line)} title={i.rule}>
                    <span className="mono">{i.line ? `L${i.line}` : "—"}</span>
                    <div>
                      <div>
                        <span className="code-badge mono">
                          {i.code} · S{i.level}
                        </span>{" "}
                        {i.message}
                      </div>
                      {i.suggestion && <div className="muted small">{i.suggestion}</div>}
                    </div>
                  </div>
                ))}
              </div>
              <details className="not-checked small">
                <summary>{tx("Kontrol edilmeyenler (")}{v.not_checked.length})</summary>
                <ul>
                  {v.not_checked.map((t, k) => (
                    <li key={k}>{t}</li>
                  ))}
                </ul>
              </details>
              <div className="muted tiny">{v.disclaimer}</div>
            </section>
          )}
          <section className="card code-card">
            <h4>
              Program{" "}
              <span className="muted small">
                ({gcode.source === "generated" ? tx("üretilen · post: {0}", {"0": gcode.post}) : gcode.source.replace("upload:", tx("yüklenen: "))})
              </span>
            </h4>
            <div className="code" ref={codeRef}>
              {gcode.text.split("\n").map((l, i) => (
                <div key={i} data-line={i + 1} className={`code-line ${line === i + 1 ? "active" : ""} ${issueLines.get(i + 1) ?? ""}`}>
                  <span className="ln">{i + 1}</span>
                  <span>{l || " "}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

export default function Inspector(props: {
  tab: "model" | "gcode" | "shop" | "settings";
  setTab: (t: "model" | "gcode" | "shop" | "settings") => void;
  model: ModelSummary | null;
  selection: Selection[];
  gcode: GcodeRecord | null;
  config: MachiningConfig | null;
  profiles: MachineProfile[];
  activeLine: number | null;
  onProfileChange: (id: string) => void;
  onDeleteFeature: (id: string) => void;
  onSelectFace: (id: string, add?: boolean) => void;
  regions: RegionInfo[];
  onSelectRegion: (id: string, add: boolean) => void;
  onDeselect: (id: string) => void;
  onClearSelection: () => void;
  onDeleteRegions: (ids: string[]) => void;
  onEngraved: (msg: string) => void;
  onGenerate: (ops: string[]) => void;
  onGcodeLoaded: (g: GcodeRecord) => void;
  onConfigSaved: (c: MachiningConfig) => void;
  onProfilesChanged: (profiles: MachineProfile[], c: MachiningConfig, msg: string) => void;
  onError: (m: string) => void;
  onAsk: (text: string) => void;
  onShowLine: (line: number) => void;
}) {
  useLocale();
  const { tab, setTab } = props;
  const v = props.gcode?.validation;
  const [sub, setSub] = useState<SettingsSub>("setup");
  const editSettings = (s: SettingsSub) => {
    setSub(s);
    setTab("settings");
  };
  return (
    <div className="inspector">
      <div className="tabs">
        <button className={tab === "model" ? "on" : ""} onClick={() => setTab("model")}>
          Model
        </button>
        <button className={tab === "gcode" ? "on" : ""} onClick={() => setTab("gcode")}>
          G-code {v && <span className={`dot ${v.counts.error ? "err" : v.counts.warning ? "warn" : "ok"}`} />}
        </button>
        <button className={tab === "shop" ? "on" : ""} onClick={() => setTab("shop")} title={tx("Prove-out: satır, alarm, ölçü düzeltme, program farkı")}>
           {tx("Atölye")} </button>
        <button className={tab === "settings" ? "on" : ""} onClick={() => setTab("settings")}>
           {tx("İşleme ayarları")} </button>
      </div>
      <div className="inspector-body">
        {tab === "model" && <ModelTab
            model={props.model}
            selected={props.selection}
            onDelete={props.onDeleteFeature}
            onSelectFace={props.onSelectFace}
            regions={props.regions}
            onSelectRegion={props.onSelectRegion}
            onDeselect={props.onDeselect}
            onClearSelection={props.onClearSelection}
            onDeleteRegions={props.onDeleteRegions}
            onEngraved={props.onEngraved}
            onError={props.onError}
          />}
        {tab === "gcode" && (
          <GcodeTab
            hasModel={!!props.model}
            gcode={props.gcode}
            activeLine={props.activeLine}
            profiles={props.profiles}
            config={props.config}
            onProfileChange={props.onProfileChange}
            onEditSettings={editSettings}
            onGenerate={props.onGenerate}
            onLoaded={props.onGcodeLoaded}
            onError={props.onError}
          />
        )}
        {tab === "shop" && (
          <ShopTab
            gcode={props.gcode}
            activeLine={props.activeLine}
            onShowLine={props.onShowLine}
            onAsk={props.onAsk}
            onGcodeLoaded={props.onGcodeLoaded}
            onError={props.onError}
          />
        )}
        {tab === "settings" && (
          <MachiningSettings
            config={props.config}
            profiles={props.profiles}
            model={props.model}
            gcode={props.gcode}
            sub={sub}
            setSub={setSub}
            onSaved={props.onConfigSaved}
            onProfiles={props.onProfilesChanged}
            onActivate={props.onProfileChange}
            onError={props.onError}
          />
        )}
      </div>
    </div>
  );
}
