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
  if (!dialog) return null;

  return (
    <div
      className="topo-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="topo-new-root-title"
    >
      <div className="topo-modal__backdrop" onClick={onClose} />
      <div className="topo-modal__panel" style={{ maxWidth: 420 }}>
        <div className="topo-modal__head">
          <strong id="topo-new-root-title">{t("topology.newRegion")}</strong>
          <button type="button" className="btn btn--sm btn--ghost" onClick={onClose}>
            {t("topology.discoverClose")}
          </button>
        </div>
        <p className="panel__hint topo-modal__hint">{t("topology.folderHint")}</p>
        <div className="form-grid" style={{ padding: "0 16px 8px" }}>
          <label>
            <span className="form-label">{t("topology.newRegionPrompt")}</span>
            <input
              autoFocus
              value={dialog.name}
              disabled={pending}
              onChange={(e) => onNameChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSubmit();
              }}
            />
          </label>
        </div>
        <div className="topo-modal__foot">
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={pending}
            onClick={onClose}
          >
            {t("topology.discoverClose")}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={pending || !String(dialog.name || "").trim()}
            onClick={onSubmit}
          >
            {pending ? "…" : t("topology.newRegion")}
          </button>
        </div>
      </div>
    </div>
  );
}
