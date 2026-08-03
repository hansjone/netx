import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { apiDelete, apiGet, apiPost } from "../services/api";
import { formatSystemTime } from "../utils/time";

type TokenRow = {
  id: string;
  name: string;
  user_id: string;
  username: string;
  scopes?: string[];
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
  scopes?: string[];
  is_active: boolean;
};

const ALL_SCOPE_KEYS = [
  "alarms:read",
  "ne:read",
  "ne:write",
  "ne:exec",
  "webcrt:session",
  "sql:query",
  "admin:users",
  "ops:write",
] as const;

const MCP_DEFAULT_SCOPES = ["alarms:read", "ne:read", "ne:exec"] as const;
const MCP_TOPO_WRITE_SCOPES = ["alarms:read", "ne:read", "ne:exec", "ne:write"] as const;

const SCOPE_LABEL_KEYS: Record<(typeof ALL_SCOPE_KEYS)[number], string> = {
  "alarms:read": "auth.scopeAlarmsRead",
  "ne:read": "auth.scopeNeRead",
  "ne:write": "auth.scopeNeWrite",
  "ne:exec": "auth.scopeNeExec",
  "webcrt:session": "auth.scopeWebcrt",
  "sql:query": "auth.scopeSql",
  "admin:users": "auth.scopeAdminUsers",
  "ops:write": "auth.scopeOpsWrite",
};

const EXPIRY_OPTIONS = [
  { value: 7, labelKey: "auth.expire7d" },
  { value: 30, labelKey: "auth.expire30d" },
  { value: 90, labelKey: "auth.expire90d" },
  { value: 365, labelKey: "auth.expire365d" },
  { value: 0, labelKey: "auth.expireNever" },
] as const;

function tokenStatusClass(row: TokenRow): string {
  if (row.revoked) return "pt-list-status--failed";
  if (row.expired) return "pt-list-status--warning";
  return "pt-list-status--ok";
}

function intersectScopes(available: string[], desired: readonly string[]): string[] {
  const allow = new Set(available);
  return desired.filter((s) => allow.has(s));
}

