import { Canvas, ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { GizmoHelper, GizmoViewport, Grid, OrbitControls } from "@react-three/drei";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { FaceMesh, MeshData, RegionInfo, RegionRequest, RegionShape, RegionTool, Vec3 } from "../types";
import { UNITS, UNIT_ORDER, fmtGridValue, fmtLen, getUnit, setUnit, useUnit, type Unit } from "../units";

const COLORS = {
  base: "#9aa3ad",
  hole: "#b79a5b",
  pocket: "#7f9bb3",
  hover: "#ffb347",
  selected: "#ff7a1a",
  area: "#4cc3ff",
};

function faceColor(f: FaceMesh, hovered: boolean, selected: boolean, area: boolean) {
  if (selected) return COLORS.selected;
  if (hovered) return COLORS.hover;
  if (area) return COLORS.area;
  if (f.hole_id) return COLORS.hole;
  if (f.pocket_id) return COLORS.pocket;
  return COLORS.base;
}


const Face = memo(function Face({
  face,
  hovered,
  selected,
  area,
  onHover,
  onSelect,
}: {
  face: FaceMesh;
  hovered: boolean;
  selected: boolean;
  area: boolean;
  onHover: (id: string | null) => void;
  onSelect: (id: string, add: boolean) => void;
}) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(face.vertices, 3));
    g.setIndex(face.indices);
    g.computeVertexNormals();
    return g;
  }, [face]);
  useEffect(() => () => geom.dispose(), [geom]);
  const downAt = useRef<[number, number] | null>(null);

  return (
    <mesh
      geometry={geom}
      userData={{ faceId: face.id }}
      onPointerOver={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        onHover(face.id);
      }}
      onPointerOut={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        onHover(null);
      }}
      onPointerDown={(e: ThreeEvent<PointerEvent>) => {
        downAt.current = [e.clientX, e.clientY];
      }}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        e.stopPropagation();
        // döndürme sürüklemesini tıklama sayma
        const d = downAt.current;
        if (d && Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 4) return;
        onSelect(face.id, e.shiftKey); // ⇧: seçime ekle / çıkar
      }}
    >
      <meshStandardMaterial
        color={faceColor(face, hovered, selected, area)}
        metalness={0.35}
        roughness={0.55}
        side={THREE.DoubleSide}
        polygonOffset
        polygonOffsetFactor={1}
        polygonOffsetUnits={1}
      />
    </mesh>
  );
});

function Edges({ edges }: { edges: number[][] }) {
  const geom = useMemo(() => {
    const pos: number[] = [];
    for (const e of edges) {
      for (let i = 0; i + 5 < e.length; i += 3) {
        pos.push(e[i], e[i + 1], e[i + 2], e[i + 3], e[i + 4], e[i + 5]);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    return g;
  }, [edges]);
  return (
    <lineSegments geometry={geom}>
      <lineBasicMaterial color="#1b1f24" />
    </lineSegments>
  );
}

/** Model değiştiğinde kamerayı parçaya sığdırır. */
export function FitCamera({ size, center, fitKey }: { size: number; center: THREE.Vector3; fitKey: string }) {
  const { camera, controls } = useThree();
  useEffect(() => {
    const d = Math.max(size, 20) * 1.6;
    camera.position.set(center.x + d * 0.8, center.y - d, center.z + d * 0.75);
    camera.near = d / 200;
    camera.far = d * 50;
    camera.updateProjectionMatrix();
    const c = controls as unknown as { target: THREE.Vector3; update: () => void } | null;
    if (c) {
      c.target.copy(center);
      c.update();
    } else {
      camera.lookAt(center);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, controls]);
  return null;
}

/* ------------------------------------------------------------------ ölçü etiketleri */

// Etiket dokuları metne göre önbelleğe alınır (en fazla 200; taşanlar GPU'dan bırakılır)
const labelCache = new Map<string, { tex: THREE.CanvasTexture; aspect: number }>();
function labelTexture(text: string, color: string) {
  const key = `${color}|${text}`;
  const hit = labelCache.get(key);
  if (hit) {
    labelCache.delete(key);
    labelCache.set(key, hit);
    return hit;
  }
  const fs = 40;
  const font = `500 ${fs}px "IBM Plex Mono", ui-monospace, monospace`;
  const cv = document.createElement("canvas");
  const ctx = cv.getContext("2d")!;
  ctx.font = font;
  cv.width = Math.ceil(ctx.measureText(text).width) + 22;
  cv.height = fs + 16;
  ctx.font = font;
  ctx.fillStyle = "rgba(14,17,22,0.9)";
  const r = 10;
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.arcTo(cv.width, 0, cv.width, cv.height, r);
  ctx.arcTo(cv.width, cv.height, 0, cv.height, r);
  ctx.arcTo(0, cv.height, 0, 0, r);
  ctx.arcTo(0, 0, cv.width, 0, r);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, cv.width / 2, cv.height / 2 + 2);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  const v = { tex, aspect: cv.width / cv.height };
  labelCache.set(key, v);
  if (labelCache.size > 200) {
    const [oldKey, old] = labelCache.entries().next().value as [string, { tex: THREE.CanvasTexture }];
    labelCache.delete(oldKey);
    old.tex.dispose();
  }
  return v;
}

/** Ekranda sabit piksel boyutunda duran metin etiketi (yakınlaştırınca büyümez). */
function Label3D({
  text,
  position,
  color = "#aab4bf",
  px = 18,
  onTop = false,
}: {
  text: string;
  position: [number, number, number];
  color?: string;
  px?: number;
  onTop?: boolean;
}) {
  const { tex, aspect } = useMemo(() => labelTexture(text, color), [text, color]);
  const { size, camera } = useThree();
  const fov = (camera as THREE.PerspectiveCamera).fov ?? 40;
  // sizeAttenuation=false iken ölçek, görüş yüksekliğinin 2·tan(fov/2) katı cinsindendir
  const sy = (px / Math.max(size.height, 1)) * 2 * Math.tan(THREE.MathUtils.degToRad(fov / 2));
  return (
    <sprite position={position} scale={[sy * aspect, sy, 1]} renderOrder={onTop ? 20 : 1}>
      <spriteMaterial map={tex} sizeAttenuation={false} transparent depthWrite={false} depthTest={!onTop} />
    </sprite>
  );
}

/* ------------------------------------------------------------------ uyarlanır ölçülü ızgara */

/* Izgara aralıkları seçili görünüm birimine göre (units.ts) seçilir; sahne her zaman mm'dir. */
export type GridInfo = { section: number; cell: number }; // mm
const ORIGIN = new THREE.Vector3();
const N_LABELS = 8;

function targetOf(controls: unknown) {
  return (controls as { target?: THREE.Vector3 } | null)?.target ?? ORIGIN;
}

/** Ana ızgara çizgilerinin üstüne X / Y koordinatlarını (seçili birimde) yazar. */
function GridLabels({ section }: { section: number }) {
  const { controls } = useThree();
  const unit = useUnit();
  const [k, setK] = useState<[number, number]>([0, 0]);
  const kRef = useRef(k);
  useFrame(() => {
    const t = targetOf(controls);
    const kx = Math.round(t.x / section);
    const ky = Math.round(t.y / section);
    if (kx !== kRef.current[0] || ky !== kRef.current[1]) {
      kRef.current = [kx, ky];
      setK([kx, ky]);
    }
  });
  const [kx, ky] = k;
  // Orijin görüş alanındaysa etiketler X=0 / Y=0 eksenleri boyunca, değilse görüş merkezinden geçen hat boyunca
  const ax = Math.abs(kx) <= N_LABELS ? 0 : kx;
  const ay = Math.abs(ky) <= N_LABELS ? 0 : ky;
  const out: JSX.Element[] = [];
  for (let i = kx - N_LABELS; i <= kx + N_LABELS; i++) {
    out.push(<Label3D key={`x${i}`} text={fmtGridValue(i * section, unit)} position={[i * section, ay * section, 0]} color="#f2a39e" />);
  }
  for (let j = ky - N_LABELS; j <= ky + N_LABELS; j++) {
    if (j === ay) continue; // kesişimde X etiketi zaten var
    out.push(<Label3D key={`y${j}`} text={fmtGridValue(j * section, unit)} position={[ax * section, j * section, 0]} color="#9fd8a1" />);
  }
  const lbl = UNITS[unit].label;
  out.push(<Label3D key="xt" text={`X ${lbl}`} position={[(kx + N_LABELS + 1) * section, ay * section, 0]} color="#e5534b" />);
  out.push(<Label3D key="yt" text={`Y ${lbl}`} position={[ax * section, (ky + N_LABELS + 1) * section, 0]} color="#57ab5a" />);
  return <>{out}</>;
}

function AdaptiveGrid({ gridSize, onGrid }: { gridSize: number; onGrid?: (g: GridInfo) => void }) {
  const { camera, controls } = useThree();
  const unit = useUnit();
  const def = UNITS[unit];
  const steps = def.steps;
  const [idx, setIdx] = useState(() => steps.findIndex(([s]) => s * def.mm >= 25));
  const idxRef = useRef(idx);
  idxRef.current = Math.min(idx, steps.length - 1);
  const unitRef = useRef(unit);
  useFrame(() => {
    const d = camera.position.distanceTo(targetOf(controls));
    const fov = (camera as THREE.PerspectiveCamera).fov ?? 40;
    const h = (2 * d * Math.tan(THREE.MathUtils.degToRad(fov / 2))) / def.mm; // görüş yüksekliği (seçili birimde)
    const fresh = unitRef.current !== unit; // birim değişti: gecikmesiz yeniden seç
    unitRef.current = unit;
    const n = h / steps[idxRef.current][0];
    if (!fresh && n >= 2.5 && n <= 12) return; // titremeyi önlemek için gecikmeli geçiş
    let next = steps.findIndex(([s]) => h / s <= 7);
    if (next < 0) next = steps.length - 1;
    if (next !== idxRef.current) {
      idxRef.current = next;
      setIdx(next);
    }
  });
  const [section, cell] = steps[idxRef.current].map((v) => v * def.mm);
  useEffect(() => onGrid?.({ section, cell }), [section, cell, onGrid]);
  return (
    <>
      <Grid
        rotation={[Math.PI / 2, 0, 0]}
        args={[gridSize, gridSize]}
        cellSize={cell}
        sectionSize={section}
        cellColor="#2f3640"
        sectionColor="#4a5563"
        fadeDistance={Math.max(gridSize * 2, section * 60)}
        infiniteGrid
      />
      <GridLabels section={section} />
    </>
  );
}

/**
 * Izgaranın o anki aralıkları + ölçü birimi seçici (Canvas dışında kullanılır).
 * Birim yalnızca görünümü etkiler; model, sohbet ve G-code mm ile çalışmaya devam eder.
 */
export function GridLegend({ grid }: { grid: GridInfo | null }) {
  const unit = useUnit();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("pointerdown", close, true);
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("pointerdown", close, true);
      window.removeEventListener("keydown", esc);
    };
  }, [open]);
  if (!grid) return null;
  const pick = (u: Unit) => {
    setUnit(u);
    setOpen(false);
  };
  return (
    <div className="grid-legend mono" ref={ref}>
      <span className="gl-swatch major" /> ana çizgi {fmtGridValue(grid.section, unit)}
      <span className="gl-swatch minor" /> hücre {fmtGridValue(grid.cell, unit)}
      <button className={`gl-unit ${open ? "on" : ""}`} title="Ölçü birimini değiştir" onClick={() => setOpen((o) => !o)}>
        {UNITS[unit].label} <span className="gl-caret">▾</span>
      </button>
      {open && (
        <div className="unit-pop" role="menu">
          <div className="unit-seg">
            {UNIT_ORDER.map((u) => (
              <button key={u} className={u === unit ? "on" : ""} onClick={() => pick(u)} role="menuitemradio" aria-checked={u === unit}>
                {UNITS[u].label}
              </button>
            ))}
          </div>
          <div className="unit-name">{UNITS[unit].name}</div>
          <div className="unit-note">Izgara ve 3B görünümdeki ölçüler bu birimle gösterilir. Model, sohbet ve G‑code mm ile çalışır.</div>
        </div>
      )}
    </div>
  );
}

