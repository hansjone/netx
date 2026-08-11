import {
  SIDEBAR_WIDTH_DEFAULT,
  SIDEBAR_WIDTH_KEY,
  SIDEBAR_WIDTH_MAX,
  SIDEBAR_WIDTH_MIN,
} from "./constants";

export function loadSidebarWidth(): number {
  try {
    const n = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
    if (Number.isFinite(n) && n >= SIDEBAR_WIDTH_MIN) {
      return Math.min(Math.round(n), SIDEBAR_WIDTH_MAX);
    }
  } catch {
    /* ignore */
  }
  return SIDEBAR_WIDTH_DEFAULT;
}

export function saveSidebarWidth(width: number): void {
  try {
    localStorage.setItem(SIDEBAR_WIDTH_KEY, String(Math.round(width)));
  } catch {
    /* ignore */
  }
}

export function clampSidebarWidth(width: number, pageWidth?: number): number {
  const maxByPage =
    pageWidth && pageWidth > 0
      ? Math.max(SIDEBAR_WIDTH_MIN, Math.floor(pageWidth * 0.55))
      : SIDEBAR_WIDTH_MAX;
  const maxW = Math.min(SIDEBAR_WIDTH_MAX, maxByPage);
  return Math.max(SIDEBAR_WIDTH_MIN, Math.min(maxW, Math.round(width)));
}
