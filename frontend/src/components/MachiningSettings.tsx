import { tx, useLocale } from "../i18n";
import { materialLabel, materialGroup, profileLabel, builtinText } from "../presentation";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type {
  Coolant,
  CuttingPreset,
  CuttingRec,
  GcodeRecord,
  MachineProfile,
  MachiningConfig,
  Material,
  ModelSummary,
  OpSettings,
  OpType,
  OriginXY,
  Tool,
  ToolType,
} from "../types";

/** İşleme ayarları: Kurulum · Takımlar · Operasyonlar · Makine */

export type SettingsSub = "setup" | "tools" | "ops" | "machine";

const r = (v: number, d = 2) => (Math.round(v * 10 ** d) / 10 ** d).toString();

const TOOL_TYPES = (): [ToolType, string][] => ([
  ["endmill", tx("Parmak freze")],
  ["bullmill", tx("Köşe radyuslu freze")],
  ["ballmill", tx("Küresel uçlu freze")],
  ["drill", tx("Matkap")],
  ["spot_drill", tx("Punta matkabı")],
  ["chamfer", tx("Pah kırıcı")],
  ["vbit", tx("V-uç (gravür)")],
]);
const TOOL_TR = Object.fromEntries(TOOL_TYPES()) as Record<ToolType, string>;
const MILLS: ToolType[] = ["endmill", "bullmill"];
const HAS_ANGLE: ToolType[] = ["vbit", "chamfer", "drill", "spot_drill"];

const OPS = (): { id: OpType; name: string; hint: string }[] => ([
  { id: "face", name: tx("Alın frezeleme"), hint: tx("Ham parçanın üst payını düzler. Yalnızca Kurulum'da üst pay > 0 ise çalışır.") },
  { id: "drill", name: tx("Delme"), hint: tx("Çapı eşleşen matkapla G81 / G83.") },
  { id: "helical", name: tx("Helisel delik"), hint: tx("Delme açıkken, çapına uygun matkabı olmayan delikler frezeyle helisel interpolasyonla açılır. Kapalıysa bu delikler atlanır.") },
  { id: "pocket", name: tx("Cep boşaltma"), hint: tx("İçten dışa ofset turları, kademeli derinlik.") },
  { id: "engrave", name: tx("Kazıma"), hint: tx("V-uç (yoksa en küçük freze) çizgiyi takip eder.") },
  { id: "contour", name: tx("Dış kontur"), hint: tx("Parçayı ham parçadan ayırır (boydan boya, tırmanma).") },
]);

const ORIGINS = (): { id: OriginXY; label: string; x: number; y: number }[] => ([
  { id: "back_left", label: tx("Sol arka"), x: 0, y: 0 },
  { id: "back_right", label: tx("Sağ arka"), x: 1, y: 0 },
  { id: "center", label: tx("Merkez"), x: 0.5, y: 0.5 },
  { id: "front_left", label: tx("Sol ön"), x: 0, y: 1 },
  { id: "front_right", label: tx("Sağ ön"), x: 1, y: 1 },
]);
const COOLANT_TR = (): Record<Coolant, string> => ({ off: tx("Kapalı"), flood: tx("Sıvı (flood)"), mist: tx("Sis (mist)"), air: tx("Hava") });

/* ------------------------------------------------------------------ küçük bileşenler */
function Num({
  label,
  value,
  onChange,
  unit,
  placeholder,
  nullable,
  disabled,
  step = "any",
  min,
  title,
}: {
  label: ReactNode;
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  unit?: string;
  placeholder?: string;
  nullable?: boolean;
  disabled?: boolean;
  step?: string;
  min?: number;
  title?: string;
}) {
  useLocale();
  return (
    <label className="field" title={title}>
      <span>{label}</span>
      <input
        type="number"
        step={step}
        min={min}
        disabled={disabled}
        placeholder={placeholder}
        value={value ?? ""}
        onChange={(e) => {
          const s = e.target.value;
          if (s === "") return onChange(nullable ? null : 0);
          const v = Number(s);
          if (Number.isFinite(v)) onChange(v);
        }}
      />
      <em>{unit}</em>
    </label>
  );
}