export function SceneBase({ gridSize, onGrid }: { gridSize: number; onGrid?: (g: GridInfo) => void }) {
  return (
    <>
      <ambientLight intensity={0.55} />
      <directionalLight position={[60, -80, 120]} intensity={1.4} />
      <directionalLight position={[-80, 60, 40]} intensity={0.5} />
      <hemisphereLight args={["#dfe7ef", "#2a2f36", 0.35]} />
      <AdaptiveGrid gridSize={gridSize} onGrid={onGrid} />
      <OrbitControls makeDefault enableDamping dampingFactor={0.12} />
      <GizmoHelper alignment="bottom-right" margin={[64, 64]}>
        <GizmoViewport axisColors={["#e5534b", "#57ab5a", "#539bf5"]} labelColor="#0e1116" />
      </GizmoHelper>
    </>
  );
}

/* ------------------------------------------------------------------ ⌘/Ctrl + "Yüzeyler" aracı: pencere / kesişim seçimi */

type Rect = { x0: number; y0: number; x1: number; y1: number };
type Bridge = { camera: THREE.Camera; controls: unknown };
interface Projected {
  face: FaceMesh;
  s: Float32Array; // ekran koordinatları (px), kamera arkasındaki nokta NaN
  ok: boolean; // tüm noktalar kameranın önünde mi
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** Canvas içindeki kamerayı/kontrolleri dışarıdaki seçim mantığına aktarır. */
function CameraBridge({ bridge }: { bridge: React.MutableRefObject<Bridge | null> }) {
  const { camera, controls } = useThree();
  bridge.current = { camera, controls };
  return null;
}

function projectFaces(mesh: MeshData, camera: THREE.Camera, w: number, h: number): Projected[] {
  camera.updateMatrixWorld();
  const m = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).elements;
  return mesh.faces.map((face) => {
    const v = face.vertices;
    const n = v.length / 3;
    const s = new Float32Array(n * 2);
    let ok = true, minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (let i = 0; i < n; i++) {
      const x = v[i * 3], y = v[i * 3 + 1], z = v[i * 3 + 2];
      const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
      if (cw <= 1e-6) {
        ok = false;
        s[i * 2] = s[i * 2 + 1] = NaN;
        continue;
      }
      const sx = (((m[0] * x + m[4] * y + m[8] * z + m[12]) / cw + 1) / 2) * w;
      const sy = ((1 - (m[1] * x + m[5] * y + m[9] * z + m[13]) / cw) / 2) * h;
      s[i * 2] = sx;
      s[i * 2 + 1] = sy;
      if (sx < minX) minX = sx;
      if (sx > maxX) maxX = sx;
      if (sy < minY) minY = sy;
      if (sy > maxY) maxY = sy;
    }
    return { face, s, ok, minX, minY, maxX, maxY };
  });
}

