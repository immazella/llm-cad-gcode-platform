import { Fragment, useEffect, useRef, useState } from "react";

/** Asistanın arayüzde görünen adı */
export const ASSISTANT_NAME = "Toni Kross";
import Markdown from "./Markdown";
import type { ChatAction, ChatMessage, Selection } from "../types";
import { isRegion, selectionId } from "../types";

const EXAMPLES = [
  "60 × 60 × 10 mm boyutlarında kare bir plaka oluştur. Merkezine 10 mm yarıçaplı bir delik aç.",
  "Köşelerden 10 mm içeride dört adet 6 mm çaplı delik aç.",
  "Sağ üst bölgeye 20x15 mm, 4 mm derinliğinde, köşe yarıçapı 3 mm bir cep aç.",
  "Seçtiğim deliğin yarıçapını 15 mm yap.",
  "Seçtiğim bölgeye 3 mm derinliğinde cep aç.",
  "Bu parça için G-code üret ve sonucu açıkla.",
];

const TOOL_LABELS: Record<string, string> = {
  create_box: "kutu oluştur",
  create_cylinder: "silindir oluştur",
  add_hole: "delik ekle",
  add_pocket: "cep ekle",
  add_fillet: "kavis",
  add_chamfer: "pah",
  modify_base: "gövde ölçüsü",
  modify_hole: "delik düzenle",
  modify_feature: "özellik düzenle",
  delete_feature: "sil",
  get_geometry_info: "geometri bilgisi",
  get_holes: "delikleri listele",
  get_face_info: "yüzey bilgisi",
  undo: "geri al",
  export_step: "STEP dışa aktar",
  generate_gcode: "G-code üret",
  validate_gcode: "G-code doğrula",
  list_machine_profiles: "makine profilleri",
  set_machine_profile: "makine profili seç",
  set_tool_length: "takım boyu",
  add_engraving_text: "yazı kazı",
  add_engraving_path: "yol kazı",
  get_line_state: "satır durumu",
  prove_out_checklist: "prove-out listesi",
  explain_alarm: "alarm açıklama",
  suggest_wear_offset: "ölçü düzeltme",
  diff_programs: "program farkı",
};

function argText(args: Record<string, unknown>) {
  return Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(", ");
}

function valText(v: unknown): { text: string; kind: string; full?: string } {
  if (typeof v === "string") return { text: `"${v}"`, kind: "str" };
  if (typeof v === "number" || typeof v === "boolean") return { text: String(v), kind: "num" };
  const full = JSON.stringify(v);
  return full.length > 48 ? { text: full.slice(0, 45) + "…", kind: "obj", full } : { text: full, kind: "obj" };
}

/** Asistanın çağırdığı araç: fonksiyon imzası gibi, eş aralıklı yazıyla */
function ToolCall({ a }: { a: ChatAction }) {
  const args = Object.entries(a.args).filter(([, v]) => v !== null && v !== undefined);
  return (
    <div className={`fn ${a.ok ? "ok" : "fail"}`} title={argText(a.args)}>
      <span className="fn-status" aria-label={a.ok ? "başarılı" : "başarısız"}>{a.ok ? "✓" : "✕"}</span>
      <code className="fn-sig">
        <span className="fn-name">{a.tool}</span>
        <span className="fn-punc">(</span>
        {args.map(([k, v], i) => {
          const t = valText(v);
          return (
            <Fragment key={k}>
              {i > 0 && <span className="fn-punc">, </span>}
              <span className="fn-key">{k}</span>
              <span className="fn-punc">=</span>
              <span className={`fn-val ${t.kind}`} title={t.full}>
                {t.text}
              </span>
            </Fragment>
          );
        })}
        <span className="fn-punc">)</span>
      </code>
      {TOOL_LABELS[a.tool] && <span className="fn-label">{TOOL_LABELS[a.tool]}</span>}
      {a.error && <div className="fn-err">{a.error}</div>}
    </div>
  );
}

function ToolCalls({ actions }: { actions: ChatAction[] }) {
  const fails = actions.filter((a) => !a.ok).length;
  return (
    <details className="fns" open={actions.length <= 3 || fails > 0}>
      <summary>
        <span className="fns-icon">ƒ</span> {actions.length} araç çağrısı
        {fails > 0 && <span className="fns-fail"> · {fails} başarısız</span>}
      </summary>
      <div className="fns-list">
        {actions.map((a, j) => (
          <ToolCall key={j} a={a} />
        ))}
      </div>
    </details>
  );
}

