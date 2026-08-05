import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useI18n } from "../../i18n";
import { fetchOpsTasks, type OpsTaskItem } from "../../services/api";
import { formatSystemTime } from "../../utils/time";

const POLL_MS = 4000;

function statusTone(status: string): string {
  const s = status.toLowerCase();
  if (s === "collecting" || s === "running" || s === "connecting" || s === "testing") return "running";
  if (s === "ready" || s === "idle") return "ok";
  if (s === "paused" || s === "pending" || s === "detached") return "paused";
  if (s === "failed" || s === "error") return "stopped";
  return "other";
}

function kindLabel(kind: string, t: (k: string, vars?: Record<string, string | number>) => string): string {
  const key = `audit.tasks.kind.${kind}`;
  const tr = t(key);
  return tr === key ? kind : tr;
}

function statusLabel(status: string, t: (k: string, vars?: Record<string, string | number>) => string): string {
  const key = `audit.tasks.status.${status}`;
  const tr = t(key);
  return tr === key ? status : tr;
}

function triggerLabel(trigger: string, t: (k: string, vars?: Record<string, string | number>) => string): string {
  const raw = String(trigger || "").trim();
  if (!raw || raw === "—") return "—";
  const key = `audit.tasks.trigger.${raw}`;
  const tr = t(key);
  return tr === key ? raw : tr;
}

function runtimeTaskSubject(
  taskId: string,
  t: (k: string, vars?: Record<string, string | number>) => string,
): string {
  const key = `ume.tasks.runtimeTask.${taskId}`;
  const label = t(key);
  return label === key ? taskId : label;
}

function taskTitle(row: OpsTaskItem, t: (k: string, vars?: Record<string, string | number>) => string): string {
  const kind = kindLabel(row.kind, t);
  const subjectRaw = String(row.title || row.id || "").trim();
  const subject =
    row.kind === "ume_runtime" && subjectRaw ? runtimeTaskSubject(subjectRaw, t) : subjectRaw;
  if (!subject) return kind;
  // Avoid "Kind · Kind · x" if an old backend still prefixed the localized kind.
  if (subject === kind || subject.startsWith(`${kind} · `) || subject.startsWith(`${kind}·`)) {
    return subject;
  }
  return `${kind} · ${subject}`;
}

function progressLabel(row: OpsTaskItem, t: (k: string, vars?: Record<string, string | number>) => string): string {
  const base = String(row.progress || "").trim();
  const inflight = Number(row.inflight || 0);
  if (inflight > 0) {
    const extra = t("audit.tasks.inflight", { n: inflight });
    return base ? `${base} · ${extra}` : extra;
  }
  return base || "—";
}

export function TaskOverviewPage() {
  const { t } = useI18n();
  const query = useQuery({
    queryKey: ["opsTasks"],
    queryFn: fetchOpsTasks,
    refetchInterval: POLL_MS,
    staleTime: 1500,
  });

  const data = query.data;
  const items = useMemo(() => data?.items || [], [data]);
  const byKind = data?.by_kind || {};
  const byStatus = data?.by_status || {};

  return (
    <div className="page-stack system-page">
      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("audit.tasks.title")}</h2>
          <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
            {query.isFetching ? t("common.refreshing") : t("common.refresh")}
          </button>
        </div>
        <p className="panel__hint">{t("audit.tasks.hint")}</p>

        <div className="pt-list-kpis">
          <div className="pt-list-kpi pt-list-kpi--live">
            <div className="pt-list-kpi__label">{t("audit.tasks.kpiActive")}</div>
            <div className="pt-list-kpi__value">{data?.active ?? "—"}</div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("audit.tasks.kpiTotal")}</div>
            <div className="pt-list-kpi__value">{data?.total ?? "—"}</div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("audit.tasks.kpiKinds")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: 13, fontWeight: 500 }}>
              {Object.keys(byKind).length
                ? Object.entries(byKind)
                    .map(([k, n]) => `${kindLabel(k, t)} ${n}`)
                    .join(" · ")
                : "—"}
            </div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("audit.tasks.kpiStatuses")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: 13, fontWeight: 500 }}>
              {Object.keys(byStatus).length
                ? Object.entries(byStatus)
                    .map(([k, n]) => `${statusLabel(k, t)} ${n}`)
                    .join(" · ")
                : "—"}
            </div>
          </div>
        </div>

        {query.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

        {!items.length && !query.isLoading ? (
          <div className="pt-list-empty">
            <p>{t("audit.tasks.empty")}</p>
          </div>
        ) : (
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("audit.tasks.colKind")}</th>
                  <th>{t("audit.tasks.colTitle")}</th>
                  <th>{t("audit.tasks.colStatus")}</th>
                  <th>{t("audit.tasks.colActor")}</th>
                  <th>{t("audit.tasks.colTrigger")}</th>
                  <th>{t("audit.tasks.colProgress")}</th>
                  <th>{t("audit.tasks.colStarted")}</th>
                  <th>{t("audit.tasks.colUpdated")}</th>
                  <th>{t("audit.tasks.colLink")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={`${row.kind}:${row.id}`} title={row.detail || undefined}>
                    <td>{kindLabel(row.kind, t)}</td>
                    <td>
                      <div className="pt-list-task-name">{taskTitle(row, t)}</div>
                      {row.detail ? (
                        <div className="muted" style={{ fontSize: 12 }}>
                          {row.detail}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      <span className={`pt-list-status pt-list-status--${statusTone(row.status)}`}>
                        {statusLabel(row.status, t)}
                      </span>
                    </td>
                    <td>{row.actor || "—"}</td>
                    <td>{triggerLabel(row.trigger, t)}</td>
                    <td className="pt-list-num">{progressLabel(row, t)}</td>
                    <td className="pt-list-time">{formatSystemTime(row.started_at) || "—"}</td>
                    <td className="pt-list-time">{formatSystemTime(row.updated_at) || "—"}</td>
                    <td>
                      {row.href ? (
                        <Link to={row.href} className="link-btn">
                          {t("audit.tasks.open")}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
