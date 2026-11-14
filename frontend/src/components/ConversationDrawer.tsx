import { tx, useLocale } from "../i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ConvSummary } from "../types";

/** Soldan kayan sohbet çekmecesi: sohbet listesi, yeni sohbet, geçiş, yeniden adlandırma, silme. */

const DAY = 864e5;
function groupOf(iso: string): string {
  const d = new Date(iso);
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const t = d.getTime();
  if (t >= start.getTime()) return tx("Bugün");
  if (t >= start.getTime() - DAY) return tx("Dün");
  if (t >= start.getTime() - 7 * DAY) return tx("Son 7 gün");
  if (t >= start.getTime() - 30 * DAY) return tx("Son 30 gün");
  return tx("Daha eski");
}
const ORDER = () => ([tx("Bugün"), tx("Dün"), tx("Son 7 gün"), tx("Son 30 gün"), tx("Daha eski")]);
const hm = (iso: string) => {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  const p = (n: number) => String(n).padStart(2, "0");
  return today ? `${p(d.getHours())}:${p(d.getMinutes())}` : `${p(d.getDate())}.${p(d.getMonth() + 1)}`;
};

const Icon = {
  plus: <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M8 3v10M3 8h10" /></svg>,
  edit: <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"><path d="M10.5 2.5l3 3L6 13H3v-3z" /></svg>,
  trash: <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.7 8.5h5.6l.7-8.5" /></svg>,
  close: <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M4 4l8 8M12 4l-8 8" /></svg>,
};

function Item({ c, busy, onSelect, onRename, onDelete }: {
  c: ConvSummary;
  busy: boolean;
  onSelect: () => void;
  onRename: (t: string) => void;
  onDelete: () => void;
}) {
  useLocale();
  const [mode, setMode] = useState<"view" | "edit" | "confirm">("view");
  const [title, setTitle] = useState(c.title);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (mode === "edit") input.current?.select();
  }, [mode]);
  useEffect(() => setTitle(c.title), [c.title]);
  const save = () => {
    const t = title.trim();
    if (t && t !== c.title) onRename(t);
    setMode("view");
  };
  return (
    <li className={`conv ${c.active ? "active" : ""} ${mode}`}>
      {mode === "edit" ? (
        <input
          ref={input}
          className="conv-edit"
          value={title}
          maxLength={80}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") {
              e.stopPropagation();
              setTitle(c.title);
              setMode("view");
            }
          }}
          onBlur={save}
          aria-label={tx("Sohbet adı")}
        />
      ) : (
        <button className="conv-main" disabled={busy && !c.active} onClick={onSelect} title={c.preview || c.title}>
          <span className={`conv-title ${c.titled ? "" : "untitled"}`}>{c.titled ? c.title : tx("Yeni sohbet")}</span>
          <span className="conv-meta">
            {c.user_count > 0 ? tx("{0} mesaj", {"0": c.user_count}) : tx("boş")} · {hm(c.updated)}
          </span>
        </button>
      )}
      {mode === "confirm" ? (
        <div className="conv-confirm">
          <span>{tx("Silinsin mi?")}</span>
          <button className="btn small danger-ghost" onClick={onDelete}>
             {tx("Sil")} </button>
          <button className="btn ghost small" onClick={() => setMode("view")}>
             {tx("Vazgeç")} </button>
        </div>
      ) : (
        mode === "view" && (
          <div className="conv-actions">
            <button className="icon-btn" title={tx("Yeniden adlandır")} onClick={() => setMode("edit")}>
              {Icon.edit}
            </button>
            <button className="icon-btn" title={tx("Sil")} disabled={busy} onClick={() => setMode("confirm")}>
              {Icon.trash}
            </button>
          </div>
        )
      )}
    </li>
  );
}

export default function ConversationDrawer({
  open,
  conversations,
  busy,
  onClose,
  onNew,
  onSelect,
  onRename,
  onDelete,
}: {
  open: boolean;
  conversations: ConvSummary[];
  busy: boolean;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const { locale } = useLocale();
  const [q, setQ] = useState("");
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => search.current?.focus(), 180);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("keydown", esc);
    };
  }, [open, onClose]);

  const groups = useMemo(() => {
    const ql = q.trim().toLocaleLowerCase(locale);
    const rows = ql ? conversations.filter((c) => `${c.title} ${c.preview}`.toLocaleLowerCase(locale).includes(ql)) : conversations;
    const g = new Map<string, ConvSummary[]>();
    rows.forEach((c) => {
      const k = groupOf(c.updated);
      g.set(k, [...(g.get(k) ?? []), c]);
    });
    return ORDER().filter((k) => g.has(k)).map((k) => [k, g.get(k)!] as const);
  }, [conversations, q, locale]);

  return (
    <>
      <div className={`drawer-backdrop ${open ? "open" : ""}`} onClick={onClose} aria-hidden />
      <aside className={`drawer ${open ? "open" : ""}`} aria-hidden={!open} aria-label={tx("Sohbetler")}>
        <div className="drawer-head">
          <span>{tx("Sohbetler")}</span>
          <button className="icon-btn" onClick={onClose} title={tx("Kapat (Esc)")}>
            {Icon.close}
          </button>
        </div>
        <button
          className="btn primary drawer-new"
          disabled={busy}
          onClick={() => {
            onNew();
            onClose();
          }}
        >
          {Icon.plus}  {tx("Yeni sohbet")} </button>
        <input ref={search} className="drawer-search" placeholder={tx("Sohbetlerde ara")} value={q} onChange={(e) => setQ(e.target.value)} tabIndex={open ? 0 : -1} />
        <div className="drawer-list">
          {groups.length === 0 && <div className="muted small pad">{tx("Eşleşen sohbet yok.")}</div>}
          {groups.map(([g, list]) => (
            <section key={g}>
              <div className="drawer-group">{g}</div>
              <ul>
                {list.map((c) => (
                  <Item
                    key={c.id}
                    c={c}
                    busy={busy}
                    onSelect={() => {
                      if (!c.active) onSelect(c.id);
                      onClose();
                    }}
                    onRename={(t) => onRename(c.id, t)}
                    onDelete={() => onDelete(c.id)}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
        <div className="drawer-foot small muted">{conversations.length}  {tx("sohbet · her birinin geçmişi ayrı tutulur")}</div>
      </aside>
    </>
  );
}
