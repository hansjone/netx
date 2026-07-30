import { useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";

export function LoginPage() {
  const { t } = useI18n();
  const { ready, user, login } = useAuth();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (ready && user) {
    const next = params.get("next") || "/";
    return <Navigate to={next.startsWith("/") ? next : "/"} replace />;
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err) || t("auth.loginFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={(e) => void onSubmit(e)}>
        <div className="login-card__brand">NetX</div>
        <h1 className="login-card__title">{t("auth.loginTitle")}</h1>
        <p className="login-card__hint">{t("auth.loginHint")}</p>
        <label className="login-card__label">
          {t("auth.username")}
          <input
            autoFocus
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={busy || !ready}
          />
        </label>
        <label className="login-card__label">
          {t("auth.password")}
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy || !ready}
          />
        </label>
        {error ? <div className="login-card__error">{error}</div> : null}
        <button type="submit" className="login-card__submit" disabled={busy || !ready || !password}>
          {busy ? t("auth.loggingIn") : t("auth.login")}
        </button>
      </form>
    </div>
  );
}
