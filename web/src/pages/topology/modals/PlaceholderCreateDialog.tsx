import { Button, Input, Label, Modal, TextField } from "@heroui/react";
import { TopoModalShell } from "../../../components/ui/TopoModalShell";
import { useI18n } from "../../../i18n";

export type PlaceholderCreateDialogState = {
  flowX: number;
  flowY: number;
  name: string;
  ip_address: string;
};

export type PlaceholderCreateDialogProps = {
  dialog: PlaceholderCreateDialogState | null;
  busy: boolean;
  onChange: (patch: Partial<Pick<PlaceholderCreateDialogState, "name" | "ip_address">>) => void;
  onClose: () => void;
  onSubmit: () => void;
};

export function PlaceholderCreateDialog({
  dialog,
  busy,
  onChange,
  onClose,
  onSubmit,
}: PlaceholderCreateDialogProps) {
  const { t } = useI18n();
  const open = Boolean(dialog);

  return (
    <TopoModalShell
      open={open}
      onClose={onClose}
      dismissible={!busy}
      size="sm"
    >
      <Modal.Header>
        <Modal.Heading>{t("topology.createNeTitle")}</Modal.Heading>
        <Modal.CloseTrigger isDisabled={busy} />
      </Modal.Header>
      <Modal.Body className="flex flex-col gap-3">
        <p className="text-sm text-muted">{t("topology.createNeHint")}</p>
        <TextField
          fullWidth
          autoFocus
          isRequired
          value={dialog?.name ?? ""}
          onChange={(name) => onChange({ name })}
          isDisabled={busy}
          onKeyDown={(e) => {
            if (e.key === "Enter") void onSubmit();
          }}
        >
          <Label>{t("topology.createNeName")}</Label>
          <Input placeholder={t("topology.createNeNamePh")} />
        </TextField>
        <TextField
          fullWidth
          value={dialog?.ip_address ?? ""}
          onChange={(ip_address) => onChange({ ip_address })}
          isDisabled={busy}
          onKeyDown={(e) => {
            if (e.key === "Enter") void onSubmit();
          }}
        >
          <Label>{t("topology.createNeIp")}</Label>
          <Input placeholder={t("topology.createNeIpPh")} />
        </TextField>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="tertiary" isDisabled={busy} onPress={onClose}>
          {t("topology.discoverClose")}
        </Button>
        <Button variant="primary" isDisabled={busy} onPress={() => void onSubmit()}>
          {busy ? t("topology.createNeBusy") : t("topology.createNePlaceholder")}
        </Button>
      </Modal.Footer>
    </TopoModalShell>
  );
}