/** Liang–Barsky: [a,b] doğru parçası dikdörtgene değiyor mu */
function segHitsRect(ax: number, ay: number, bx: number, by: number, L: number, T: number, R: number, B: number) {
  let t0 = 0, t1 = 1;
  const dx = bx - ax, dy = by - ay;
  const p = [-dx, dx, -dy, dy];
  const q = [ax - L, R - ax, ay - T, B - ay];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return false;
    } else {
      const t = q[i] / p[i];
      if (p[i] < 0) {
        if (t > t1) return false;
        if (t > t0) t0 = t;
      } else {
        if (t < t0) return false;
        if (t < t1) t1 = t;
      }
    }
  }
  return true;
}

function inTri(px: number, py: number, ax: number, ay: number, bx: number, by: number, cx: number, cy: number) {
  const d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by);
  const d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy);
  const d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay);
  const neg = d1 < 0 || d2 < 0 || d3 < 0;
  const pos = d1 > 0 || d2 > 0 || d3 > 0;
  return !(neg && pos);
}

/** Kesişim modu: yüzeyin herhangi bir üçgeni dikdörtgene değiyor mu */
function faceTouches(p: Projected, L: number, T: number, R: number, B: number) {
  const s = p.s;
  for (let i = 0; i < s.length; i += 2) {
    const x = s[i], y = s[i + 1];
    if (x >= L && x <= R && y >= T && y <= B) return true;
  }
  const idx = p.face.indices;
  const corners = [L, T, R, T, R, B, L, B];
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const ax = s[idx[t] * 2], ay = s[idx[t] * 2 + 1];
    const bx = s[idx[t + 1] * 2], by = s[idx[t + 1] * 2 + 1];
    const cx = s[idx[t + 2] * 2], cy = s[idx[t + 2] * 2 + 1];
    if (Number.isNaN(ax) || Number.isNaN(bx) || Number.isNaN(cx)) continue;
    if (Math.max(ax, bx, cx) < L || Math.min(ax, bx, cx) > R || Math.max(ay, by, cy) < T || Math.min(ay, by, cy) > B) continue;
    for (let c = 0; c < 8; c += 2) if (inTri(corners[c], corners[c + 1], ax, ay, bx, by, cx, cy)) return true;
    if (segHitsRect(ax, ay, bx, by, L, T, R, B) || segHitsRect(bx, by, cx, cy, L, T, R, B) || segHitsRect(cx, cy, ax, ay, L, T, R, B))
      return true;
  }
  return false;
}

/**
 * CAD alışkanlığı: soldan sağa sürükleme = pencere (yalnız tamamen içerideki yüzeyler),
 * sağdan sola = kesişim (dikdörtgene değen tüm yüzeyler).
 */
function pickFaces(proj: Projected[], r: Rect, crossing: boolean) {
  const L = Math.min(r.x0, r.x1), R = Math.max(r.x0, r.x1);
  const T = Math.min(r.y0, r.y1), B = Math.max(r.y0, r.y1);
  const ids: string[] = [];
  for (const p of proj) {
    if (p.maxX < L || p.minX > R || p.maxY < T || p.minY > B) continue;
    if (crossing ? faceTouches(p, L, T, R, B) : p.ok && p.minX >= L && p.maxX <= R && p.minY >= T && p.maxY <= B) ids.push(p.face.id);
  }
  return ids;
}

function bboxOf(mesh: MeshData, ids: Set<string>) {
  if (!ids.size) return null;
  const b = new THREE.Box3();
  const v3 = new THREE.Vector3();
  for (const f of mesh.faces) {
    if (!ids.has(f.id)) continue;
    const v = f.vertices;
    for (let i = 0; i < v.length; i += 3) b.expandByPoint(v3.set(v[i], v[i + 1], v[i + 2]));
  }
  return b.isEmpty() ? null : b;
}

/** Dönüş merkezi derinliğinde 1 pikselin kaç mm'ye karşılık geldiği */
function mmPerPx(br: Bridge, h: number) {
  const cam = br.camera as THREE.PerspectiveCamera;
  const dir = cam.getWorldDirection(new THREE.Vector3());
  const d = targetOf(br.controls).clone().sub(cam.position).dot(dir);
  return (2 * Math.max(d, 1e-3) * Math.tan(THREE.MathUtils.degToRad((cam.fov ?? 40) / 2))) / Math.max(h, 1);
}

/** Seçilen geometrinin sınır kutusu + kenarlarında X/Y/Z ölçüleri */
function AreaBox({ box }: { box: THREE.Box3 }) {
  const helper = useMemo(() => {
    const h = new THREE.Box3Helper(box, new THREE.Color(COLORS.area));
    const mat = h.material as THREE.LineBasicMaterial;
    mat.depthTest = false;
    mat.transparent = true;
    mat.opacity = 0.85;
    h.renderOrder = 19;
    return h;
  }, [box]);
  useEffect(
    () => () => {
      helper.geometry.dispose();
      (helper.material as THREE.Material).dispose();
    },
    [helper],
  );
  const unit = useUnit();
  const s = box.getSize(new THREE.Vector3());
  const c = box.getCenter(new THREE.Vector3());
  const lbl = "#bfe9ff";
  return (
    <>
      <primitive object={helper} />
      {s.x > 1e-3 && <Label3D text={`X ${fmtLen(s.x, unit)}`} position={[c.x, box.min.y, box.min.z]} color={lbl} px={19} onTop />}
      {s.y > 1e-3 && <Label3D text={`Y ${fmtLen(s.y, unit)}`} position={[box.max.x, c.y, box.min.z]} color={lbl} px={19} onTop />}
      {s.z > 1e-3 && <Label3D text={`Z ${fmtLen(s.z, unit)}`} position={[box.max.x, box.min.y, c.z]} color={lbl} px={19} onTop />}
    </>
  );
}

type Drag = Rect & { crossing: boolean; w: number; h: number; vw: number; vh: number };

function SelectionOverlay({ drag, ids, box, mesh }: { drag: Drag; ids: Set<string>; box: THREE.Box3 | null; mesh: MeshData }) {
  const unit = useUnit();
  const L = Math.min(drag.x0, drag.x1), T = Math.min(drag.y0, drag.y1);
  const W = Math.abs(drag.x1 - drag.x0), H = Math.abs(drag.y1 - drag.y0);
  const s = box?.getSize(new THREE.Vector3());
  // ipucu kutusu imlecin dikdörtgenin dışında kalan tarafında; ekran kenarına yaklaşınca diğer tarafa geçer
  let flipX = drag.x1 < drag.x0;
  let flipY = drag.y1 < drag.y0;
  if (!flipX && drag.x1 > drag.vw - 250) flipX = true;
  else if (flipX && drag.x1 < 250) flipX = false;
  if (!flipY && drag.y1 > drag.vh - 110) flipY = true;
  else if (flipY && drag.y1 < 110) flipY = false;
  return (
    <>
      <div className={`sel-rect ${drag.crossing ? "crossing" : ""}`} style={{ left: L, top: T, width: W, height: H }}>
        {W > 24 && <span className={`sel-dim w ${T < 26 ? "inside" : ""}`}>↔ ≈ {fmtLen(drag.w, unit)}</span>}
        {H > 18 && <span className={`sel-dim h ${L + W > drag.vw - 100 ? "inside" : ""}`}>↕ ≈ {fmtLen(drag.h, unit)}</span>}
      </div>
      <div
        className="sel-tip mono"
        style={{
          left: drag.x1 + (flipX ? -14 : 14),
          top: drag.y1 + (flipY ? -14 : 14),
          transform: `translate(${flipX ? "-100%" : "0"}, ${flipY ? "-100%" : "0"})`,
        }}
      >
        <div className="sel-tip-mode">{drag.crossing ? "Kesişim seçimi · değenler" : "Pencere seçimi · tamamen içeridekiler"}</div>
        {s ? (
          <>
            <div>
              ΔX <b>{fmtLen(s.x, unit, false)}</b> · ΔY <b>{fmtLen(s.y, unit, false)}</b> · ΔZ <b>{fmtLen(s.z, unit, false)}</b> {UNITS[unit].label}
            </div>
            <div className="muted">
              {ids.size} yüzey{featureSummary(mesh, ids)}
            </div>
          </>
        ) : (
          <div className="muted">Seçimde geometri yok</div>
        )}
      </div>
    </>
  );
}

