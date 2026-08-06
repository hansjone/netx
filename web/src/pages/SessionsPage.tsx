import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { apiDelete, apiGet, apiPost } from "../services/api";
import { formatSystemTime } from "../utils/time";

type SessionRow = {
  id: string;
  created_at: string | null;
  expires_at: string | null;
  refresh_expires_at: string | null;
  last_seen_at: string | null;
  client_ip: string;
  user_agent: string;
  current: boolean;
};

export function SessionsPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const qc = useQueryClient();

  const sessionsQuery = useQuery({
    queryKey: ["auth-sessions"],
    queryFn: () => apiGet<{ items: SessionRow[]; total: number }>("/v1/auth/sessions"),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => apiDelete(`/v1/auth/sessions/${encodeURIComponent(id)}`),
    onSuccess: () => {
      showOk(t("auth.sessionRevoked"));
      void qc.invalidateQueries({ queryKey: ["auth-sessions"] });
    },
    onError: (err) => showError(String(err instanceof Error ? err.message : err)),
  });

  const revokeOthersMut = useMutation({
    mutationFn: () => apiPost<{ revoked: number }>("/v1/auth/sessions/revoke-others", {}),
    onSuccess: (data) => {
          showOk(t("auth.sessionsRevokedOthers", { count: data.revoked ?? 0 }));
      void qc.invalidateQueries({ queryKey: ["auth-sessions"] });
    },
    onError: (err) => showError(String(err instanceof Error ? err.message : err)),
  });

  const items = sessionsQuery.data?.items || [];

  return (
    <div className="page">
      <header className="page-header">
        <h1>{t("auth.sessionsTitle")}</h1>
        <p className="panel__hint">{t("auth.sessionsHint")}</p>
      </header>

      <div className="panel">
        <div className="filter-inline" style={{ marginBottom: 12 }}>
          <button
            type="button"
            disabled={revokeOthersMut.isPending || items.filter((s) => !s.current).length === 0}
            onClick={() => revokeOthersMut.mutate()}
          >
            {t("auth.revokeOtherSessions")}
          </button>
          <button type="button" onClick={() => void sessionsQuery.refetch()} disabled={sessionsQuery.isFetching}>
            {t("common.refresh")}
          </button>
        </div>

        {sessionsQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

        {!items.length && !sessionsQuery.isLoading ? (
          <p className="muted">{t("auth.sessionsEmpty")}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("auth.colSession")}</th>
                <th>{t("auth.colIp")}</th>
                <th>{t("auth.colLastSeen")}</th>
                <th>{t("auth.colCreated")}</th>
                <th>{t("auth.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={row.id}>
                  <td>
                    <code title={row.user_agent}>{row.id.slice(0, 10)}…</code>
                    {row.current ? (
                      <span className="pt-list-status pt-list-status--ok" style={{ marginLeft: 8 }}>
                        {t("auth.sessionCurrent")}
                      </span>
                    ) : null}
                  </td>
                  <td>{row.client_ip || "—"}</td>
                  <td>{row.last_seen_at ? formatSystemTime(row.last_seen_at) : "—"}</td>
                  <td>{row.created_at ? formatSystemTime(row.created_at) : "—"}</td>
                  <td>
                    <button
                      type="button"
                      disabled={revokeMut.isPending}
                      onClick={() => {
                        if (row.current && !window.confirm(t("auth.revokeCurrentConfirm"))) return;
                        revokeMut.mutate(row.id);
                      }}
                    >
                      {t("auth.revokeSession")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
