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
  if (!dialog) return null;

  return (
    <div
      className="topo-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="topo-create-ne-title"
    >
      <div
        className="topo-modal__backdrop"
        onClick={() => {
          if (busy) return;
          onClose();
        }}
      />
      <div className="topo-modal__panel" style={{ maxWidth: 420 }}>
        <div className="topo-modal__head">
          <strong id="topo-create-ne-title">{t("topology.createNeTitle")}</strong>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={busy}
            onClick={onClose}
          >
            {t("topology.discoverClose")}
          </button>
        </div>
        <p className="panel__hint topo-modal__hint">{t("topology.createNeHint")}</p>
        <div className="form-grid" style={{ padding: "0 16px 8px" }}>
          <label>
            <span className="form-label">
              {t("topology.createNeName")}
              <span className="form-label__required" aria-hidden="true">
                {" "}
                *
              </span>
            </span>
            <input
              autoFocus
              value={dialog.name}
              placeholder={t("topology.createNeNamePh")}
              disabled={busy}
              onChange={(e) => onChange({ name: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") void onSubmit();
              }}
            />
          </label>
          <label>
            <span className="form-label">{t("topology.createNeIp")}</span>
            <input
              value={dialog.ip_address}
              placeholder={t("topology.createNeIpPh")}
              disabled={busy}
              onChange={(e) => onChange({ ip_address: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") void onSubmit();
              }}
            />
          </label>
        </div>
        <div className="topo-modal__foot">
          <button type="button" className="btn btn--ghost" disabled={busy} onClick={onClose}>
            {t("topology.discoverClose")}
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => void onSubmit()}
          >
            {busy ? t("topology.createNeBusy") : t("topology.createNePlaceholder")}
          </button>
        </div>
      </div>
    </div>
  );
}
