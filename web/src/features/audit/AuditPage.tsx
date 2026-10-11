import { Button, Input } from "@heroui/react";
import { Fragment, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ListPager } from "../../components/ListPager";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../../i18n";
import { apiGet } from "../../services/api";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";

/** Backend caps audit-logs at 200 rows per page. */
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200];
const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_KEY = "netx.audit.pageSize";

function loadPageSize(): number {
  try {
    const n = Number(localStorage.getItem(PAGE_SIZE_KEY));
    return PAGE_SIZE_OPTIONS.includes(n) ? n : DEFAULT_PAGE_SIZE;
  } catch {
    return DEFAULT_PAGE_SIZE;
  }
}

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
      ok: String(d.ok_count != null ? Number(d.ok_count) : 0),
      fail: String(d.fail_count != null ? Number(d.fail_count) : 0),
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
  const [pageSize, setPageSize] = useState(loadPageSize);
  const [username, setUsername] = useState("");
  const [action, setAction] = useState("");
  const [quick, setQuick] = useState<QuickFilter>("business");
  const [expanded, setExpanded] = useState<string | null>(null);

  const derived = quickToQuery(quick);
  const effectiveAction = action.trim() || derived.action;
  const excludeNoise = action.trim() ? false : derived.excludeNoise;

  const query = useQuery({
    queryKey: ["auditLogs", page, pageSize, username, effectiveAction, excludeNoise],
    queryFn: () => {
      const p = new URLSearchParams();
      p.set("page", String(page));
      p.set("page_size", String(pageSize));
      p.set("exclude_noise", excludeNoise ? "true" : "false");
      if (username.trim()) p.set("username", username.trim());
      if (effectiveAction) p.set("action", effectiveAction);
      return apiGet<{ total: number; page: number; page_size: number; items: AuditItem[] }>(
        `/v1/audit-logs?${p.toString()}`,
      );
    },
    enabled: ready,
    // Keep the current rows on screen while the next page loads (no flicker).
    placeholderData: keepPreviousData,
  });

  const items = useMemo(() => query.data?.items || [], [query.data]);
  const total = query.data?.total || 0;
  const pages = pageCount(total, pageSize);

  // Total shrank (filters / purge): never sit on a page past the end.
  useEffect(() => {
    if (!query.isPlaceholderData && page > pages) setPage(pages);
  }, [page, pages, query.isPlaceholderData]);

  const changePageSize = (size: number) => {
    setPageSize(size);
    setPage(1);
    setExpanded(null);
    try {
      localStorage.setItem(PAGE_SIZE_KEY, String(size));
    } catch {
      /* ignore */
    }
  };
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
              <Button
                key={value}
                size="sm"
                variant={quick === value && !action.trim() ? "primary" : "secondary"}
                onPress={() => setQuickFilter(value)}
              >
                {t(labelKey)}
              </Button>
            ))}
          </div>

          <div className="filter-inline">
            {isAdmin ? (
              <Input
                placeholder={t("auth.filterUsername")}
                value={username}
                onChange={(e) => {
                  setPage(1);
                  setUsername(e.target.value);
                }}
              />
            ) : null}
            <Input
              placeholder={t("auth.filterAction")}
              value={action}
              onChange={(e) => {
                setPage(1);
                setAction(e.target.value);
                if (e.target.value.trim()) setQuick("all");
              }}
            />
            <Button size="sm" variant="secondary" onPress={() => void query.refetch()} isDisabled={query.isFetching}>
              {query.isFetching ? t("common.refreshing") : t("common.refresh")}
            </Button>
            <Button
              size="sm"
              variant="tertiary"
              isDisabled={!hasFilters}
              onPress={() => {
                setUsername("");
                setAction("");
                setQuick("business");
                setPage(1);
              }}
            >
              {t("common.clearFilters")}
            </Button>
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
                            <Button
                              size="sm"
                              variant="ghost"
                              className="linkish"
                              onPress={() => setExpanded(open ? null : row.id)}
                            >
                              {open ? t("audit.hideDetail") : t("audit.showDetail")}
                            </Button>
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

          <ListPager
            page={page}
            pages={pages}
            total={total}
            pageSize={pageSize}
            pageSizeOptions={PAGE_SIZE_OPTIONS}
            onPageChange={(next) => {
              setExpanded(null);
              setPage(next);
            }}
            onPageSizeChange={changePageSize}
            disabled={query.isLoading}
          />
        </div>
      </section>
    </div>
  );
}
