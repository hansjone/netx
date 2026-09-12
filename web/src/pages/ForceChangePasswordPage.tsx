import { Alert, Button, Card, Form, Input, Label, TextField } from "@heroui/react";
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
    if (newPassword.length < 8) {
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
      <Card className="login-card login-card--heroui" variant="transparent">
        <Card.Header className="login-card__head">
          <Card.Title className="login-card__title">{t("auth.forceChangeTitle")}</Card.Title>
          <div className="login-card__brand" aria-label="NETX">
            NETX
          </div>
        </Card.Header>
        <Card.Content>
          <p className="login-card__hint">
            {t("auth.forceChangeHint", { user: user?.username || "admin" })}
          </p>
          <Form className="login-card__form" onSubmit={(e) => void onSubmit(e)}>
            <TextField
              fullWidth
              type="password"
              autoComplete="current-password"
              autoFocus
              value={oldPassword}
              onChange={setOldPassword}
              isDisabled={busy}
              isRequired
            >
              <Label className="login-card__sr">{t("auth.oldPassword")}</Label>
              <Input placeholder={t("auth.oldPassword")} />
            </TextField>
            <TextField
              fullWidth
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={setNewPassword}
              isDisabled={busy}
              isRequired
              minLength={8}
            >
              <Label className="login-card__sr">{t("auth.newPassword")}</Label>
              <Input placeholder={t("auth.newPassword")} />
            </TextField>
            <TextField
              fullWidth
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={setConfirm}
              isDisabled={busy}
              isRequired
              minLength={8}
            >
              <Label className="login-card__sr">{t("auth.confirmPassword")}</Label>
              <Input placeholder={t("auth.confirmPassword")} />
            </TextField>
            {error ? (
              <Alert status="danger" className="login-card__error">
                <Alert.Content>
                  <Alert.Description>{error}</Alert.Description>
                </Alert.Content>
              </Alert>
            ) : null}
            <Button type="submit" variant="primary" fullWidth isDisabled={busy}>
              {busy ? t("auth.savingPassword") : t("auth.savePassword")}
            </Button>
            <Button
              type="button"
              variant="tertiary"
              fullWidth
              isDisabled={busy}
              onPress={() => void logout()}
            >
              {t("auth.logout")}
            </Button>
          </Form>
        </Card.Content>
      </Card>
    </LoginShell>
  );
}
