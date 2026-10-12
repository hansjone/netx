import { useMemo } from "react";

type T = (key: string, vars?: Record<string, string | number>) => string;

type Line = { index: number; before: string; after: string };

/** Lenient parse: a half-filled `port,` line is a row waiting for its target. */
function readLines(text: string): Line[] {
  const out: Line[] = [];
  text.split(/\r?\n/).forEach((raw, index) => {
    const s = raw.trim();
    if (!s || s.startsWith("#")) return;
    const delimited = /[,|\t]/.test(s);
    const parts = (delimited ? s.split(/[,|\t]+/) : s.split(/\s+/)).map((x) => x.trim());
    out.push({ index, before: parts[0] || "", after: parts.slice(1).join(" ").trim() });
  });
  return out;
}

/**
 * Table view over the mapping text. The text stays the single source of truth, so
 * comments, order and the "save" flow are shared with the text editor.
 */
export function MappingTable({ text, onChange, t }: { text: string; onChange: (next: string) => void; t: T }) {
  const rows = useMemo(() => readLines(text), [text]);
  const seen = new Map<string, number>();
  rows.forEach((r) => seen.set(r.before, (seen.get(r.before) || 0) + 1));

  const replaceLine = (index: number, line: string | null) => {
    const lines = text.split(/\r?\n/);
    if (line === null) lines.splice(index, 1);
    else lines[index] = line;
    onChange(lines.join("\n"));
  };

  return (
    <div className="cx-maptable" data-testid="mapping-table">
      <table className="data-table pt-list-table">
        <thead>
          <tr>
            <th>{t("bizCompare.mapTable.before")}</th>
            <th>{t("bizCompare.mapTable.after")}</th>
            <th>{t("bizCompare.mapTable.status")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const status = !r.before
              ? "invalid"
              : (seen.get(r.before) || 0) > 1
                ? "duplicate"
                : !r.after
                  ? "todo"
                  : "ok";
            return (
              <tr key={r.index} className={`cx-maptable__row is-${status}`}>
                <td>
                  <input
                    className="ui-field__input cx-maptable__input"
                    aria-label={`${t("bizCompare.mapTable.before")} ${r.index + 1}`}
                    value={r.before}
                    onChange={(e) => replaceLine(r.index, `${e.target.value},${r.after}`)}
                  />
                </td>
                <td>
                  <input
                    className="ui-field__input cx-maptable__input"
                    aria-label={`${t("bizCompare.mapTable.after")} ${r.index + 1}`}
                    value={r.after}
                    onChange={(e) => replaceLine(r.index, `${r.before},${e.target.value}`)}
                  />
                </td>
                <td>
                  <span className={`cx-state cx-state--${status === "ok" ? "ok" : status === "todo" ? "no_data" : "suspect_parse"}`}>
                    {t(`bizCompare.mapTable.${status}`)}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    className="mt-icon-btn"
                    aria-label={`${t("bizCompare.mapTable.remove")} ${r.index + 1}`}
                    onClick={() => replaceLine(r.index, null)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
          {!rows.length ? (
            <tr>
              <td colSpan={4} className="muted">
                {t("bizCompare.mapTable.empty")}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      <button
        type="button"
        className="mt-link-btn"
        onClick={() => onChange(`${text.replace(/\s+$/, "")}${text.trim() ? "\n" : ""},`)}
      >
        {t("bizCompare.mapTable.add")}
      </button>
    </div>
  );
}
