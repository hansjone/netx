const systemTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

type FormatTimeOptions = {
  /** Treat timezone-less ISO timestamp strings as UTC. */
  assumeUtcNaive?: boolean;
};

function normalizeTimeInput(value: string | number | Date, options?: FormatTimeOptions): string | number | Date {
  if (!options?.assumeUtcNaive || typeof value !== "string") return value;
  const text = value.trim();
  if (!text) return value;
  // Match "YYYY-MM-DDTHH:mm:ss(.sss)" without explicit timezone offset.
  const isIsoLike = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text);
  return isIsoLike ? `${text}Z` : value;
}

export function formatSystemTime(
  value: string | number | Date | null | undefined,
  options?: FormatTimeOptions,
): string {
  if (value === null || value === undefined) return "";
  const normalized = normalizeTimeInput(value, options);
  const d = normalized instanceof Date ? normalized : new Date(normalized);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, {
    hour12: false,
    timeZone: systemTimeZone,
  });
}

