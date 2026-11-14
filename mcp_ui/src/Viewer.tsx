import { Canvas, ThreeEvent, useThree } from "@react-three/fiber";
import { GizmoHelper, GizmoViewport, Grid, OrbitControls } from "@react-three/drei";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { Face } from "./mesh";

export type Palette = {
  bg: string;
  base: string;
  hole: string;
  pocket: string;
  feature: string;
  hover: string;
  selected: string;
  edge: string;
  grid: string;
  gridSection: string;
};

export const DARK: Palette = {
  bg: "#14181d",
  base: "#9aa3ad",
  hole: "#c9a25a",
  pocket: "#7f9bb3",
  feature: "#a58bd0",
  hover: "#ffb347",
  selected: "#ff7a1a",
  edge: "#1b1f24",
  grid: "#2f3640",
  gridSection: "#46505c",
};

export const LIGHT: Palette = {
  bg: "#eef1f4",
  base: "#a7b0ba",
  hole: "#c9a25a",
  pocket: "#6f8fb0",
  feature: "#9a7fc8",
  hover: "#ffb347",
  selected: "#ff6a00",
  edge: "#2a2f36",
  grid: "#d3d9e0",
  gridSection: "#b8c1cb",
};

function faceColor(p: Palette, f: Face, ftype: string | undefined, hovered: boolean, selected: boolean) {
  if (selected) return p.selected;
  if (hovered) return p.hover;
  if (f.hole_id || ftype === "hole") return p.hole;
  if (f.pocket_id || ftype === "pocket") return p.pocket; // cep tabanı + yan duvarları
  if (f.feature_id) return p.feature;
  return p.base;
}

/** Birlikte vurgulanacak grup: delik ya da özellik (cep, kazıma); yoksa tek yüzey. */
const groupOf = (f: Face | undefined) => (f ? f.hole_id ?? f.feature_id ?? null : null);

function FaceMesh({
  face,
  ftype,
  palette,
  hovered,
  selected,
  onHover,
  onSelect,
}: {
  face: Face;
  ftype: string | undefined;
  palette: Palette;
  hovered: boolean;
  selected: boolean;
  onHover: (id: string | null) => void;
  onSelect: (id: string) => void;
}) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(face.positions, 3));
    g.setIndex(new THREE.BufferAttribute(face.indices, 1));
    g.computeVertexNormals();
    return g;
  }, [face]);
  useEffect(() => () => geom.dispose(), [geom]);
  const downAt = useRef<[number, number] | null>(null);

  return (
    <mesh
      geometry={geom}
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
        const d = downAt.current; // döndürme sürüklemesini tıklama sayma
        if (d && Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 4) return;
        onSelect(face.id);
      }}
    >
      <meshStandardMaterial
        color={faceColor(palette, face, ftype, hovered, selected)}
        metalness={0.3}
        roughness={0.55}
        side={THREE.DoubleSide}
        polygonOffset
        polygonOffsetFactor={1}
        polygonOffsetUnits={1}
      />
    </mesh>
  );
}

function Edges({ segments, color }: { segments: Float32Array; color: string }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(segments, 3));
    return g;
  }, [segments]);
  useEffect(() => () => geom.dispose(), [geom]);
  return (
    <lineSegments geometry={geom}>
      <lineBasicMaterial color={color} />
    </lineSegments>
  );
}

function FitCamera({ size, center, fitKey }: { size: number; center: THREE.Vector3; fitKey: string }) {
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

export default function Viewer({
  faces,
  featureTypes,
  edges,
  bbox,
  fitKey,
  palette,
  hovered,
  selected,
  onHover,
  onSelect,
}: {
  faces: Face[];
  featureTypes: Record<string, string>;
  edges: Float32Array;
  bbox: { min: number[]; max: number[] } | null;
  fitKey: string;
  palette: Palette;
  hovered: string | null;
  selected: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string | null) => void;
}) {
  const { center, size } = useMemo(() => {
    if (!bbox) return { center: new THREE.Vector3(0, 0, 0), size: 60 };
    const { min, max } = bbox;
    return {
      center: new THREE.Vector3((min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2),
      size: Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]),
    };
  }, [bbox]);

  // Bir deliğin / cebin tüm yüzeyleri (STEP'te delik genelde iki yarım silindir) birlikte vurgulanır
  const byId = useMemo(() => new Map(faces.map((f) => [f.id, f])), [faces]);
  const hoverGroup = hovered ? groupOf(byId.get(hovered)) : null;
  const selGroup = selected ? groupOf(byId.get(selected)) : null;
  const gridSize = Math.max(200, size * 4);

  return (
    <Canvas
      camera={{ up: [0, 0, 1], position: [80, -100, 80], fov: 40 }}
      onPointerMissed={(e) => e.button === 0 && e.type === "click" && onSelect(null)}
      style={{ cursor: hovered ? "pointer" : "grab" }}
      dpr={[1, 2]}
    >
      <color attach="background" args={[palette.bg]} />
      <ambientLight intensity={0.55} />
      <directionalLight position={[60, -80, 120]} intensity={1.4} />
      <directionalLight position={[-80, 60, 40]} intensity={0.5} />
      <hemisphereLight args={["#dfe7ef", "#2a2f36", 0.35]} />
      <Grid
        rotation={[Math.PI / 2, 0, 0]}
        args={[gridSize, gridSize]}
        cellSize={5}
        sectionSize={25}
        cellColor={palette.grid}
        sectionColor={palette.gridSection}
        fadeDistance={gridSize * 2}
        infiniteGrid
      />
      <OrbitControls makeDefault enableDamping dampingFactor={0.12} />
      <GizmoHelper alignment="bottom-right" margin={[56, 56]}>
        <GizmoViewport axisColors={["#e5534b", "#57ab5a", "#539bf5"]} labelColor="#0e1116" />
      </GizmoHelper>
      <FitCamera size={size} center={center} fitKey={fitKey} />
      <group>
        {faces.map((f) => (
          <FaceMesh
            key={`${fitKey}-${f.id}`}
            face={f}
            ftype={f.feature_id ? featureTypes[f.feature_id] : undefined}
            palette={palette}
            hovered={hovered === f.id || (!!hoverGroup && groupOf(f) === hoverGroup)}
            selected={selected === f.id || (!!selGroup && groupOf(f) === selGroup)}
            onHover={onHover}
            onSelect={onSelect}
          />
        ))}
        <Edges segments={edges} color={palette.edge} />
      </group>
    </Canvas>
  );
}
