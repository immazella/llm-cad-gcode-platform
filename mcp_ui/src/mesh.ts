// MCP sunucusunun compact_mesh() çıktısı (backend/app/mcp_server.py) ve çözücüsü.

export type CompactFace = {
  id: string;
  v: string; // base64 Float32 (x,y,z,...)
  i: string; // base64 Uint16 / Uint32 üçgen indeksleri
  i32: boolean;
  hole_id: string | null;
  pocket_id: string | null;
  feature_id: string | null;
};

export type CompactMesh = {
  model_id: string;
  version: number;
  faces: CompactFace[];
  edges: string; // base64 Float32, segment çiftleri
  summary: {
    name: string;
    version: number;
    bbox: { min: number[]; max: number[]; size: number[] } | null;
    holes: { id: string; diameter: number; top_point: number[] }[];
    pockets: { id: string }[];
    features: { id: string; type: string }[];
  };
};

export type Face = {
  id: string;
  positions: Float32Array;
  indices: Uint16Array | Uint32Array;
  hole_id: string | null;
  pocket_id: string | null;
  feature_id: string | null;
};

export type SelectionInfo = {
  face_id: string;
  role?: string;
  hole_id?: string | null;
  pocket_id?: string | null;
  feature_id?: string | null;
  surface?: string;
  center?: number[];
  normal?: number[];
  hole?: { diameter?: number; depth?: number; through?: boolean } & Record<string, unknown>;
};

function bytes(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let k = 0; k < bin.length; k++) out[k] = bin.charCodeAt(k);
  return out.buffer;
}

export function decodeFaces(m: CompactMesh): Face[] {
  return m.faces.map((f) => ({
    id: f.id,
    positions: new Float32Array(bytes(f.v)),
    indices: f.i32 ? new Uint32Array(bytes(f.i)) : new Uint16Array(bytes(f.i)),
    hole_id: f.hole_id,
    pocket_id: f.pocket_id,
    feature_id: f.feature_id,
  }));
}

export function decodeEdges(m: CompactMesh): Float32Array {
  return m.edges ? new Float32Array(bytes(m.edges)) : new Float32Array(0);
}
