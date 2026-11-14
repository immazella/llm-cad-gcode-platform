export type Vec3 = [number, number, number];

export interface Hole {
  id: string;
  feature_id: string | null;
  diameter: number;
  depth: number;
  through: boolean;
  top_point: Vec3;
  axis_dir: Vec3;
  face_ids: string[];
}

export interface Pocket {
  id: string;
  feature_id: string | null;
  floor_face_id: string;
  floor_z: number;
  top_z: number;
  depth: number;
  size_xy: [number, number];
  center_xy: [number, number];
}

export interface Feature {
  id: string;
  type: "hole" | "pocket" | "fillet" | "chamfer" | "engrave";
  [k: string]: unknown;
}

export interface ModelSummary {
  model_id: string;
  name: string;
  kind: "parametric" | "imported";
  version: number;
  base: Record<string, unknown>;
  bbox: { min: Vec3; max: Vec3; size: Vec3 };
  volume_mm3: number;
  face_count: number;
  features: Feature[];
  holes: Hole[];
  pockets: Pocket[];
  can_undo: boolean;
  log: string[];
}

export interface FaceMesh {
  id: string;
  vertices: number[];
  indices: number[];
  surface: string;
  feature_id: string | null;
  hole_id: string | null;
  pocket_id: string | null;
}

export interface MeshData {
  model_id: string;
  version: number;
  faces: FaceMesh[];
  edges: number[][];
  summary: ModelSummary;
}

export interface FaceInfo {
  face_id: string;
  surface: string;
  area: number;
  center: Vec3;
  normal?: Vec3;
  radius?: number;
  diameter?: number;
  role: string;
  feature_id: string | null;
  hole_id: string | null;
  pocket_id: string | null;
  [k: string]: unknown;
}

/** ⌘/Ctrl ile çizilen bölge aracı ("faces": eski pencere/kesişim yüzey seçimi) */
export type RegionTool = "rect" | "circle" | "lasso" | "polygon" | "faces";
export type RegionShape = Exclude<RegionTool, "faces">;

/** Bir yüzeyin üzerinde seçilmiş bölge (r1, r2 …). Modeli değiştirmez; gerçek yüzey gibi seçilir. */
export interface RegionInfo {
  kind: "region";
  region_id: string;
  shape: RegionShape;
  mode: "plane" | "view";
  face_id: string;
  surface: string;
  face_role: string;
  role: string;
  area: number;
  center: Vec3;
  normal?: Vec3;
  normal_at_center?: Vec3;
  bbox: [Vec3, Vec3];
  pieces: number;
  clipped: boolean | null;
  placement?: { face_id: string; x: number; y: number; length: number; width: number; axes: string };
  circle?: { x: number; y: number; diameter: number };
  outline_xy?: [number, number][];
  radius?: number;
  diameter?: number;
  holes: string[];
  feature_id: string | null;
  hole_id: string | null;
  pocket_id: string | null;
  model_version: number;
  mesh: { vertices: number[]; indices: number[] };
  outline: number[][];
}

/** Arayüzdeki seçim: gerçek bir yüzey veya bir bölge */
export type Selection = FaceInfo | RegionInfo;
export const isRegion = (s: Selection | null | undefined): s is RegionInfo => !!s && (s as RegionInfo).kind === "region";
export const selectionId = (s: Selection) => (isRegion(s) ? s.region_id : s.face_id);

/** Bölge isteği (backend /api/models/current/regions) */
export interface RegionRequest {
  shape: RegionShape;
  face_id: string;
  model_version: number;
  anchor_point: Vec3;
  points?: Vec3[];
  center?: Vec3;
  radius?: number;
  rays?: number[][];
}

export interface Issue {
  severity: "error" | "warning" | "info";
  code: string;
  level: 1 | 2 | 3;
  rule: string;
  line: number | null;
  text: string;
  message: string;
  suggestion: string;
  data: Record<string, unknown>;
}

export type Counts = { error: number; warning: number; info: number };

