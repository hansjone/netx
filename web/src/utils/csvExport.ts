export type CsvColumn<T> = {
  key: string;
  header: string;
  value?: (row: T) => string | number | null | undefined;
};

function escapeCsvCell(raw: string): string {
  if (/[",\r\n]/.test(raw)) return `"${raw.replace(/"/g, '""')}"`;
  return raw;
}

/** Build CSV text (UTF-8 BOM for Excel). */
export function toCsv<T>(rows: T[], columns: CsvColumn<T>[]): string {
  const header = columns.map((c) => escapeCsvCell(c.header)).join(",");
  const lines = rows.map((row) =>
    columns
      .map((c) => {
        const v = c.value
          ? c.value(row)
          : (row as Record<string, unknown>)[c.key];
        return escapeCsvCell(v == null ? "" : String(v));
      })
      .join(","),
  );
  return `\uFEFF${[header, ...lines].join("\r\n")}\r\n`;
}

export function downloadTextFile(filename: string, content: string, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadCsv<T>(filename: string, rows: T[], columns: CsvColumn<T>[]) {
  downloadTextFile(filename, toCsv(rows, columns));
}

/** Page through a list API up to maxRows (default 2000). */
export async function fetchAllPages<T>(opts: {
  pageSize?: number;
  maxRows?: number;
  fetchPage: (page: number, pageSize: number) => Promise<{ total?: number; items: T[] }>;
}): Promise<T[]> {
  const pageSize = Math.max(1, Math.min(500, opts.pageSize || 200));
  const maxRows = Math.max(1, opts.maxRows || 2000);
  const out: T[] = [];
  let page = 1;
  while (out.length < maxRows) {
    const res = await opts.fetchPage(page, pageSize);
    const batch = res.items || [];
    if (!batch.length) break;
    out.push(...batch);
    if (batch.length < pageSize) break;
    if (typeof res.total === "number" && out.length >= res.total) break;
    page += 1;
  }
  return out.slice(0, maxRows);
}
