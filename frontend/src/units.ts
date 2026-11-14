/**
 * Görünüm ölçü birimi (ızgara etiketleri ve 3B görünümdeki ölçü yazıları).
 *
 * Yalnızca GÖSTERİMİ değiştirir: model, API, LLM ve G-code her zaman mm ile çalışır.
 * Seçim tarayıcıda hatırlanır; Canvas içindeki bileşenler de okuyabilsin diye
 * React bağlamı yerine küçük bir dış depo (useSyncExternalStore) kullanılır.
 */
import { useSyncExternalStore } from "react";

export type Unit = "mm" | "cm" | "m" | "in";

interface UnitDef {
  /** kısa yazım (etiketlerde) */
  label: string;
  /** seçicide görünen ad */
  name: string;
  /** 1 birim kaç mm */
  mm: number;
  /** ondalık basamak (ölçü yazılarında) */
  digits: number;
  /** [ana çizgi, hücre] aralıkları, bu birim cinsinden; yakınlaştırmaya göre seçilir */
  steps: [number, number][];
}

export const UNITS: Record<Unit, UnitDef> = {
  mm: {
    label: "mm", name: "Milimetre", mm: 1, digits: 2,
    steps: [[1, 0.2], [2, 0.5], [5, 1], [10, 2], [25, 5], [50, 10], [100, 20], [250, 50], [500, 100], [1000, 200], [2500, 500]],
  },
  cm: {
    label: "cm", name: "Santimetre", mm: 10, digits: 2,
    steps: [[0.1, 0.02], [0.2, 0.05], [0.5, 0.1], [1, 0.2], [2, 0.5], [5, 1], [10, 2], [25, 5], [50, 10], [100, 20], [250, 50]],
  },
  m: {
    label: "m", name: "Metre", mm: 1000, digits: 4,
    steps: [[0.001, 0.0002], [0.002, 0.0005], [0.005, 0.001], [0.01, 0.002], [0.025, 0.005], [0.05, 0.01], [0.1, 0.02], [0.25, 0.05], [0.5, 0.1], [1, 0.2], [2.5, 0.5]],
  },
  in: {
    label: "in", name: "İnç", mm: 25.4, digits: 3,
    steps: [[1 / 32, 1 / 128], [1 / 16, 1 / 64], [1 / 8, 1 / 32], [1 / 4, 1 / 16], [1 / 2, 1 / 8], [1, 1 / 4], [2, 1 / 2], [5, 1], [10, 2], [25, 5], [50, 10], [100, 20]],
  },
};
export const UNIT_ORDER: Unit[] = ["mm", "cm", "m", "in"];

const KEY = "busproje.unit";
let current: Unit = (() => {
  try {
    const u = localStorage.getItem(KEY);
    if (u && u in UNITS) return u as Unit;
  } catch {
    /* depolama kullanılamıyor */
  }
  return "mm";
})();
const listeners = new Set<() => void>();

export function setUnit(u: Unit) {
  if (u === current) return;
  current = u;
  try {
    localStorage.setItem(KEY, u);
  } catch {
    /* yok say */
  }
  listeners.forEach((l) => l());
}
export const getUnit = () => current;

export function useUnit(): Unit {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getUnit,
    getUnit,
  );
}

/** İnç değerini kesirle yazar: 0.25 → "1/4", 1.5 → "1 1/2" (1/64'e kadar; değilse ondalık) */
function inchText(v: number): string {
  const sign = v < -1e-9 ? "-" : "";
  const a = Math.abs(v);
  let whole = Math.floor(a + 1e-9);
  const n64 = (a - whole) * 64;
  let n = Math.round(n64);
  if (Math.abs(n64 - n) > 1e-6) return sign + a.toFixed(3);
  if (n === 64) {
    whole += 1;
    n = 0;
  }
  if (n === 0) return sign + whole;
  let den = 64;
  while (n % 2 === 0) {
    n /= 2;
    den /= 2;
  }
  return sign + (whole ? `${whole} ${n}/${den}` : `${n}/${den}`);
}

/** Izgara etiketi: mm cinsinden konumu seçili birimde, gereksiz sıfırlar olmadan yazar */
export function fmtGridValue(mm: number, unit: Unit): string {
  const v = mm / UNITS[unit].mm;
  if (unit === "in") return inchText(v);
  return String(+v.toFixed(5));
}

/** Uzunluk: mm cinsinden değeri seçili birimde, birimiyle yazar ("12.50 mm", "0.492 in") */
export function fmtLen(mm: number, unit: Unit, withUnit = true): string {
  const d = UNITS[unit];
  const v = mm / d.mm;
  const digits = Math.abs(v) >= 100 ? Math.max(1, d.digits - 1) : d.digits;
  return v.toFixed(digits) + (withUnit ? ` ${d.label}` : "");
}
