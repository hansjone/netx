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

/**
 * `<input type="datetime-local">` (browser system wall clock) → UTC ISO with `Z` for API/DB.
 * Global deployments store UTC; each operator edits in their local timezone.
 */
export function localDatetimeInputToUtcIso(localValue: string): string | null {
  const v = String(localValue || "").trim();
  if (!v) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/.exec(v);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]) - 1;
  const day = Number(m[3]);
  const h = Number(m[4]);
  const mi = Number(m[5]);
  const sec = Number(m[6] || 0);
  const local = new Date(y, mo, day, h, mi, sec);
  if (Number.isNaN(local.getTime())) return null;
  return local.toISOString();
}

/** API/DB UTC (naive or `Z`) → `datetime-local` value in the browser system timezone. */
export function utcIsoToLocalDatetimeInput(iso: string | null | undefined): string {
  const d = parseApiTime(iso);
  if (!d) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