function Text({ label, value, onChange, placeholder, disabled }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; disabled?: boolean }) {
  useLocale();
  return (
    <label className="field wide">
      <span>{label}</span>
      <input value={value} placeholder={placeholder} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Seg<T extends string>({ value, options, onChange, disabled }: { value: T; options: [T, string][]; onChange: (v: T) => void; disabled?: boolean }) {
  useLocale();
  return (
    <div className={`seg ${disabled ? "disabled" : ""}`} role="radiogroup">
      {options.map(([k, l]) => (
        <button key={k} type="button" role="radio" aria-checked={value === k} className={value === k ? "on" : ""} disabled={disabled} onClick={() => onChange(k)}>
          {l}
        </button>
      ))}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  useLocale();
  return (
    <div className="field row">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ Kurulum */
function OriginPicker({ value, onChange, side }: { value: OriginXY; onChange: (v: OriginXY) => void; side: number }) {
  useLocale();
  // Üstten görünüş: yukarı = arka (Y+), sağ = X+. Oklar her zaman pozitif yönü gösterir.
  const W = 168,
    H = 108,
    P = 22;
  const inset = side > 0 ? Math.min(10, 4 + side) : 0;
  const pos = (o: { x: number; y: number }) => [P + o.x * (W - 2 * P), P + o.y * (H - 2 * P)] as const;
  const [ox, oy] = pos(ORIGINS().find((x) => x.id === value)!);
  return (
    <svg className="origin-pick" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="radiogroup" aria-label={tx("İş sıfırı XY")}>
      <rect x={P} y={P} width={W - 2 * P} height={H - 2 * P} rx="3" className="stock" />
      {inset > 0 && <rect x={P + inset} y={P + inset} width={W - 2 * P - 2 * inset} height={H - 2 * P - 2 * inset} rx="2" className="part" />}
      <line x1={ox} y1={oy} x2={ox + 18} y2={oy} className="ax x" />
      <line x1={ox} y1={oy} x2={ox} y2={oy - 18} className="ax y" />
      <text x={ox + 21} y={oy + 3.5} className="axl x">
        X
      </text>
      <text x={ox} y={oy - 21} className="axl y" textAnchor="middle">
        Y
      </text>
      {ORIGINS().map((p) => {
        const [cx, cy] = pos(p);
        return (
          <g key={p.id} className={`opt ${p.id === value ? "on" : ""}`} onClick={() => onChange(p.id)} role="radio" aria-checked={p.id === value} aria-label={p.label}>
            <circle cx={cx} cy={cy} r="10" className="hit" />
            <circle cx={cx} cy={cy} r={p.id === value ? 5 : 3.5} className="dot" />
            <title>{p.label}</title>
          </g>
        );
      })}
      <text x={W / 2} y={H - 5} className="front" textAnchor="middle">
         {tx("ön · operatör")} </text>
    </svg>
  );
}

function SetupPane({
  c,
  setC,
  profile,
  materials,
  model,
}: {
  c: MachiningConfig;
  setC: (c: MachiningConfig) => void;
  profile?: MachineProfile;
  materials: Material[];
  model: ModelSummary | null;
}) {
  useLocale();
  const st = c.setup;
  const setSt = (p: Partial<MachiningConfig["setup"]>) => setC({ ...c, setup: { ...st, ...p } });
  const cut = c.cutting;
  const setCut = (k: string, v: number | string) => setC({ ...c, cutting: { ...cut, [k]: v } });
  const offset = (st.work_offset ?? profile?.default_work_offset ?? [0, 0, 0]) as [number, number, number];
  const { locale } = useLocale();
  const groups = useMemo(() => {
    const g = new Map<string, Material[]>();
    materials.forEach((m) => { const label = `${m.group} · ${materialGroup(m.group, m.group_tr)}`; g.set(label, [...(g.get(label) ?? []), m]); });
    return [...g.entries()];
  }, [materials, locale]);
  const size = model?.bbox.size;
  const coolants: Coolant[] = ["off", ...(Object.keys(profile?.coolant_modes ?? { flood: "M8" }) as Coolant[])];
  if (!coolants.includes(cut.coolant)) coolants.push(cut.coolant); // makinede yok ama ayarlı: göster, uyarı aşağıda
  const wcsList = profile?.work_offsets?.length ? profile.work_offsets : [54];
  const originLabel = ORIGINS().find((o) => o.id === st.origin_xy)?.label ?? "";

  return (
    <>
      <section className="card">
        <h4>{tx("Malzeme ve kesme verisi")}</h4>
        <label className="field wide">
          <span>{tx("İş parçası")}</span>
          <select className="select" value={st.material} onChange={(e) => setSt({ material: e.target.value })}>
            {groups.map(([g, list]) => (
              <optgroup key={g} label={g}>
                {list.map((m) => (
                  <option key={m.id} value={m.id}>
                    {materialLabel(m)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <Row label={tx("Kesme verisi")}>
          <Seg
            value={c.cutting_mode}
            options={[
              ["auto", tx("Otomatik")],
              ["manual", tx("Elle")],
            ]}
            onChange={(v) => setC({ ...c, cutting_mode: v })}
          />
        </Row>
        <div className="muted small">
          {c.cutting_mode === "auto"
            ? tx("Devir ve ilerleme önce operasyon ayarından, sonra takımın bu malzemeye uyan presetinden, yoksa malzeme tablosundan (n = 1000·Vc/πD, F = n·z·fz) gelir. Makine sınırını aşanlar aynı talaş kalınlığıyla düşürülür.")
            : tx("Aşağıdaki sabit değerler tüm frezeleme ve delme operasyonlarında kullanılır (operasyon ayarı varsa o geçerli).")}
        </div>
        {c.cutting_mode === "manual" && (
          <div className="grid2 tight">
            <Num label={tx("Freze devri")} unit={tx("dev/dk")} value={cut.spindle_rpm} onChange={(v) => setCut("spindle_rpm", v ?? 0)} />
            <Num label={tx("İlerleme")} unit={tx("mm/dk")} value={cut.feed_rate} onChange={(v) => setCut("feed_rate", v ?? 0)} />
            <Num label={tx("Dalma")} unit={tx("mm/dk")} value={cut.plunge_rate} onChange={(v) => setCut("plunge_rate", v ?? 0)} />
            <Num label={tx("Kademe")} unit="mm" value={cut.depth_per_pass} onChange={(v) => setCut("depth_per_pass", v ?? 0)} />
            <Num label={tx("Yan paso")} unit="×D" value={cut.stepover_ratio} onChange={(v) => setCut("stepover_ratio", v ?? 0)} />
            <Num label={tx("Matkap devri")} unit={tx("dev/dk")} value={cut.drill_rpm} onChange={(v) => setCut("drill_rpm", v ?? 0)} />
            <Num label={tx("Delme ilerl.")} unit={tx("mm/dk")} value={cut.drill_feed} onChange={(v) => setCut("drill_feed", v ?? 0)} />
            <Num label={tx("Gaga (Q)")} unit="mm" value={cut.peck_depth} onChange={(v) => setCut("peck_depth", v ?? 0)} />
          </div>
        )}
      </section>

      <section className="card">
        <h4>{tx("Ham parça")}</h4>
        <div className="grid2 tight">
          <Num label={tx("Yan pay")} unit="mm" min={0} value={st.stock_side} onChange={(v) => setSt({ stock_side: Math.max(0, v ?? 0) })} title={tx("Parçanın her yanında")} />
          <Num label={tx("Üst pay")} unit="mm" min={0} value={st.stock_top} onChange={(v) => setSt({ stock_top: Math.max(0, v ?? 0) })} title={tx("> 0 ise alın frezeleme eklenir")} />
        </div>
        <div className="muted small">
          {size
            ? tx("Ham parça {0} × {1} × {2} mm (parça {3} × {4} × {5}).", {"0": r(size[0] + 2 * st.stock_side, 1), "1": r(size[1] + 2 * st.stock_side, 1), "2": r(size[2] + st.stock_top, 1), "3": r(size[0], 1), "4": r(size[1], 1), "5": r(size[2], 1)})
            : tx("Ham parça = parçanın sınır kutusu + paylar.")}{" "}
          {st.stock_top > 0 ? tx("Üst pay alın frezeleme ile kaldırılır.") : ""}
        </div>
      </section>

      <section className="card">
        <h4>{tx("İş sıfırı")}</h4>
        <div className="origin-row">
          <OriginPicker value={st.origin_xy} onChange={(v) => setSt({ origin_xy: v })} side={st.stock_side} />
          <div className="small">
            <div>
              <b>{originLabel}</b> <span className="muted">{tx("(üstten görünüş)")}</span>
            </div>
            <div className="muted">{tx("XY: ham parçanın")} {st.origin_xy === "center" ? tx("merkezi") : tx("köşesi")}{tx(". Z0: ham parçanın üst yüzeyi.")}</div>
            <label className="field wide" style={{ marginTop: 6 }}>
              <span>{tx("Koordinat sistemi")}</span>
              <select className="select" value={st.wcs} onChange={(e) => setSt({ wcs: Number(e.target.value) })}>
                {wcsList.map((w) => (
                  <option key={w} value={w}>
                    G{w}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        <div className="muted small">
           {tx("Bu sıfır noktasının makine koordinatındaki yeri (G")}{st.wcs}  {tx("ofseti). Kurs kontrolü: makine = iş + ofset (+ takım boyu).")} </div>
        <div className="grid3">
          {(["X", "Y", "Z"] as const).map((ax, i) => (
            <Num
              key={ax}
              label={ax}
              unit="mm"
              value={offset[i]}
              onChange={(v) => {
                const o = [...offset] as [number, number, number];
                o[i] = v ?? 0;
                setSt({ work_offset: o });
              }}
            />
          ))}
        </div>
        {st.work_offset && profile && (
          <button className="btn small ghost" onClick={() => setSt({ work_offset: null })}>
             {tx("Makine varsayılanına dön (")}{profile.default_work_offset.join(", ")})
          </button>
        )}
      </section>

      <section className="card">
        <h4>{tx("Güvenlik ve soğutma")}</h4>
        <div className="grid2 tight">
          <Num label={tx("Güvenli Z")} unit="mm" value={cut.safe_z} onChange={(v) => setCut("safe_z", v ?? 0)} title={tx("Ham parça üstüne göre")} />
          <Num label={tx("R düzlemi")} unit="mm" value={cut.retract_z} onChange={(v) => setCut("retract_z", v ?? 0)} title={tx("Delme döngüsü, delik ağzının üstü")} />
          <Num label={tx("Taşma")} unit="mm" value={cut.breakthrough} onChange={(v) => setCut("breakthrough", v ?? 0)} title={tx("Boydan boya işlemede alt yüzeyin altına")} />
        </div>
        <Row label={tx("Soğutma")}>
          <Seg value={cut.coolant} options={coolants.map((k) => [k, COOLANT_TR()[k]])} onChange={(v) => setCut("coolant", v)} />
        </Row>
        {cut.coolant !== "off" && !profile?.coolant_modes?.[cut.coolant] && (
          <div className="banner warn small">{tx("Seçili makine bu soğutma modunu desteklemiyor; program soğutmasız üretilir.")}</div>
        )}
      </section>
    </>
  );
}

/* ------------------------------------------------------------------ Takımlar */
function newTool(type: ToolType, number: number): Tool {
  const base: Tool = {
    number,
    type,
    diameter: type === "drill" ? 5 : type === "vbit" || type === "chamfer" ? 6 : 6,
    description: "",
    length: null,
    angle: type === "vbit" ? 60 : type === "chamfer" ? 90 : type === "drill" ? 118 : type === "spot_drill" ? 90 : null,
    flutes: type === "vbit" ? 1 : 2,
    flute_length: null,
    stickout: null,
    shank_diameter: null,
    corner_radius: type === "bullmill" ? 0.5 : null,
    tool_material: type === "drill" ? "hss" : "carbide",
    coating: "",
    length_offset: null,
    diameter_offset: null,
    holder: "",
    vendor: "",
    product_id: "",
    presets: [],
  };
  return base;
}

const toolLabel = (t: Tool) => t.description || `Ø${r(t.diameter)} ${TOOL_TR[t.type].toLowerCase()}`;

function RecommendBox({ tool, material, materialName, onAdd }: { tool: Tool; material: string; materialName: string; onAdd: (p: CuttingPreset) => void }) {
  useLocale();
  const [rec, setRec] = useState<(CuttingRec & { machine: string }) | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const key = `${tool.type}|${tool.diameter}|${tool.flutes}|${tool.tool_material}|${material}`;
  useEffect(() => {
    setRec(null);
    setErr(null);
  }, [key]);
  const ask = async () => {
    try {
      setErr(null);
      setRec(await api.recommend({ tool_type: tool.type, diameter: tool.diameter, flutes: tool.flutes, tool_material: tool.tool_material, material }));
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const drill = tool.type === "drill" || tool.type === "spot_drill";
  return (
    <div className="rec-box">
      {!rec ? (
        <button className="btn small" onClick={ask} disabled={!(tool.diameter > 0)}>
          {materialName}  {tx("için kesme verisi öner")} </button>
      ) : (
        <>
          <div className="rec-nums">
            <div>
              <b>{rec.rpm}</b>
              <span>{tx("dev/dk")}</span>
            </div>
            <div>
              <b>{rec.feed}</b>
              <span>{tx("F mm/dk")}</span>
            </div>
            {drill ? (
              <div>
                <b>{rec.peck}</b>
                <span>{tx("gaga mm")}</span>
              </div>
            ) : (
              <>
                <div>
                  <b>{rec.plunge}</b>
                  <span>{tx("dalma")}</span>
                </div>
                <div>
                  <b>{rec.stepdown}</b>
                  <span>ap mm</span>
                </div>
                <div>
                  <b>{Math.round((rec.stepover_ratio ?? 0) * 100)}%</b>
                  <span>ae</span>
                </div>
              </>
            )}
          </div>
          <div className="mono tiny rec-formula">
            Vc {rec.vc}  {tx("m/dk · fz")} {rec.fz}  {tx("mm/diş ·")} {rec.formula}
          </div>
          {rec.notes.map((n, i) => (
            <div key={i} className="small warn-text">
              {n}
            </div>
          ))}
          <div className="muted tiny">
            {rec.machine}  {tx("sınırlarıyla.")} {rec.source}
          </div>
          <div className="btn-row">
            <button
              className="btn small"
              onClick={() =>
                onAdd({
                  name: tx("{0} (öneri)", {"0": materialName.split(" (")[0]}),
                  material,
                  vc: rec.vc,
                  fz: rec.fz,
                  rpm: null,
                  feed: null,
                  plunge: null,
                  stepdown: rec.stepdown,
                  stepover_ratio: rec.stepover_ratio,
                })
              }
            >
               {tx("Preset olarak ekle")} </button>
            <button className="btn small ghost" onClick={() => setRec(null)}>
               {tx("Kapat")} </button>
          </div>
        </>
      )}
      {err && <div className="small err-text">{err}</div>}
    </div>
  );
}

function ToolEditor({
  t,
  c,
  setTool,
  usesLength,
  materials,
  isDefaultMill,
  setDefaultMill,
}: {
  t: Tool;
  c: MachiningConfig;
  setTool: (patch: Partial<Tool>) => void;
  usesLength: boolean;
  materials: Material[];
  isDefaultMill: boolean;
  setDefaultMill: () => void;
}) {
  useLocale();
  const matName = (id: string | null) => { const m = materials.find((m) => m.id === id); return m ? materialLabel(m) : id ?? tx("Tüm malzemeler"); };
  const setPreset = (i: number, p: Partial<CuttingPreset>) => setTool({ presets: t.presets.map((x, k) => (k === i ? { ...x, ...p } : x)) });
  const drill = t.type === "drill" || t.type === "spot_drill";
  return (
    <div className="tool-editor">
      <div className="grid2 tight">
        <label className="field wide span2">
          <span>{tx("Tür")}</span>
          <select className="select" value={t.type} onChange={(e) => setTool({ type: e.target.value as ToolType })}>
            {TOOL_TYPES().map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <div className="span2">
          <Text label={tx("Açıklama")} value={t.description} placeholder={toolLabel({ ...t, description: "" })} onChange={(v) => setTool({ description: v })} />
        </div>
        <Num label={tx("Çap")} unit="mm" min={0} value={t.diameter} onChange={(v) => setTool({ diameter: v ?? 0 })} />
        <Num label={tx("Ağız sayısı")} step="1" min={1} value={t.flutes} onChange={(v) => setTool({ flutes: Math.max(1, Math.round(v ?? 1)) })} />
        <Num label={tx("Ağız boyu")} unit="mm" nullable placeholder="?" value={t.flute_length} onChange={(v) => setTool({ flute_length: v })} title={tx("Kesici boy; derinlik bunu aşarsa uyarı verilir")} />
        <Num label={tx("Taşma")} unit="mm" nullable placeholder="?" value={t.stickout} onChange={(v) => setTool({ stickout: v })} title={tx("Tutucudan çıkan boy")} />
        <Num label={tx("Sap çapı")} unit="mm" nullable placeholder="=Ø" value={t.shank_diameter} onChange={(v) => setTool({ shank_diameter: v })} />
        {t.type === "bullmill" && <Num label={tx("Köşe R")} unit="mm" nullable value={t.corner_radius} onChange={(v) => setTool({ corner_radius: v })} />}
        {HAS_ANGLE.includes(t.type) && <Num label={tx("Uç açısı")} unit="°" nullable value={t.angle ?? null} onChange={(v) => setTool({ angle: v })} />}
      </div>
      <Row label={tx("Takım malzemesi")}>
        <Seg
          value={t.tool_material}
          options={[
            ["carbide", tx("Karbür")],
            ["hss", "HSS"],
          ]}
          onChange={(v) => setTool({ tool_material: v })}
        />
      </Row>
      <div className="grid2 tight">
        <Num
          label={tx("Takım boyu")}
          unit="mm"
          nullable
          placeholder="?"
          disabled={!usesLength}
          value={t.length}
          onChange={(v) => setTool({ length: v })}
          title={usesLength ? tx("İş mili burnundan uca; G43 H ile Z'ye eklenir") : tx("Bu makine G43 H kullanmaz")}
        />
        <Num label="H no" step="1" nullable placeholder={String(t.number)} value={t.length_offset} onChange={(v) => setTool({ length_offset: v == null ? null : Math.round(v) })} />
      </div>
      <div className="grid2 tight">
        <Text label={tx("Tutucu")} value={t.holder} placeholder={tx("ör. BT40 ER32")} onChange={(v) => setTool({ holder: v })} />
        <Text label={tx("Kaplama")} value={t.coating} placeholder={tx("ör. AlTiN")} onChange={(v) => setTool({ coating: v })} />
        <Text label={tx("Üretici")} value={t.vendor} onChange={(v) => setTool({ vendor: v })} />
        <Text label={tx("Ürün kodu")} value={t.product_id} onChange={(v) => setTool({ product_id: v })} />
      </div>
      {MILLS.includes(t.type) && (
        <label className="check small">
          <input type="radio" checked={isDefaultMill} onChange={setDefaultMill} />  {tx("Varsayılan freze (operasyonda takım seçilmemişse cep / kontur / helisel bununla)")} </label>
      )}

      <div className="presets">
        <div className="presets-head">
          <b>{tx("Kesme presetleri")}</b>
          <button
            className="btn small ghost"
            onClick={() =>
              setTool({
                presets: [...t.presets, { name: `Preset ${t.presets.length + 1}`, material: c.setup.material, vc: null, fz: null, rpm: null, feed: null, plunge: null, stepdown: null, stepover_ratio: null }],
              })
            }
          >
            + Preset
          </button>
        </div>
        {t.presets.length === 0 && <div className="muted small">{tx("Preset yok: malzeme tablosundan hesaplanır.")}</div>}
        {t.presets.map((p, i) => (
          <div key={i} className="preset">
            <div className="preset-top">
              <input className="preset-name" value={p.name} onChange={(e) => setPreset(i, { name: e.target.value })} aria-label={tx("Preset adı")} />
              <select className="select" value={p.material ?? ""} onChange={(e) => setPreset(i, { material: e.target.value || null })} aria-label={tx("Malzeme")}>
                <option value="">{tx("Tüm malzemeler")}</option>
                {materials.map((m) => (
                  <option key={m.id} value={m.id}>
                    {materialLabel(m)}
                  </option>
                ))}
              </select>
              <button className="icon-btn" title={tx("Preseti sil")} onClick={() => setTool({ presets: t.presets.filter((_, k) => k !== i) })}>
                ✕
              </button>
            </div>
            <div className="grid2 tight">
              <Num label="Vc" unit={tx("m/dk")} nullable placeholder={tx("tablo")} value={p.vc} onChange={(v) => setPreset(i, { vc: v })} />
              <Num label={drill ? "fz (fn/2)" : "fz"} unit="mm" nullable placeholder={tx("tablo")} value={p.fz} onChange={(v) => setPreset(i, { fz: v })} />
              <Num label={tx("Devir (sabit)")} unit={tx("dev/dk")} nullable placeholder={tx("Vc'den")} value={p.rpm} onChange={(v) => setPreset(i, { rpm: v })} />
              <Num label={tx("İlerleme")} unit={tx("mm/dk")} nullable placeholder={tx("fz'den")} value={p.feed} onChange={(v) => setPreset(i, { feed: v })} />
              {!drill && (
                <>
                  <Num label={tx("Dalma")} unit={tx("mm/dk")} nullable placeholder={tx("oran")} value={p.plunge} onChange={(v) => setPreset(i, { plunge: v })} />
                  <Num label={tx("Kademe ap")} unit="mm" nullable placeholder={tx("tablo")} value={p.stepdown} onChange={(v) => setPreset(i, { stepdown: v })} />
                  <Num label={tx("Yan paso ae")} unit="×D" nullable placeholder={tx("tablo")} value={p.stepover_ratio} onChange={(v) => setPreset(i, { stepover_ratio: v })} />
                </>
              )}
            </div>
            <div className="muted tiny">{matName(p.material)}  {tx("için. Boş alanlar malzeme tablosundan gelir.")}</div>
          </div>
        ))}
        <RecommendBox
          tool={t}
          material={c.setup.material}
          materialName={matName(c.setup.material)}
          onAdd={(p) => setTool({ presets: [...t.presets, p] })}
        />
      </div>
    </div>
  );
}

function ToolsPane({ c, setC, profile, materials }: { c: MachiningConfig; setC: (c: MachiningConfig) => void; profile?: MachineProfile; materials: Material[] }) {
  useLocale();
  const [open, setOpen] = useState<number | null>(null);
  const [addType, setAddType] = useState<ToolType>("endmill");
  const tools = [...c.tools].sort((a, b) => a.number - b.number);
  const setTool = (num: number, patch: Partial<Tool>) => setC({ ...c, tools: c.tools.map((t) => (t.number === num ? { ...t, ...patch } : t)) });
  const usesLength = profile?.requires_tool_length_comp ?? true;
  const used = (num: number) => OPS().filter((o) => c.op_settings[o.id]?.tool === num).map((o) => o.name);
  const remove = (num: number) => {
    const ops = Object.fromEntries(Object.entries(c.op_settings).map(([k, o]) => [k, o.tool === num ? { ...o, tool: null, preset: null } : o])) as MachiningConfig["op_settings"];
    const rest = c.tools.filter((t) => t.number !== num);
    const mill = c.mill_tool === num ? rest.find((t) => MILLS.includes(t.type))?.number ?? c.mill_tool : c.mill_tool;
    setC({ ...c, tools: rest, op_settings: ops, mill_tool: mill });
    setOpen(null);
  };
  const add = () => {
    const number = Math.max(0, ...c.tools.map((t) => t.number)) + 1;
    setC({ ...c, tools: [...c.tools, newTool(addType, number)] });
    setOpen(number);
  };
  const maxD = profile?.max_tool_diameter;
  return (
    <section className="card">
      <h4>
         {tx("Takım kütüphanesi")} <span className="muted small">{tools.length}  {tx("takım")}</span>
      </h4>
      <ul className="tool-list">
        {tools.map((t) => {
          const u = used(t.number);
          return (
            <li key={t.number} className={open === t.number ? "open" : ""}>
              <div className="tool-row" onClick={() => setOpen(open === t.number ? null : t.number)} role="button" aria-expanded={open === t.number}>
                <span className="mono tno">T{t.number}</span>
                <span className={`ttype ${t.type}`} title={TOOL_TR[t.type]} />
                <span className="tname">
                  {toolLabel(t)}
                  <span className="muted small">
                    {" "}
                    · {t.flutes}  {tx("ağız ·")} {t.tool_material === "hss" ? "HSS" : tx("karbür")}
                    {t.presets.length ? ` · ${t.presets.length} preset` : ""}
                  </span>
                </span>
                {c.mill_tool === t.number && <span className="chip">{tx("varsayılan")}</span>}
                {maxD && t.diameter > maxD && <span className="chip off">Ø &gt; {maxD}</span>}
                {usesLength && t.length == null && <span className="chip off" title={tx("Takım boyu girilmemiş")}>{tx("boy ?")}</span>}
                <button
                  className="icon-btn"
                  title={u.length ? tx("Kullanan operasyon: {0} (otomatiğe döner)", {"0": u.join(", ")}) : tx("Takımı sil")}
                  disabled={tools.length === 1}
                  onClick={(e) => {
                    e.stopPropagation();
                    remove(t.number);
                  }}
                >
                  ✕
                </button>
              </div>
              {open === t.number && (
                <ToolEditor
                  t={t}
                  c={c}
                  materials={materials}
                  usesLength={usesLength}
                  setTool={(p) => setTool(t.number, p)}
                  isDefaultMill={c.mill_tool === t.number}
                  setDefaultMill={() => setC({ ...c, mill_tool: t.number })}
                />
              )}
            </li>
          );
        })}
      </ul>
      <div className="btn-row">
        <select className="select" value={addType} onChange={(e) => setAddType(e.target.value as ToolType)} aria-label={tx("Eklenecek takım türü")}>
          {TOOL_TYPES().map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
        <button className="btn small" onClick={add}>
           {tx("+ Takım ekle")} </button>
      </div>
      <div className="muted tiny">
         {tx("Takım numarası sabittir; boy (G43 H) takımın kaydında durur.")} {usesLength ? "" : tx("Bu makine G43 H kullanmaz, boy alanı kullanılmaz.")}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ Operasyonlar */
function OpCard({
  op,
  c,
  setC,
  last,
}: {
  op: ReturnType<typeof OPS>[number];
  c: MachiningConfig;
  setC: (c: MachiningConfig) => void;
  last: GcodeRecord["plan"];
}) {
  useLocale();
  const s = c.op_settings[op.id];
  // helisel delik ayrı bir operasyon listesi elemanı değil: delme açıkken matkabı olmayan deliklere uygulanır
  const enabled = op.id === "helical" ? s.enabled : s.enabled && c.operations.includes(op.id);
  const set = (p: Partial<OpSettings>) => setC({ ...c, op_settings: { ...c.op_settings, [op.id]: { ...s, ...p } } });
  const toggle = (on: boolean) => {
    if (op.id === "helical") return set({ enabled: on });
    const order = OPS().map((o) => o.id);
    const ops = on ? order.filter((o) => o === op.id || c.operations.includes(o)) : c.operations.filter((o) => o !== op.id);
    setC({ ...c, operations: ops, op_settings: { ...c.op_settings, [op.id]: { ...s, enabled: on } } });
  };
  const fits = (t: Tool) =>
    op.id === "drill" ? t.type === "drill" : op.id === "engrave" ? ["vbit", "chamfer", ...MILLS].includes(t.type) : MILLS.includes(t.type);
  const tools = c.tools.filter(fits).sort((a, b) => a.number - b.number);
  const tool = c.tools.find((t) => t.number === s.tool) ?? (op.id === "drill" || op.id === "engrave" ? null : c.tools.find((t) => t.number === c.mill_tool));
  const presets = tool?.presets ?? [];
  const manual = c.cutting_mode === "manual";
  const drill = op.id === "drill";
  const used = last.filter((p) => p.op === op.id);
  const u = used[0];
  const overrides = [s.rpm, s.feed, s.plunge, s.stepdown, s.stepover_ratio, drill ? s.peck : null].filter((x) => x != null).length;
  const summary = [
    s.tool != null ? `T${s.tool}` : tx("otomatik takım"),
    s.preset ? `preset: ${s.preset}` : null,
    overrides ? tx("{0} elle değer", {"0": overrides}) : null,
    (op.id === "pocket" || op.id === "contour") && s.entry === "ramp" ? tx("rampa {0}°", {"0": s.ramp_angle}) : null,
    s.finish_pass ? tx("son paso {0} mm", {"0": s.finish_allowance}) : null,
    s.stock_xy || s.stock_z ? tx("pay {0}/{1}", {"0": s.stock_xy, "1": s.stock_z}) : null,
    drill ? (s.cycle === "auto" ? tx("G81/G83 oto") : s.cycle) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <details className={`card op-card ${enabled ? "" : "off"}`}>
      <summary>
        <label className="op-toggle" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={enabled} onChange={(e) => toggle(e.target.checked)} aria-label={tx("{0} etkin", {"0": op.name})} />
        </label>
        <div className="op-title">
          <b>{op.name}</b>
          <span className="muted small">{summary}</span>
        </div>
        <span className="chev" aria-hidden>
          ▸
        </span>
      </summary>
      <div className="op-body">
        <div className="muted small">{op.hint}</div>
        {u && (
          <div className="last-used small">
             {tx("Son üretim")}{used.length > 1 ? tx(" ({0} işlem)", {"0": used.length}) : ""}: <span className="mono">T{u.tool}</span> · <b>{u.rpm}</b>  {tx("dev/dk · F")}<b>{u.feed}</b>
            {u.stepdown != null ? ` · ap ${u.stepdown}` : ""}
            {u.peck != null && u.cycle?.startsWith("G83") ? ` · Q${u.peck}` : ""}
            <div className="muted tiny">{u.source}</div>
          </div>
        )}
        <div className="grid2 tight">
          <label className="field wide">
            <span>{tx("Takım")}</span>
            <select className="select" value={s.tool ?? ""} onChange={(e) => set({ tool: e.target.value === "" ? null : Number(e.target.value), preset: null })}>
              <option value="">{drill ? tx("Otomatik (çapı eşleşen)") : op.id === "engrave" ? tx("Otomatik (V-uç)") : tx("Otomatik (T{0})", {"0": c.mill_tool})}</option>
              {tools.map((t) => (
                <option key={t.number} value={t.number}>
                  T{t.number} · {toolLabel(t)}
                </option>
              ))}
            </select>
          </label>
          <label className="field wide">
            <span>Preset</span>
            <select className="select" disabled={manual} value={s.preset ?? ""} onChange={(e) => set({ preset: e.target.value || null })}>
              <option value="">{tx("Malzemeye göre")}</option>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="sub-h">{tx("Kesme verisi (boş =")} {manual ? tx("elle ayarlar") : tx("otomatik")})</div>
        <div className="grid2 tight">
          <Num label={tx("Devir")} unit={tx("dev/dk")} nullable placeholder={tx("oto")} value={s.rpm} onChange={(v) => set({ rpm: v })} />
          <Num label={tx("İlerleme")} unit={tx("mm/dk")} nullable placeholder={tx("oto")} value={s.feed} onChange={(v) => set({ feed: v })} />
          {!drill && <Num label={tx("Dalma")} unit={tx("mm/dk")} nullable placeholder={tx("oto")} value={s.plunge} onChange={(v) => set({ plunge: v })} />}
          {!drill && <Num label={tx("Kademe")} unit="mm" nullable placeholder={tx("oto")} value={s.stepdown} onChange={(v) => set({ stepdown: v })} />}
          {(op.id === "pocket" || op.id === "face") && <Num label={tx("Yan paso")} unit="×D" nullable placeholder={tx("oto")} value={s.stepover_ratio} onChange={(v) => set({ stepover_ratio: v })} />}
        </div>
        {drill && (
          <>
            <Row label={tx("Döngü")}>
              <Seg
                value={s.cycle}
                options={[
                  ["auto", tx("Otomatik")],
                  ["G81", tx("G81 düz")],
                  ["G83", tx("G83 gagalı")],
                ]}
                onChange={(v) => set({ cycle: v })}
              />
            </Row>
            <div className="muted tiny">{tx("Otomatik: derinlik &gt; 3×Ø ise G83. Döngüsü olmayan makinede post G0/G1'e açar.")}</div>
            <Num label={tx("Gaga (Q)")} unit="mm" nullable placeholder={tx("oto")} value={s.peck} onChange={(v) => set({ peck: v })} disabled={s.cycle === "G81"} />
          </>
        )}
        {(op.id === "pocket" || op.id === "contour") && (
          <>
            <Row label={tx("Giriş")}>
              <Seg
                value={s.entry}
                options={[
                  ["plunge", tx("Dalma")],
                  ["ramp", tx("Rampa")],
                ]}
                onChange={(v) => set({ entry: v })}
              />
            </Row>
            {s.entry === "ramp" && <Num label={tx("Rampa açısı")} unit="°" min={0.5} value={s.ramp_angle} onChange={(v) => set({ ramp_angle: Math.min(45, Math.max(0.5, v ?? 3)) })} />}
            <label className="check small">
              <input type="checkbox" checked={s.finish_pass} onChange={(e) => set({ finish_pass: e.target.checked })} />  {tx("Son paso (kabada pay bırak, sonra tam ölçüye tek geçiş)")} </label>
            {s.finish_pass && <Num label={tx("Son paso payı")} unit="mm" value={s.finish_allowance} onChange={(v) => set({ finish_allowance: Math.max(0.01, v ?? 0.2) })} />}
          </>
        )}
        {op.id !== "drill" && (
          <>
            <div className="sub-h">{tx("Bırakılacak pay (sonraki operasyon için)")}</div>
            <div className="grid2 tight">
              {op.id !== "face" && op.id !== "engrave" && <Num label={tx("Yan (XY)")} unit="mm" min={0} value={s.stock_xy} onChange={(v) => set({ stock_xy: Math.max(0, v ?? 0) })} />}
              {op.id !== "contour" && <Num label={tx("Taban (Z)")} unit="mm" min={0} value={s.stock_z} onChange={(v) => set({ stock_z: Math.max(0, v ?? 0) })} />}
            </div>
          </>
        )}
        {overrides > 0 && (
          <button className="btn small ghost" onClick={() => set({ rpm: null, feed: null, plunge: null, stepdown: null, stepover_ratio: null, peck: null })}>
             {tx("Elle değerleri temizle")} </button>
        )}
      </div>
    </details>
  );
}

/* ------------------------------------------------------------------ Makine */
const CONTROLLERS: [string, string][] = [
  ["fanuc", "Fanuc"],
  ["linuxcnc", "LinuxCNC"],
  ["grbl", "GRBL"],
];

function MachineView({ p }: { p: MachineProfile }) {
  useLocale();
  const po = p.post_options;
  return (
    <table className="kv small">
      <tbody>
        {(p.manufacturer || p.machine_model) && (
          <tr>
            <td>{tx("Üretici / model")}</td>
            <td>
              {[p.manufacturer, p.machine_model].filter(Boolean).join(" ")}
              {p.serial ? ` · SN ${p.serial}` : ""}
            </td>
          </tr>
        )}
        <tr>
          <td>{tx("Kontrol ünitesi / post")}</td>
          <td>
            {p.controller} / {p.post}
          </td>
        </tr>
        <tr>
          <td>{tx("Kurs (makine koord.)")}</td>
          <td>{(["X", "Y", "Z"] as const).map((a) => `${a} ${p.travel[a].min}…${p.travel[a].max}`).join(" · ")} mm</td>
        </tr>
        <tr>
          <td>{tx("İş mili")}</td>
          <td>
            {p.min_spindle} … {p.max_spindle}  {tx("dev/dk")}{p.spindle_power_kw ? ` · ${p.spindle_power_kw} kW` : ""}
          </td>
        </tr>
        <tr>
          <td>{tx("İlerleme / hızlı")}</td>
          <td>
            ≤ {p.max_feed} / {p.rapid_feed}  {tx("mm/dk")} </td>
        </tr>
        <tr>
          <td>{tx("Takım")}</td>
          <td>
            {p.tool_changer ? tx("M6, {0} cep, ~{1} sn", {"0": p.max_tools, "1": p.tool_change_time_s}) : tx("elle (M0), ~{0} sn", {"0": p.tool_change_time_s})}
            {p.max_tool_diameter ? ` · Ø ≤ ${p.max_tool_diameter}` : ""}
            {p.max_tool_length ? tx(" · boy ≤ {0}", {"0": p.max_tool_length}) : ""}
          </td>
        </tr>
        <tr>
          <td>{tx("Delme döngüsü / G43")}</td>
          <td>
            {p.canned_cycles ? tx("G81/G83 var") : tx("yok (G0/G1'e açılır)")} · {p.requires_tool_length_comp ? "G43 H" : tx("G43 yok")}
          </td>
        </tr>
        <tr>
          <td>{tx("Soğutma")}</td>
          <td>
            {Object.entries(p.coolant_modes)
              .map(([k, v]) => `${COOLANT_TR()[k as Coolant] ?? k} ${v}`)
              .join(" · ") || tx("yok")}
          </td>
        </tr>
        <tr>
          <td>{tx("İş koordinatları")}</td>
          <td>{p.work_offsets.map((w) => `G${w}`).join(" ")}</td>
        </tr>
        <tr>
          <td>{tx("Post seçenekleri")}</td>
          <td>
            {po.line_numbers ? tx("N{0} numaralı", {"0": po.line_increment}) : tx("N yok")} · {po.comments ? tx("yorumlu") : tx("yorumsuz")}  {tx("· yay")} {po.arc_format === "r" ? "R" : "I/J"}  {tx("· son:")}{" "}
            {po.home_at_end === "none" ? tx("park yok") : po.home_at_end}
          </td>
        </tr>
      </tbody>
    </table>
  );
}

function MachineForm({ d, setD, isNew, ids }: { d: MachineProfile; setD: (d: MachineProfile) => void; isNew: boolean; ids: string[] }) {
  useLocale();
  const set = (p: Partial<MachineProfile>) => setD({ ...d, ...p });
  const setTravel = (ax: "X" | "Y" | "Z", k: "min" | "max", v: number) => set({ travel: { ...d.travel, [ax]: { ...d.travel[ax], [k]: v } } });
  const setPo = (p: Partial<MachineProfile["post_options"]>) => set({ post_options: { ...d.post_options, ...p } });
  const accel = (d.accel ?? {}) as Record<string, number>;
  const idBad = isNew && (!/^[a-z0-9][a-z0-9_-]{1,47}$/.test(d.id) || ids.includes(d.id));
  return (
    <div className="machine-form">
      <div className="sub-h">{tx("Kimlik")}</div>
      {isNew && (
        <label className={`field wide ${idBad ? "bad" : ""}`}>
          <span>{tx("Kimlik")}</span>
          <input value={d.id} onChange={(e) => set({ id: e.target.value.toLowerCase() })} />
        </label>
      )}
      {idBad && <div className="small err-text">{tx("Küçük harf, rakam, _ veya -; başka bir makineyle aynı olamaz.")}</div>}
      <Text label={tx("Ad")} value={d.name} onChange={(v) => set({ name: v })} />
      <div className="grid2 tight">
        <Text label={tx("Üretici")} value={d.manufacturer} onChange={(v) => set({ manufacturer: v })} />
        <Text label="Model" value={d.machine_model} onChange={(v) => set({ machine_model: v })} />
        <Text label={tx("Seri no")} value={d.serial} onChange={(v) => set({ serial: v })} />
      </div>
      <label className="field wide">
        <span>{tx("Açıklama")}</span>
        <textarea rows={2} value={d.description} onChange={(e) => set({ description: e.target.value })} />
      </label>

      <div className="sub-h">{tx("Kontrol ünitesi")}</div>
      <div className="grid2 tight">
        <label className="field wide">
          <span>{tx("Kontrol")}</span>
          <select className="select" value={d.controller} onChange={(e) => set({ controller: e.target.value })}>
            {CONTROLLERS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="field wide">
          <span>Post</span>
          <select className="select" value={d.post} onChange={(e) => set({ post: e.target.value })}>
            {CONTROLLERS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="check small">
        <input type="checkbox" checked={d.canned_cycles} onChange={(e) => set({ canned_cycles: e.target.checked })} />  {tx("Delme döngüleri (G81/G83)")} </label>
      <label className="check small">
        <input type="checkbox" checked={d.program_number} onChange={(e) => set({ program_number: e.target.checked })} />  {tx("Program numarası (O....)")} </label>

      <div className="sub-h">{tx("Eksen kursları (makine koordinatı, mm)")}</div>
      {(["X", "Y", "Z"] as const).map((ax) => (
        <div key={ax} className="grid2 tight">
          <Num label={`${ax} min`} value={d.travel[ax].min} onChange={(v) => setTravel(ax, "min", v ?? 0)} />
          <Num label={`${ax} max`} value={d.travel[ax].max} onChange={(v) => setTravel(ax, "max", v ?? 0)} />
        </div>
      ))}
      <div className="grid3">
        {(["X", "Y", "Z"] as const).map((ax, i) => (
          <Num
            key={ax}
            label={tx("Ofset {0}", {"0": ax})}
            unit="mm"
            value={d.default_work_offset[i]}
            onChange={(v) => {
              const o = [...d.default_work_offset] as [number, number, number];
              o[i] = v ?? 0;
              set({ default_work_offset: o });
            }}
            title={tx("Varsayılan iş sıfırı (makine koordinatı)")}
          />
        ))}
      </div>

      <div className="sub-h">{tx("İş mili ve ilerleme")}</div>
      <div className="grid2 tight">
        <Num label={tx("Min devir")} unit={tx("dev/dk")} value={d.min_spindle} onChange={(v) => set({ min_spindle: v ?? 0 })} />
        <Num label={tx("Maks devir")} unit={tx("dev/dk")} value={d.max_spindle} onChange={(v) => set({ max_spindle: v ?? 0 })} />
        <Num label={tx("Güç")} unit="kW" nullable value={d.spindle_power_kw} onChange={(v) => set({ spindle_power_kw: v })} />
        <Num label={tx("Maks ilerleme")} unit={tx("mm/dk")} value={d.max_feed} onChange={(v) => set({ max_feed: v ?? 0 })} />
        <Num label={tx("Hızlı (G0)")} unit={tx("mm/dk")} value={d.rapid_feed} onChange={(v) => set({ rapid_feed: v ?? 0 })} />
      </div>
      <div className="grid3">
        {(["X", "Y", "Z"] as const).map((ax) => (
          <Num key={ax} label={tx("İvme {0}", {"0": ax})} unit="mm/s²" value={accel[ax] ?? 0} onChange={(v) => set({ accel: { ...accel, [ax]: v ?? 0 } })} />
        ))}
      </div>

      <div className="sub-h">{tx("Takım")}</div>
      <label className="check small">
        <input type="checkbox" checked={d.tool_changer} onChange={(e) => set({ tool_changer: e.target.checked })} />  {tx("Otomatik takım değiştirici (M6)")} </label>
      <label className="check small">
        <input type="checkbox" checked={d.requires_tool_length_comp} onChange={(e) => set({ requires_tool_length_comp: e.target.checked })} />  {tx("Takım boyu telafisi (G43 H)")} </label>
      <div className="grid2 tight">
        <Num label={tx("Magazin")} unit={tx("cep")} step="1" value={d.max_tools} onChange={(v) => set({ max_tools: Math.max(1, Math.round(v ?? 1)) })} />
        <Num label={tx("Değişim süresi")} unit={tx("sn")} value={d.tool_change_time_s} onChange={(v) => set({ tool_change_time_s: v ?? 0 })} />
        <Num label={tx("Maks takım Ø")} unit="mm" nullable value={d.max_tool_diameter} onChange={(v) => set({ max_tool_diameter: v })} />
        <Num label={tx("Maks takım boyu")} unit="mm" nullable value={d.max_tool_length} onChange={(v) => set({ max_tool_length: v })} />
      </div>

      <div className="sub-h">{tx("Soğutma modları")}</div>
      {(["flood", "mist", "air"] as const).map((k) => {
        const on = k in d.coolant_modes;
        return (
          <div key={k} className="coolant-row small">
            <label className="check">
              <input
                type="checkbox"
                checked={on}
                onChange={(e) => {
                  const m = { ...d.coolant_modes };
                  if (e.target.checked) m[k] = k === "flood" ? "M8" : k === "mist" ? "M7" : "M7";
                  else delete m[k];
                  set({ coolant_modes: m });
                }}
              />{" "}
              {COOLANT_TR()[k]}
            </label>
            <input className="mcode mono" disabled={!on} value={d.coolant_modes[k] ?? ""} onChange={(e) => set({ coolant_modes: { ...d.coolant_modes, [k]: e.target.value.toUpperCase() } })} />
          </div>
        );
      })}

      <div className="sub-h">{tx("İş koordinat sistemleri")}</div>
      <div className="wcs-row">
        {[54, 55, 56, 57, 58, 59].map((w) => (
          <label key={w} className="check small mono">
            <input
              type="checkbox"
              checked={d.work_offsets.includes(w)}
              onChange={(e) => set({ work_offsets: e.target.checked ? [...d.work_offsets, w].sort() : d.work_offsets.filter((x) => x !== w) })}
            />
            G{w}
          </label>
        ))}
      </div>

      <div className="sub-h">{tx("Post seçenekleri")}</div>
      <label className="check small">
        <input type="checkbox" checked={d.post_options.line_numbers} onChange={(e) => setPo({ line_numbers: e.target.checked })} />  {tx("Satır numarası (N)")} </label>
      {d.post_options.line_numbers && <Num label={tx("Artış")} step="1" value={d.post_options.line_increment} onChange={(v) => setPo({ line_increment: Math.max(1, Math.round(v ?? 10)) })} />}
      <label className="check small">
        <input type="checkbox" checked={d.post_options.comments} onChange={(e) => setPo({ comments: e.target.checked })} />  {tx("Yorum satırları")} </label>
      <Row label={tx("Yay biçimi")}>
        <Seg
          value={d.post_options.arc_format}
          options={[
            ["ijk", "I / J"],
            ["r", "R"],
          ]}
          onChange={(v) => setPo({ arc_format: v })}
        />
      </Row>
      <Row label={tx("Program sonu")}>
        <Seg
          value={d.post_options.home_at_end}
          options={[
            ["none", tx("Park yok")],
            ["G28", "G28"],
            ["G53", "G53 Z0"],
          ]}
          onChange={(v) => setPo({ home_at_end: v })}
        />
      </Row>
    </div>
  );
}

function MachinePane({
  profiles,
  activeId,
  onProfiles,
  onActivate,
  onError,
}: {
  profiles: MachineProfile[];
  activeId: string;
  onProfiles: (profiles: MachineProfile[], cfg: MachiningConfig, msg: string) => void;
  onActivate: (id: string) => void;
  onError: (m: string) => void;
}) {
  useLocale();
  const [sel, setSel] = useState(activeId);
  const [draft, setDraft] = useState<MachineProfile | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => setConfirm(false), [sel]);
  const p = profiles.find((x) => x.id === sel) ?? profiles[0];
  if (!p) return null;
  const mine = profiles.filter((x) => !x.builtin);
  const lib = profiles.filter((x) => x.builtin);

  const copy = () => {
    let id = `${p.id.replace(/_kopya\d*$/, "")}_kopya`;
    for (let k = 2; profiles.some((x) => x.id === id); k++) id = `${p.id.replace(/_kopya\d*$/, "")}_kopya${k}`;
    setDraft({ ...structuredClone(p), id, name: tx("{0} (kopya)", {"0": p.name.replace(/ \(örnek\)$/, "")}), builtin: false });
    setIsNew(true);
  };
  const save = async (activate: boolean) => {
    if (!draft) return;
    try {
      const res = await api.saveMachine(draft, activate);
      onProfiles(res.profiles, res.config, tx("{0} kaydedildi{1}.", {"0": res.profile.name, "1": activate ? tx(" ve etkinleştirildi") : ""}));
      setSel(res.profile.id);
      setDraft(null);
      setIsNew(false);
    } catch (e) {
      onError((e as Error).message);
    }
  };
  const del = async () => {
    try {
      const res = await api.deleteMachine(p.id);
      onProfiles(res.profiles, res.config, tx("{0} silindi.", {"0": p.name}));
      setSel(res.config.profile_id);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const Item = ({ x }: { x: MachineProfile }) => (
    <li>
      <button className={`m-item ${x.id === sel && !draft ? "on" : ""}`} onClick={() => (setSel(x.id), setDraft(null))}>
        <span className="m-name">{profileLabel(x)}</span>
        <span className="m-meta mono">
          {x.controller}
          {x.id === activeId ? tx(" · aktif") : ""}
        </span>
      </button>
    </li>
  );

  return (
    <>
      <section className="card">
        <h4>{tx("Makineler")}</h4>
        <div className="sub-h">{tx("Makinelerim")}</div>
        {mine.length === 0 ? <div className="muted small">{tx("Henüz yok. Hazır bir makineyi kopyalayıp kendi tezgâhınıza göre düzenleyin.")}</div> : <ul className="m-list">{mine.map((x) => <Item key={x.id} x={x} />)}</ul>}
        <div className="sub-h">{tx("Hazır kütüphane (salt okunur)")}</div>
        <ul className="m-list">
          {lib.map((x) => (
            <Item key={x.id} x={x} />
          ))}
        </ul>
      </section>
      <section className="card">
        {draft ? (
          <>
            <h4>{isNew ? tx("Yeni makine") : tx("Düzenle: {0}", {"0": p.name})}</h4>
            <MachineForm d={draft} setD={setDraft} isNew={isNew} ids={profiles.map((x) => x.id)} />
            <div className="btn-row sticky-actions">
              <button className="btn primary" onClick={() => save(false)}>
                 {tx("Kaydet")} </button>
              {draft.id !== activeId && (
                <button className="btn" onClick={() => save(true)}>
                   {tx("Kaydet ve kullan")} </button>
              )}
              <button className="btn ghost" onClick={() => (setDraft(null), setIsNew(false))}>
                 {tx("Vazgeç")} </button>
            </div>
          </>
        ) : (
          <>
            <h4>
              <span>{profileLabel(p)}</span>
              <span className={`chip ${p.builtin ? "" : "mine"}`}>{p.builtin ? tx("hazır") : tx("benim")}</span>
            </h4>
            {p.description && <div className="muted small">{p.builtin ? builtinText(p.description) : p.description}</div>}
            <MachineView p={p} />
            {p.notes.map((t, k) => (
              <div key={k} className="muted tiny">
                • {p.builtin ? builtinText(t) : t}
              </div>
            ))}
            <div className="btn-row">
              {p.id !== activeId && (
                <button className="btn primary" onClick={() => onActivate(p.id)}>
                   {tx("Bu makineyi kullan")} </button>
              )}
              {!p.builtin && (
                <button className="btn" onClick={() => (setDraft(structuredClone(p)), setIsNew(false))}>
                   {tx("Düzenle")} </button>
              )}
              <button className="btn" onClick={copy}>
                 {tx("Kopyala")}{p.builtin ? tx(" ve düzenle") : ""}
              </button>
              {!p.builtin &&
                (confirm ? (
                  <>
                    <button className="btn danger-ghost" onClick={del}>
                       {tx("Silinsin")} </button>
                    <button className="btn ghost" onClick={() => setConfirm(false)}>
                       {tx("Vazgeç")} </button>
                  </>
                ) : (
                  <button className="btn danger-ghost" onClick={() => setConfirm(true)}>
                     {tx("Sil")} </button>
                ))}
            </div>
          </>
        )}
      </section>
    </>
  );
}

/* ------------------------------------------------------------------ ana */
export default function MachiningSettings({
  config,
  profiles,
  model,
  gcode,
  sub,
  setSub,
  onSaved,
  onProfiles,
  onActivate,
  onError,
}: {
  config: MachiningConfig | null;
  profiles: MachineProfile[];
  model: ModelSummary | null;
  gcode: GcodeRecord | null;
  sub: SettingsSub;
  setSub: (s: SettingsSub) => void;
  onSaved: (c: MachiningConfig) => void;
  onProfiles: (profiles: MachineProfile[], cfg: MachiningConfig, msg: string) => void;
  onActivate: (id: string) => void;
  onError: (m: string) => void;
}) {
  useLocale();
  const [c, setC] = useState<MachiningConfig | null>(config);
  const [materials, setMaterials] = useState<Material[]>([]);
  useEffect(() => setC(config), [config]);
  useEffect(() => {
    api.materials().then((m) => setMaterials(m.materials), () => {});
  }, []);
  const dirty = useMemo(() => !!c && !!config && JSON.stringify(c) !== JSON.stringify(config), [c, config]);
  if (!c) return null;
  const profile = profiles.find((p) => p.id === c.profile_id);

  const save = async () => {
    const tools = [...c.tools].filter((t) => t.diameter > 0).sort((a, b) => a.number - b.number);
    if (!tools.some((t) => t.number === c.mill_tool && MILLS.includes(t.type))) {
      onError(tx("Varsayılan freze olarak bir parmak freze (düz veya köşe radyuslu) seçin."));
      setSub("tools");
      return;
    }
    try {
      onSaved(await api.putConfig({ ...c, tools }));
    } catch (e) {
      onError((e as Error).message);
    }
  };
  const tabs: [SettingsSub, string][] = [
    ["setup", tx("Kurulum")],
    ["tools", tx("Takımlar")],
    ["ops", tx("Operasyonlar")],
    ["machine", tx("Makine")],
  ];

  return (
    <div className="settings">
      <div className="subtabs" role="tablist">
        {tabs.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={sub === k} className={sub === k ? "on" : ""} onClick={() => setSub(k)}>
            {l}
          </button>
        ))}
      </div>
      <div className="pad stack">
        {sub === "setup" && <SetupPane c={c} setC={setC} profile={profile} materials={materials} model={model} />}
        {sub === "tools" && <ToolsPane c={c} setC={setC} profile={profile} materials={materials} />}
        {sub === "ops" && (
          <>
            <div className="muted small">
               {tx("Her operasyon türü için takım, kesme verisi ve strateji. Öncelik: buradaki elle değer → takım preseti → malzeme tablosu")} {c.cutting_mode === "manual" ? tx(" (şu an elle mod: Kurulum'daki sabit değerler)") : ""}.
            </div>
            {OPS().map((op) => (
              <OpCard key={op.id} op={op} c={c} setC={setC} last={gcode?.source === "generated" ? gcode.plan : []} />
            ))}
          </>
        )}
        {sub === "machine" && <MachinePane profiles={profiles} activeId={c.profile_id} onProfiles={onProfiles} onActivate={onActivate} onError={onError} />}
      </div>
      {sub !== "machine" && dirty && (
        <div className="savebar">
          <span>{tx("Kaydedilmemiş değişiklik")}</span>
          <button className="btn ghost small" onClick={() => setC(config)}>
             {tx("Geri al")} </button>
          <button className="btn primary small" onClick={save}>
             {tx("Kaydet")} </button>
        </div>
      )}
    </div>
  );
}
