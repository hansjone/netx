import { useMemo, useState } from "react";
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

function statusClass(code: number): string {
  if (code >= 500) return "pt-list-status--failed";
  if (code >= 400) return "pt-list-status--warning";
  if (code >= 200 && code < 300) return "pt-list-status--ok";
  return "pt-list-status--other";
}

export function AuditPage() {
  const { t } = useI18n();
  const { ready, isAdmin } = useAuth();
  const [page, setPage] = useState(1);
  const [username, setUsername] = useState("");
  const [action, setAction] = useState("");

  const query = useQuery({
    queryKey: ["auditLogs", page, username, action],
    queryFn: () => {
      const p = new URLSearchParams();
      p.set("page", String(page));
      p.set("page_size", "50");
      if (username.trim()) p.set("username", username.trim());
      if (action.trim()) p.set("action", action.trim());
      return apiGet<{ total: number; page: number; page_size: number; items: AuditItem[] }>(
        `/v1/audit-logs?${p.toString()}`,
      );
    },
    enabled: ready,
  });

  const items = useMemo(() => query.data?.items || [], [query.data]);
  const total = query.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / 50));
  const hasFilters = Boolean(username.trim() || action.trim());

  return (
    <div className="page-stack system-page">
      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("audit.logsTitle")}</h2>
        </div>
        <p className="panel__hint">{isAdmin ? t("auth.auditHintAdmin") : t("auth.auditHintUser")}</p>

        <div className="pt-list">
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
                    <th>{t("auth.colMethod")}</th>
                    <th>{t("auth.colPath")}</th>
                    <th>{t("auth.colStatus")}</th>
                    <th>{t("auth.colIp")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={row.id} title={JSON.stringify(row.detail || {})}>
                      <td className="pt-list-time">
                        {row.ts ? formatSystemTime(row.ts) : t("common.empty")}
                      </td>
                      <td className="pt-list-task-name">{row.actor_username || t("common.empty")}</td>
                      <td>{row.action}</td>
                      <td className="pt-list-num">{row.method}</td>
                      <td
                        className="pt-list-num"
                        style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis" }}
                      >
                        {row.path}
                      </td>
                      <td>
                        <span className={`pt-list-status ${statusClass(row.status_code)}`}>
                          {row.status_code}
                        </span>
                      </td>
                      <td className="pt-list-num">{row.client_ip || t("common.empty")}</td>
                    </tr>
                  ))}
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