export interface ProfileSummary {
  id: string;
  name: string;
  controller: string;
  post: string;
}

export interface MachineProfile extends ProfileSummary {
  description: string;
  axes: number;
  travel: Record<"X" | "Y" | "Z", { min: number; max: number }>;
  max_feed: number;
  rapid_feed: number;
  max_spindle: number;
  min_spindle: number;
  tool_changer: boolean;
  max_tools: number;
  tool_change_time_s: number;
  canned_cycles: boolean;
  requires_tool_length_comp: boolean;
  program_number: boolean;
  default_work_offset: Vec3;
  notes: string[];
  manufacturer: string;
  machine_model: string;
  serial: string;
  spindle_power_kw: number | null;
  max_tool_diameter: number | null;
  max_tool_length: number | null;
  coolant_modes: Partial<Record<Coolant, string>>;
  work_offsets: number[];
  post_options: PostOptions;
  builtin: boolean;
  [k: string]: unknown;
}

export interface PostOptions {
  line_numbers: boolean;
  line_increment: number;
  comments: boolean;
  home_at_end: "none" | "G28" | "G53";
  arc_format: "ijk" | "r";
}

export type Coolant = "off" | "flood" | "mist" | "air";
export type OriginXY = "center" | "front_left" | "front_right" | "back_left" | "back_right";

export interface Setup {
  work_offset: Vec3 | null;
  wcs: number;
  origin_xy: OriginXY;
  material: string;
  stock_side: number;
  stock_top: number;
}

export interface Validation {
  valid: boolean;
  counts: Counts;
  levels: Record<string, { name: string; checked: boolean; counts: Counts }>;
  issues: Issue[];
  profile: ProfileSummary;
  setup: { work_offset: Vec3; tool_lengths: Record<string, number> };
  not_checked: string[];
  stats: {
    estimated_time_s: number;
    motion_time_s: number;
    tool_change_time_s: number;
    cut_length_mm: number;
    rapid_length_mm: number;
    line_count: number;
    tool_changes: number;
    tools: number[];
    bbox: { min: Vec3; max: Vec3 } | null;
  };
  disclaimer: string;
}

export interface GcodeRecord {
  gcode_id: string;
  source: string;
  model_id: string | null;
  model_version: number | null;
  plan: PlanItem[];
  warnings: string[];
  validation: Validation;
  line_count: number;
  stale: boolean;
  generated_for: string | null;
  post: string | null;
  /** iş sıfırının model koordinatı (üretilen programlarda) */
  work_origin_model?: Vec3 | null;
  stock_model?: { xmin: number; xmax: number; ymin: number; ymax: number; zmin: number; zmax: number } | null;
  setup?: { wcs: number; origin_xy: OriginXY; material: string; stock_side: number; stock_top: number } | null;
  cutting_mode?: "auto" | "manual" | null;
  text: string;
}

export interface PlanItem {
  op: string;
  feature: string;
  tool: number;
  cycle?: string;
  rpm?: number;
  feed?: number;
  plunge?: number;
  stepdown?: number;
  stepover_ratio?: number;
  peck?: number;
  source?: string;
}

export interface ToolpathSegment {
  kind: "rapid" | "feed" | "arc" | "drill";
  line: number;
  feed: number;
  points: Vec3[];
  duration: number;
}

export interface Toolpath {
  segments: ToolpathSegment[];
  stats: { segment_count: number; estimated_time_s: number; cut_length_mm: number; rapid_length_mm: number };
}

export interface EngraveResult {
  ok: boolean;
  feature_id: string;
  stroke_count: number;
  depth: number;
  width: number;
  extent_xy: { x: [number, number]; y: [number, number] };
}

export interface ChatAction {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  error: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  text: string;
  actions?: ChatAction[];
  selection?: string | null;
}

export type ToolType = "endmill" | "bullmill" | "ballmill" | "drill" | "spot_drill" | "chamfer" | "vbit";

