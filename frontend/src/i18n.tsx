import { createContext, useContext, useEffect, useSyncExternalStore, type ReactNode } from 'react';
import { en, tr } from './catalogs';

export type Locale = 'tr' | 'en';
export const LOCALE_KEY = 'busproje.locale';
export const normalizeLocale = (value: unknown): Locale => value === 'en' ? 'en' : 'tr';
export function readLocale(): Locale {
  try { return normalizeLocale(window.localStorage.getItem(LOCALE_KEY)); } catch { return 'tr'; }
}
let current: Locale = readLocale();
const listeners = new Set<() => void>();
export const getLocale = () => current;
export function setLocale(value: Locale) {
  current = normalizeLocale(value);
  try { window.localStorage.setItem(LOCALE_KEY, current); } catch { /* private browsing: keep in memory */ }
  listeners.forEach(listener => listener());
}
export function translate(locale: Locale, key: string, values: Record<string, unknown> = {}): string {
  const catalog = locale === 'en' ? en : tr;
  const phrase = Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : Object.prototype.hasOwnProperty.call(tr, key) ? tr[key] : key;
  return phrase.replace(/\{(\w+)\}/g, (token, name: string) => Object.prototype.hasOwnProperty.call(values, name) ? String(values[name] ?? "—") : token);
}
/** Stable function identity: changing language must not retrigger model-loading effects. */
export const tx = (key: string, values?: Record<string, unknown>) => translate(current, key, values);
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const LocaleContext = createContext<Locale | null>(null);
export function LocaleProvider({ children }: { children: ReactNode }) {
  const locale = useSyncExternalStore(subscribe, getLocale, () => 'tr' as Locale);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  useEffect(() => {
    const change = (event: StorageEvent) => { if (event.key === LOCALE_KEY || event.key === null) setLocale(readLocale()); };
    window.addEventListener('storage', change);
    return () => window.removeEventListener('storage', change);
  }, []);
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}
/** External-store subscription also works inside the separate React Three Fiber root. */
export function useLocale() {
  const providedLocale = useContext(LocaleContext);
  const fallbackLocale = useSyncExternalStore(subscribe, getLocale, () => 'tr' as Locale);
  return { locale: providedLocale ?? fallbackLocale, setLocale, t: tx };
}
export function LanguageSelector() {
  const { locale } = useLocale();
  return <label className="locale-selector"><span>{locale === 'tr' ? 'Dil' : 'Language'}</span><select aria-label={locale === 'tr' ? 'Dil' : 'Language'} value={locale} onChange={event => setLocale(normalizeLocale(event.target.value))}><option value="tr">Türkçe</option><option value="en">English</option></select></label>;
}
