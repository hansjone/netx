import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { apiGet, apiPatch, apiPost } from "../services/api";

type UserRow = {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
  created_at?: string | null;
};

export function UsersPage() {
  const { t } = useI18n();
  const { isAdmin, ready } = useAuth();
  const { showOk, showError } = useToast();
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [resetPwd, setResetPwd] = useState<Record<string, string>>({});

  const usersQuery = useQuery({
    queryKey: ["appUsers"],
    queryFn: () => apiGet<{ items: UserRow[] }>("/v1/users"),
    enabled: ready && isAdmin,
  });

  const createMut = useMutation({
    mutationFn: () => apiPost("/v1/users", { username, password, role }),
    onSuccess: async () => {
      setUsername("");
      setPassword("");
      setRole("user");
      showOk(t("auth.userCreated"));
      await qc.invalidateQueries({ queryKey: ["appUsers"] });
    },
    onError: (e) => showError(String(e instanceof Error ? e.message : e)),
  });

  const patchMut = useMutation({
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) =>
      apiPatch(`/v1/users/${encodeURIComponent(payload.id)}`, payload.body),
    onSuccess: async () => {
      showOk(t("auth.userUpdated"));
      await qc.invalidateQueries({ queryKey: ["appUsers"] });
    },
    onError: (e) => showError(String(e instanceof Error ? e.message : e)),
  });

  const items = useMemo(() => usersQuery.data?.items || [], [usersQuery.data]);

  if (ready && !isAdmin) return <Navigate to="/" replace />;

  const onCreate = (e: FormEvent) => {
    e.preventDefault();
    createMut.mutate();
  };

  return (
    <div className="page-stack system-page">
      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("auth.usersTitle")}</h2>
        </div>
        <p className="panel__hint">{t("auth.usersHint")}</p>

        <div className="pt-list">
          <form className="filter-inline" onSubmit={onCreate}>
            <input
              placeholder={t("auth.username")}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder={t("auth.password")}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
            />
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="user">{t("auth.roleUser")}</option>
              <option value="admin">{t("auth.roleAdmin")}</option>
            </select>
            <button type="submit" disabled={createMut.isPending}>
              {t("auth.addUser")}
            </button>
          </form>

          {usersQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

          {!items.length && !usersQuery.isLoading ? (
            <div className="pt-list-empty">
              <p>{t("common.empty")}</p>
            </div>
          ) : (
            <div className="pt-list-table-wrap">
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th>{t("auth.username")}</th>
                    <th>{t("auth.role")}</th>
                    <th>{t("auth.status")}</th>
                    <th>{t("auth.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((u) => (
                    <tr key={u.id}>
                      <td className="pt-list-task-name">{u.username}</td>
                      <td>{u.role === "admin" ? t("auth.roleAdmin") : t("auth.roleUser")}</td>
                      <td>
                        <span
                          className={`pt-list-status ${
                            u.is_active ? "pt-list-status--ok" : "pt-list-status--other"
                          }`}
                        >
                          {u.is_active ? t("auth.active") : t("auth.disabled")}
                        </span>
                      </td>
                      <td>
                        <div className="btn-row pt-list-actions table-actions">
                          <button
                            type="button"
                            onClick={() =>
                              patchMut.mutate({ id: u.id, body: { is_active: !u.is_active } })
                            }
                          >
                            {u.is_active ? t("auth.disable") : t("auth.enable")}
                          </button>
                          <select
                            value={u.role}
                            onChange={(e) =>
                              patchMut.mutate({ id: u.id, body: { role: e.target.value } })
                            }
                          >
                            <option value="user">{t("auth.roleUser")}</option>
                            <option value="admin">{t("auth.roleAdmin")}</option>
                          </select>
                          <input
                            type="password"
                            placeholder={t("auth.newPassword")}
                            value={resetPwd[u.id] || ""}
                            onChange={(e) =>
                              setResetPwd((m) => ({ ...m, [u.id]: e.target.value }))
                            }
                            style={{ width: 140, minWidth: 140, flex: "0 0 auto" }}
                          />
                          <button
                            type="button"
                            disabled={!resetPwd[u.id] || resetPwd[u.id].length < 6}
                            onClick={() => {
                              const pwd = resetPwd[u.id];
                              patchMut.mutate({ id: u.id, body: { password: pwd } });
                              setResetPwd((m) => ({ ...m, [u.id]: "" }));
                            }}
                          >
                            {t("auth.resetPassword")}
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