export interface CuttingPreset {
  name: string;
  material: string | null;
  vc: number | null;
  fz: number | null;
  rpm: number | null;
  feed: number | null;
  plunge: number | null;
  stepdown: number | null;
  stepover_ratio: number | null;
}

export interface Tool {
  number: number;
  type: ToolType;
  diameter: number;
  description: string;
  length: number | null;
  angle?: number | null;
  flutes: number;
  flute_length: number | null;
  stickout: number | null;
  shank_diameter: number | null;
  corner_radius: number | null;
  tool_material: "carbide" | "hss";
  coating: string;
  length_offset: number | null;
  diameter_offset: number | null;
  holder: string;
  vendor: string;
  product_id: string;
  presets: CuttingPreset[];
}

export type OpType = "face" | "drill" | "helical" | "pocket" | "engrave" | "contour";

export interface OpSettings {
  enabled: boolean;
  tool: number | null;
  preset: string | null;
  rpm: number | null;
  feed: number | null;
  plunge: number | null;
  stepdown: number | null;
  stepover_ratio: number | null;
  stock_xy: number;
  stock_z: number;
  finish_pass: boolean;
  finish_allowance: number;
  entry: "plunge" | "ramp";
  ramp_angle: number;
  cycle: "auto" | "G81" | "G83";
  peck: number | null;
}

export interface Cutting {
  spindle_rpm: number;
  feed_rate: number;
  plunge_rate: number;
  depth_per_pass: number;
  stepover_ratio: number;
  drill_rpm: number;
  drill_feed: number;
  peck_depth: number;
  safe_z: number;
  retract_z: number;
  breakthrough: number;
  coolant: Coolant;
  [k: string]: number | string | boolean;
}

export interface MachiningConfig {
  profile_id: string;
  setup: Setup;
  tools: Tool[];
  cutting: Cutting;
  cutting_mode: "auto" | "manual";
  operations: OpType[];
  op_settings: Record<OpType, OpSettings>;
  mill_tool: number;
}

export interface Material {
  id: string;
  name: string;
  group: string;
  group_tr: string;
  vc_carbide_mill: number;
  vc_hss_drill: number;
}

export interface CuttingRec {
  material: string;
  material_name: string;
  tool_material: string;
  vc: number;
  fz: number;
  flutes: number;
  rpm: number;
  feed: number;
  plunge: number;
  stepdown: number | null;
  stepover_ratio: number | null;
  peck?: number;
  formula: string;
  notes: string[];
  source: string;
}

export interface AppState {
  model: ModelSummary | null;
  gcode: Omit<GcodeRecord, "text"> | null;
  config: MachiningConfig;
  profile: MachineProfile;
  llm: LlmInfo;
  ui: UiState;
  /** MCP istemcisinin (Claude Desktop, Cursor…) yaptığı değişikliklerin sayacı */
  external?: { rev: number; last: string | null };
}

/** Backend'de oturumla birlikte saklanan arayüz durumu */
export interface UiState {
  messages?: ChatMessage[];
  view?: "model" | "toolpath" | "audit";
  tab?: "model" | "gcode" | "shop" | "settings";
  /** mesajların ait olduğu sohbet (PUT /api/ui) */
  conversation_id?: string;
  /** GET /api/state: aktif sohbet */
  conversation?: ConvSummary;
}

export interface ConvSummary {
  id: string;
  title: string;
  titled: boolean;
  created: string;
  updated: string;
  count: number;
  user_count: number;
  active: boolean;
  preview: string;
}
export interface ConvPayload {
  conversation: ConvSummary;
  messages: ChatMessage[];
  conversations: ConvSummary[];
}

export interface ProfileChange {
  ok: boolean;
  profile: ProfileSummary;
  work_offset: Vec3;
  revalidation?: { gcode_id: string; valid: boolean; counts: Counts };
  note?: string;
  config: MachiningConfig;
}

export interface LlmInfo {
  provider: string;
  model: string;
  available: boolean;
  configured_provider: string;
  configured_model: string;
}

