export type MapRow = { before_if: string; after_if: string };

export type MapParse = {
  rows: MapRow[];
  /** 1-based line numbers that could not be read as a "before, after" pair. */
  invalidLines: number[];
  /** before_if values that appear on more than one line (first one is kept). */
  duplicateBefore: string[];
};

const HEADER = /^(old|before)\w*[\s,|]+(new|after)/i;

/**
 * Parse the port-mapping editor text. Pairs are split on `,` `|` or Tab; a line
 * with exactly two whitespace-separated tokens is accepted too. Anything else is
 * reported in `invalidLines` instead of being dropped silently.
 */
export function parseMapText(text: string): MapParse {
  const rows: MapRow[] = [];
  const invalidLines: number[] = [];
  const duplicateBefore: string[] = [];
  const seen = new Set<string>();
  String(text ?? "").split(/\r?\n/).forEach((line, index) => {
    const s = line.trim();
    if (!s || s.startsWith("#") || HEADER.test(s)) return;
    const delimited = /[,|\t]/.test(s);
    const parts = (delimited ? s.split(/[,|\t]+/) : s.split(/\s+/)).map((x) => x.trim()).filter(Boolean);
    if (parts.length !== 2) {
      invalidLines.push(index + 1);
      return;
    }
    const [before_if, after_if] = parts;
    if (seen.has(before_if)) {
      if (!duplicateBefore.includes(before_if)) duplicateBefore.push(before_if);
      return;
    }
    seen.add(before_if);
    rows.push({ before_if, after_if });
  });
  return { rows, invalidLines, duplicateBefore };
}

const pairKey = (r: MapRow) => `${r.before_if}\u0000${r.after_if}`;

/** True when the editor text differs from the saved rows (order-insensitive). */
export function mapTextDirty(text: string, saved: MapRow[] | undefined): boolean {
  const parsed = parseMapText(text);
  if (parsed.invalidLines.length || parsed.duplicateBefore.length) return true;
  const a = parsed.rows.map(pairKey).sort();
  const b = (saved ?? []).map(pairKey).sort();
  return a.length !== b.length || a.some((v, i) => v !== b[i]);
}