export function ApiTokensPage() {
  const { t } = useI18n();
  const { ready, user, isAdmin, scopes: myScopes } = useAuth();
  const { showOk, showError } = useToast();
  const qc = useQueryClient();
  const [name, setName] = useState("mcp");
  const [expiresInDays, setExpiresInDays] = useState(90);
  const [ownerUserId, setOwnerUserId] = useState("");
  const [inheritScopes, setInheritScopes] = useState(false);
  const [selectedScopes, setSelectedScopes] = useState<string[]>([...MCP_DEFAULT_SCOPES]);
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

  const items = useMemo(() => tokensQuery.data?.items || [], [tokensQuery.data]);
  const users = useMemo(() => usersQuery.data?.items || [], [usersQuery.data]);

  const availableScopes = useMemo(() => {
    if (ownerUserId) {
      const owner = users.find((u) => u.id === ownerUserId);
      return [...(owner?.scopes || [])].sort();
    }
    return [...(myScopes || [])].sort();
  }, [ownerUserId, users, myScopes]);

  useEffect(() => {
    setSelectedScopes((prev) => {
      const next = prev.filter((s) => availableScopes.includes(s));
      if (next.length) return next;
      return intersectScopes(availableScopes, MCP_DEFAULT_SCOPES);
    });
  }, [availableScopes]);

  const createMut = useMutation({
    mutationFn: () =>
      apiPost<{ token: TokenRow & { token: string } }>("/v1/api-tokens", {
        name: name.trim() || "mcp",
        expires_in_days: expiresInDays,
        user_id: isAdmin && ownerUserId ? ownerUserId : undefined,
        scopes: inheritScopes ? [] : selectedScopes,
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

  const onCreate = (e: FormEvent) => {
    e.preventDefault();
    if (!inheritScopes && selectedScopes.length === 0) {
      showError(t("auth.scopesRequired"));
      return;
    }
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

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope].sort(),
    );
  };

  const applyPreset = (desired: readonly string[]) => {
    setInheritScopes(false);
    setSelectedScopes(intersectScopes(availableScopes, desired));
  };

  const formatScopes = (scopes: string[] | undefined) => {
    if (!scopes || scopes.length === 0) return t("auth.scopesInheritShort");
    return scopes.join(", ");
  };

  return (
    <div className="page-stack system-page">
      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("auth.apiKeysTitle")}</h2>
        </div>
        <p className="panel__hint">{t("auth.apiKeysHint")}</p>

        <div className="pt-list">
          <form className="token-create" onSubmit={onCreate}>
            <div className="filter-inline">
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
            </div>

            <div className="token-scopes">
              <div className="token-scopes__head">
                <strong>{t("auth.scopesTitle")}</strong>
                <span className="panel__hint" style={{ margin: 0 }}>
                  {t("auth.scopesHint")}
                </span>
              </div>
              <div className="token-scopes__presets">
                <button type="button" className="btn--ghost btn--sm" onClick={() => applyPreset(MCP_DEFAULT_SCOPES)}>
                  {t("auth.scopePresetMcp")}
                </button>
                <button
                  type="button"
                  className="btn--ghost btn--sm"
                  onClick={() => applyPreset(MCP_TOPO_WRITE_SCOPES)}
                  disabled={!availableScopes.includes("ne:write")}
                >
                  {t("auth.scopePresetTopoWrite")}
                </button>
              </div>
              <label className="token-scopes__inherit">
                <input
                  type="checkbox"
                  checked={inheritScopes}
                  onChange={(e) => setInheritScopes(e.target.checked)}
                />
                {t("auth.scopesInherit")}
              </label>
              <div className={`token-scopes__grid${inheritScopes ? " is-disabled" : ""}`}>
                {ALL_SCOPE_KEYS.filter((s) => availableScopes.includes(s)).map((scope) => (
                  <label key={scope}>
                    <input
                      type="checkbox"
                      disabled={inheritScopes}
                      checked={selectedScopes.includes(scope)}
                      onChange={() => toggleScope(scope)}
                    />
                    {t(SCOPE_LABEL_KEYS[scope])}
                  </label>
                ))}
                {!availableScopes.length ? (
                  <span className="muted">{t("auth.scopesNoneAvailable")}</span>
                ) : null}
              </div>
            </div>
          </form>

          {createdPlain ? (
            <div className="token-once">
              <div className="panel__hint" style={{ margin: 0 }}>
                {t("auth.tokenOnceHint")}
              </div>
              <code>{createdPlain}</code>
              <button type="button" onClick={() => void copyToken()}>
                {t("auth.copyToken")}
              </button>
            </div>
          ) : null}

          {tokensQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

          {!items.length && !tokensQuery.isLoading ? (
            <div className="pt-list-empty">
              <p>{t("common.empty")}</p>
            </div>
          ) : (
            <div className="pt-list-table-wrap">
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th>{t("auth.tokenName")}</th>
                    <th>{t("auth.tokenOwner")}</th>
                    <th>{t("auth.scopesCol")}</th>
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
                      <td className="pt-list-task-name">{row.name}</td>
                      <td>{row.username || row.user_id}</td>
                      <td className="token-scopes-cell" title={formatScopes(row.scopes)}>
                        {formatScopes(row.scopes)}
                      </td>
                      <td className="pt-list-time">
                        {row.created_at ? formatSystemTime(row.created_at) : t("common.empty")}
                      </td>
                      <td className="pt-list-time">
                        {row.expires_at ? formatSystemTime(row.expires_at) : t("auth.expireNever")}
                      </td>
                      <td className="pt-list-time">
                        {row.last_used_at ? formatSystemTime(row.last_used_at) : t("common.empty")}
                      </td>
                      <td>
                        <span className={`pt-list-status ${tokenStatusClass(row)}`}>
                          {row.revoked
                            ? t("auth.tokenStatusRevoked")
                            : row.expired
                              ? t("auth.tokenStatusExpired")
                              : t("auth.tokenStatusActive")}
                        </span>
                      </td>
                      <td>
                        <div className="btn-row pt-list-actions table-actions">
                          <button
                            type="button"
                            className="btn--danger"
                            disabled={row.revoked || revokeMut.isPending}
                            onClick={() => {
                              if (window.confirm(t("auth.revokeConfirm"))) revokeMut.mutate(row.id);
                            }}
                          >
                            {t("auth.revokeToken")}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
