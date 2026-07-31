const systemTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

type FormatTimeOptions = {
  /**
   * Treat timezone-less timestamps as UTC (DB/API default).
   * Defaults to true so naive ISO strings show in the browser system timezone.
   */
  assumeUtcNaive?: boolean;
};

function normalizeTimeInput(value: string | number | Date, options?: FormatTimeOptions): string | number | Date {
  const assumeUtc = options?.assumeUtcNaive !== false;
  if (!assumeUtc || typeof value !== "string") return value;
  const text = value.trim();
  if (!text) return value;
  // Already has explicit offset / Z.
  if (/[zZ]$|[+-]\d{2}:\d{2}$/.test(text)) return value;
  // "YYYY-MM-DDTHH:mm:ss(.sss)" or "YYYY-MM-DD HH:mm:ss(.sss)"
  const isoLike = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text);
  if (!isoLike) return value;
  return `${text.replace(" ", "T")}Z`;
}

/** Parse API timestamps; naive ISO strings are treated as UTC. */
export function parseApiTime(
  value: string | number | Date | null | undefined,
  options?: FormatTimeOptions,
): Date | null {
  if (value === null || value === undefined) return null;
  const normalized = normalizeTimeInput(value, options);
  const d = normalized instanceof Date ? normalized : new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatSystemTime(
  value: string | number | Date | null | undefined,
  options?: FormatTimeOptions,
): string {
  if (value === null || value === undefined) return "";
  const d = parseApiTime(value, options);
  if (!d) return String(value);
  return d.toLocaleString(undefined, {
    hour12: false,
    timeZone: systemTimeZone,
  });
}
