import { useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n";
import { LoginShell } from "./LoginShell";

export function LoginPage() {
  const { t } = useI18n();
  const { ready, user, login } = useAuth();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
    <LoginShell>
      <form className="login-card" onSubmit={(e) => void onSubmit(e)}>
        <div className="login-card__head">
          <h1 className="login-card__title">{t("auth.loginTitle")}</h1>
          <div className="login-card__brand" aria-label="NETX">
            NETX
          </div>
        </div>
        <label className="login-card__label">
          <span className="login-card__sr">{t("auth.username")}</span>
          <input
            autoFocus
            autoComplete="username"
            placeholder={t("auth.username")}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={busy || !ready}
          />
        </label>
        <label className="login-card__label">
          <span className="login-card__sr">{t("auth.password")}</span>
          <span className="login-card__password">
            <input
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              placeholder={t("auth.password")}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy || !ready}
            />
            <button
              type="button"
              className="login-card__eye"
              tabIndex={-1}
              aria-label={showPassword ? "Hide password" : "Show password"}
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? (
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
                  <path
                    d="M3 3l18 18M10.6 10.6a2.5 2.5 0 0 0 3.5 3.5M9.9 5.1A10 10 0 0 1 12 4.8c5.5 0 9.2 5.3 10.2 6.7a1.2 1.2 0 0 1 0 1.4c-.4.6-1.3 1.8-2.7 3M6.1 6.1C4.2 7.5 2.9 9.3 2 11.1a1.2 1.2 0 0 0 0 1.4C3 13.9 6.7 19.2 12 19.2c1.4 0 2.7-.3 3.9-.8"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
                  <path
                    d="M2 12.2C3 10.5 6.7 4.8 12 4.8s9 5.7 10 7.4a1.2 1.2 0 0 1 0 1.2C21 15.1 17.3 20.8 12 20.8S3 15.1 2 13.4a1.2 1.2 0 0 1 0-1.2Z"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinejoin="round"
                  />
                  <circle cx="12" cy="12.8" r="3" stroke="currentColor" strokeWidth="1.75" />
                </svg>
              )}
            </button>
          </span>
        </label>
        {error ? (
          <div className="login-card__error" role="alert">
            {error}
          </div>
        ) : null}
        <button type="submit" className="login-card__submit" disabled={busy || !ready || !password}>
          {busy ? t("auth.loggingIn") : t("auth.login")}
        </button>
      </form>
    </LoginShell>
  );
}
