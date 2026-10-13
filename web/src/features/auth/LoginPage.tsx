import { Alert, Button, Card, Form, Input, Label, TextField } from "@heroui/react";
import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { useI18n } from "../../i18n";
import { LoginShell } from "./LoginShell";
import { DropletGlassFilter } from "./DropletGlass";
import { ApiRequestError } from "../../services/api/http";

const LAST_USER_KEY = "netx.login.lastUser";

function readLastUser(): string {
  try {
    return localStorage.getItem(LAST_USER_KEY) || "";
  } catch {
    return "";
  }
}

/** Map the login endpoint's error codes to readable text. */
function loginErrorText(err: unknown, t: (key: string, vars?: Record<string, string | number>) => string): string {
  if (err instanceof ApiRequestError) {
    if (err.detail === "invalid_credentials") return t("auth.badCredentials");
    const d = err.detail as { error?: string; retry_after_sec?: number } | null;
    if (d && typeof d === "object" && d.error === "login_locked") {
      return t("auth.loginLocked", { minutes: Math.max(1, Math.ceil((d.retry_after_sec || 60) / 60)) });
    }
  }
  return String(err instanceof Error ? err.message : err) || t("auth.loginFailed");
}

export function LoginPage() {
  const { t } = useI18n();
  const { ready, user, login } = useAuth();
  const [params] = useSearchParams();
  const [lastUser] = useState(readLastUser);
  const [username, setUsername] = useState(lastUser || "admin");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  if (ready && user) {
    const next = params.get("next") || "/";
    return <Navigate to={next.startsWith("/") ? next : "/"} replace />;
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    const name = username.trim();
    try {
      await login(name, password);
      try {
        localStorage.setItem(LAST_USER_KEY, name);
      } catch {
        /* storage blocked — nothing to remember */
      }
    } catch (err) {
      setError(loginErrorText(err, t));
    } finally {
      setBusy(false);
    }
  };

  const syncCaps = (e: KeyboardEvent) => setCapsLock(e.getModifierState("CapsLock"));

  return (
    <LoginShell>
      <DropletGlassFilter id="login-droplet" target={cardRef} />
      <Card ref={cardRef} className="login-card login-card--heroui" variant="transparent">
        <Card.Header className="login-card__head">
          <div className="login-card__brand" aria-hidden="true">
            NETX
          </div>
          <Card.Title className="login-card__sr">{t("auth.loginTitle")}</Card.Title>
        </Card.Header>
        <Card.Content>
          <Form className="login-card__form" onSubmit={(e) => void onSubmit(e)}>
            <TextField
              fullWidth
              name="username"
              value={username}
              onChange={(v) => {
                setUsername(v);
                setError("");
              }}
              isDisabled={busy || !ready}
              autoFocus={!lastUser}
              autoComplete="username"
            >
              <Label className="login-card__sr">{t("auth.username")}</Label>
              <Input placeholder={t("auth.username")} />
            </TextField>

            <div className="login-card__password" onKeyDown={syncCaps} onKeyUp={syncCaps}>
              <TextField
                fullWidth
                name="password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(v) => {
                  setPassword(v);
                  setError("");
                }}
                isDisabled={busy || !ready}
                autoFocus={!!lastUser}
                autoComplete="current-password"
              >
                <Label className="login-card__sr">{t("auth.password")}</Label>
                <Input placeholder={t("auth.password")} />
              </TextField>
              {capsLock ? <span className="login-card__caps">{t("auth.capsLock")}</span> : null}
              <button
                type="button"
                className="login-card__eye"
                aria-label={showPassword ? "Hide password" : "Show password"}
                tabIndex={-1}
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
            </div>

            {error ? (
              <Alert status="danger" className="login-card__error">
                <Alert.Content>
                  <Alert.Description>{error}</Alert.Description>
                </Alert.Content>
              </Alert>
            ) : null}

            <Button
              type="submit"
              variant="primary"
              fullWidth
              className="login-card__submit"
              isDisabled={busy || !ready || !password}
            >
              {busy ? t("auth.loggingIn") : t("auth.login")}
            </Button>
          </Form>
        </Card.Content>
      </Card>
    </LoginShell>
  );
}
