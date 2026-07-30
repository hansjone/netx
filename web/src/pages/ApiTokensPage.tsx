import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { apiDelete, apiGet, apiPost } from "../services/api";

type TokenRow = {
  id: string;
  name: string;
  user_id: string;
  username: string;
  created_at: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
  expired: boolean;
  active: boolean;
};

type UserRow = {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
};

const EXPIRY_OPTIONS = [
  { value: 7, labelKey: "auth.expire7d" },
  { value: 30, labelKey: "auth.expire30d" },
  { value: 90, labelKey: "auth.expire90d" },
  { value: 365, labelKey: "auth.expire365d" },
  { value: 0, labelKey: "auth.expireNever" },
] as const;

export function ApiTokensPage() {
  const { t } = useI18n();
  const { ready, user, isAdmin } = useAuth();
  const { showOk, showError } = useToast();
  const qc = useQueryClient();
  const [name, setName] = useState("mcp");
  const [expiresInDays, setExpiresInDays] = useState(90);
  const [ownerUserId, setOwnerUserId] = useState("");
  const [createdPlain, setCreatedPlain] = useState("");

  const tokensQuery = useQuery({
    queryKey: ["apiTokens"],
    queryFn: () => apiGet<{ items: TokenRow[] }>("/v1/api-tokens"),
    enabled: ready,
  });

  const usersQuery = useQuery({
    queryKey: ["appUsers"],
    queryFn: () => apiGet<{ items: UserRow[] }>("/v1/users"),
    enabled: ready && isAdmin,
  });

  const createMut = useMutation({
    mutationFn: () =>
      apiPost<{ token: TokenRow & { token: string } }>("/v1/api-tokens", {
        name: name.trim() || "mcp",
        expires_in_days: expiresInDays,
        user_id: isAdmin && ownerUserId ? ownerUserId : undefined,
      }),
    onSuccess: async (data) => {
      setCreatedPlain(data.token.token);
      showOk(t("auth.tokenCreated"));
      await qc.invalidateQueries({ queryKey: ["apiTokens"] });
    },
    onError: (e) => showError(String(e instanceof Error ? e.message : e)),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => apiDelete(`/v1/api-tokens/${encodeURIComponent(id)}`),
    onSuccess: async () => {
      showOk(t("auth.tokenRevoked"));
      await qc.invalidateQueries({ queryKey: ["apiTokens"] });
    },
    onError: (e) => showError(String(e instanceof Error ? e.message : e)),
  });

  const items = useMemo(() => tokensQuery.data?.items || [], [tokensQuery.data]);
  const users = useMemo(() => usersQuery.data?.items || [], [usersQuery.data]);

  const onCreate = (e: FormEvent) => {
    e.preventDefault();
    setCreatedPlain("");
    createMut.mutate();
  };

  const copyToken = async () => {
    try {
      await navigator.clipboard.writeText(createdPlain);
      showOk(t("auth.tokenCopied"));
    } catch {
      showError(t("auth.tokenCopyFailed"));
    }
  };

  return (
    <div className="panel">
      <h2 className="panel__title">{t("auth.apiKeysTitle")}</h2>
      <p className="panel__hint">{t("auth.apiKeysHint")}</p>

      <form className="form-row" onSubmit={onCreate} style={{ gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        <input
          placeholder={t("auth.tokenName")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <select
          value={expiresInDays}
          onChange={(e) => setExpiresInDays(Number(e.target.value))}
          aria-label={t("auth.expiresIn")}
        >
          {EXPIRY_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {t(opt.labelKey)}
            </option>
          ))}
        </select>
        {isAdmin ? (
          <select
            value={ownerUserId}
            onChange={(e) => setOwnerUserId(e.target.value)}
            aria-label={t("auth.tokenOwner")}
          >
            <option value="">{t("auth.tokenOwnerSelf", { user: user?.username || "" })}</option>
            {users
              .filter((u) => u.is_active)
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username} ({u.role})
                </option>
              ))}
          </select>
        ) : null}
        <button type="submit" disabled={createMut.isPending}>
          {t("auth.createToken")}
        </button>
      </form>

      {createdPlain ? (
        <div className="panel" style={{ marginBottom: 16, background: "#f8fafc" }}>
          <div className="panel__hint">{t("auth.tokenOnceHint")}</div>
          <code style={{ wordBreak: "break-all", display: "block", margin: "8px 0" }}>{createdPlain}</code>
          <button type="button" onClick={() => void copyToken()}>
            {t("auth.copyToken")}
          </button>
        </div>
      ) : null}

      {tokensQuery.isLoading ? <div>{t("common.refreshing")}</div> : null}
      <table className="data-table">
        <thead>
          <tr>
            <th>{t("auth.tokenName")}</th>
            <th>{t("auth.tokenOwner")}</th>
            <th>{t("auth.colTime")}</th>
            <th>{t("auth.expiresAt")}</th>
            <th>{t("auth.lastUsed")}</th>
            <th>{t("auth.status")}</th>
            <th>{t("auth.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.id}>
              <td>{row.name}</td>
              <td>{row.username || row.user_id}</td>
              <td>{row.created_at || "-"}</td>
              <td>{row.expires_at || t("auth.expireNever")}</td>
              <td>{row.last_used_at || "-"}</td>
              <td>
                {row.revoked
                  ? t("auth.tokenStatusRevoked")
                  : row.expired
                    ? t("auth.tokenStatusExpired")
                    : t("auth.tokenStatusActive")}
              </td>
              <td>
                <button
                  type="button"
                  disabled={row.revoked || revokeMut.isPending}
                  onClick={() => {
                    if (window.confirm(t("auth.revokeConfirm"))) revokeMut.mutate(row.id);
                  }}
                >
                  {t("auth.revokeToken")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
