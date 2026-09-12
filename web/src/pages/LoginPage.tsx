import { Alert, Button, Card, Form, Input, InputGroup, Label, TextField } from "@heroui/react";
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
      <Card className="login-card login-card--heroui" variant="transparent">
        <Card.Header className="login-card__head">
          <Card.Title className="login-card__title">{t("auth.loginTitle")}</Card.Title>
          <div className="login-card__brand" aria-label="NETX">
            NETX
          </div>
        </Card.Header>
        <Card.Content>
          <Form className="login-card__form" onSubmit={(e) => void onSubmit(e)}>
            <TextField
              fullWidth
              name="username"
              value={username}
              onChange={setUsername}
              isDisabled={busy || !ready}
              autoFocus
              autoComplete="username"
            >
              <Label className="login-card__sr">{t("auth.username")}</Label>
              <Input placeholder={t("auth.username")} />
            </TextField>

            <TextField
              fullWidth
              name="password"
              value={password}
              onChange={setPassword}
              isDisabled={busy || !ready}
              autoComplete="current-password"
            >
              <Label className="login-card__sr">{t("auth.password")}</Label>
              <InputGroup fullWidth>
                <InputGroup.Input
                  type={showPassword ? "text" : "password"}
                  placeholder={t("auth.password")}
                />
                <InputGroup.Suffix>
                  <Button
                    isIconOnly
                    size="sm"
                    variant="ghost"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    onPress={() => setShowPassword((v) => !v)}
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
                  </Button>
                </InputGroup.Suffix>
              </InputGroup>
            </TextField>

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
