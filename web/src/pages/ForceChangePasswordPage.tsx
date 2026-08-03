import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";
import { apiPost } from "../services/api";
import { LoginShell } from "./LoginShell";

export function ForceChangePasswordPage() {
  const { t } = useI18n();
  const { user, refreshMe, logout } = useAuth();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (newPassword.length < 6) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    if (newPassword !== confirm) {
      setError(t("auth.passwordMismatch"));
      return;
    }
    if (newPassword === oldPassword || newPassword === "admin123") {
      setError(t("auth.passwordMustChange"));
      return;
    }
    setBusy(true);
    try {
      await apiPost("/v1/auth/change-password", {
        old_password: oldPassword,
        new_password: newPassword,
      });
      await refreshMe();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <LoginShell>
      <form className="login-card" onSubmit={(e) => void onSubmit(e)}>
        <div className="login-card__head">
          <h1 className="login-card__title">{t("auth.forceChangeTitle")}</h1>
          <div className="login-card__brand" aria-label="NETX">
            NETX
          </div>
        </div>
        <p className="login-card__hint">
          {t("auth.forceChangeHint", { user: user?.username || "admin" })}
        </p>
        <label className="login-card__label">
          <span className="login-card__sr">{t("auth.oldPassword")}</span>
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            placeholder={t("auth.oldPassword")}
            value={oldPassword}
            onChange={(e) => setOldPassword(e.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="login-card__label">
          <span className="login-card__sr">{t("auth.newPassword")}</span>
          <input
            type="password"
            autoComplete="new-password"
            placeholder={t("auth.newPassword")}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            disabled={busy}
            required
            minLength={6}
          />
        </label>
        <label className="login-card__label">
          <span className="login-card__sr">{t("auth.confirmPassword")}</span>
          <input
            type="password"
            autoComplete="new-password"
            placeholder={t("auth.confirmPassword")}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={busy}
            required
            minLength={6}
          />
        </label>
        {error ? (
          <div className="login-card__error" role="alert">
            {error}
          </div>
        ) : null}
        <button type="submit" className="login-card__submit" disabled={busy}>
          {busy ? t("auth.savingPassword") : t("auth.savePassword")}
        </button>
        <button
          type="button"
          className="login-card__submit login-card__submit--ghost"
          disabled={busy}
          onClick={() => void logout()}
        >
          {t("auth.logout")}
        </button>
      </form>
    </LoginShell>
  );
}
