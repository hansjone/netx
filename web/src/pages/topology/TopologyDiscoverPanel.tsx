import type { TopologyDiscoverOut } from "../../types";
import { useI18n } from "../../i18n";
import { openOrFocusModule } from "../../utils/moduleWindows";

export type DiscoverProgress = {
  index: number;
  total: number;
  neName: string;
  neIp: string;
  edgesAdded: number;
  edgesUpdated: number;
};

export type DiscoverSummary = {
  scanned: number;
  added: number;
  updated: number;
  missing: number;
  failed: number;
};

export type TopologyDiscoverPanelProps = {
  discovering: boolean;
  discoverProgress: DiscoverProgress;
  discoverPct: number;
  discoverReport: TopologyDiscoverOut | null;
  discoverError: string;
  hasResults: boolean;
  discoverSummary: DiscoverSummary;
  onCancelDiscover: () => void;
  onClose: () => void;
};

export function TopologyDiscoverPanel({
  discovering,
  discoverProgress,
  discoverPct,
  discoverReport,
  discoverError,
  hasResults,
  discoverSummary,
  onCancelDiscover,
  onClose,
}: TopologyDiscoverPanelProps) {
  const { t } = useI18n();

  return (
    <div className="topo-discover">
      <div className="topo-discover__head">
        <strong>{t("topology.discoverReport")}</strong>
        <button
          type="button"
          className="btn btn--sm btn--ghost"
          onClick={() => {
            if (discovering) {
              void onCancelDiscover();
              return;
            }
            onClose();
          }}
        >
          {discovering ? t("topology.discoverCancel") : t("topology.discoverClose")}
        </button>
      </div>
      {discovering && discoverProgress.total > 0 ? (
        <div className="topo-discover__progress" aria-live="polite">
          <div className="topo-discover__bar">
            <div className="topo-discover__bar-fill" style={{ width: `${discoverPct}%` }} />
          </div>
          <div className="topo-discover__progress-meta">
            <span>
              {t("topology.discoverProgressLive")
                .replace("{{index}}", String(discoverProgress.index))
                .replace("{{total}}", String(discoverProgress.total))
                .replace("{{name}}", discoverProgress.neName || discoverProgress.neIp || "?")}
            </span>
            <span>{discoverPct}%</span>
          </div>
        </div>
      ) : null}
      {discovering && discoverProgress.total <= 0 ? (
        <p className="panel__hint panel__hint--live">{t("topology.discoverNoTargets")}</p>
      ) : null}
      {!discovering && discoverReport && discoverReport.scanned === 0 ? (
        <p className="panel__hint">{t("topology.discoverNoTargets")}</p>
      ) : null}
      {discoverError ? <p className="topo-discover__error">{discoverError}</p> : null}
      {hasResults ? (
        <>
          <p className="topo-discover__summary">
            {t("topology.discoverSummary")
              .replace("{{scanned}}", String(discoverSummary.scanned))
              .replace("{{added}}", String(discoverSummary.added))
              .replace("{{updated}}", String(discoverSummary.updated))
              .replace("{{stale}}", String(discoverSummary.missing))
              .replace("{{failed}}", String(discoverSummary.failed))}
          </p>
          <p className="panel__hint">
            <button
              type="button"
              className="btn btn--sm btn--ghost"
              onClick={() => {
                const jobId = String(discoverReport?.job_id || "").trim();
                const qs = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
                openOrFocusModule({
                  moduleId: "network",
                  path: `/network/topology/lldp${qs}`,
                });
              }}
            >
              {t("topology.discoverGoLldp")}
            </button>
          </p>
        </>
      ) : null}
    </div>
  );
}
