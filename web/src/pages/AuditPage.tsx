import { Fragment, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";
import { apiGet } from "../services/api";
import { formatSystemTime } from "../utils/time";

type AuditItem = {
  id: string;
  ts: string | null;
  actor_username: string;
  action: string;
  method: string;
  path: string;
  status_code: number;
  client_ip: string;
  detail: Record<string, unknown>;
};

/** Quick filters map to action substring and/or exclude_noise flag. */
type QuickFilter = "business" | "webcrt." | "ne.exec" | "auth." | "http." | "all";

function statusClass(code: number): string {
  if (code >= 500) return "pt-list-status--failed";
  if (code >= 400) return "pt-list-status--warning";
  if (code >= 200 && code < 300) return "pt-list-status--ok";
  return "pt-list-status--other";
}

function detailStr(detail: Record<string, unknown>, key: string): string {
  const v = detail?.[key];
  if (v == null) return "";
  return String(v).trim();
}

function deviceLabel(detail: Record<string, unknown>): string {
  const name = detailStr(detail, "ne_name");
  const ip = detailStr(detail, "ne_ip");
  if (name && ip) return `${name} (${ip})`;
  return name || ip || detailStr(detail, "ne_id") || "";
}

export function auditSummary(
  action: string,
  detail: Record<string, unknown>,
  t: (key: string, vars?: Record<string, string>) => string,
  row?: Pick<AuditItem, "method" | "path">,
): string {
  const d = detail || {};
  const device = deviceLabel(d);
  if (action === "webcrt.session_connecting") {
    return t("audit.summary.connecting", { device: device || t("common.empty") });
  }
  if (action === "webcrt.session_created") {
    return t("audit.summary.loginOk", { device: device || t("common.empty") });
  }
  if (action === "webcrt.session_open_failed") {
    const err = detailStr(d, "error") || t("common.empty");
    return t("audit.summary.loginFail", { device: device || t("common.empty"), error: err.slice(0, 80) });
  }
  if (action === "webcrt.session_closed") {
    const reason = detailStr(d, "reason");
    return reason
      ? t("audit.summary.logoutReason", { device: device || t("common.empty"), reason })
      : t("audit.summary.logout", { device: device || t("common.empty") });
  }
  if (action === "webcrt.command") {
    const cmd = detailStr(d, "command") || t("common.empty");
    const redacted = Boolean(d.redacted);
    return t("audit.summary.command", {
      device: device || t("common.empty"),
      command: redacted ? "***" : cmd,
    });
  }
  if (action === "ne.exec") {
    const cmds = Array.isArray(d.commands) ? d.commands.map(String).filter(Boolean) : [];
    return t("audit.summary.neExec", {
      device: device || t("common.empty"),
      commands: cmds.slice(0, 3).join("; ") || t("common.empty"),
    });
  }
  if (action === "ne.exec_batch") {
    const neIds = Array.isArray(d.ne_ids) ? (d.ne_ids as unknown[]) : [];
    const targetCount =
      d.target_count != null && d.target_count !== ""
        ? Number(d.target_count)
        : neIds.length;
    return t("audit.summary.neExecBatch", {
      n: String(Number.isFinite(targetCount) ? targetCount : 0),
      ok: String(d.ok_count ?? 0),
      fail: String(d.fail_count ?? 0),
    });
  }
  if (action.startsWith("auth.")) {
    if (action === "auth.unauthorized") {
      const method = row?.method || "GET";
      const path = row?.path || "";
      return path ? `${method} ${path}` : "unauthorized";
    }
    return action.replace(/^auth\./, "");
  }
  if (action.startsWith("http.") || action.startsWith("ume.")) {
    const method = row?.method || action.replace(/^http\./, "").toUpperCase();
    const path = row?.path || "";
    return path ? `${method} ${path}` : method;
  }
  return "";
}

function quickToQuery(quick: QuickFilter): { action: string; excludeNoise: boolean } {
  if (quick === "business") return { action: "", excludeNoise: true };
  if (quick === "all") return { action: "", excludeNoise: false };
  if (quick === "http.") return { action: "http.", excludeNoise: false };
  return { action: quick, excludeNoise: true };
}

export function AuditPage() {
  const { t } = useI18n();
  const { ready, isAdmin } = useAuth();
  const [page, setPage] = useState(1);
  const [username, setUsername] = useState("");
  const [action, setAction] = useState("");
  const [quick, setQuick] = useState<QuickFilter>("business");
  const [expanded, setExpanded] = useState<string | null>(null);

  const derived = quickToQuery(quick);
  const effectiveAction = action.trim() || derived.action;
  const excludeNoise = action.trim() ? false : derived.excludeNoise;

  const query = useQuery({
    queryKey: ["auditLogs", page, username, effectiveAction, excludeNoise],
    queryFn: () => {
      const p = new URLSearchParams();
      p.set("page", String(page));
      p.set("page_size", "50");
      p.set("exclude_noise", excludeNoise ? "true" : "false");
      if (username.trim()) p.set("username", username.trim());
      if (effectiveAction) p.set("action", effectiveAction);
      return apiGet<{ total: number; page: number; page_size: number; items: AuditItem[] }>(
        `/v1/audit-logs?${p.toString()}`,
      );
    },
    enabled: ready,
  });

  const items = useMemo(() => query.data?.items || [], [query.data]);
  const total = query.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / 50));
  const hasFilters = Boolean(username.trim() || action.trim() || quick !== "business");

  const setQuickFilter = (next: QuickFilter) => {
    setPage(1);
    setQuick(next);
    if (next !== "all" && next !== "business") setAction("");
    else setAction("");
  };

  return (
    <div className="page-stack system-page">
      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("audit.logsTitle")}</h2>
        </div>

        <div className="pt-list">
          <div className="filter-inline audit-quick-filters">
            {(
              [
                ["business", "audit.filterBusiness"],
                ["webcrt.", "audit.filterWebcrt"],
                ["ne.exec", "audit.filterNeExec"],
                ["auth.", "audit.filterAuth"],
                ["http.", "audit.filterHttp"],
                ["all", "audit.filterAll"],
              ] as const
            ).map(([value, labelKey]) => (
              <button
                key={value}
                type="button"
                className={quick === value && !action.trim() ? "is-active" : undefined}
                onClick={() => setQuickFilter(value)}
              >
                {t(labelKey)}
              </button>
            ))}
          </div>

          <div className="filter-inline">
            {isAdmin ? (
              <input
                placeholder={t("auth.filterUsername")}
                value={username}
                onChange={(e) => {
                  setPage(1);
                  setUsername(e.target.value);
                }}
              />
            ) : null}
            <input
              placeholder={t("auth.filterAction")}
              value={action}
              onChange={(e) => {
                setPage(1);
                setAction(e.target.value);
                if (e.target.value.trim()) setQuick("all");
              }}
            />
            <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
              {query.isFetching ? t("common.refreshing") : t("common.refresh")}
            </button>
            <button
              type="button"
              disabled={!hasFilters}
              onClick={() => {
                setUsername("");
                setAction("");
                setQuick("business");
                setPage(1);
              }}
            >
              {t("common.clearFilters")}
            </button>
          </div>

          {query.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

          {!items.length && !query.isLoading ? (
            <div className="pt-list-empty">
              <p>{t("common.empty")}</p>
            </div>
          ) : (
            <div className="pt-list-table-wrap">
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th>{t("auth.colTime")}</th>
                    <th>{t("auth.colUser")}</th>
                    <th>{t("auth.colAction")}</th>
                    <th>{t("audit.colSummary")}</th>
                    <th>{t("auth.colStatus")}</th>
                    <th>{t("auth.colIp")}</th>
                    <th>{t("audit.colDetail")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => {
                    const open = expanded === row.id;
                    const summary = auditSummary(row.action, row.detail || {}, t, row);
                    return (
                      <Fragment key={row.id}>
                        <tr>
                          <td className="pt-list-time">
                            {row.ts ? formatSystemTime(row.ts) : t("common.empty")}
                          </td>
                          <td className="pt-list-task-name">{row.actor_username || t("common.empty")}</td>
                          <td className="pt-list-num">{row.action}</td>
                          <td className="audit-summary-cell">{summary || row.path || t("common.empty")}</td>
                          <td>
                            {row.status_code ? (
                              <span className={`pt-list-status ${statusClass(row.status_code)}`}>
                                {row.status_code}
                              </span>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                          <td className="pt-list-num">{row.client_ip || t("common.empty")}</td>
                          <td>
                            <button
                              type="button"
                              className="linkish"
                              onClick={() => setExpanded(open ? null : row.id)}
                            >
                              {open ? t("audit.hideDetail") : t("audit.showDetail")}
                            </button>
                          </td>
                        </tr>
                        {open ? (
                          <tr className="audit-detail-row">
                            <td colSpan={7}>
                              <pre className="audit-detail-pre">{JSON.stringify(row.detail || {}, null, 2)}</pre>
                              {row.method || row.path ? (
                                <p className="muted audit-detail-meta">
                                  {row.method} {row.path}
                                </p>
                              ) : null}
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="pager pt-list-pager">
            <span className="muted">
              {t("common.pagerMeta", {
                total: String(total),
                page: String(page),
                pages: String(pages),
              })}
            </span>
            <div className="btn-row">
              <button type="button" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                {t("common.prevPage")}
              </button>
              <button
                type="button"
                disabled={page >= pages}
                onClick={() => setPage((p) => Math.min(pages, p + 1))}
              >
                {t("common.nextPage")}
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