function featureSummary(mesh: MeshData, ids: Set<string>) {
  const holes = new Set<string>(), pockets = new Set<string>();
  for (const f of mesh.faces) {
    if (!ids.has(f.id)) continue;
    if (f.hole_id) holes.add(f.hole_id);
    if (f.pocket_id) pockets.add(f.pocket_id);
  }
  return `${holes.size ? ` · ${holes.size} delik` : ""}${pockets.size ? ` · ${pockets.size} cep` : ""}`;
}

/* ------------------------------------------------------------------ ⌘/Ctrl + çizim: yüzey üzerinde bölge */

const ICON_PROPS = { width: 15, height: 15, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinejoin: "round" as const, strokeLinecap: "round" as const };
const ICONS: Record<RegionTool, JSX.Element> = {
  rect: <svg {...ICON_PROPS}><rect x="2" y="3.5" width="12" height="9" rx="0.5" /></svg>,
  circle: <svg {...ICON_PROPS}><circle cx="8" cy="8" r="5.5" /></svg>,
  lasso: <svg {...ICON_PROPS}><path d="M3 9.5C1.5 6 5 2.5 9 3s5.5 3.5 3.5 6S7 12.5 5.5 11 4 13.5 6 14.5" /></svg>,
  polygon: <svg {...ICON_PROPS}><path d="M8 2l5.5 4-2 7h-7l-2-7z" /><circle cx="8" cy="2" r="0.6" fill="currentColor" /></svg>,
  faces: <svg {...ICON_PROPS}><rect x="2" y="2" width="8" height="8" strokeDasharray="2 1.6" /><rect x="6" y="6" width="8" height="8" /></svg>,
};
const TOOLS: { id: RegionTool; label: string; hint: string }[] = [
  { id: "rect", label: "Dikdörtgen", hint: "Yüzeyin üzerinde sürükleyin" },
  { id: "circle", label: "Daire", hint: "Merkezden dışa doğru sürükleyin" },
  { id: "lasso", label: "Serbest", hint: "Basılı tutup şekli çizin" },
  { id: "polygon", label: "Çokgen", hint: "Tıklayarak köşe ekleyin · ⌘/Ctrl bırak, çift tık veya Enter: kapat · ⌫ son köşe · Esc iptal" },
  { id: "faces", label: "Yüzeyler", hint: "Bütün yüzeyleri seç · → pencere, ← kesişim · ⇧ ile ekle" },
];
const TOOL_KEY = "busproje.regionTool";

function loadTool(): RegionTool {
  try {
    const t = localStorage.getItem(TOOL_KEY);
    if (t && TOOLS.some((x) => x.id === t)) return t as RegionTool;
  } catch {
    /* depolama kullanılamıyor */
  }
  return "rect";
}
function saveTool(t: RegionTool) {
  try {
    localStorage.setItem(TOOL_KEY, t);
  } catch {
    /* yok say */
  }
}

function RegionToolbar({ tool, onTool, note }: { tool: RegionTool; onTool: (t: RegionTool) => void; note: string | null }) {
  const cur = TOOLS.find((t) => t.id === tool)!;
  return (
    <div className="region-toolbar">
      <div className="rt-tools">
        {TOOLS.map((t) => (
          <button key={t.id} className={`rt-btn ${t.id === tool ? "on" : ""} ${t.id === "faces" ? "sep" : ""}`} title={t.hint} onClick={() => onTool(t.id)}>
            <span className="rt-ico">{ICONS[t.id]}</span>
            {t.label}
          </button>
        ))}
      </div>
      <div className={`rt-hint ${note ? "warn" : ""}`}>{note ?? cur.hint}</div>
    </div>
  );
}

type P2 = [number, number];
type Anchor = { faceId: string; surface: string; point: THREE.Vector3; planar: boolean; plane: THREE.Plane; u: THREE.Vector3; v: THREE.Vector3 };
type Draw = {
  tool: RegionShape;
  anchor: Anchor;
  screen: P2[]; // tıklanan / çizilen ekran noktaları (dikdörtgen, daire: yalnızca başlangıç)
  world: THREE.Vector3[]; // düzlemsel yüzeyde aynı noktaların yüzey düzlemindeki karşılıkları
  cur: P2;
  curWorld: THREE.Vector3 | null;
  mm: number; // 1 px ≈ kaç mm (eğri yüzeyde yaklaşık ölçü için)
  lastDown: number;
};
type DrawView = { pts: P2[]; closed: boolean; rubber: [P2, P2] | null; first: P2 | null; dots: boolean; label: string; sub: string; tip: P2; vw: number; vh: number; pending?: boolean };

/** Düzlem eksenleri (u, v): backend'deki plane_frame / add_pocket kuralı ile aynı */
function planeFrame(n: THREE.Vector3) {
  const u = Math.abs(Math.abs(n.z) - 1) < 1e-6 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1).cross(n).normalize();
  return { u, v: n.clone().cross(u).normalize() };
}

const vec3 = (v: THREE.Vector3): Vec3 => [v.x, v.y, v.z];

/** Douglas–Peucker (açık çoklu çizgi) */
function rdp(pts: P2[], tol: number): P2[] {
  if (pts.length < 3) return pts;
  const [ax, ay] = pts[0], [bx, by] = pts[pts.length - 1];
  const dx = bx - ax, dy = by - ay;
  const L = Math.hypot(dx, dy) || 1e-12;
  let imax = 0, dmax = 0;
  for (let i = 1; i < pts.length - 1; i++) {
    const d = L > 1e-9 ? Math.abs(dy * (pts[i][0] - ax) - dx * (pts[i][1] - ay)) / L : Math.hypot(pts[i][0] - ax, pts[i][1] - ay);
    if (d > dmax) [imax, dmax] = [i, d];
  }
  if (dmax <= tol) return [pts[0], pts[pts.length - 1]];
  return [...rdp(pts.slice(0, imax + 1), tol).slice(0, -1), ...rdp(pts.slice(imax), tol)];
}

