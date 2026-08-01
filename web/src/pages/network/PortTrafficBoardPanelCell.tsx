import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { fetchPortTrafficCompare } from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import type { PortTrafficBoardPanel } from "../../types";
import { PortTrafficWall, type WallYMode } from "./PortTrafficWall";

const POLL_MS = 5000;
const EMPTY: never[] = [];

type Props = {
  boardId: string;
  panel: PortTrafficBoardPanel;
  editing?: boolean;
  dense?: boolean;
  onEdit?: () => void;
  onRemove?: () => void;
  onMove?: (dir: -1 | 1) => void;
};

export function PortTrafficBoardPanelCell({
  boardId,
  panel,
  editing,
  dense,
  onEdit,
  onRemove,
  onMove,
}: Props) {
  const { t } = useI18n();
  const baseline = panel.baseline || "off";
  const compareQuery = useQuery({
    queryKey: [
      ...queryKeys.portTrafficCompare(
        panel.target_id,
        panel.range_hours,
        baseline,
        baseline === "custom" ? panel.offset_hours : 0,
        panel.baseline_target_id || "",
      ),
      boardId,
      panel.id,
    ],
    queryFn: () =>
      fetchPortTrafficCompare({
        targetId: panel.target_id,
        rangeHours: panel.range_hours,
        baseline,
        offsetHours: baseline === "custom" ? panel.offset_hours : undefined,
        baselineTargetId: panel.baseline_target_id || undefined,
      }),
    enabled: Boolean(panel.target_id) && !panel.stale,
    staleTime: 1000,
    placeholderData: keepPreviousData,
    refetchInterval: POLL_MS,
  });

  const points = compareQuery.data?.current ?? EMPTY;
  const baselinePoints =
    baseline === "off" && !panel.baseline_target_id
      ? EMPTY
      : (compareQuery.data?.baseline ?? EMPTY);
  const target = panel.target || compareQuery.data?.meta?.current_target || null;
  const baselineTarget =
    compareQuery.data?.meta?.baseline_target || panel.baseline_target || null;
  const title =
    panel.title ||
    (target ? `${target.ne_name || target.ne_ip || "—"} · ${target.ifname}` : t("portTraffic.pickPort"));

  const rangeLabel = `${panel.range_hours}h${
    baseline === "off"
      ? ""
      : baseline === "day"
        ? ` · ${t("portTraffic.compareDay")}`
        : baseline === "week"
          ? ` · ${t("portTraffic.compareWeek")}`
          : baseline === "shift"
            ? ` · ${t("portTraffic.compareShift")}`
            : ` · ${t("portTraffic.compareCustom")}`
  }${panel.baseline_target_id ? ` · ${t("portTraffic.mapBaselinePort")}` : ""}`;

  return (
    <div
      className={`pt-board-panel${panel.stale ? " is-stale" : ""}`}
      style={{ gridColumn: `span ${Math.max(1, panel.col_span || 1)}` }}
    >
      {editing ? (
        <div className="pt-board-panel__editbar">
          <span className="pt-board-panel__edit-title" title={title}>
            {title}
          </span>
          <div className="btn-row">
            <button type="button" onClick={() => onMove?.(-1)}>
              ↑
            </button>
            <button type="button" onClick={() => onMove?.(1)}>
              ↓
            </button>
            <button type="button" onClick={() => onEdit?.()}>
              {t("portTraffic.edit")}
            </button>
            <button type="button" className="btn--danger" onClick={() => onRemove?.()}>
              {t("portTraffic.delete")}
            </button>
          </div>
        </div>
      ) : null}
      {panel.stale ? (
        <div className="pt-board-panel__stale">
          <p>{t("portTraffic.boardPanelStale")}</p>
          {editing ? (
            <button type="button" className="btn-primary" onClick={() => onEdit?.()}>
              {t("portTraffic.boardRetarget")}
            </button>
          ) : null}
        </div>
      ) : (
        <PortTrafficWall
          target={target}
          baselineTarget={baselineTarget}
          points={points}
          baselinePoints={baselinePoints}
          yMode={(panel.y_mode as WallYMode) || "auto"}
          rangeLabel={rangeLabel}
          loading={compareQuery.isLoading || compareQuery.isFetching}
          hint={t("portTraffic.waitSamples")}
          dense={dense}
        />
      )}
    </div>
  );
}
