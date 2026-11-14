import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { MeshData, Toolpath, Vec3 } from "../types";
import { FitCamera, GridLegend, SceneBase, type GridInfo } from "./ModelViewer";

// Lejanttaki sRGB renkler; THREE.Color doğrusal (linear) uzaya çevirir
const KIND_COLOR: Record<string, [number, number, number]> = Object.fromEntries(
  Object.entries({ rapid: "#f28c33", feed: "#40ccf2", arc: "#73f299", drill: "#f259bf" }).map(([k, hex]) => {
    const c = new THREE.Color(hex);
    return [k, [c.r, c.g, c.b]];
  }),
);

interface Flat {
  pos: Float32Array; // tüm noktalar (sürekli polyline)
  col: Float32Array;
  time: Float64Array; // her noktaya ulaşılan kümülatif süre (sn)
  line: Int32Array; // her noktanın G-code satırı
  total: number;
}

function flatten(tp: Toolpath): Flat {
  const pts: number[] = [];
  const cols: number[] = [];
  const times: number[] = [];
  const lines: number[] = [];
  let t = 0;
  tp.segments.forEach((s) => {
    const c = KIND_COLOR[s.kind] ?? [1, 1, 1];
    // segment içindeki uzunluk oranına göre süre dağıt
    const L: number[] = [0];
    for (let i = 1; i < s.points.length; i++) {
      const a = s.points[i - 1], b = s.points[i];
      L.push(L[i - 1] + Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]));
    }
    const total = L[L.length - 1] || 1;
    s.points.forEach((p, i) => {
      if (i === 0 && pts.length) {
        // önceki segmentin sonuyla aynı nokta: rengi bu segmente geçir
        const k = pts.length / 3 - 1;
        if (Math.hypot(pts[k * 3] - p[0], pts[k * 3 + 1] - p[1], pts[k * 3 + 2] - p[2]) < 1e-6) return;
      }
      pts.push(p[0], p[1], p[2]);
      cols.push(...c);
      times.push(t + (s.duration * L[i]) / total);
      lines.push(s.line);
    });
    t += s.duration;
  });
  return { pos: new Float32Array(pts), col: new Float32Array(cols), time: new Float64Array(times), line: new Int32Array(lines), total: t };
}

function findIndex(time: Float64Array, t: number) {
  let lo = 0, hi = time.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (time[mid] <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

function Animated({
  flat,
  playing,
  speed,
  simTime,
  setSimTime,
  toolDiameter,
  onLine,
}: {
  flat: Flat;
  playing: boolean;
  speed: number;
  simTime: React.MutableRefObject<number>;
  setSimTime: (t: number) => void;
  toolDiameter: number;
  onLine: (l: number) => void;
}) {
  const toolRef = useRef<THREE.Group>(null);
  const lastUi = useRef(0);
  const lastLine = useRef(-1);

  const fullGeom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(flat.pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(flat.col, 3));
    return g;
  }, [flat]);
  const doneGeom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(flat.pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(flat.col, 3));
    g.setDrawRange(0, 0);
    return g;
  }, [flat]);
  const fullLine = useMemo(
    () => new THREE.Line(fullGeom, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.22 })),
    [fullGeom],
  );
  const doneLine = useMemo(() => new THREE.Line(doneGeom, new THREE.LineBasicMaterial({ vertexColors: true })), [doneGeom]);

  useFrame((_, dt) => {
    if (playing) {
      simTime.current = Math.min(flat.total, simTime.current + dt * speed);
    }
    const t = simTime.current;
    const n = flat.time.length;
    if (!n) return;
    const i = findIndex(flat.time, t);
    const j = Math.min(i + 1, n - 1);
    const t0 = flat.time[i], t1 = flat.time[j];
    const a = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
    const x = flat.pos[i * 3] + (flat.pos[j * 3] - flat.pos[i * 3]) * a;
    const y = flat.pos[i * 3 + 1] + (flat.pos[j * 3 + 1] - flat.pos[i * 3 + 1]) * a;
    const z = flat.pos[i * 3 + 2] + (flat.pos[j * 3 + 2] - flat.pos[i * 3 + 2]) * a;
    toolRef.current?.position.set(x, y, z);
    doneGeom.setDrawRange(0, i + 1);
    const now = performance.now();
    if (now - lastUi.current > 100) {
      lastUi.current = now;
      setSimTime(t);
      const ln = flat.line[j];
      if (ln !== lastLine.current) {
        lastLine.current = ln;
        onLine(ln);
      }
    }
  });

  const r = toolDiameter / 2;
  return (
    <>
      <primitive object={fullLine} />
      <primitive object={doneLine} />
      <group ref={toolRef}>
        {/* Takım: uç noktası konumda, gövde yukarı doğru */}
        <mesh position={[0, 0, 12]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[r, r, 24, 24]} />
          <meshStandardMaterial color="#d8dde3" metalness={0.8} roughness={0.25} transparent opacity={0.85} />
        </mesh>
        <mesh position={[0, 0, 34]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[r * 2.2, r * 2.2, 20, 24]} />
          <meshStandardMaterial color="#5b6573" metalness={0.6} roughness={0.4} />
        </mesh>
      </group>
    </>
  );
}

