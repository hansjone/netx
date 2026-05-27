/** Map legacy API labels to English for UI display. */

const LEGACY_INTERVAL_LABEL_EN: Record<string, string> = {
  未启用: "disabled",
  实时: "realtime",
};

export function runtimeIntervalLabel(label?: string | null): string {
  const raw = String(label ?? "").trim();
  if (!raw) return "—";
  return LEGACY_INTERVAL_LABEL_EN[raw] ?? raw;
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, pageSize)));
}
