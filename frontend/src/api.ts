import type { AppState, AuditEntry, AuditList, ConvPayload, ConvSummary, EngraveResult, FaceInfo, LlmInfo, RegionInfo, RegionRequest, UiState, GcodeRecord, MachineProfile, MachiningConfig, MeshData, ProfileChange, Toolpath, Vec3, Material, CuttingRec } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* gövde JSON değil */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  /** LLM ile aynı araçları doğrudan çağırır; araç hatası ({ok:false, error}) istisnaya çevrilir */
  tool: async <T,>(name: string, args: Record<string, unknown> = {}): Promise<T> => {
    const r = await req<T & { ok?: boolean; error?: string }>(`/api/tools/${name}`, json("POST", args));
    if (r && r.ok === false) throw new Error(r.error || "İşlem başarısız.");
    return r;
  },
  state: () => req<AppState>("/api/state"),
  mesh: () => req<MeshData>("/api/models/current/mesh"),
  face: (id: string, version: number) => req<FaceInfo>(`/api/models/current/faces/${id}?version=${version}`),
  primitive: (body: Record<string, unknown>) => req("/api/models/primitive", json("POST", body)),
  uploadStep: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ message: string }>("/api/models/upload", { method: "POST", body: fd });
  },
  engraveText: (body: Record<string, unknown>) => req<EngraveResult>("/api/models/current/engrave-text", json("POST", body)),
  engraveSvg: (file: File, params: Record<string, string | number | null>) => {
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(params).forEach(([k, v]) => v !== null && v !== "" && fd.append(k, String(v)));
    return req<EngraveResult>("/api/models/current/engrave-svg", { method: "POST", body: fd });
  },
  undo: () => req<{ undone: string }>("/api/models/current/undo", { method: "POST" }),
  deleteFeature: (id: string) => req(`/api/models/current/features/${id}`, { method: "DELETE" }),
  regions: () => req<{ regions: RegionInfo[]; dropped: string[]; model_version: number | null }>("/api/models/current/regions"),
  createRegion: (body: RegionRequest) => req<RegionInfo>("/api/models/current/regions", json("POST", body)),
  deleteRegion: (id: string) => req(`/api/models/current/regions/${id}`, { method: "DELETE" }),
  conversations: () => req<{ conversations: ConvSummary[]; active_id: string }>("/api/conversations"),
  newConversation: () => req<ConvPayload>("/api/conversations", { method: "POST" }),
  activateConversation: (id: string) => req<ConvPayload>(`/api/conversations/${id}/activate`, { method: "POST" }),
  renameConversation: (id: string, title: string) =>
    req<{ conversations: ConvSummary[] }>(`/api/conversations/${id}`, json("PATCH", { title })),
  deleteConversation: (id: string) => req<ConvPayload>(`/api/conversations/${id}`, { method: "DELETE" }),
  chat: (message: string, selected_face_id: string | null, model_version: number | null, selected_region_id: string | null = null, selected_ids: string[] = [], conversation_id: string | null = null) =>
    req<{
      reply: string;
      actions: { tool: string; args: Record<string, unknown>; ok: boolean; error: string | null }[];
      model_changed: boolean;
      gcode_changed: boolean;
      state: Omit<AppState, "llm">;
    }>("/api/chat", json("POST", { message, selected_face_id, selected_region_id, selected_ids, model_version, conversation_id })),
  auditList: (p: { q?: string; kind?: string; offset?: number; limit?: number }) =>
    req<AuditList>(`/api/audit?${new URLSearchParams({ q: p.q ?? "", kind: p.kind ?? "", offset: String(p.offset ?? 0), limit: String(p.limit ?? 100) })}`),
  auditIds: (p: { q?: string; kind?: string }) =>
    req<{ total: number; ids: string[] }>(`/api/audit?${new URLSearchParams({ q: p.q ?? "", kind: p.kind ?? "", ids_only: "true" })}`),
  auditEntry: (id: string) => req<AuditEntry>(`/api/audit/${id}`),
  /** Kayıtları JSON dosyası olarak indirir (ids=null → tümü) */
  auditDownload: async (ids: string[] | null) => {
    const r = await fetch("/api/audit/export", json("POST", { ids }));
    if (!r.ok) throw new Error(`İndirme başarısız: ${r.status}`);
    const blob = await r.blob();
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, "0");
    const name = `llm-audit-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${ids ? `-secili-${ids.length}` : ""}.json`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 2000);
  },
  chatReset: () => req("/api/chat/reset", { method: "POST" }),
  putUi: (ui: UiState) => req("/api/ui", json("PUT", ui)),
  putSelection: (face_id: string | null, model_version: number | null, region_id: string | null = null, ids: string[] = []) =>
    req("/api/selection", json("PUT", { face_id, region_id, ids, model_version })),
  sessionReset: () => req("/api/session/reset", { method: "POST" }),
  setLlmMode: (offline: boolean) => req<LlmInfo>("/api/llm/mode", json("POST", { offline })),
  getConfig: () => req<MachiningConfig>("/api/config"),
  putConfig: (cfg: MachiningConfig) => req<MachiningConfig>("/api/config", json("PUT", cfg)),
  profiles: () => req<MachineProfile[]>("/api/profiles"),
  setProfile: (profile_id: string, work_offset?: Vec3 | null) =>
    req<ProfileChange>("/api/profile", json("POST", { profile_id, work_offset: work_offset ?? null })),
  generate: (operations: string[]) => req<GcodeRecord>("/api/gcode/generate", json("POST", { operations })),
  gcode: (id: string) => req<GcodeRecord>(`/api/gcode/${id}`),
  validate: (id: string) => req<GcodeRecord>(`/api/gcode/${id}/validate`, { method: "POST" }),
  gcodeText: (text: string, name: string) => req<GcodeRecord>("/api/gcode/text", json("POST", { text, name })),
  toolpath: (id: string) => req<Toolpath>(`/api/gcode/${id}/toolpath`),
  materials: () => req<{ active: string; materials: Material[]; note: string }>("/api/materials"),
  recommend: (q: { tool_number?: number; tool_type?: string; diameter?: number; flutes?: number; tool_material?: string; material?: string }) =>
    req<CuttingRec & { ok: boolean; machine: string }>("/api/cutting/recommend", json("POST", q)),
  saveMachine: (profile: MachineProfile, activate = false) =>
    req<{ profile: MachineProfile; profiles: MachineProfile[]; config: MachiningConfig }>("/api/profiles", json("POST", { profile, activate })),
  deleteMachine: (id: string) =>
    req<{ profiles: MachineProfile[]; config: MachiningConfig }>(`/api/profiles/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
