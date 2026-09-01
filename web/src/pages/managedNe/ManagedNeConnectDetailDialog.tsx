import { useMutation } from "@tanstack/react-query";
import { connectTestManagedNe } from "../../services/api";
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

  if (!row) return null;

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal modal--wide" role="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>{t("managedNe.connectDetailTitle")}</h3>
        <p className="form-hint">
          {row.name || row.ip_address} · {row.ip_address}:{row.port}/{row.protocol}
          {row.connect_tested_at
            ? ` · ${formatSystemTime(row.connect_tested_at, { assumeUtcNaive: true })}`
            : ""}
        </p>
        <p>
          <span className={`conn-pill conn-pill--${connectPillLevel(row.connect_status)}`}>
            {row.connect_status}
          </span>
          {row.connect_message ? (
            <span className="connect-detail-summary"> — {row.connect_message}</span>
          ) : null}
        </p>
        <pre className="connect-log">
          {row.connect_detail?.trim() || row.connect_message?.trim() || t("managedNe.connectDetailEmpty")}
        </pre>
        <div className="modal__actions">
          <button
            type="button"
            disabled={connectMutation.isPending}
            onClick={() => connectMutation.mutate([row.id])}
          >
            {connectMutation.isPending ? t("managedNe.connect.running") : t("managedNe.connect.retest")}
          </button>
          <button type="button" onClick={onClose}>
            {t("managedNe.form.cancel")}
          </button>
        </div>
        {connectMutation.isError ? (
          <p className="form-hint" role="alert">
            {String(connectMutation.error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
