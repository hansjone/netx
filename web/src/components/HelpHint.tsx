import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

type Props = {
  text: string;
  ariaLabel: string;
  align?: "start" | "end";
  nowrap?: boolean;
};

const VIEWPORT_MARGIN = 8;
const POPOVER_MAX_WIDTH = 360;

export function HelpHint({ text, ariaLabel, align = "start", nowrap = false }: Props) {
  const [open, setOpen] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});
  const rootRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const updatePopoverPosition = () => {
    const el = rootRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const popover = popoverRef.current;
    const maxWidth = Math.min(POPOVER_MAX_WIDTH, window.innerWidth - VIEWPORT_MARGIN * 2);
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
  }, [open, align, text, nowrap]);

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
              }`}
              style={popoverStyle}
              role="tooltip"
            >
              {text}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