/** Modeli G-code iş koordinatlarına taşınmış yarı saydam hayalet olarak çizer. */
function Ghost({ mesh, origin }: { mesh: MeshData; origin?: Vec3 | null }) {
  const { min, max } = mesh.summary.bbox;
  // iş sıfırı: üretimde kaydedilen model koordinatı; eski / yüklenen programlarda parça üst yüzey merkezi
  const o = origin ?? [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, max[2]];
  const off: [number, number, number] = [-o[0], -o[1], -o[2]];
  const geoms = useMemo(
    () =>
      mesh.faces.map((f) => {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(f.vertices, 3));
        g.setIndex(f.indices);
        g.computeVertexNormals();
        return g;
      }),
    [mesh],
  );
  return (
    <group position={off}>
      {geoms.map((g, i) => (
        <mesh key={i} geometry={g}>
          <meshStandardMaterial color="#8d97a3" transparent opacity={0.18} depthWrite={false} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </group>
  );
}

export default function ToolpathViewer({
  toolpath,
  mesh,
  origin,
  toolDiameter,
  onLine,
  seek,
}: {
  toolpath: Toolpath | null;
  mesh: MeshData | null;
  origin?: Vec3 | null;
  toolDiameter: number;
  onLine: (line: number) => void;
  /** Atölye sekmesinden: simülasyonu bu satırın başladığı ana getir */
  seek?: { line: number; n: number } | null;
}) {
  const flat = useMemo(() => (toolpath ? flatten(toolpath) : null), [toolpath]);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(8);
  const [uiTime, setUiTime] = useState(0);
  const simTime = useRef(0);

  useEffect(() => {
    simTime.current = 0;
    setUiTime(0);
    setPlaying(false);
  }, [flat]);

  useEffect(() => {
    if (!seek || !flat) return;
    let j = flat.line.findIndex((l) => l >= seek.line);
    if (j < 0) j = flat.line.length - 1;
    // satırın ilk noktasına ulaşılan an; bir önceki noktadan hemen sonrası (hareket bu satırla başlar)
    const t = j > 0 ? flat.time[j - 1] + 1e-6 : 0;
    simTime.current = t;
    setUiTime(t);
    setPlaying(false);
    onLine(flat.line[j]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seek, flat]);

  const { center, size } = useMemo(() => {
    if (!flat || !flat.pos.length) return { center: new THREE.Vector3(0, 0, 0), size: 60 };
    const b = new THREE.Box3().setFromArray(flat.pos);
    const s = new THREE.Vector3();
    b.getSize(s);
    return { center: b.getCenter(new THREE.Vector3()), size: Math.max(s.x, s.y, s.z) };
  }, [flat]);

  const [grid, setGrid] = useState<GridInfo | null>(null);

  if (!toolpath || !flat) {
    return <div className="empty-state">Takım yolunu görmek için önce G-code üretin veya yükleyin.</div>;
  }
  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  return (
    <div className="toolpath-wrap">
      <Canvas camera={{ up: [0, 0, 1], position: [80, -100, 80], fov: 40 }}>
        <color attach="background" args={["#14181d"]} />
        <SceneBase gridSize={Math.max(200, size * 4)} onGrid={setGrid} />
        <FitCamera size={size} center={center} fitKey={String(flat.total) + flat.pos.length} />
        {mesh && <Ghost mesh={mesh} origin={origin} />}
        <Animated flat={flat} playing={playing} speed={speed} simTime={simTime} setSimTime={setUiTime} toolDiameter={toolDiameter} onLine={onLine} />
      </Canvas>
      <GridLegend grid={grid} />
      <div className="anim-bar">
        <button
          className="btn primary"
          onClick={() => {
            if (simTime.current >= flat.total) simTime.current = 0;
            setPlaying((p) => !p);
          }}
        >
          {playing ? "❚❚ Duraklat" : simTime.current > 0 && simTime.current < flat.total ? "▶ Devam" : "▶ Başlat"}
        </button>
        <button
          className="btn"
          onClick={() => {
            simTime.current = 0;
            setUiTime(0);
            setPlaying(false);
          }}
        >
          ⟲ Başa sar
        </button>
        <input
          className="scrub"
          type="range"
          min={0}
          max={flat.total}
          step={flat.total / 1000}
          value={uiTime}
          onChange={(e) => {
            simTime.current = Number(e.target.value);
            setUiTime(simTime.current);
          }}
        />
        <span className="mono small">
          {fmt(uiTime)} / {fmt(flat.total)}
        </span>
        <label className="small">
          Hız
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {[1, 2, 4, 8, 16, 32, 64].map((s) => (
              <option key={s} value={s}>
                {s}×
              </option>
            ))}
          </select>
        </label>
        <div className="legend small">
          <span><i style={{ background: "#f28c33" }} /> G0 hızlı</span>
          <span><i style={{ background: "#40ccf2" }} /> G1 kesme</span>
          <span><i style={{ background: "#73f299" }} /> G2/G3 yay</span>
          <span><i style={{ background: "#f259bf" }} /> delme</span>
        </div>
      </div>
    </div>
  );
}
