export function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="12"
      height="12"
      aria-hidden="true"
      className="topo-svg-icon"
      style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.12s ease" }}
    >
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9 6l6 6-6 6"
      />
    </svg>
  );
}

export function SidebarFoldIcon({ expand }: { expand?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        d={expand ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"}
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        fill="currentColor"
        d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"
      />
    </svg>
  );
}

export function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        d="M12 5v14M5 12h14"
      />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        d="M6 6l12 12M18 6L6 18"
      />
    </svg>
  );
}

export function RegionGlyph({ size = 16 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
      <path
        d="M4 8.5L12 4l8 4.5v7L12 20l-8-4.5v-7z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M12 4v16M4 8.5l8 4.5 8-4.5" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.7" />
    </svg>
  );
}

export function LayerGlyph({ role, size = 16 }: { role?: string; size?: number }) {
  const r = String(role || "core").toLowerCase();
  if (r === "aggregation") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
        <rect x="4" y="5" width="16" height="4" rx="1.5" fill="currentColor" opacity="0.9" />
        <rect x="6" y="11" width="12" height="3.5" rx="1.2" fill="currentColor" opacity="0.65" />
        <rect x="8" y="16.5" width="8" height="3" rx="1" fill="currentColor" opacity="0.45" />
      </svg>
    );
  }
  if (r === "access") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
        <circle cx="7" cy="12" r="2.4" fill="currentColor" />
        <circle cx="17" cy="7" r="2.2" fill="currentColor" opacity="0.8" />
        <circle cx="17" cy="17" r="2.2" fill="currentColor" opacity="0.8" />
        <path d="M9.4 12H14.5M14.5 12L16 8.4M14.5 12L16 15.6" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
      <rect x="7" y="3.5" width="10" height="17" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="12" cy="8" r="2.2" fill="currentColor" />
      <path d="M9 13.5h6M9 16.5h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
