const systemTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

export function formatSystemTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined) return "";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, {
    hour12: false,
    timeZone: systemTimeZone,
  });
}

