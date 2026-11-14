import type { SelectionInfo } from "./mesh";

export type Locale = "tr" | "en";
export const LANGUAGE_KEY = "busproje.viewer.language";

const tr = {
  title: "3B model görüntüleyici",
  language: "Dil",
  loading: "Model yükleniyor…",
  hostRequired: "Bu sayfa bir MCP istemcisinin (ChatGPT, Claude…) içinde açılmalı.",
  modelError: "Model alınamadı.",
  modelErrorDetail: "Model alınamadı: {detail}",
  missingMesh: "Sunucu mesh verisi göndermedi.",
  toolError: "Araç hata döndürdü.",
  selectionError: "Seçim gönderilemedi: {detail}",
  selectionToolError: "Seçim gönderilemedi.",
  version: "sürüm",
  refresh: "Yenile",
  refreshTitle: "Modeli yeniden al",
  fullscreen: "Tam ekran",
  exitFullscreen: "Küçült",
  selected: "seçildi",
  selectionHint: 'Sohbette "seçtiğim yüzey" diyerek bu yüzey üzerinde işlem isteyebilirsiniz.',
  hole: "delik",
  pocket: "cep",
  feature: "kazıma / özellik",
  controlsHint: "Döndür: sürükle · Yakınlaş: tekerlek · Seç: yüzeye tıkla",
  dismissError: "Hatayı kapat",
  selectionNotice: 'Kullanıcı 3B görüntüleyicide {face} yüzeyini seçti: {what} (model sürümü {version}). "Seçtiğim yüzey/delik" derse bunu kastediyor; gerekirse get_selection ile doğrula.',
  selectionCleared: "Kullanıcı görüntüleyicide seçimi kaldırdı.",
};
export type MessageKey = keyof typeof tr;
const en: Record<MessageKey, string> = {
  title: "3D model viewer",
  language: "Language",
  loading: "Loading model…",
  hostRequired: "This page must be opened inside an MCP client (ChatGPT, Claude…).",
  modelError: "Could not load model.",
  modelErrorDetail: "Could not load model: {detail}",
  missingMesh: "The server did not send mesh data.",
  toolError: "The tool returned an error.",
  selectionError: "Could not send selection: {detail}",
  selectionToolError: "Could not send selection.",
  version: "version",
  refresh: "Refresh",
  refreshTitle: "Reload model",
  fullscreen: "Fullscreen",
  exitFullscreen: "Exit fullscreen",
  selected: "selected",
  selectionHint: 'In chat, say "the face I selected" to request an operation on this face.',
  hole: "hole",
  pocket: "pocket",
  feature: "engraving / feature",
  controlsHint: "Rotate: drag · Zoom: wheel · Select: click a face",
  dismissError: "Dismiss error",
  selectionNotice: 'The user selected face {face} in the 3D viewer: {what} (model version {version}). If they say "the face/hole I selected", this is what they mean; verify with get_selection if needed.',
  selectionCleared: "The user cleared the selection in the viewer.",
};
export const catalogs = { tr, en };

export function translate(locale: Locale, key: MessageKey, values: Record<string, string | number> = {}): string {
  // A single pass keeps user-supplied braces and replacement symbols unchanged.
  return catalogs[locale][key].replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : placeholder,
  );
}

export function describeSelection(locale: Locale, selection: SelectionInfo | null, version: number): string {
  return selection
    ? translate(locale, "selectionNotice", { face: selection.face_id, what: selection.role ?? selection.face_id, version })
    : translate(locale, "selectionCleared");
}

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;
export function createLocalePreference(storage: () => PreferenceStorage = () => window.localStorage) {
  let memory: Locale | undefined;
  return {
    read(): Locale {
      if (memory) return memory;
      try { memory = storage().getItem(LANGUAGE_KEY) === "en" ? "en" : "tr"; }
      catch { memory = "tr"; }
      return memory;
    },
    write(locale: Locale) {
      memory = locale;
      // Sandboxed/opaque origins and quota/privacy restrictions can deny storage.
      // Keep working in memory; persistence across reloads requires host permission.
      try { storage().setItem(LANGUAGE_KEY, locale); } catch { /* memory fallback */ }
    },
  };
}