/** Düzlemsel yüzeyde çizilen şeklin köşeleri (dünya koordinatı, yüzey düzleminde) */
function planarShape(d: Draw): THREE.Vector3[] {
  const a = d.anchor, p0 = a.point;
  if (d.tool === "rect" || d.tool === "circle") {
    if (!d.curWorld) return [];
    const dv = d.curWorld.clone().sub(p0);
    if (d.tool === "rect") {
      const du = dv.dot(a.u), dw = dv.dot(a.v);
      return [p0.clone(), p0.clone().addScaledVector(a.u, du), p0.clone().addScaledVector(a.u, du).addScaledVector(a.v, dw), p0.clone().addScaledVector(a.v, dw)];
    }
    const r = dv.length();
    return Array.from({ length: 64 }, (_, i) => {
      const t = (i / 64) * Math.PI * 2;
      return p0.clone().addScaledVector(a.u, Math.cos(t) * r).addScaledVector(a.v, Math.sin(t) * r);
    });
  }
  return d.world;
}

/** Eğri yüzeyde ekrandaki şekil (bakış yönünde izdüşürülecek) */
function screenShape(d: Draw): P2[] {
  const s0 = d.screen[0], s1 = d.cur;
  if (d.tool === "rect") return [s0, [s1[0], s0[1]], s1, [s0[0], s1[1]]];
  if (d.tool === "circle") {
    const r = Math.hypot(s1[0] - s0[0], s1[1] - s0[1]);
    return Array.from({ length: 48 }, (_, i) => {
      const t = (i / 48) * Math.PI * 2;
      return [s0[0] + Math.cos(t) * r, s0[1] + Math.sin(t) * r] as P2;
    });
  }
  return d.screen;
}

function DrawOverlay({ v }: { v: DrawView }) {
  const path = v.pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") + (v.closed ? " Z" : "");
  let flipX = v.tip[0] > v.vw - 230;
  let flipY = v.tip[1] > v.vh - 80;
  if (v.tip[0] < 230) flipX = false;
  if (v.tip[1] < 80) flipY = false;
  return (
    <>
      <svg className="draw-svg" width={v.vw} height={v.vh}>
        {v.pts.length > 1 && <path d={path} className={`draw-path ${v.closed ? "closed" : ""}`} />}
        {v.rubber && <line x1={v.rubber[0][0]} y1={v.rubber[0][1]} x2={v.rubber[1][0]} y2={v.rubber[1][1]} className="draw-rubber" />}
        {v.dots && v.pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={3} className="draw-dot" />)}
        {v.first && <circle cx={v.first[0]} cy={v.first[1]} r={7} className="draw-first" />}
      </svg>
      <div
        className="sel-tip mono"
        style={{
          left: v.tip[0] + (flipX ? -14 : 14),
          top: v.tip[1] + (flipY ? -14 : 14),
          transform: `translate(${flipX ? "-100%" : "0"}, ${flipY ? "-100%" : "0"})`,
        }}
      >
        <div className="sel-tip-mode">{v.sub}</div>
        <div>{v.pending ? <span className="muted">Bölge hesaplanıyor…</span> : <b>{v.label}</b>}</div>
      </div>
    </>
  );
}

/** Seçilmiş bölge: yüzeyin hafif önünde yarı saydam katman + sınır çizgisi + kimlik etiketi */
const RegionMesh = memo(function RegionMesh({
  region,
  eps,
  hovered,
  selected,
  onHover,
  onSelect,
}: {
  region: RegionInfo;
  eps: number;
  hovered: boolean;
  selected: boolean;
  onHover: (id: string | null) => void;
  onSelect: (id: string, add: boolean) => void;
}) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(region.mesh.vertices, 3));
    g.setIndex(region.mesh.indices);
    g.computeVertexNormals();
    // yüzeyle aynı yerde titreşmesin ve tıklama önce bölgeye gelsin diye normal yönünde hafifçe öne it
    const p = g.getAttribute("position") as THREE.BufferAttribute;
    const n = g.getAttribute("normal") as THREE.BufferAttribute;
    for (let i = 0; i < p.count; i++) p.setXYZ(i, p.getX(i) + n.getX(i) * eps, p.getY(i) + n.getY(i) * eps, p.getZ(i) + n.getZ(i) * eps);
    p.needsUpdate = true;
    g.computeBoundingSphere();
    return g;
  }, [region, eps]);
  const lines = useMemo(() => {
    const pos: number[] = [];
    for (const e of region.outline) for (let i = 0; i + 5 < e.length; i += 3) pos.push(e[i], e[i + 1], e[i + 2], e[i + 3], e[i + 4], e[i + 5]);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    return g;
  }, [region]);
  useEffect(
    () => () => {
      geom.dispose();
      lines.dispose();
    },
    [geom, lines],
  );
  const downAt = useRef<[number, number] | null>(null);
  const color = selected ? COLORS.selected : hovered ? COLORS.hover : COLORS.area;
  const n = region.normal ?? region.normal_at_center ?? [0, 0, 1];
  const c = region.center;
  return (
    <group>
      <mesh
        geometry={geom}
        renderOrder={5}
        onPointerOver={(e: ThreeEvent<PointerEvent>) => {
          e.stopPropagation();
          onHover(region.region_id);
        }}
        onPointerOut={(e: ThreeEvent<PointerEvent>) => {
          e.stopPropagation();
          onHover(null);
        }}
        onPointerDown={(e: ThreeEvent<PointerEvent>) => {
          downAt.current = [e.clientX, e.clientY];
        }}
        onClick={(e: ThreeEvent<MouseEvent>) => {
          e.stopPropagation();
          const d = downAt.current;
          if (d && Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 4) return;
          onSelect(region.region_id, e.shiftKey);
        }}
      >
        <meshBasicMaterial
          color={color}
          transparent
          opacity={selected ? 0.5 : hovered ? 0.45 : 0.3}
          side={THREE.DoubleSide}
          depthWrite={false}
          polygonOffset
          polygonOffsetFactor={-2}
          polygonOffsetUnits={-2}
        />
      </mesh>
      <lineSegments geometry={lines} renderOrder={6} raycast={() => null}>
        <lineBasicMaterial color={color} />
      </lineSegments>
      <Label3D text={region.region_id} position={[c[0] + n[0] * eps * 4, c[1] + n[1] * eps * 4, c[2] + n[2] * eps * 4]} color={color} px={15} onTop />
    </group>
  );
});

/* ------------------------------------------------------------------ görüntüleyici */

const EMPTY = new Set<string>();

