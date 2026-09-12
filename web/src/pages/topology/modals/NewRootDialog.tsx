import { Button, Input, Label, Modal, TextField } from "@heroui/react";
import { TopoModalShell } from "../../../components/ui/TopoModalShell";
import { useI18n } from "../../../i18n";

export type NewRootDialogProps = {
  dialog: { name: string } | null;
  pending: boolean;
  onNameChange: (name: string) => void;
  onClose: () => void;
  onSubmit: () => void;
};

export function NewRootDialog({ dialog, pending, onNameChange, onClose, onSubmit }: NewRootDialogProps) {
  const { t } = useI18n();
  const open = Boolean(dialog);

  return (
    <TopoModalShell open={open} onClose={onClose} dismissible={!pending} size="sm">
      <Modal.Header>
        <Modal.Heading>{t("topology.newRegion")}</Modal.Heading>
        <Modal.CloseTrigger isDisabled={pending} />
      </Modal.Header>
      <Modal.Body className="flex flex-col gap-3">
        <p className="text-sm text-muted">{t("topology.folderHint")}</p>
        <TextField
          fullWidth
          autoFocus
          value={dialog?.name ?? ""}
          onChange={onNameChange}
          isDisabled={pending}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSubmit();
          }}
        >
          <Label>{t("topology.newRegionPrompt")}</Label>
          <Input />
        </TextField>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="tertiary" size="sm" isDisabled={pending} onPress={onClose}>
          {t("topology.discoverClose")}
        </Button>
        <Button
          variant="primary"
          size="sm"
          isDisabled={pending || !String(dialog?.name || "").trim()}
          onPress={onSubmit}
        >
          {pending ? "…" : t("topology.newRegion")}
        </Button>
      </Modal.Footer>
    </TopoModalShell>
  );
}
