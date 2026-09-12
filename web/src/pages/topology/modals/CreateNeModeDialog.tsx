import { Button, Modal } from "@heroui/react";
import { TopoModalShell } from "../../../components/ui/TopoModalShell";
import { useI18n } from "../../../i18n";

export type CreateNeModeDialogProps = {
  open: boolean;
  onClose: () => void;
  onPickManaged: () => void;
  onPickPlaceholder: () => void;
};

/** Choose formal Managed NE vs topology placeholder before opening the create flow. */
export function CreateNeModeDialog({
  open,
  onClose,
  onPickManaged,
  onPickPlaceholder,
}: CreateNeModeDialogProps) {
  const { t } = useI18n();

  return (
    <TopoModalShell open={open} onClose={onClose} size="sm">
      <Modal.Header>
        <Modal.Heading>{t("topology.createNe")}</Modal.Heading>
        <Modal.CloseTrigger />
      </Modal.Header>
      <Modal.Body className="flex flex-col gap-3">
        <p className="text-sm text-muted">{t("topology.createNeModeHint")}</p>
        <div className="flex flex-col gap-2.5">
          <Button
            variant="primary"
            className="h-auto flex-col items-start gap-1 whitespace-normal px-4 py-3 text-left"
            onPress={onPickManaged}
          >
            <span className="font-semibold">{t("topology.createNeManaged")}</span>
            <span className="text-sm font-normal opacity-90">{t("topology.createNeManagedHint")}</span>
          </Button>
          <Button
            variant="secondary"
            className="h-auto flex-col items-start gap-1 whitespace-normal px-4 py-3 text-left"
            onPress={onPickPlaceholder}
          >
            <span className="font-semibold">{t("topology.createNePlaceholder")}</span>
            <span className="text-sm font-normal text-muted">{t("topology.createNePlaceholderHint")}</span>
          </Button>
        </div>
      </Modal.Body>
    </TopoModalShell>
  );
}
