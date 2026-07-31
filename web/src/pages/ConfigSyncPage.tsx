import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createConfigSyncCycle,
  fetchCliTargets,
  fetchConfigSyncCycleTasks,
  fetchConfigSyncCycles,
  fetchConfigSyncDashboard,
  pauseConfigSyncCycle,
  resumeConfigSyncCycle,
  updateConfigSyncPolicy,
} from "../services/api";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { CliTargetItem, ConfigSyncTargetRef } from "../types";
import { pageCount } from "../utils/display";
import { formatSystemTime } from "../utils/time";

const POLL_MS = 2500;
const TARGET_PAGE_SIZE = 20;

export function ConfigSyncPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();

  const [cyclePage, setCyclePage] = useState(1);
  const [expandedCycleId, setExpandedCycleId] = useState("");
  const [taskPage, setTaskPage] = useState(1);
  const [taskStatus, setTaskStatus] = useState("");
  const [taskKeyword, setTaskKeyword] = useState("");

  const [enabled, setEnabled] = useState(false);
  const [intervalDays, setIntervalDays] = useState(3);
  const [concurrency, setConcurrency] = useState(5);
  const [scopeMode, setScopeMode] = useState<"all" | "selected">("all");
  const [historyKeep, setHistoryKeep] = useState(3);
  const [selectedMap, setSelectedMap] = useState<Record<string, ConfigSyncTargetRef>>({});
  const [policyHydrated, setPolicyHydrated] = useState(false);

  const [targetKeyword, setTargetKeyword] = useState("");
  const [targetPage, setTargetPage] = useState(1);

  const dashQuery = useQuery({
    queryKey: queryKeys.configSyncDashboard,
    queryFn: fetchConfigSyncDashboard,
    staleTime: 1000,
    refetchInterval: (q) => {
      const running = q.state.data?.running_cycle;
      return running && (running.status === "running" || running.status === "paused") ? POLL_MS : false;
    },
  });

  useEffect(() => {
    if (!dashQuery.data || policyHydrated) return;
    const p = dashQuery.data.policy;
    setEnabled(Boolean(p.enabled));
    setIntervalDays(Number(p.interval_days || 3));
    setConcurrency(Number(p.concurrency || 5));
    setScopeMode(p.scope_mode === "selected" ? "selected" : "all");
    setHistoryKeep(Number(p.history_keep ?? 3));
    const map: Record<string, ConfigSyncTargetRef> = {};
    for (const ref of p.selected_targets || []) {
      map[`${ref.source}:${ref.id}`] = { source: ref.source, id: ref.id };
    }
    setSelectedMap(map);
    setPolicyHydrated(true);
  }, [dashQuery.data, policyHydrated]);

  const cyclesQuery = useQuery({
    queryKey: queryKeys.configSyncCycles(cyclePage),
    queryFn: () => fetchConfigSyncCycles({ page: cyclePage, pageSize: 10 }),
    staleTime: 1000,
    refetchInterval: () => (dashQuery.data?.running_cycle ? POLL_MS : false),
  });

  const tasksQuery = useQuery({
    queryKey: queryKeys.configSyncCycleTasks(expandedCycleId, taskPage, taskStatus, taskKeyword),
    queryFn: () =>
      fetchConfigSyncCycleTasks({
        cycleId: expandedCycleId,
        page: taskPage,
        pageSize: 20,
        status: taskStatus,
        keyword: taskKeyword,
      }),
    enabled: Boolean(expandedCycleId),
    staleTime: 800,
    refetchInterval: () => (dashQuery.data?.running_cycle?.id === expandedCycleId ? POLL_MS : false),
  });

  const targetsQuery = useQuery({
    queryKey: queryKeys.cliTargets(targetKeyword, targetPage, TARGET_PAGE_SIZE),
    queryFn: () =>
      fetchCliTargets({ source: "all", keyword: targetKeyword, page: targetPage, pageSize: TARGET_PAGE_SIZE }),
    enabled: scopeMode === "selected",
    staleTime: 5000,
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.configSyncDashboard }),
      queryClient.invalidateQueries({ queryKey: queryKeys.configSyncCyclesAll }),
      queryClient.invalidateQueries({ queryKey: queryKeys.configSyncCycleTasksAll }),
    ]);
  };

  const savePolicyMut = useMutation({
    mutationFn: () =>
      updateConfigSyncPolicy({
        enabled,
        interval_days: intervalDays,
        concurrency,
        scope_mode: scopeMode,
        history_keep: historyKeep,
        selected_targets: Object.values(selectedMap),
      }),
    onSuccess: async () => {
      showOk(t("configSync.policySaved"));
      setPolicyHydrated(false);
      await refresh();
    },
    onError: (err) => showError(String(err)),
  });

  const startMut = useMutation({
    mutationFn: (mode: "full" | "retry_failed") => createConfigSyncCycle({ mode }),
    onSuccess: async () => {
      showOk(t("configSync.started"));
      await refresh();
    },
    onError: (err) => showError(String(err)),
  });

  const pauseMut = useMutation({
    mutationFn: (id: string) => pauseConfigSyncCycle(id),
    onSuccess: async () => {
      showOk(t("configSync.paused"));
      await refresh();
    },
    onError: (err) => showError(String(err)),
  });

  const resumeMut = useMutation({
    mutationFn: (id: string) => resumeConfigSyncCycle(id),
    onSuccess: async () => {
      showOk(t("configSync.resumed"));
      await refresh();
    },
    onError: (err) => showError(String(err)),
  });

  const dash = dashQuery.data;
  const running = dash?.running_cycle;
  const last = dash?.last_cycle;
  const cycles = cyclesQuery.data?.items ?? [];
  const cycleTotal = Number(cyclesQuery.data?.total || 0);
  const cyclePages = pageCount(cycleTotal, 10);
  const selectedCount = useMemo(() => Object.keys(selectedMap).length, [selectedMap]);

  const toggleTarget = (row: CliTargetItem) => {
    const source = row.source === "ume" ? "ume" : "managed";
    const key = `${source}:${row.id}`;
    setSelectedMap((prev) => {
      const next = { ...prev };
      if (next[key]) delete next[key];
      else next[key] = { source, id: row.id };
      return next;
    });
  };

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("configSync.title")}</h2>
        <div className="btn-row">
          <button type="button" onClick={() => void refresh()} disabled={dashQuery.isFetching}>
            {t("common.refresh")}
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={Boolean(running) || startMut.isPending}
            onClick={() => startMut.mutate("full")}
          >
            {t("configSync.syncNow")}
          </button>
          <button
            type="button"
            disabled={Boolean(running) || startMut.isPending || !(last && last.fail_count > 0)}
            onClick={() => startMut.mutate("retry_failed")}
          >
            {t("configSync.retryFailed")}
          </button>
          {running?.status === "running" ? (
            <button type="button" onClick={() => pauseMut.mutate(running.id)} disabled={pauseMut.isPending}>
              {t("configSync.pause")}
            </button>
          ) : null}
          {running?.status === "paused" ? (
            <button type="button" onClick={() => resumeMut.mutate(running.id)} disabled={resumeMut.isPending}>
              {t("configSync.resume")}
            </button>
          ) : null}
        </div>
      </div>

      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <div className="muted">{t("configSync.kpi.snapshots")}</div>
          <strong>{dash?.snapshot_count ?? "-"}</strong>
        </div>
        <div className="stat-card">
          <div className="muted">{t("configSync.kpi.running")}</div>
          <strong>
            {running
              ? `${running.status} · ${running.success_count}/${running.planned_count}`
              : t("configSync.kpi.idle")}
          </strong>
        </div>
        <div className="stat-card">
          <div className="muted">{t("configSync.kpi.last")}</div>
          <strong>
            {last
              ? `${last.status} · ok ${last.success_count} / fail ${last.fail_count}`
              : t("common.empty")}
          </strong>
        </div>
        <div className="stat-card">
          <div className="muted">{t("configSync.kpi.nextDue")}</div>
          <strong>{dash?.next_due_at ? formatSystemTime(dash.next_due_at) : t("common.empty")}</strong>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <h3>{t("configSync.policyTitle")}</h3>
        <div className="config-sync-policy-row">
          <label className="config-sync-policy-check">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <span>{t("configSync.enabled")}</span>
          </label>
          <label className="config-sync-policy-field">
            <span>{t("configSync.intervalDays")}</span>
            <input
              type="number"
              min={1}
              max={365}
              value={intervalDays}
              onChange={(e) => setIntervalDays(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <label className="config-sync-policy-field">
            <span>{t("configSync.concurrency")}</span>
            <input
              type="number"
              min={1}
              max={30}
              value={concurrency}
              onChange={(e) => setConcurrency(Math.max(1, Math.min(30, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="config-sync-policy-field">
            <span>{t("configSync.historyKeep")}</span>
            <input
              type="number"
              min={0}
              max={30}
              value={historyKeep}
              onChange={(e) => setHistoryKeep(Math.max(0, Math.min(30, Number(e.target.value) || 0)))}
            />
          </label>
          <label className="config-sync-policy-field">
            <span>{t("configSync.scope")}</span>
            <select
              value={scopeMode}
              onChange={(e) => setScopeMode(e.target.value === "selected" ? "selected" : "all")}
            >
              <option value="all">{t("configSync.scopeAll")}</option>
              <option value="selected">{t("configSync.scopeSelected")}</option>
            </select>
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={savePolicyMut.isPending}
            onClick={() => savePolicyMut.mutate()}
          >
            {t("configSync.savePolicy")}
          </button>
        </div>

        {scopeMode === "selected" ? (
          <div style={{ marginTop: 12 }}>
            <p className="muted">{t("configSync.selectedCount", { count: String(selectedCount) })}</p>
            <div className="filter-inline">
              <input
                value={targetKeyword}
                placeholder={t("configSync.targetKeywordPh")}
                onChange={(e) => {
                  setTargetKeyword(e.target.value);
                  setTargetPage(1);
                }}
              />
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th />
                  <th>{t("configSync.col.source")}</th>
                  <th>{t("configSync.col.name")}</th>
                  <th>IP</th>
                  <th>{t("configSync.col.vendor")}</th>
                </tr>
              </thead>
              <tbody>
                {(targetsQuery.data?.items ?? []).map((row) => {
                  const source = row.source === "ume" ? "ume" : "managed";
                  const key = `${source}:${row.id}`;
                  return (
                    <tr key={key}>
                      <td>
                        <input
                          type="checkbox"
                          checked={Boolean(selectedMap[key])}
                          onChange={() => toggleTarget(row)}
                        />
                      </td>
                      <td>{source}</td>
                      <td>{row.name}</td>
                      <td>{row.ip_address}</td>
                      <td>{row.vendor || "-"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="pager">
              <button type="button" disabled={targetPage <= 1} onClick={() => setTargetPage((p) => p - 1)}>
                {t("common.prevPage")}
              </button>
              <span className="muted">
                {t("common.pagerMeta", {
                  total: String(targetsQuery.data?.total ?? 0),
                  page: String(targetPage),
                  pages: String(pageCount(Number(targetsQuery.data?.total || 0), TARGET_PAGE_SIZE)),
                })}
              </span>
              <button
                type="button"
                disabled={targetPage >= pageCount(Number(targetsQuery.data?.total || 0), TARGET_PAGE_SIZE)}
                onClick={() => setTargetPage((p) => p + 1)}
              >
                {t("common.nextPage")}
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <h3>{t("configSync.cyclesTitle")}</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th />
            <th>ID</th>
            <th>{t("configSync.col.trigger")}</th>
            <th>{t("configSync.col.status")}</th>
            <th>{t("configSync.col.progress")}</th>
            <th>{t("configSync.col.started")}</th>
            <th>{t("configSync.col.ended")}</th>
          </tr>
        </thead>
        <tbody>
          {cycles.map((c) => (
            <tr key={c.id}>
              <td>
                <button
                  type="button"
                  onClick={() => {
                    setExpandedCycleId((id) => (id === c.id ? "" : c.id));
                    setTaskPage(1);
                  }}
                >
                  {expandedCycleId === c.id ? t("configSync.collapse") : t("configSync.expand")}
                </button>
              </td>
              <td title={c.id}>{c.id.slice(0, 8)}</td>
              <td>{c.trigger_mode}</td>
              <td>{c.status}</td>
              <td>
                {c.success_count}/{c.planned_count} · fail {c.fail_count}
              </td>
              <td>{c.started_at ? formatSystemTime(c.started_at) : "-"}</td>
              <td>{c.ended_at ? formatSystemTime(c.ended_at) : "-"}</td>
            </tr>
          ))}
          {!cycles.length ? (
            <tr>
              <td colSpan={7} className="muted">
                {t("configSync.cyclesEmpty")}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      <div className="pager">
        <button type="button" disabled={cyclePage <= 1} onClick={() => setCyclePage((p) => p - 1)}>
          {t("common.prevPage")}
        </button>
        <span className="muted">
          {t("common.pagerMeta", { total: String(cycleTotal), page: String(cyclePage), pages: String(cyclePages) })}
        </span>
        <button type="button" disabled={cyclePage >= cyclePages} onClick={() => setCyclePage((p) => p + 1)}>
          {t("common.nextPage")}
        </button>
      </div>

      {expandedCycleId ? (
        <div style={{ marginTop: 16 }}>
          <h3>{t("configSync.tasksTitle")}</h3>
          <div className="filter-inline">
            <input
              value={taskKeyword}
              placeholder={t("configSync.taskKeywordPh")}
              onChange={(e) => {
                setTaskKeyword(e.target.value);
                setTaskPage(1);
              }}
            />
            <select
              value={taskStatus}
              onChange={(e) => {
                setTaskStatus(e.target.value);
                setTaskPage(1);
              }}
            >
              <option value="">{t("configSync.allStatus")}</option>
              <option value="pending">pending</option>
              <option value="running">running</option>
              <option value="success">success</option>
              <option value="fail">fail</option>
              <option value="skipped">skipped</option>
            </select>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("configSync.col.name")}</th>
                <th>IP</th>
                <th>{t("configSync.col.vendor")}</th>
                <th>{t("configSync.col.source")}</th>
                <th>{t("configSync.col.status")}</th>
                <th>{t("configSync.col.message")}</th>
              </tr>
            </thead>
            <tbody>
              {(tasksQuery.data?.items ?? []).map((task) => (
                <tr key={task.id}>
                  <td>{task.ne_name || task.target_id}</td>
                  <td>{task.ne_ip}</td>
                  <td>{task.vendor || "-"}</td>
                  <td>{task.source}</td>
                  <td>{task.status}</td>
                  <td title={task.message}>{task.message || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pager">
            <button type="button" disabled={taskPage <= 1} onClick={() => setTaskPage((p) => p - 1)}>
              {t("common.prevPage")}
            </button>
            <span className="muted">
              {t("common.pagerMeta", {
                total: String(tasksQuery.data?.total ?? 0),
                page: String(taskPage),
                pages: String(pageCount(Number(tasksQuery.data?.total || 0), 20)),
              })}
            </span>
            <button
              type="button"
              disabled={taskPage >= pageCount(Number(tasksQuery.data?.total || 0), 20)}
              onClick={() => setTaskPage((p) => p + 1)}
            >
              {t("common.nextPage")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
