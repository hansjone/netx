import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";
import { apiGet } from "../services/api";

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

  return (
    <div className="panel">
      <h2 className="panel__title">{t("auth.auditTitle")}</h2>
      <p className="panel__hint">{isAdmin ? t("auth.auditHintAdmin") : t("auth.auditHintUser")}</p>

      <div className="form-row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
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
        <button type="button" onClick={() => void query.refetch()}>
          {t("common.refresh")}
        </button>
      </div>

      {query.isLoading ? <div>{t("common.refreshing")}</div> : null}
      <table className="data-table">
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
              <td>{row.ts || "-"}</td>
              <td>{row.actor_username || "-"}</td>
              <td>{row.action}</td>
              <td>{row.method}</td>
              <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis" }}>{row.path}</td>
              <td>{row.status_code}</td>
              <td>{row.client_ip || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="form-row" style={{ gap: 8, marginTop: 12 }}>
        <button type="button" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
          {t("common.prevPage")}
        </button>
        <span>
          {t("common.pagerMeta", { total, page, pages })}
        </span>
        <button
          type="button"
          disabled={page >= pages}
          onClick={() => setPage((p) => Math.min(pages, p + 1))}
        >
          {t("common.nextPage")}
        </button>
      </div>
    </div>
  );
}