/* ------------------------------------------------------------------ atölye (prove-out) */
export interface ShopState {
  tool: number | null;
  tool_desc: string | null;
  tool_length: number | null;
  motion: string | null;
  feed_mm_min: number | null;
  spindle: { on: boolean; rpm: number | null };
  coolant: boolean;
  tool_length_comp: string;
  position_work: (number | null)[];
  program_ended: boolean;
}
export interface ShopIssue {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  suggestion?: string;
}
export interface LineState {
  gcode_id: string;
  line: number;
  text: string;
  comment_only: boolean;
  before: ShopState;
  after: ShopState;
  changes: string[];
  move?: {
    kind: string;
    kind_tr: string;
    end_work: (number | null)[];
    end_machine: (number | null)[];
    length_mm: number | null;
    feed_mm_min: number | null;
    end_above_top_mm: number | null;
    passes_through_finished_part: boolean;
  };
  issues: ShopIssue[];
  warnings: string[];
  summary: string;
}
export interface Checklist {
  gcode_id: string;
  preparation: string[];
  items: { line: number; kind: string; severity: "error" | "warning" | "info"; message: string; action: string }[];
  counts: { error: number; warning: number; info: number };
}
export interface AlarmEntry {
  code: string;
  title: string;
  description_en: string;
  meaning_tr: string | null;
  actions: string[];
  related_rules: string[];
}
export interface AlarmResult {
  controller: string;
  found: boolean;
  alarm?: AlarmEntry;
  note?: string;
  source: { title: string; url: string } | null;
  found_in_other?: { controller: string; code: string; title: string }[];
  matching_issues?: ShopIssue[];
  line_state?: { line: number; text: string; summary: string };
}
export interface WearResult {
  action: "none" | "tool" | "length_wear" | "radius_wear" | "cam";
  error_mm: number;
  message: string;
  warning?: string;
}
export interface ProgramDiff {
  base_id: string;
  edited_id: string;
  change_count: number;
  counts: { error: number; warning: number; info: number };
  changes: { base_line: number | null; edited_line: number | null; old: string | null; new: string | null; severity: string; risks: string[] }[];
  note: string;
}

/* ------------------------------------------------------------------ LLM audit */
export interface AuditSummary {
  id: string;
  seq: number;
  ts: string;
  kind: string;
  client: string | null;
  provider: string | null;
  model: string | null;
  message: string;
  reply: string;
  error: string | null;
  tool_count: number;
  tool_fail: number;
  tools: string[];
  duration_ms: number | null;
  usage: { input_tokens?: number; output_tokens?: number } | null;
  rounds: number;
}
export interface AuditChain {
  ok: boolean;
  count: number;
  last_hash?: string;
  broken_at_seq?: number | null;
  broken_at_index?: number;
  message: string;
}
export interface AuditList {
  total: number;
  all: number;
  offset: number;
  entries: AuditSummary[];
  chain: AuditChain;
}
export interface AuditEntry {
  id: string;
  seq: number;
  ts: string;
  kind: string;
  client: string | null;
  provider: string | null;
  model: string | null;
  system_prompt_sha256?: string | null;
  tools_sha256?: string;
  state?: { model_id: string | null; model_version: number | null; gcode_id: string | null; machine_profile: string | null };
  input?: { message?: string; selection?: string[]; context?: string };
  rounds?: {
    round: number;
    latency_ms: number;
    stop_reason: string | null;
    response_id?: string | null;
    usage: { input_tokens?: number | null; output_tokens?: number | null } | null;
    text: string;
    reasoning?: string | null;
    tool_requests: { id: string; name: string; input: unknown }[];
  }[];
  tool_calls?: { name: string; args: Record<string, unknown>; ok: boolean; error: string | null; duration_ms: number | null; result: unknown }[];
  output?: { reply: string | null; error: string | null };
  duration_ms?: number;
  usage?: Record<string, number> | null;
  prev_hash: string;
  hash: string;
}
