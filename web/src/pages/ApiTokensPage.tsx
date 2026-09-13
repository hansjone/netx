import { Button, Checkbox, Input, Modal } from "@heroui/react";
import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { AppModalShell } from "../components/ui/AppModalShell";
import { FieldSelect } from "../components/ui/FieldSelect";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { apiDelete, apiGet, apiPatch, apiPost } from "../services/api";
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
  "sql:query",
  "webcrt:session",
  "admin:users",
  "ops:write",
] as const;

/** Applied on create; refine later via「改权限」. */
const CREATE_DEFAULT_SCOPES = ["alarms:read", "ne:read", "ne:exec", "ne:write"] as const;

const SCOPE_LABEL_KEYS: Record<(typeof ALL_SCOPE_KEYS)[number], string> = {
  "alarms:read": "auth.scopeAlarmsRead",
  "ne:read": "auth.scopeNeRead",
  "ne:write": "auth.scopeNeWrite",
  "ne:exec": "auth.scopeNeExec",
  "sql:query": "auth.scopeSql",
  "webcrt:session": "auth.scopeWebcrt",
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
  const [createdPlain, setCreatedPlain] = useState("");
  const [editingRow, setEditingRow] = useState<TokenRow | null>(null);
  const [editScopes, setEditScopes] = useState<string[]>([]);

  const tokensQuery = useQuery({
    queryKey: ["apiTokens"],
    queryFn: () =>
      apiGet<{ items: TokenRow[]; active_count?: number; max_count?: number }>("/v1/api-tokens"),
    enabled: ready,
  });

  const usersQuery = useQuery({
    queryKey: ["appUsers"],
    queryFn: () => apiGet<{ items: UserRow[] }>("/v1/users"),
    enabled: ready && isAdmin,
  });

  const items = useMemo(() => tokensQuery.data?.items || [], [tokensQuery.data]);
  const users = useMemo(() => usersQuery.data?.items || [], [usersQuery.data]);
  const tokenMax = Number(tokensQuery.data?.max_count ?? 20);
  const tokenActive = Number(tokensQuery.data?.active_count ?? items.filter((r) => !r.revoked).length);
  const atTokenLimit = tokenMax > 0 && tokenActive >= tokenMax;

  const availableForCreate = useMemo(() => {
    if (ownerUserId) {
      const owner = users.find((u) => u.id === ownerUserId);
      return [...(owner?.scopes || [])].sort();
    }
    return [...(myScopes || [])].sort();
  }, [ownerUserId, users, myScopes]);

  const editAvailable = useMemo(() => {
    if (!editingRow) return [];
    if (editingRow.user_id === user?.id) return [...(myScopes || [])].sort();
    const owner = users.find((u) => u.id === editingRow.user_id);
    return [...(owner?.scopes || myScopes || [])].sort();
  }, [editingRow, user?.id, users, myScopes]);

  const createMut = useMutation({
    mutationFn: () => {
      const scopes = intersectScopes(availableForCreate, CREATE_DEFAULT_SCOPES);
      if (!scopes.length) {
        throw new Error(t("auth.scopesNoneAvailable"));
      }
      return apiPost<{ token: TokenRow & { token: string } }>("/v1/api-tokens", {
        name: name.trim() || "mcp",
        expires_in_days: expiresInDays,
        user_id: isAdmin && ownerUserId ? ownerUserId : undefined,
        scopes,
      });
    },
    onSuccess: async (data) => {
      setCreatedPlain(data.token.token);
      showOk(t("auth.tokenCreated"));
      await qc.invalidateQueries({ queryKey: ["apiTokens"] });
    },
    onError: (e) => showError(String(e instanceof Error ? e.message : e)),
  });

  const updateMut = useMutation({
    mutationFn: (payload: { id: string; scopes: string[] }) =>
      apiPatch<{ token: TokenRow }>(`/v1/api-tokens/${encodeURIComponent(payload.id)}`, {
        scopes: payload.scopes,
      }),
    onSuccess: async () => {
      showOk(t("auth.tokenUpdated"));
      setEditingRow(null);
      await qc.invalidateQueries({ queryKey: ["apiTokens"] });
    },
    onError: (e) => showError(String(e instanceof Error ? e.message : e)),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => apiDelete(`/v1/api-tokens/${encodeURIComponent(id)}`),
    onSuccess: async () => {
      showOk(t("auth.tokenRevoked"));
      setEditingRow(null);
      await qc.invalidateQueries({ queryKey: ["apiTokens"] });
    },
    onError: (e) => showError(String(e instanceof Error ? e.message : e)),
  });

  const onCreate = (e: FormEvent) => {
    e.preventDefault();
    setCreatedPlain("");
    createMut.mutate();
  };

  const dismissCreatedPlain = () => setCreatedPlain("");

  const copyToken = async () => {
    try {
      await navigator.clipboard.writeText(createdPlain);
      showOk(t("auth.tokenCopied"));
      dismissCreatedPlain();
    } catch {
      showError(t("auth.tokenCopyFailed"));
    }
  };

  const toggleEditScope = (scope: string) => {
    setEditScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope].sort(),
    );
  };

  const startEdit = (row: TokenRow) => {
    const available =
      row.user_id === user?.id
        ? [...(myScopes || [])]
        : [...(users.find((u) => u.id === row.user_id)?.scopes || myScopes || [])];
    const current = row.scopes?.length ? [...row.scopes] : [...available];
    setEditingRow(row);
    setEditScopes(intersectScopes(available, current.length ? current : CREATE_DEFAULT_SCOPES));
  };

  const formatScopes = (scopes: string[] | undefined) => {
    if (!scopes || scopes.length === 0) return t("auth.scopesUnsetLegacy");
    return scopes.join(", ");
  };

  return (
    <div className="page-stack system-page">
      <section className="panel">
        <div className="panel__toolbar">
          <h2 className="token-page__title">
            {t("auth.apiKeysTitle")}
            <span className="help-q" tabIndex={0} aria-label={t("auth.apiKeysHelp", { max: tokenMax || 20 })}>
              ?
              <span className="help-q__tip" role="tooltip">
                {t("auth.apiKeysHelp", { max: tokenMax || 20 })}
              </span>
            </span>
          </h2>
          {tokenMax > 0 ? (
            <span className="muted" style={{ fontSize: 13 }}>
              {t("auth.tokenQuota", { active: tokenActive, max: tokenMax })}
            </span>
          ) : null}
        </div>

        <div className="pt-list">
          {atTokenLimit ? (
            <p className="panel__hint panel__hint--live">{t("auth.tokenLimitReached", { max: tokenMax })}</p>
          ) : null}
          <form className="token-create" onSubmit={onCreate}>
            <div className="filter-inline">
              <Input
                placeholder={t("auth.tokenName")}
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                disabled={atTokenLimit}
              />
              <FieldSelect
                aria-label={t("auth.expiresIn")}
                value={String(expiresInDays)}
                onChange={(e) => setExpiresInDays(Number(e.target.value))}
                disabled={atTokenLimit}
              >
                {EXPIRY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {t(opt.labelKey)}
                  </option>
                ))}
              </FieldSelect>
              {isAdmin ? (
                <FieldSelect
                  aria-label={t("auth.tokenOwner")}
                  value={ownerUserId}
                  onChange={(e) => setOwnerUserId(e.target.value)}
                  disabled={atTokenLimit}
                >
                  <option value="">{t("auth.tokenOwnerSelf", { user: user?.username || "" })}</option>
                  {users
                    .filter((u) => u.is_active)
                    .map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.username} ({u.role})
                      </option>
                    ))}
                </FieldSelect>
              ) : null}
              <Button
                type="submit"
                size="sm"
                variant="primary"
                isDisabled={createMut.isPending || atTokenLimit}
              >
                {t("auth.createToken")}
              </Button>
            </div>
          </form>

          {createdPlain ? (
            <div className="token-once">
              <div className="panel__hint" style={{ margin: 0 }}>
                {t("auth.tokenOnceHint")}
              </div>
              <code>{createdPlain}</code>
              <Button size="sm" variant="primary" onPress={() => void copyToken()}>
                {t("auth.copyToken")}
              </Button>
              <Button size="sm" variant="tertiary" onPress={dismissCreatedPlain}>
                {t("common.close")}
              </Button>
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
                          <Button
                            size="sm"
                            variant="ghost"
                            isDisabled={row.revoked || row.expired}
                            onPress={() => startEdit(row)}
                          >
                            {t("auth.editScopes")}
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            isDisabled={row.revoked || revokeMut.isPending}
                            onPress={() => {
                              if (window.confirm(t("auth.revokeConfirm"))) revokeMut.mutate(row.id);
                            }}
                          >
                            {t("auth.revokeToken")}
                          </Button>
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

      <AppModalShell open={Boolean(editingRow)} onClose={() => setEditingRow(null)} size="md">
        <Modal.Header>
          <Modal.Heading>{t("auth.editScopes")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {editingRow ? (
            <p className="text-sm text-muted">
              {editingRow.name}
              {editingRow.username ? ` · ${editingRow.username}` : ""}
            </p>
          ) : null}
          <ul className="token-scopes-modal__list">
            {ALL_SCOPE_KEYS.filter((s) => editAvailable.includes(s)).map((scope) => (
              <li key={scope}>
                <Checkbox
                  isSelected={editScopes.includes(scope)}
                  onChange={() => toggleEditScope(scope)}
                >
                  <Checkbox.Control>
                    <Checkbox.Indicator />
                  </Checkbox.Control>
                  <Checkbox.Content>{t(SCOPE_LABEL_KEYS[scope])}</Checkbox.Content>
                </Checkbox>
              </li>
            ))}
            {!editAvailable.length ? (
              <li className="muted">{t("auth.scopesNoneAvailable")}</li>
            ) : null}
          </ul>
        </Modal.Body>
        <Modal.Footer>
          <Button
            variant="primary"
            isDisabled={updateMut.isPending || editScopes.length === 0 || !editingRow}
            onPress={() => {
              if (editingRow) updateMut.mutate({ id: editingRow.id, scopes: editScopes });
            }}
          >
            {t("auth.saveScopes")}
          </Button>
          <Button variant="tertiary" onPress={() => setEditingRow(null)}>
            {t("common.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </div>
  );
}
