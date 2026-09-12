import { Alert, Button, Chip, Modal } from "@heroui/react";
import { useMutation } from "@tanstack/react-query";
import { connectTestManagedNe } from "../../services/api";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { useI18n } from "../../i18n";
import { formatSystemTime } from "../../utils/time";
import type { ManagedNeItem } from "../../types";
import { connectPillLevel } from "./connectStatus";

export type ManagedNeConnectDetailDialogProps = {
  row: ManagedNeItem | null;
  onClose: () => void;
  /** Called after a retest is submitted so parent can refresh/poll. */
  onRetestSubmitted?: (rowId: string) => void;
};

function chipColor(level: string): "success" | "danger" | "warning" | "default" {
  if (level === "up" || level === "pass") return "success";
  if (level === "down" || level === "fail") return "danger";
  if (level === "testing") return "warning";
  return "default";
}

export function ManagedNeConnectDetailDialog({
  row,
  onClose,
  onRetestSubmitted,
}: ManagedNeConnectDetailDialogProps) {
  const { t } = useI18n();
  const connectMutation = useMutation({
    mutationFn: connectTestManagedNe,
    onSuccess: () => {
      if (row) onRetestSubmitted?.(row.id);
    },
  });

  const open = Boolean(row);
  const level = row ? connectPillLevel(row.connect_status) : "unknown";

  return (
    <AppModalShell open={open} onClose={onClose} size="lg">
      <Modal.Header>
        <Modal.Heading>{t("managedNe.connectDetailTitle")}</Modal.Heading>
        <Modal.CloseTrigger />
      </Modal.Header>
      <Modal.Body className="flex flex-col gap-3">
        {row ? (
          <>
            <p className="text-sm text-muted">
              {row.name || row.ip_address} · {row.ip_address}:{row.port}/{row.protocol}
              {row.connect_tested_at
                ? ` · ${formatSystemTime(row.connect_tested_at, { assumeUtcNaive: true })}`
                : ""}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Chip size="sm" color={chipColor(level)} variant="soft">
                <Chip.Label>{row.connect_status}</Chip.Label>
              </Chip>
              {row.connect_message ? (
                <span className="connect-detail-summary text-sm">— {row.connect_message}</span>
              ) : null}
            </div>
            <pre className="connect-log">
              {row.connect_detail?.trim() ||
                row.connect_message?.trim() ||
                t("managedNe.connectDetailEmpty")}
            </pre>
          </>
        ) : null}
        {connectMutation.isError ? (
          <Alert status="danger">
            <Alert.Content>
              <Alert.Description>{String(connectMutation.error)}</Alert.Description>
            </Alert.Content>
          </Alert>
        ) : null}
      </Modal.Body>
      <Modal.Footer>
        <Button
          variant="primary"
          isDisabled={!row || connectMutation.isPending}
          onPress={() => {
            if (row) connectMutation.mutate([row.id]);
          }}
        >
          {connectMutation.isPending ? t("managedNe.connect.running") : t("managedNe.connect.retest")}
        </Button>
        <Button variant="tertiary" onPress={onClose}>
          {t("managedNe.form.cancel")}
        </Button>
      </Modal.Footer>
    </AppModalShell>
  );
}
