import type { ReactNode } from "react";

type T = (key: string, vars?: Record<string, string | number>) => string;

export type PreflightSheet = {
  sheet_id: string;
  title?: string;
  metric_id: string;
  before_count?: number;
  after_count?: number;
  skipped?: string;
  data_state?: string;
  data_state_reasons?: string[];
  counts?: Record<string, number>;
  mapped_exact?: string[];
  mapped_by_parent?: string[];
  identity_same_name?: string[];
  suspect_unmapped?: string[];
  suspect_unmapped_total?: number;
  mapped_target_missing_on_after?: string[];
};

export type PreflightReportData = {
  verdict: "ready" | "risk";
  risk_reasons: string[];
  coverage: { mapped: number; suspect_unmapped: number; identity_same_name: number; rate: number | null };
  mapping: {
    pairs: number;
    conflicts?: Array<Record<string, string>>;
    duplicate_before?: Array<Record<string, string>>;
    unused_map_keys?: string[];
    unused_map_keys_total?: number;
    map_targets_missing_on_after?: string[];
    map_targets_missing_on_after_total?: number;
    n_to_1_targets?: Array<{ target: string; sources: string[] }>;
  };
  sheets: PreflightSheet[];
  command_coverage?: { only_before?: string[]; only_after?: string[] };
};

export function dataStateLabel(state: string | undefined, t: T): string {
  return t(`bizCompare.dataState.${state || "ok"}`);
}

function List({ items, total, children }: { items?: string[]; total?: number; children?: ReactNode }) {
  if (!items?.length) return null;
  const more = (total ?? items.length) - items.length;
  return (
    <ul className="cx-preflight__list">
      {items.map((x) => (
        <li key={x}>
          <code>{x}</code>
        </li>
      ))}
      {more > 0 ? <li className="muted">+{more}</li> : null}
      {children}
    </ul>
  );
}

/**
 * Pre-flight result shared by the compare page ("validate mapping") and the cutover
 * project: mapping coverage first, then per-sheet data state and details.
 */
export function PreflightReport({
  report,
  t,
  onAddSuspects,
}: {
  report: PreflightReportData;
  t: T;
  /** Compare editor only: append unmapped before-side ports as empty rows to the mapping text. */
  onAddSuspects?: (ports: string[]) => void;
}) {
  const m = report.mapping;
  const suspects = report.sheets.flatMap((s) => s.suspect_unmapped || []);
  const issues: Array<[string, ReactNode]> = [];
  if (m.duplicate_before?.length) {
    issues.push([
      "duplicate",
      <List key="d" items={m.duplicate_before.map((d) => `${d.before_if} → ${d.kept_to} / ${d.dropped_to}`)} />,
    ]);
  }
  if (m.conflicts?.length) {
    issues.push([
      "conflicts",
      <List key="c" items={m.conflicts.map((c) => `${c.dropped_from} ↔ ${c.kept_from}`)} />,
    ]);
  }
  if (m.unused_map_keys?.length) {
    issues.push(["unused", <List key="u" items={m.unused_map_keys} total={m.unused_map_keys_total} />]);
  }
  if (m.map_targets_missing_on_after?.length) {
    issues.push([
      "missingTargets",
      <List key="t" items={m.map_targets_missing_on_after} total={m.map_targets_missing_on_after_total} />,
    ]);
  }
  if (m.n_to_1_targets?.length) {
    issues.push([
      "nTo1",
      <List key="n" items={m.n_to_1_targets.map((x) => `${x.sources.join(", ")} → ${x.target}`)} />,
    ]);
  }
  const rate = report.coverage.rate;
  return (
    <div className={`cx-preflight is-${report.verdict}`} data-testid="preflight-report">
      <div className="cx-preflight__head" role="status">
        <strong>{t(`bizCompare.preflight.${report.verdict}`)}</strong>
        <span>
          {t("bizCompare.preflight.coverage", {
            mapped: report.coverage.mapped,
            suspect: report.coverage.suspect_unmapped,
            rate: rate === null ? "—" : `${rate}%`,
          })}
        </span>
        <span className="muted">{t("bizCompare.preflight.pairs", { n: m.pairs })}</span>
      </div>

      {suspects.length ? (
        <div className="cx-preflight__block">
          <h5>{t("bizCompare.preflight.suspect")}</h5>
          <List items={suspects.slice(0, 200)} total={report.coverage.suspect_unmapped} />
          {onAddSuspects ? (
            <button type="button" className="mt-link-btn" onClick={() => onAddSuspects(suspects)}>
              {t("bizCompare.preflight.addSuspects")}
            </button>
          ) : null}
        </div>
      ) : null}

      {issues.map(([key, body]) => (
        <div className="cx-preflight__block" key={key}>
          <h5>{t(`bizCompare.preflight.issue.${key}`)}</h5>
          {body}
        </div>
      ))}

      {report.command_coverage?.only_before?.length || report.command_coverage?.only_after?.length ? (
        <div className="cx-preflight__block">
          <h5>{t("bizCompare.preflight.commandDiff")}</h5>
          <List items={(report.command_coverage.only_before || []).map((x) => `${t("bizCompare.scopeBefore")}: ${x}`)} />
          <List items={(report.command_coverage.only_after || []).map((x) => `${t("bizCompare.scopeAfter")}: ${x}`)} />
        </div>
      ) : null}

      <div className="cx-preflight__scroll">
      <table className="data-table pt-list-table cx-preflight__table">
        <thead>
          <tr>
            <th>{t("bizCompare.field")}</th>
            <th>{t("bizCompare.preflight.rows")}</th>
            <th>{t("bizCompare.preflight.state")}</th>
            <th>{t("bizCompare.preflight.mapping")}</th>
          </tr>
        </thead>
        <tbody>
          {report.sheets.map((s) => {
            const state = s.data_state || "ok";
            const c = s.counts || {};
            return (
              <tr key={s.sheet_id}>
                <td>
                  <code>{s.title || s.sheet_id}</code>
                </td>
                <td>{`${s.before_count ?? 0} → ${s.after_count ?? 0}`}</td>
                <td>
                  <span className={`cx-state cx-state--${state}`}>{dataStateLabel(state, t)}</span>
                  {s.data_state_reasons?.length ? (
                    <div className="muted cx-state__why">{s.data_state_reasons.join(" · ")}</div>
                  ) : null}
                </td>
                <td>
                  {s.skipped ? (
                    <span className="muted">{t("bizCompare.preflight.skippedHeavy")}</span>
                  ) : s.counts ? (
                    <details>
                      <summary>
                        {t("bizCompare.preflight.sheetCounts", {
                          exact: (c.mapped_exact || 0) + (c.mapped_by_parent || 0),
                          same: c.identity_same_name || 0,
                          suspect: s.suspect_unmapped_total ?? c.suspect_unmapped ?? 0,
                        })}
                      </summary>
                      <List items={s.suspect_unmapped} total={s.suspect_unmapped_total} />
                      {s.mapped_target_missing_on_after?.length ? (
                        <>
                          <h6>{t("bizCompare.preflight.issue.missingTargets")}</h6>
                          <List items={s.mapped_target_missing_on_after} />
                        </>
                      ) : null}
                    </details>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </div>
  );
}
