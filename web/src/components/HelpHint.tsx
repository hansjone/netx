import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Props = {
  text: string;
  ariaLabel: string;
  align?: "start" | "end";
  nowrap?: boolean;
};

const VIEWPORT_MARGIN = 8;
const POPOVER_MAX_WIDTH = 420;

type HelpBlock =
  | { kind: "title"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "para"; text: string };

const SECTION_RE = /^[【\[](.+?)[】\]]\s*$/;
const BULLET_RE = /^[·•\-\*]\s+(.+)$/;
/** Highlight field names / env keys / templates — skip short ALLCAPS acronyms (UME, DSH). */
const CODEISH_RE =
  /(`[^`]+`|\{[A-Za-z0-9_./:@-]+\}|[A-Z][A-Z0-9_]{5,}(?::[a-z]+)?|[a-z]+(?:_[a-z0-9]+)+|[a-z]+[A-Z][A-Za-z0-9]*)/g;

function parseHelpBlocks(raw: string): HelpBlock[] {
  const lines = String(raw || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.trimEnd());
  const blocks: HelpBlock[] = [];
  let listBuf: string[] = [];
  let paraBuf: string[] = [];

  const flushList = () => {
    if (!listBuf.length) return;
    blocks.push({ kind: "list", items: listBuf });
    listBuf = [];
  };
  const flushPara = () => {
    if (!paraBuf.length) return;
    blocks.push({ kind: "para", text: paraBuf.join(" ").trim() });
    paraBuf = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      flushPara();
      continue;
    }
    const section = trimmed.match(SECTION_RE);
    if (section) {
      flushList();
      flushPara();
      blocks.push({ kind: "title", text: section[1].trim() });
      continue;
    }
    const bullet = trimmed.match(BULLET_RE);
    if (bullet) {
      flushPara();
      listBuf.push(bullet[1].trim());
      continue;
    }
    flushList();
    paraBuf.push(trimmed);
  }
  flushList();
  flushPara();
  return blocks;
}

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  const re = new RegExp(CODEISH_RE.source, "g");
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const raw = m[0];
    const body = raw.startsWith("`") && raw.endsWith("`") ? raw.slice(1, -1) : raw;
    nodes.push(
      <code key={`${m.index}-${body}`} className="help-hint__code">
        {body}
      </code>,
    );
    last = m.index + raw.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Long single paragraphs → clause list so plain help is still scannable. */
function expandPlainPara(text: string): HelpBlock[] {
  const parts = text
    .split(/(?<=[。；;])\s*/)
    .map((s) => s.replace(/[。；;]\s*$/, "").trim())
    .filter(Boolean);
  if (parts.length >= 2 && text.length >= 48) {
    return [{ kind: "list", items: parts }];
  }
  return [{ kind: "para", text }];
}

function HelpBody({ text, nowrap }: { text: string; nowrap: boolean }) {
  const blocks = useMemo(() => {
    const parsed = parseHelpBlocks(text);
    if (nowrap) return parsed;
    if (parsed.length === 1 && parsed[0].kind === "para") {
      return expandPlainPara(parsed[0].text);
    }
    return parsed.flatMap((b) => (b.kind === "para" && b.text.length >= 96 ? expandPlainPara(b.text) : [b]));
  }, [text, nowrap]);

  if (nowrap) {
    return <div className="help-hint__plain">{renderInline(text)}</div>;
  }
  return (
    <div className="help-hint__body">
      {blocks.map((b, i) => {
        if (b.kind === "title") {
          return (
            <div key={`t-${i}`} className="help-hint__section-title">
              {b.text}
            </div>
          );
        }
        if (b.kind === "list") {
          return (
            <ul key={`l-${i}`} className="help-hint__list">
              {b.items.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={`p-${i}`} className="help-hint__para">
            {renderInline(b.text)}
          </p>
        );
      })}
    </div>
  );
}

export function HelpHint({ text, ariaLabel, align = "start", nowrap = false }: Props) {
  const [open, setOpen] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});
  const rootRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const structured = useMemo(() => {
    const parsed = parseHelpBlocks(text);
    if (parsed.some((b) => b.kind === "title" || b.kind === "list")) return true;
    // Long plain paragraphs are auto-split into a list in HelpBody.
    return parsed.length === 1 && parsed[0].kind === "para" && parsed[0].text.length >= 48;
  }, [text]);

  const updatePopoverPosition = () => {
    const el = rootRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const popover = popoverRef.current;
    const preferred = structured && !nowrap ? POPOVER_MAX_WIDTH : 360;
    const maxWidth = Math.min(preferred, window.innerWidth - VIEWPORT_MARGIN * 2);
    const popWidth = popover?.offsetWidth || maxWidth;
    const popHeight = popover?.offsetHeight || 0;

    let left = align === "end" ? rect.right - popWidth : rect.left;
    left = Math.max(VIEWPORT_MARGIN, Math.min(left, window.innerWidth - popWidth - VIEWPORT_MARGIN));

    let top = rect.bottom + 6;
    if (popHeight > 0 && top + popHeight > window.innerHeight - VIEWPORT_MARGIN) {
      top = Math.max(VIEWPORT_MARGIN, rect.top - popHeight - 6);
    }

    setPopoverStyle({
      position: "fixed",
      top,
      left,
      right: "auto",
      maxWidth,
      maxHeight: Math.min(window.innerHeight - VIEWPORT_MARGIN * 2, 480),
    });
  };

  useLayoutEffect(() => {
    if (!open) return;
    updatePopoverPosition();
    const raf = window.requestAnimationFrame(updatePopoverPosition);
    const onReflow = () => updatePopoverPosition();
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
  }, [open, align, text, nowrap, structured]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (ev: MouseEvent) => {
      const root = rootRef.current;
      const target = ev.target as Node;
      if (root?.contains(target)) return;
      if (target instanceof Element && target.closest(".help-hint__popover")) return;
      setOpen(false);
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className={`help-hint${align === "end" ? " help-hint--align-end" : ""}`} ref={rootRef}>
      <button
        type="button"
        className="help-hint__trigger"
        aria-label={ariaLabel}
        aria-expanded={open}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        ?
      </button>
      {open
        ? createPortal(
            <div
              ref={popoverRef}
              className={`help-hint__popover help-hint__popover--portal${
                nowrap ? " help-hint__popover--nowrap" : ""
              }${structured && !nowrap ? " help-hint__popover--structured" : ""}`}
              style={popoverStyle}
              role="tooltip"
            >
              <HelpBody text={text} nowrap={nowrap} />
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