export default function ChatPanel({
  messages,
  busy,
  selection,
  onRemove,
  onClearSelection,
  onSend,
  onReset,
  onEntity,
  onLine,
  title,
  conversationCount,
  onOpenDrawer,
}: {
  messages: ChatMessage[];
  busy: boolean;
  selection: Selection[];
  onRemove: (id: string) => void;
  onClearSelection: () => void;
  onSend: (text: string) => void;
  onReset: () => void;
  onEntity?: (id: string) => void;
  onLine?: (line: number) => void;
  title: string;
  conversationCount: number;
  onOpenDrawer: () => void;
}) {
  const [text, setText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const send = () => {
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  };

  return (
    <div className="chat">
      <div className="panel-head chat-head">
        <button className="drawer-toggle" onClick={onOpenDrawer} title="Sohbetler" aria-label="Sohbet listesini aç">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <path d="M2.5 4h11M2.5 8h11M2.5 12h7" />
          </svg>
          {conversationCount > 1 && <span className="drawer-count">{conversationCount}</span>}
        </button>
        <div className="chat-head-title">
          <span lang="en">{ASSISTANT_NAME}</span>
          <span className="chat-conv-title" title={title}>{title}</span>
        </div>
        <button className="btn ghost small" onClick={onReset} title="Yeni bir sohbet başlat (bu sohbet çekmecede kalır)">
          + Yeni sohbet
        </button>
      </div>
      <div className="chat-list" ref={listRef}>
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>Parçayı doğal dille tarif edin, mevcut bir STEP dosyası yükleyin ya da 3D görünümde bir yüzeye tıklayıp onu düzenlemesini isteyin.</p>
            <div className="examples">
              {EXAMPLES.map((e) => (
                <button key={e} className="example" onClick={() => setText(e)}>
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.role === "assistant" && <div className="msg-role" lang="en">{ASSISTANT_NAME}</div>}
            {m.selection && <div className="msg-sel">⌖ {m.selection}</div>}
            {m.actions && m.actions.length > 0 && <ToolCalls actions={m.actions} />}
            {m.role === "assistant" ? (
              <Markdown text={m.text} handlers={{ onEntity, onLine }} />
            ) : (
              <div className="msg-text">{m.text}</div>
            )}
          </div>
        ))}
        {busy && (
          <div className="msg assistant">
            <div className="typing">
              <i />
              <i />
              <i />
            </div>
          </div>
        )}
      </div>
      <div className="chat-input">
        {selection.length === 1 && (
          <div className="sel-chip">
            <span>
              {isRegion(selection[0]) ? "▭" : "⌖"} <b>{selectionId(selection[0])}</b> — {selection[0].role}
            </span>
            <button onClick={onClearSelection} title="Seçimi kaldır">
              ×
            </button>
          </div>
        )}
        {selection.length > 1 && (
          <div className="sel-chip multi">
            <span className="sel-count">{selection.length} eleman</span>
            <span className="sel-tags">
              {selection.map((x) => (
                <span key={selectionId(x)} className="sel-tag" title={x.role}>
                  {isRegion(x) ? "▭" : "⌖"} {selectionId(x)}
                  <button onClick={() => onRemove(selectionId(x))} title="Seçimden çıkar">
                    ×
                  </button>
                </span>
              ))}
            </span>
            <button onClick={onClearSelection} title="Seçimi bırak">
              ×
            </button>
          </div>
        )}
        <textarea
          value={text}
          placeholder={selection.length > 1 ? "Seçili elemanların hepsi için komut yazın… (ör. seçtiğim bölgelere 2 mm cep aç)" : selection.length ? (isRegion(selection[0]) ? "Seçili bölge için komut yazın… (ör. bu bölgeye 3 mm cep aç)" : "Seçili eleman için komut yazın… (ör. çapını 12 mm yap)") : "Komut yazın… (Enter gönderir, Shift+Enter yeni satır)"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={3}
        />
        <button className="btn primary send" onClick={send} disabled={busy || !text.trim()}>
          Gönder
        </button>
      </div>
    </div>
  );
}