export default function ModelViewer({
  mesh,
  hovered,
  selectedIds,
  regions,
  onHover,
  onSelect,
  onSelectRegion,
  onCreateRegion,
}: {
  mesh: MeshData | null;
  hovered: string | null;
  selectedIds: Set<string>; // seçili yüzey (f3) ve bölge (r1) kimlikleri
  regions: RegionInfo[];
  onHover: (id: string | null) => void;
  onSelect: (id: string | null, add?: boolean) => void;
  onSelectRegion: (id: string, add: boolean) => void;
  onCreateRegion: (req: RegionRequest) => Promise<unknown>;
}) {
  const { center, size } = useMemo(() => {
    if (!mesh) return { center: new THREE.Vector3(0, 0, 0), size: 60 };
    const { min, max } = mesh.summary.bbox;
    return {
      center: new THREE.Vector3((min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2),
      size: Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]),
    };
  }, [mesh]);
  const diag = useMemo(() => {
    if (!mesh) return 100;
    const s = mesh.summary.bbox.size;
    return Math.hypot(s[0], s[1], s[2]);
  }, [mesh]);

  const [grid, setGrid] = useState<GridInfo | null>(null);
  const [areaIds, setAreaIds] = useState<Set<string>>(EMPTY);
  const [areaBox, setAreaBox] = useState<THREE.Box3 | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [modDown, setModDown] = useState(false);
  const [inside, setInside] = useState(false);
  const [tool, setTool] = useState<RegionTool>(loadTool);
  const [drawing, setDrawing] = useState(false);
  const [drawView, setDrawView] = useState<DrawView | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const unit = useUnit();

  const wrapRef = useRef<HTMLDivElement>(null);
  const facesRef = useRef<THREE.Group>(null);
  const bridge = useRef<Bridge | null>(null);
  const meshRef = useRef(mesh);
  meshRef.current = mesh;
  const areaRef = useRef(areaIds);
  areaRef.current = areaIds;
  const toolRef = useRef(tool);
  toolRef.current = tool;
  const createRef = useRef(onCreateRegion);
  createRef.current = onCreateRegion;
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;

  const chooseTool = useCallback((t: RegionTool) => {
    setTool(t);
    saveTool(t);
  }, []);

  const clearArea = useCallback(() => {
    setAreaIds(EMPTY);
    setAreaBox(null);
  }, []);

  // Model/sürüm değişince eski alan seçimi geçersiz
  useEffect(clearArea, [mesh?.model_id, mesh?.version, clearArea]);

  // ⌘ (Mac) / Ctrl (Windows) basılıyken imleç artı işaretine döner ve araç çubuğu açılır; Esc seçimi temizler
  useEffect(() => {
    const isMod = (k: string) => k === "Meta" || k === "Control";
    const kd = (e: KeyboardEvent) => {
      if (isMod(e.key)) setModDown(true);
      const tag = (e.target as HTMLElement | null)?.tagName ?? "";
      if (e.key === "Escape" && !/INPUT|TEXTAREA|SELECT/.test(tag)) clearArea();
    };
    const ku = (e: KeyboardEvent) => isMod(e.key) && setModDown(false);
    const blur = () => setModDown(false);
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("blur", blur);
    };
  }, [clearArea]);

  // ⌘/Ctrl basılıyken, sürüklerken veya çizerken kamera kontrolü tamamen kapalı
  useEffect(() => {
    const c = bridge.current?.controls as { enabled?: boolean } | null | undefined;
    if (c && "enabled" in c) c.enabled = !(modDown || drag || drawing);
  }, [modDown, drag, drawing]);

  // Seçim olayları: yakalama (capture) aşamasında dinlenir, böylece ⌘ basılıyken
  // OrbitControls döndürmeye ve yüzey tıklamasına hiç başlamaz.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    let rect: Rect | null = null; // "Yüzeyler" aracı: pencere / kesişim dikdörtgeni
    let draw: Draw | null = null; // bölge araçları
    let proj: Projected[] = [];
    let base = new Set<string>();
    let mm = 1;
    let raf = 0;
    let rafD = 0;
    let noteTimer = 0;
    let suppressClick = false;
    let pointerId = -1;
    const raycaster = new THREE.Raycaster();

    const flash = (m: string) => {
      setNote(m);
      window.clearTimeout(noteTimer);
      noteTimer = window.setTimeout(() => setNote(null), 2600);
    };

    const update = (final: boolean) => {
      raf = 0;
      const m = meshRef.current;
      if (!rect || !m) return;
      const b = el.getBoundingClientRect();
      const crossing = rect.x1 < rect.x0;
      const ids = new Set(base);
      for (const id of pickFaces(proj, rect, crossing)) ids.add(id);
      setAreaIds(ids);
      setAreaBox(bboxOf(m, ids));
      setDrag(
        final
          ? null
          : { ...rect, crossing, w: Math.abs(rect.x1 - rect.x0) * mm, h: Math.abs(rect.y1 - rect.y0) * mm, vw: b.width, vh: b.height },
      );
    };

    const local = (e: PointerEvent) => {
      const b = el.getBoundingClientRect();
      return [Math.min(Math.max(e.clientX - b.left, 0), b.width), Math.min(Math.max(e.clientY - b.top, 0), b.height)] as P2;
    };

    const setControls = (on: boolean) => {
      const c = bridge.current?.controls as { enabled?: boolean } | null | undefined;
      if (c && "enabled" in c) c.enabled = on;
    };

    const rayAt = (x: number, y: number) => {
      const b = el.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((x / b.width) * 2 - 1, -(y / b.height) * 2 + 1), bridge.current!.camera);
      return raycaster.ray;
    };

    /** İmlecin altındaki yüzey + o noktadaki düzlem */
    const pick = (x: number, y: number): Anchor | null => {
      const g = facesRef.current, m = meshRef.current;
      if (!g || !m || !bridge.current) return null;
      const ray = rayAt(x, y);
      const hit = raycaster.intersectObjects(g.children, false).find((i) => i.object.userData.faceId && i.face);
      if (!hit) return null;
      const faceId = hit.object.userData.faceId as string;
      const fm = m.faces.find((f) => f.id === faceId);
      const n = hit.face!.normal.clone();
      n.set(...(n.toArray().map((c) => (Math.abs(c) < 1e-6 ? 0 : c)) as Vec3)).normalize();
      if (n.dot(ray.direction) > 0) n.negate(); // kameraya bakan taraf
      const { u, v } = planeFrame(n);
      return {
        faceId,
        surface: fm?.surface ?? "?",
        point: hit.point.clone(),
        planar: fm?.surface === "plane",
        plane: new THREE.Plane().setFromNormalAndCoplanarPoint(n, hit.point),
        u,
        v,
      };
    };

    const onPlane = (x: number, y: number) => (draw ? rayAt(x, y).intersectPlane(draw.anchor.plane, new THREE.Vector3()) : null);

    const addPoint = (x: number, y: number) => {
      if (!draw) return;
      if (draw.anchor.planar) {
        const w = onPlane(x, y);
        if (!w) return;
        draw.world.push(w);
      }
      draw.screen.push([x, y]);
    };

    const updateDraw = () => {
      rafD = 0;
      const d = draw;
      if (!d || !bridge.current) return;
      const b = el.getBoundingClientRect();
      const cam = bridge.current.camera;
      const toS = (w: THREE.Vector3): P2 => {
        const p = w.clone().project(cam);
        return [((p.x + 1) / 2) * b.width, ((1 - p.y) / 2) * b.height];
      };
      const a = d.anchor;
      let pts: P2[];
      let label = "";
      if (d.tool === "rect" || d.tool === "circle") {
        if (a.planar) {
          pts = planarShape(d).map(toS);
          const dv = (d.curWorld ?? a.point).clone().sub(a.point);
          const u = getUnit();
          label =
            d.tool === "rect"
              ? `${fmtLen(Math.abs(dv.dot(a.u)), u, false)} × ${fmtLen(Math.abs(dv.dot(a.v)), u)}`
              : `Ø ${fmtLen(2 * dv.length(), u)}`;
        } else {
          pts = screenShape(d);
          const [s0, s1] = [d.screen[0], d.cur];
          const u = getUnit();
          label =
            d.tool === "rect"
              ? `≈ ${fmtLen(Math.abs(s1[0] - s0[0]) * d.mm, u, false)} × ${fmtLen(Math.abs(s1[1] - s0[1]) * d.mm, u)}`
              : `≈ Ø ${fmtLen(2 * Math.hypot(s1[0] - s0[0], s1[1] - s0[1]) * d.mm, u)}`;
        }
      } else {
        pts = d.screen.slice();
        label = d.tool === "lasso" ? `${d.screen.length} nokta` : `${d.screen.length} köşe${d.screen.length >= 3 ? " · ⌘/Ctrl bırak: kapat" : ""}`;
      }
      const poly = d.tool === "polygon";
      setDrawView({
        pts,
        closed: !poly,
        rubber: poly && pts.length ? [pts[pts.length - 1], d.cur] : null,
        first: poly && pts.length >= 3 ? pts[0] : null,
        dots: poly,
        label,
        sub: `${a.faceId} · ${a.surface}${a.planar ? " · yüzey düzleminde" : " · bakış yönünde izdüşüm"}`,
        tip: d.cur,
        vw: b.width,
        vh: b.height,
      });
    };
    const scheduleDraw = () => {
      if (!rafD) rafD = requestAnimationFrame(updateDraw);
    };

    const buildRequest = (d: Draw): RegionRequest | string => {
      const m = meshRef.current!;
      const a = d.anchor;
      const base = { shape: d.tool, face_id: a.faceId, model_version: m.version, anchor_point: vec3(a.point) };
      if (a.planar) {
        if (d.tool === "circle") {
          const r = d.curWorld ? d.curWorld.distanceTo(a.point) : 0;
          return r < 0.05 ? "Daire çok küçük." : { ...base, center: vec3(a.point), radius: r };
        }
        let pts = planarShape(d);
        if (d.tool === "rect") {
          const dv = (d.curWorld ?? a.point).clone().sub(a.point);
          if (Math.abs(dv.dot(a.u)) < 0.05 || Math.abs(dv.dot(a.v)) < 0.05) return "Dikdörtgen çok küçük.";
        } else if (pts.length > 3) {
          // düzlemdeki (u, v) koordinatlarında seyrelt
          const o = a.point;
          let tol = d.mm;
          let uv: P2[] = pts.map((p) => [p.clone().sub(o).dot(a.u), p.clone().sub(o).dot(a.v)]);
          let s = rdp(uv, tol);
          while (s.length > 380) s = rdp(uv, (tol *= 2));
          uv = s;
          pts = uv.map(([x, y]) => o.clone().addScaledVector(a.u, x).addScaledVector(a.v, y));
        }
        return pts.length < 3 ? "Şekil için en az 3 nokta gerekli." : { ...base, points: pts.map(vec3) };
      }
      let s = screenShape(d);
      const [s0, s1] = [d.screen[0], d.cur];
      if (d.tool === "rect" && (Math.abs(s1[0] - s0[0]) < 4 || Math.abs(s1[1] - s0[1]) < 4)) return "Dikdörtgen çok küçük.";
      if (d.tool === "circle" && Math.hypot(s1[0] - s0[0], s1[1] - s0[1]) < 3) return "Daire çok küçük.";
      if (d.tool === "lasso" || d.tool === "polygon") s = rdp(s, 1.2);
      if (s.length < 3) return "Şekil için en az 3 nokta gerekli.";
      const rays = s.map(([x, y]) => {
        const r = rayAt(x, y);
        return [r.origin.x, r.origin.y, r.origin.z, r.direction.x, r.direction.y, r.direction.z];
      });
      return { ...base, rays };
    };

    const cancelDraw = () => {
      draw = null;
      if (rafD) cancelAnimationFrame(rafD);
      rafD = 0;
      setDrawing(false);
      setDrawView(null);
    };

    const finishDraw = () => {
      const d = draw;
      if (!d) return;
      if (rafD) cancelAnimationFrame(rafD);
      rafD = 0;
      draw = null;
      setDrawing(false);
      const req = buildRequest(d);
      if (typeof req === "string") {
        setDrawView(null);
        flash(req);
        return;
      }
      setDrawView((v) => (v ? { ...v, pending: true, rubber: null, first: null, closed: true } : v));
      void Promise.resolve(createRef.current(req)).finally(() => setDrawView(null));
    };

    const down = (e: PointerEvent) => {
      suppressClick = false;
      if (!(e.target instanceof Element) || !el.contains(e.target) || e.target.closest(".region-toolbar, .area-panel, .grid-legend")) return;
      if (e.button !== 0 || !(e.metaKey || e.ctrlKey) || !meshRef.current || !bridge.current) return;
      e.stopImmediatePropagation();
      e.preventDefault();
      setControls(false);
      const b = el.getBoundingClientRect();
      const [x, y] = local(e);
      const t = toolRef.current;

      if (t === "faces") {
        proj = projectFaces(meshRef.current, bridge.current.camera, b.width, b.height);
        mm = mmPerPx(bridge.current, b.height);
        base = e.shiftKey ? new Set(areaRef.current) : new Set();
        rect = { x0: x, y0: y, x1: x, y1: y };
        pointerId = e.pointerId;
        el.setPointerCapture(e.pointerId);
        return;
      }

      suppressClick = true; // bu tıklama yüzey/bölge seçimine dönüşmesin
      if (t === "polygon" && draw) {
        const now = performance.now();
        const first = draw.screen[0], last = draw.screen[draw.screen.length - 1];
        if (draw.screen.length >= 3 && Math.hypot(x - first[0], y - first[1]) < 8) return finishDraw();
        if (now - draw.lastDown < 320 && Math.hypot(x - last[0], y - last[1]) < 6) return finishDraw();
        draw.lastDown = now;
        addPoint(x, y);
        scheduleDraw();
        return;
      }
      const anchor = pick(x, y);
      if (!anchor) {
        flash("Çizime modelin bir yüzeyinin üzerinden başlayın.");
        return;
      }
      draw = {
        tool: t,
        anchor,
        screen: [[x, y]],
        world: anchor.planar ? [anchor.point.clone()] : [],
        cur: [x, y],
        curWorld: anchor.planar ? anchor.point.clone() : null,
        mm: mmPerPx(bridge.current, b.height),
        lastDown: performance.now(),
      };
      setDrawing(true);
      hoverRef.current(null); // çizim sırasında yüzey vurgusu kalmasın
      if (t !== "polygon") {
        pointerId = e.pointerId;
        el.setPointerCapture(e.pointerId);
      }
      scheduleDraw();
    };

    const move = (e: PointerEvent) => {
      if (draw) {
        e.stopPropagation(); // çizim sırasında yüzey vurgusu değişmesin
        const [x, y] = local(e);
        draw.cur = [x, y];
        if (draw.anchor.planar) {
          const w = onPlane(x, y);
          if (w) draw.curWorld = w;
        }
        if (draw.tool === "lasso") {
          const last = draw.screen[draw.screen.length - 1];
          if (Math.hypot(x - last[0], y - last[1]) >= 3 && draw.screen.length < 800) addPoint(x, y);
        }
        scheduleDraw();
        return;
      }
      if (!rect) {
        // tuş olayı kaçırılmışsa imleci fare olayından da güncelle
        const mod = e.metaKey || e.ctrlKey;
        setModDown((m) => (m === mod ? m : mod));
        return;
      }
      e.stopPropagation(); // sürükleme sırasında yüzey vurgusu değişmesin
      [rect.x1, rect.y1] = local(e);
      if (!raf) raf = requestAnimationFrame(() => update(false));
    };

    const up = (e: PointerEvent) => {
      if (draw) {
        e.stopPropagation();
        if (draw.tool === "polygon") return; // çokgen tıklamalarla sürer
        if (el.hasPointerCapture(pointerId)) el.releasePointerCapture(pointerId);
        const [x, y] = local(e);
        draw.cur = [x, y];
        if (draw.anchor.planar) {
          const w = onPlane(x, y);
          if (w) draw.curWorld = w;
        }
        const s0 = draw.screen[0];
        const tiny = draw.tool === "lasso" ? draw.screen.length < 3 : Math.hypot(x - s0[0], y - s0[1]) < 4;
        if (tiny || e.type === "pointercancel") cancelDraw();
        else finishDraw();
        setControls(!(e.metaKey || e.ctrlKey));
        return;
      }
      if (!rect) return;
      e.stopPropagation();
      if (el.hasPointerCapture(pointerId)) el.releasePointerCapture(pointerId);
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      suppressClick = true;
      const tiny = Math.abs(rect.x1 - rect.x0) < 4 && Math.abs(rect.y1 - rect.y0) < 4;
      if (tiny) {
        // ⌘ + tek tık: (Shift yoksa) alan seçimini temizle
        if (!e.shiftKey) {
          setAreaIds(EMPTY);
          setAreaBox(null);
        }
        setDrag(null);
      } else {
        update(true);
      }
      rect = null;
      proj = [];
      setControls(!(e.metaKey || e.ctrlKey));
    };

    const click = (e: MouseEvent) => {
      if (!suppressClick) return;
      suppressClick = false;
      e.stopPropagation();
    };
    const ctx = (e: MouseEvent) => {
      if (rect || draw) e.preventDefault(); // Mac'te Ctrl+tık sağ tık menüsünü açmasın
    };
    const mdown = (e: MouseEvent) => {
      if (rect || draw || ((e.metaKey || e.ctrlKey) && toolRef.current !== "faces")) e.stopImmediatePropagation();
    };

    // Çokgen: Enter / ⌘-Ctrl bırakma kapatır, ⌫ son köşeyi siler, Esc iptal
    const kd = (e: KeyboardEvent) => {
      if (!draw) return;
      if (["Escape", "Enter", "Backspace", "Delete"].includes(e.key)) e.stopPropagation(); // çizim sürerken seçim kısayolları çalışmasın
      if (e.key === "Escape") cancelDraw();
      else if (draw.tool === "polygon" && e.key === "Enter") {
        e.preventDefault();
        finishDraw();
      } else if (draw.tool === "polygon" && e.key === "Backspace" && draw.screen.length > 1) {
        e.preventDefault();
        draw.screen.pop();
        if (draw.anchor.planar) draw.world.pop();
        scheduleDraw();
      }
    };
    const ku = (e: KeyboardEvent) => {
      if (draw?.tool === "polygon" && (e.key === "Meta" || e.key === "Control")) {
        if (draw.screen.length >= 3) finishDraw();
        else cancelDraw();
      }
    };
    const blur = () => draw && cancelDraw();

    window.addEventListener("pointerdown", down, true);
    el.addEventListener("pointermove", move, true);
    el.addEventListener("pointerup", up, true);
    el.addEventListener("pointercancel", up, true);
    el.addEventListener("click", click, true);
    el.addEventListener("mousedown", mdown, true);
    el.addEventListener("contextmenu", ctx, true);
    window.addEventListener("keydown", kd, true);
    window.addEventListener("keyup", ku, true);
    window.addEventListener("blur", blur);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      if (rafD) cancelAnimationFrame(rafD);
      window.clearTimeout(noteTimer);
      window.removeEventListener("pointerdown", down, true);
      el.removeEventListener("pointermove", move, true);
      el.removeEventListener("pointerup", up, true);
      el.removeEventListener("pointercancel", up, true);
      el.removeEventListener("click", click, true);
      el.removeEventListener("mousedown", mdown, true);
      el.removeEventListener("contextmenu", ctx, true);
      window.removeEventListener("keydown", kd, true);
      window.removeEventListener("keyup", ku, true);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // Bir deliğin tüm yüzeyleri (STEP'te genelde iki yarım silindir) birlikte vurgulanır
  const holeOf = (id: string | null) => (id && mesh ? mesh.faces.find((f) => f.id === id)?.hole_id ?? null : null);
  const hoverHole = holeOf(hovered);
  const selHoles = useMemo(() => {
    const out = new Set<string>();
    if (mesh) for (const f of mesh.faces) if (f.hole_id && selectedIds.has(f.id)) out.add(f.hole_id);
    return out;
  }, [mesh, selectedIds]);
  const areaSize = areaBox?.getSize(new THREE.Vector3());
  const eps = Math.max(0.01, diag * 2e-4);
  const showToolbar = !!mesh && ((modDown && inside) || drawing || !!drawView);

  return (
    <div
      ref={wrapRef}
      className="model-wrap"
      onPointerEnter={() => setInside(true)}
      onPointerLeave={() => setInside(false)}
      style={{ cursor: drag || drawing || (modDown && inside) ? "crosshair" : hovered ? "pointer" : "default" }}
    >
      <Canvas
        camera={{ up: [0, 0, 1], position: [80, -100, 80], fov: 40 }}
        onPointerMissed={(e) => {
          if (e.button === 0 && e.type === "click" && !e.shiftKey) {
            onSelect(null);
            clearArea();
          }
        }}
      >
        <color attach="background" args={["#14181d"]} />
        <SceneBase gridSize={Math.max(200, size * 4)} onGrid={setGrid} />
        <CameraBridge bridge={bridge} />
        {mesh && <FitCamera size={size} center={center} fitKey={mesh.model_id} />}
        {mesh && (
          <group>
            <group ref={facesRef}>
              {mesh.faces.map((f) => (
                <Face
                  key={`${mesh.version}-${f.id}`}
                  face={f}
                  hovered={hovered === f.id || (!!hoverHole && f.hole_id === hoverHole)}
                  selected={selectedIds.has(f.id) || (!!f.hole_id && selHoles.has(f.hole_id))}
                  area={areaIds.has(f.id)}
                  onHover={onHover}
                  onSelect={onSelect}
                />
              ))}
            </group>
            <Edges edges={mesh.edges} />
          </group>
        )}
        {mesh &&
          regions.map((r) => (
            <RegionMesh
              key={`${r.region_id}-${r.model_version}`}
              region={r}
              eps={eps}
              hovered={hovered === r.region_id}
              selected={selectedIds.has(r.region_id)}
              onHover={onHover}
              onSelect={onSelectRegion}
            />
          ))}
        {areaBox && <AreaBox box={areaBox} />}
      </Canvas>

      {showToolbar && <RegionToolbar tool={tool} onTool={chooseTool} note={note} />}
      {drawView && <DrawOverlay v={drawView} />}
      {drag && mesh && <SelectionOverlay drag={drag} ids={areaIds} box={areaBox} mesh={mesh} />}

      {!drag && mesh && areaIds.size > 0 && (
        <div className="area-panel">
          <div className="area-head">
            <b>▭ Alan seçimi</b>
            <span className="muted">
              {areaIds.size} yüzey{featureSummary(mesh, areaIds)}
            </span>
            <button className="icon-btn" title="Seçimi temizle (Esc)" onClick={clearArea}>
              ✕
            </button>
          </div>
          {areaSize && (
            <div className="mono area-dims">
              <span>X <b>{fmtLen(areaSize.x, unit, false)}</b></span>
              <span>Y <b>{fmtLen(areaSize.y, unit, false)}</b></span>
              <span>Z <b>{fmtLen(areaSize.z, unit, false)}</b></span>
              <span className="muted">{UNITS[unit].label}</span>
            </div>
          )}
          <div className="muted small">⇧ ile ekle · Esc temizler</div>
        </div>
      )}

      {!showToolbar && <GridLegend grid={grid} />}
    </div>
  );
}
