import { Button } from "@heroui/react";
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

/** Short browser/OS label so same NAT IP can still be told apart. */
function summarizeUa(ua: string): string {
  const s = String(ua || "").trim();
  if (!s) return "—";
  const browser = /Edg\//.test(s)
    ? "Edge"
    : /Chrome\//.test(s)
      ? "Chrome"
      : /Firefox\//.test(s)
        ? "Firefox"
        : /Safari\//.test(s) && !/Chrome\//.test(s)
          ? "Safari"
          : /curl\//i.test(s)
            ? "curl"
            : /python/i.test(s)
              ? "python"
              : "Client";
  const os = /Windows NT/i.test(s)
    ? "Windows"
    : /Android/i.test(s)
      ? "Android"
      : /iPhone|iPad/i.test(s)
        ? "iOS"
        : /Mac OS X|Macintosh/i.test(s)
          ? "macOS"
          : /Linux/i.test(s)
            ? "Linux"
            : "";
  return os ? `${browser} · ${os}` : browser;
}

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
          <Button
            size="sm"
            variant="secondary"
            isDisabled={revokeOthersMut.isPending || items.filter((s) => !s.current).length === 0}
            onPress={() => revokeOthersMut.mutate()}
          >
            {t("auth.revokeOtherSessions")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onPress={() => void sessionsQuery.refetch()}
            isDisabled={sessionsQuery.isFetching}
          >
            {t("common.refresh")}
          </Button>
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
                <th>{t("auth.colClient")}</th>
                <th>{t("auth.colLastSeen")}</th>
                <th>{t("auth.colCreated")}</th>
                <th>{t("auth.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={row.id}>
                  <td>
                    <code title={row.id}>{row.id.slice(0, 10)}…</code>
                    {row.current ? (
                      <span className="pt-list-status pt-list-status--ok" style={{ marginLeft: 8 }}>
                        {t("auth.sessionCurrent")}
                      </span>
                    ) : null}
                  </td>
                  <td>
                    <code>{row.client_ip || "—"}</code>
                  </td>
                  <td title={row.user_agent || undefined}>{summarizeUa(row.user_agent)}</td>
                  <td>{row.last_seen_at ? formatSystemTime(row.last_seen_at) : "—"}</td>
                  <td>{row.created_at ? formatSystemTime(row.created_at) : "—"}</td>
                  <td>
                    <Button
                      size="sm"
                      variant="secondary"
                      isDisabled={revokeMut.isPending}
                      onPress={() => {
                        if (row.current && !window.confirm(t("auth.revokeCurrentConfirm"))) return;
                        revokeMut.mutate(row.id);
                      }}
                    >
                      {t("auth.revokeSession")}
                    </Button>
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
