import { Fragment, type ReactNode } from "react";

/**
 * Sohbet mesajları için küçük, güvenli markdown çizici (HTML enjekte etmez; yalnızca React düğümleri üretir).
 * Destek: paragraf, **kalın**, *italik*, `kod`, ``` kod bloğu ```, - / • / 1. listeler, # başlık, > alıntı, [bağlantı](url).
 * Ayrıca CAD/CNC kimlikleri biçimlenir: hole_1, r2, f5 (tıklanabilir), G43 / T1 / PS0010 gibi kodlar (eş aralıklı),
 * "30. satır" / "satır 30" / "N30" satır referansları (tıklanınca o satıra gider).
 */

export interface MdHandlers {
  onEntity?: (id: string) => void;
  onLine?: (line: number) => void;
}

const ENTITY = String.raw`\b(?:hole|pocket|engrave|fillet|chamfer)_\d+\b|\b[rf]\d+\b`;
const CODE = String.raw`\b(?:PS|OT|SV|SP)\d{4}\b|\berror:\d+\b|\bALARM:\d+\b|\b[GMTHD]\d{1,3}(?:\.\d)?\b|Ø\s?\d+(?:[.,]\d+)?`;
const LINE = String.raw`\b(\d{1,5})\.\s?satır\w*|\bsatır\s(\d{1,5})\b|\bN(\d{1,5})\b`;
const INLINE = new RegExp(
  String.raw`(\x60[^\x60]+\x60)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\((?:https?:\/\/|\/)[^)\s]+\))|(${LINE})|(${ENTITY})|(${CODE})`,
  "g",
);

function inline(text: string, h: MdHandlers, key = ""): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE)) {
    const i = m.index ?? 0;
    if (i > last) out.push(text.slice(last, i));
    const t = m[0];
    const id = `${key}-${k++}`;
    if (m[1]) out.push(<code key={id} className="md-code">{t.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={id}>{inline(t.slice(2, -2), h, id)}</strong>);
    else if (m[3]) out.push(<em key={id}>{inline(t.slice(1, -1), h, id)}</em>);
    else if (m[4]) {
      const mm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(t)!;
      out.push(
        <a key={id} href={mm[2]} target="_blank" rel="noreferrer">
          {mm[1]}
        </a>,
      );
    } else if (m[5]) {
      const n = Number(m[6] ?? m[7] ?? m[8]);
      out.push(
        h.onLine ? (
          <button key={id} className="md-line" onClick={() => h.onLine!(n)} title={`${n}. satırı takım yolunda göster`}>
            {t}
          </button>
        ) : (
          <span key={id} className="md-line">{t}</span>
        ),
      );
    } else if (m[9]) {
      out.push(
        h.onEntity ? (
          <button key={id} className="md-entity" onClick={() => h.onEntity!(t)} title={`${t} seç`}>
            {t}
          </button>
        ) : (
          <span key={id} className="md-entity">{t}</span>
        ),
      );
    } else out.push(<span key={id} className="md-gword">{t}</span>);
    last = i + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { t: "p"; lines: string[] }
  | { t: "h"; level: number; text: string }
  | { t: "ul" | "ol"; items: string[] }
  | { t: "code"; lang: string; lines: string[] }
  | { t: "quote"; lines: string[] };

function blocks(src: string): Block[] {
  const out: Block[] = [];
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  const listRe = /^\s*(?:[-*•]|\d+[.)])\s+/;
  while (i < lines.length) {
    const l = lines[i];
    const fence = /^\s*```(\w*)/.exec(l);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push({ t: "code", lang: fence[1], lines: body });
    } else if (!l.trim()) {
      i++;
    } else if (/^#{1,4}\s/.test(l)) {
      out.push({ t: "h", level: l.indexOf(" "), text: l.replace(/^#+\s/, "") });
      i++;
    } else if (/^\s*>\s?/.test(l)) {
      const q: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push({ t: "quote", lines: q });
    } else if (listRe.test(l)) {
      const ordered = /^\s*\d+[.)]\s/.test(l);
      const items: string[] = [];
      while (i < lines.length && listRe.test(lines[i])) {
        let item = lines[i++].replace(listRe, "");
        // devam satırları (girintili, liste işareti olmayan)
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !listRe.test(lines[i])) item += " " + lines[i++].trim();
        items.push(item);
      }
      out.push({ t: ordered ? "ol" : "ul", items });
    } else {
      const p: string[] = [];
      while (i < lines.length && lines[i].trim() && !listRe.test(lines[i]) && !/^\s*(```|#{1,4}\s|>)/.test(lines[i])) p.push(lines[i++]);
      out.push({ t: "p", lines: p });
    }
  }
  return out;
}

export default function Markdown({ text, handlers = {} }: { text: string; handlers?: MdHandlers }) {
  return (
    <div className="md">
      {blocks(text).map((b, i) => {
        const k = String(i);
        switch (b.t) {
          case "h":
            return <div key={k} className={`md-h md-h${b.level}`}>{inline(b.text, handlers, k)}</div>;
          case "ul":
          case "ol": {
            const L = b.t;
            return (
              <L key={k}>
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it, handlers, `${k}.${j}`)}</li>
                ))}
              </L>
            );
          }
          case "code":
            return (
              <pre key={k} className="md-pre">
                <code>{b.lines.join("\n")}</code>
              </pre>
            );
          case "quote":
            return <blockquote key={k}>{b.lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l, handlers, `${k}.${j}`)}</Fragment>)}</blockquote>;
          default:
            return (
              <p key={k}>
                {b.lines.map((l, j) => (
                  <Fragment key={j}>
                    {j > 0 && <br />}
                    {inline(l, handlers, `${k}.${j}`)}
                  </Fragment>
                ))}
              </p>
            );
        }
      })}
    </div>
  );
}
