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
  if (!open) return null;

  return (
    <div className="topo-modal" role="dialog" aria-modal="true" aria-labelledby="topo-create-ne-mode-title">
      <div className="topo-modal__backdrop" onClick={onClose} />
      <div className="topo-modal__panel" style={{ maxWidth: 440 }}>
        <div className="topo-modal__head">
          <strong id="topo-create-ne-mode-title">{t("topology.createNe")}</strong>
          <button type="button" className="btn btn--sm btn--ghost" onClick={onClose}>
            {t("topology.discoverClose")}
          </button>
        </div>
        <p className="panel__hint topo-modal__hint">{t("topology.createNeModeHint")}</p>
        <div className="topo-create-ne-mode" style={{ padding: "0 16px 16px", display: "grid", gap: 10 }}>
          <button type="button" className="btn" onClick={onPickManaged}>
            <span style={{ display: "block", fontWeight: 600 }}>{t("topology.createNeManaged")}</span>
            <span className="panel__hint" style={{ display: "block", marginTop: 4 }}>
              {t("topology.createNeManagedHint")}
            </span>
          </button>
          <button type="button" className="btn btn--ghost" onClick={onPickPlaceholder}>
            <span style={{ display: "block", fontWeight: 600 }}>{t("topology.createNePlaceholder")}</span>
            <span className="panel__hint" style={{ display: "block", marginTop: 4 }}>
              {t("topology.createNePlaceholderHint")}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
