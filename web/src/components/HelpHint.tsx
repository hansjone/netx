import { useEffect, useRef, useState } from "react";

type Props = {
  text: string;
  ariaLabel: string;
  align?: "start" | "end";
  nowrap?: boolean;
};

export function HelpHint({ text, ariaLabel, align = "start", nowrap = false }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (ev: MouseEvent) => {
      if (!rootRef.current?.contains(ev.target as Node)) setOpen(false);
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
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open ? (
        <div
          className={`help-hint__popover${nowrap ? " help-hint__popover--nowrap" : ""}`}
          role="tooltip"
        >
          {text}
        </div>
      ) : null}
    </span>
  );
}
