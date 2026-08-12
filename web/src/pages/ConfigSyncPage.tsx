import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ListPager } from "../components/ListPager";
import { queryKeys } from "../constants/queryKeys";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useToast } from "../hooks/useToast";
import { useI18n } from "../i18n";
import {
  createConfigSyncCycle,
  fetchCliTargets,
  fetchConfigSyncCycleTasks,
  fetchConfigSyncCycles,
  fetchConfigSyncDashboard,
  formatErr,
  pauseConfigSyncCycle,
  resumeConfigSyncCycle,
  stopConfigSyncCycle,
  updateConfigSyncPolicy,
} from "../services/api";
import type { CliTargetItem, ConfigSyncCycle, ConfigSyncTargetRef, ConfigSyncTask } from "../types";
import { downloadCsv, fetchAllPages } from "../utils/csvExport";
import { pageCount } from "../utils/display";
import { formatSystemTime } from "../utils/time";

const POLL_MS = 2500;
const TARGET_PAGE_SIZE = 20;

export function ConfigSyncPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [cyclePage, setCyclePage] = useState(1);
  const [cyclePageSize, setCyclePageSize] = useState(20);
  const [cycleStatus, setCycleStatus] = useState("");
  const [cycleKeyword, setCycleKeyword] = useState("");
  const [exportingCycles, setExportingCycles] = useState(false);
  const [expandedCycleId, setExpandedCycleId] = useState("");
  const [taskPage, setTaskPage] = useState(1);
  const [taskPageSize, setTaskPageSize] = useState(20);
  const [taskStatus, setTaskStatus] = useState("");
  const [taskKeyword, setTaskKeyword] = useState("");
  const [exportingTasks, setExportingTasks] = useState(false);

  const debouncedCycleKeyword = useDebouncedValue(cycleKeyword, 300);
  const debouncedTaskKeyword = useDebouncedValue(taskKeyword, 300);

  const [enabled, setEnabled] = useState(false);
  const [intervalDays, setIntervalDays] = useState(3);
  const [concurrency, setConcurrency] = useState(5);
  const [scopeMode, setScopeMode] = useState<"all" | "selected">("all");
  const [historyKeep, setHistoryKeep] = useState(3);
  const [cycleKeep, setCycleKeep] = useState(30);
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
      return running && (running.status === "running" || running.status === "paused" || running.status === "pending")
        ? POLL_MS
        : false;
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
    setCycleKeep(Math.max(0, Math.min(200, Number(p.cycle_keep ?? 30))));
    const map: Record<string, ConfigSyncTargetRef> = {};
    for (const ref of p.selected_targets || []) {
      map[`${ref.source}:${ref.id}`] = { source: ref.source, id: ref.id };
    }
    setSelectedMap(map);
    setPolicyHydrated(true);
  }, [dashQuery.data, policyHydrated]);

  useEffect(() => {
    const id = String(searchParams.get("cycle_id") || "").trim();
    if (!id) return;
    setExpandedCycleId(id);
    setTaskPage(1);
    setTaskPageSize(20);
    setTaskKeyword("");
    setTaskStatus("");
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (next.get("cycle_id") === id) next.delete("cycle_id");
        return next;
      },
      { replace: true },
    );
  }, [searchParams, setSearchParams]);

  const cyclesQuery = useQuery({
    queryKey: queryKeys.configSyncCycles(cyclePage, cyclePageSize, cycleStatus, debouncedCycleKeyword),
    queryFn: () =>
      fetchConfigSyncCycles({
        page: cyclePage,
        pageSize: cyclePageSize,
        status: cycleStatus,
        keyword: debouncedCycleKeyword,
      }),
    staleTime: 1000,
    refetchInterval: () => (dashQuery.data?.running_cycle ? POLL_MS : false),
  });

  const tasksQuery = useQuery({
    queryKey: queryKeys.configSyncCycleTasks(
      expandedCycleId,
      taskPage,
      taskPageSize,
      taskStatus,
      debouncedTaskKeyword,
    ),
    queryFn: () =>
      fetchConfigSyncCycleTasks({
        cycleId: expandedCycleId,
        page: taskPage,
        pageSize: taskPageSize,
        status: taskStatus,
        keyword: debouncedTaskKeyword,
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
        cycle_keep: cycleKeep,
        selected_targets: Object.values(selectedMap),
      }),
    onSuccess: async (saved) => {
      // Apply server response immediately — do NOT flip policyHydrated false then
      // rehydrate from a possibly-stale dashboard cache (checkbox "pops back" bug).
      setEnabled(Boolean(saved.enabled));
      setIntervalDays(Number(saved.interval_days || 3));
      setConcurrency(Number(saved.concurrency || 5));
      setScopeMode(saved.scope_mode === "selected" ? "selected" : "all");
      setHistoryKeep(Number(saved.history_keep ?? 3));
      setCycleKeep(Math.max(0, Math.min(200, Number(saved.cycle_keep ?? 30))));
      const map: Record<string, ConfigSyncTargetRef> = {};
      for (const ref of saved.selected_targets || []) {
        map[`${ref.source}:${ref.id}`] = { source: ref.source, id: ref.id };
      }
      setSelectedMap(map);
      setPolicyHydrated(true);
      queryClient.setQueryData(queryKeys.configSyncDashboard, (prev: unknown) => {
        if (!prev || typeof prev !== "object") return prev;
        return { ...(prev as object), policy: saved };
      });
      showOk(t("configSync.policySaved"));
      await refresh();
    },
    onError: (err) => showError(formatErr(err)),
  });

  const startMut = useMutation({
    mutationFn: (mode: "full" | "retry_failed") => createConfigSyncCycle({ mode }),
    onSuccess: async () => {
      showOk(t("configSync.started"));
      await refresh();
    },
    onError: (err) => showError(formatErr(err)),
  });

  const pauseMut = useMutation({
    mutationFn: (id: string) => pauseConfigSyncCycle(id),
    onSuccess: async () => {
      showOk(t("configSync.paused"));
      await refresh();
    },
    onError: (err) => showError(formatErr(err)),
  });

  const resumeMut = useMutation({
    mutationFn: (id: string) => resumeConfigSyncCycle(id),
    onSuccess: async () => {
      showOk(t("configSync.resumed"));
      await refresh();
    },
    onError: (err) => showError(formatErr(err)),
  });

  const stopMut = useMutation({
    mutationFn: (id: string) => stopConfigSyncCycle(id),
    onSuccess: async () => {
      showOk(t("configSync.stopped"));
      await refresh();
    },
    onError: (err) => showError(formatErr(err)),
  });

  const dash = dashQuery.data;
  const running = dash?.running_cycle;
  const last = dash?.last_cycle;
  const cycles = cyclesQuery.data?.items ?? [];
  const cycleTotal = Number(cyclesQuery.data?.total || 0);
  const cyclePages = pageCount(cycleTotal, cyclePageSize);
  const taskTotal = Number(tasksQuery.data?.total || 0);
  const taskPages = pageCount(taskTotal, taskPageSize);
  const selectedCount = useMemo(() => Object.keys(selectedMap).length, [selectedMap]);
  const hasCycleFilters = Boolean(cycleKeyword.trim() || cycleStatus);

  const openCycleTasks = (cycleId: string) => {
    setExpandedCycleId(cycleId);
    setTaskPage(1);
    setTaskKeyword("");
    setTaskStatus("");
  };

  const exportCyclesCsv = async () => {
    setExportingCycles(true);
    try {
      const rows = await fetchAllPages<ConfigSyncCycle>({
        pageSize: 100,
        maxRows: 2000,
        fetchPage: (p, ps) =>
          fetchConfigSyncCycles({
            page: p,
            pageSize: ps,
            status: cycleStatus,
            keyword: debouncedCycleKeyword,
          }),
      });
      downloadCsv(
        `${t("configSync.exportCyclesName")}-${new Date().toISOString().slice(0, 10)}.csv`,
        rows,
        [
          { key: "id", header: "ID" },
          { key: "trigger_mode", header: t("configSync.col.trigger") },
          { key: "status", header: t("configSync.col.status") },
          {
            key: "progress",
            header: t("configSync.col.progress"),
            value: (r) => `${r.success_count}/${r.planned_count} fail ${r.fail_count}`,
          },
          {
            key: "started_at",
            header: t("configSync.col.started"),
            value: (r) => (r.started_at ? formatSystemTime(r.started_at) : ""),
          },
          {
            key: "ended_at",
            header: t("configSync.col.ended"),
            value: (r) => (r.ended_at ? formatSystemTime(r.ended_at) : ""),
          },
          { key: "error_message", header: t("configSync.col.message") },
        ],
      );
      if (rows.length < cycleTotal) {
        showOk(t("common.exportTruncated", { count: String(rows.length), total: String(cycleTotal) }));
      } else {
        showOk(t("common.exportOk", { count: String(rows.length) }));
      }
    } catch (err) {
      showError(t("common.exportFailed") + ": " + formatErr(err));
    } finally {
      setExportingCycles(false);
    }
  };

  const exportTasksCsv = async () => {
    if (!expandedCycleId) return;
    setExportingTasks(true);
    try {
      const rows = await fetchAllPages<ConfigSyncTask>({
        pageSize: 200,
        maxRows: 2000,
        fetchPage: (p, ps) =>
          fetchConfigSyncCycleTasks({
            cycleId: expandedCycleId,
            page: p,
            pageSize: ps,
            status: taskStatus,
            keyword: debouncedTaskKeyword,
          }),
      });
      downloadCsv(
        `${t("configSync.exportTasksName")}-${expandedCycleId.slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.csv`,
        rows,
        [
          { key: "ne_name", header: t("configSync.col.name"), value: (r) => r.ne_name || r.target_id },
          { key: "ne_ip", header: "IP" },
          { key: "vendor", header: t("configSync.col.vendor") },
          { key: "source", header: t("configSync.col.source") },
          { key: "status", header: t("configSync.col.status") },
          { key: "message", header: t("configSync.col.message") },
          { key: "target_id", header: "ID" },
        ],
      );
      if (rows.length < taskTotal) {
        showOk(t("common.exportTruncated", { count: String(rows.length), total: String(taskTotal) }));
      } else {
        showOk(t("common.exportOk", { count: String(rows.length) }));
      }
    } catch (err) {
      showError(t("common.exportFailed") + ": " + formatErr(err));
    } finally {
      setExportingTasks(false);
    }
  };

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
          {running?.status === "running" || running?.status === "pending" ? (
            <button type="button" onClick={() => pauseMut.mutate(running.id)} disabled={pauseMut.isPending}>
              {t("configSync.pause")}
            </button>
          ) : null}
          {running?.status === "paused" ? (
            <button type="button" onClick={() => resumeMut.mutate(running.id)} disabled={resumeMut.isPending}>
              {t("configSync.resume")}
            </button>
          ) : null}
          {running && (running.status === "running" || running.status === "paused" || running.status === "pending") ? (
            <button
              type="button"
              onClick={() => {
                if (window.confirm(t("configSync.confirmStop"))) stopMut.mutate(running.id);
              }}
              disabled={stopMut.isPending}
            >
              {t("configSync.stop")}
            </button>
          ) : null}
        </div>
      </div>

      <div className="pt-list" style={{ marginBottom: 16 }}>
        <div className="pt-list-kpis">
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("configSync.kpi.snapshots")}</div>
            <div className="pt-list-kpi__value">{dash?.snapshot_count ?? "—"}</div>
          </div>
          <div className={`pt-list-kpi${running ? " pt-list-kpi--live" : ""}`}>
            <div className="pt-list-kpi__label">{t("configSync.kpi.running")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: running ? 15 : 22 }}>
              {running
                ? `${running.status} · ${running.success_count}/${running.planned_count}`
                : t("configSync.kpi.idle")}
            </div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("configSync.kpi.last")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: 15 }}>
              {last
                ? `${last.status} · ok ${last.success_count} / fail ${last.fail_count}`
                : t("common.empty")}
            </div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("configSync.kpi.nextDue")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: 15 }}>
              {dash?.next_due_at ? formatSystemTime(dash.next_due_at) : t("common.empty")}
            </div>
          </div>
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
            <span>{t("configSync.cycleKeep")}</span>
            <input
              type="number"
              min={0}
              max={200}
              value={cycleKeep}
              onChange={(e) => setCycleKeep(Math.max(0, Math.min(200, Number(e.target.value) || 0)))}
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
      <div className="pt-list">
        <div className="filter-inline">
          <input
            value={cycleKeyword}
            placeholder={t("configSync.keywordPh")}
            onChange={(e) => {
              setCycleKeyword(e.target.value);
              setCyclePage(1);
            }}
          />
          <select
            value={cycleStatus}
            onChange={(e) => {
              setCycleStatus(e.target.value);
              setCyclePage(1);
            }}
          >
            <option value="">{t("configSync.statusAll")}</option>
            <option value="pending">pending</option>
            <option value="running">running</option>
            <option value="paused">paused</option>
            <option value="success">success</option>
            <option value="completed">completed</option>
            <option value="failed">failed</option>
            <option value="stopped">stopped</option>
          </select>
          <button
            type="button"
            disabled={!hasCycleFilters}
            onClick={() => {
              setCycleKeyword("");
              setCycleStatus("");
              setCyclePage(1);
            }}
          >
            {t("common.clearFilters")}
          </button>
          <button
            type="button"
            disabled={exportingCycles || cycleTotal === 0}
            onClick={() => void exportCyclesCsv()}
          >
            {exportingCycles ? t("common.exporting") : t("common.exportCsv")}
          </button>
        </div>

        {cyclesQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
        {cyclesQuery.isError ? <p className="error-text">{formatErr(cyclesQuery.error)}</p> : null}

        <div className="pt-list-table-wrap">
          <table className="data-table pt-list-table">
            <thead>
              <tr>
                <th />
                <th>ID</th>
                <th>{t("configSync.col.trigger")}</th>
                <th>{t("configSync.col.status")}</th>
                <th>{t("configSync.col.progress")}</th>
                <th>{t("configSync.col.started")}</th>
                <th>{t("configSync.col.ended")}</th>
                <th>{t("configSync.col.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {cycles.map((c) => (
                <tr key={c.id}>
                  <td>
                    <button
                      type="button"
                      className="btn btn--sm btn--ghost"
                      onClick={() => openCycleTasks(c.id)}
                    >
                      {t("configSync.expand")}
                    </button>
                  </td>
                  <td title={c.id} className="pt-list-num">{c.id.slice(0, 8)}</td>
                  <td>{c.trigger_mode}</td>
                  <td>
                    <span
                      className={`pt-list-status ${
                        c.status === "running" || c.status === "pending"
                          ? "pt-list-status--running"
                          : c.status === "paused"
                            ? "pt-list-status--paused"
                            : c.status === "failed"
                              ? "pt-list-status--failed"
                              : c.status === "success" || c.status === "completed"
                                ? "pt-list-status--ok"
                                : "pt-list-status--other"
                      }`}
                    >
                      {c.status}
                    </span>
                  </td>
                  <td>
                    {c.success_count}/{c.planned_count} · fail {c.fail_count}
                  </td>
                  <td>{c.started_at ? formatSystemTime(c.started_at) : "-"}</td>
                  <td>{c.ended_at ? formatSystemTime(c.ended_at) : "-"}</td>
                  <td>
                    <div className="btn-row">
                      {c.status === "running" || c.status === "pending" ? (
                        <button type="button" onClick={() => pauseMut.mutate(c.id)} disabled={pauseMut.isPending}>
                          {t("configSync.pause")}
                        </button>
                      ) : null}
                      {c.status === "paused" ? (
                        <button type="button" onClick={() => resumeMut.mutate(c.id)} disabled={resumeMut.isPending}>
                          {t("configSync.resume")}
                        </button>
                      ) : null}
                      {c.status === "running" || c.status === "paused" || c.status === "pending" ? (
                        <button
                          type="button"
                          onClick={() => {
                            if (window.confirm(t("configSync.confirmStop"))) stopMut.mutate(c.id);
                          }}
                          disabled={stopMut.isPending}
                        >
                          {t("configSync.stop")}
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
              {!cycles.length && !cyclesQuery.isLoading ? (
                <tr>
                  <td colSpan={8} className="muted">
                    {t("configSync.cyclesEmpty")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <ListPager
          page={cyclePage}
          pages={cyclePages}
          total={cycleTotal}
          pageSize={cyclePageSize}
          pageSizeOptions={[20, 50, 100]}
          onPageChange={setCyclePage}
          onPageSizeChange={(size) => {
            setCyclePageSize(size);
            setCyclePage(1);
          }}
          disabled={cyclesQuery.isLoading}
        />
      </div>

      {expandedCycleId ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setExpandedCycleId("")}
        >
          <div
            className="modal modal--wide ops-detail-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("configSync.tasksTitle")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="ops-detail-modal__head">
              <div className="ops-detail-modal__title">
                <h3>{t("configSync.tasksTitle")}</h3>
                <p className="muted">
                  {expandedCycleId.slice(0, 8)}
                  {(() => {
                    const c = cycles.find((x) => x.id === expandedCycleId);
                    return c
                      ? ` · ${c.trigger_mode} · ${c.status} · ${c.success_count}/${c.planned_count}`
                      : "";
                  })()}
                </p>
              </div>
              <div className="btn-row ops-detail-modal__actions">
                <button
                  type="button"
                  disabled={exportingTasks || taskTotal === 0}
                  onClick={() => void exportTasksCsv()}
                >
                  {exportingTasks ? t("common.exporting") : t("common.exportCsv")}
                </button>
                <button type="button" onClick={() => setExpandedCycleId("")}>
                  {t("networkConfigs.close")}
                </button>
              </div>
            </div>

            <div className="ops-detail-modal__toolbar filter-inline">
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
                <option value="">{t("configSync.statusAll")}</option>
                <option value="pending">pending</option>
                <option value="running">running</option>
                <option value="success">success</option>
                <option value="fail">fail</option>
                <option value="skipped">skipped</option>
              </select>
            </div>

            {tasksQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
            {tasksQuery.isError ? (
              <p className="ops-detail-modal__error">{formatErr(tasksQuery.error)}</p>
            ) : null}

            <div className="ops-detail-modal__scroll">
              <div className="pt-list-table-wrap">
                <table className="data-table pt-list-table">
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
                    {!tasksQuery.isLoading && !(tasksQuery.data?.items ?? []).length ? (
                      <tr>
                        <td colSpan={6} className="muted">
                          {t("common.empty")}
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="ops-detail-modal__foot">
              <ListPager
                page={taskPage}
                pages={taskPages}
                total={taskTotal}
                pageSize={taskPageSize}
                pageSizeOptions={[20, 50, 100, 200]}
                onPageChange={setTaskPage}
                onPageSizeChange={(size) => {
                  setTaskPageSize(size);
                  setTaskPage(1);
                }}
                disabled={tasksQuery.isLoading}
              />
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
