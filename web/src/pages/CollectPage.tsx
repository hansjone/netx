import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ListPager } from "../components/ListPager";
import {
  createNeCollection,
  deleteCollectionJob,
  fetchCollectionDashboard,
  fetchCollectionJob,
  fetchCollectionRuns,
  fetchEligibleNe,
  fetchNeCollections,
  formatErr,
  pauseCollectionJob,
  startCollectionJob,
  startCollectionFromPolicy,
  retryFailedCollectionJob,
  updateCollectionPolicy,
  collectionJobDownloadUrl,
  collectionRunDownloadUrl,
} from "../services/api";
import { queryKeys } from "../constants/queryKeys";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { CollectionJobItem, CollectionRunItem, CollectionTargetRef, EligibleNeItem } from "../types";
import { downloadCsv, fetchAllPages } from "../utils/csvExport";
import { pageCount } from "../utils/display";
import { formatSystemTime } from "../utils/time";

const POLL_MS = 2000;
const ELIGIBLE_PAGE_SIZE = 20;
const POLICY_TARGET_PAGE_SIZE = 20;
const JOB_STATUS_OPTIONS = ["draft", "pending", "running", "paused", "done", "failed", "cancelled"] as const;
const JOB_PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
const RUN_PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

function eligibleKey(row: Pick<EligibleNeItem, "id" | "source">): string {
  const src = String(row.source || "managed").trim().toLowerCase() || "managed";
  return `${src}:${row.id}`;
}

function targetKey(ref: CollectionTargetRef): string {
  const src = String(ref.source || "managed").trim().toLowerCase() || "managed";
  return `${src}:${ref.id}`;
}

function collectionErrorMessage(err: unknown, t: (key: string) => string): string {
  const raw = String(err);
  if (raw.includes("collection_ne_busy")) return t("collect.neBusy");
  if (raw.includes("collection_job_running")) return t("collect.jobRunning");
  if (raw.includes("commands_empty") || raw.includes("commands_required_for_schedule")) {
    return t("collect.policyCommandsRequired");
  }
  if (raw.includes("no_selected_targets") || raw.includes("no_eligible_ne")) {
    return t("collect.policyTargetsRequired");
  }
  return raw;
}

export function CollectPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [commands, setCommands] = useState("");
  const [title, setTitle] = useState("");
  const [selectedMap, setSelectedMap] = useState<Record<string, EligibleNeItem>>({});
  const [pickSelectedIds, setPickSelectedIds] = useState<string[]>([]);
  const [neKeyword, setNeKeyword] = useState("");
  const [nePage, setNePage] = useState(1);
  const [jobPage, setJobPage] = useState(1);
  const [jobPageSize, setJobPageSize] = useState(20);
  const [jobStatus, setJobStatus] = useState("");
  const [jobKeyword, setJobKeyword] = useState("");
  const [exportingJobs, setExportingJobs] = useState(false);
  const [expandedJobId, setExpandedJobId] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const [policyEnabled, setPolicyEnabled] = useState(false);
  const [policyIntervalValue, setPolicyIntervalValue] = useState(1);
  const [policyIntervalUnit, setPolicyIntervalUnit] = useState<"days" | "hours">("days");
  const [policyHistoryKeep, setPolicyHistoryKeep] = useState(3);
  const [policyScopeMode, setPolicyScopeMode] = useState<"all" | "selected">("all");
  const [policyTitle, setPolicyTitle] = useState("");
  const [policyCommands, setPolicyCommands] = useState("");
  const [policySelectedMap, setPolicySelectedMap] = useState<Record<string, CollectionTargetRef>>({});
  const [policyHydrated, setPolicyHydrated] = useState(false);
  const [policyTargetKeyword, setPolicyTargetKeyword] = useState("");
  const [policyTargetPage, setPolicyTargetPage] = useState(1);

  const debouncedJobKeyword = useDebouncedValue(jobKeyword, 300);
  const debouncedPolicyTargetKeyword = useDebouncedValue(policyTargetKeyword, 300);

  const selectedIds = useMemo(() => Object.keys(selectedMap), [selectedMap]);
  const selectedList = useMemo(() => Object.values(selectedMap), [selectedMap]);

  // Deep link: /collect?job_id=… → open runs modal, then strip param
  useEffect(() => {
    const jobId = String(searchParams.get("job_id") || "").trim();
    if (!jobId) return;
    setExpandedJobId(jobId);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (next.get("job_id") === jobId) next.delete("job_id");
        return next;
      },
      { replace: true },
    );
  }, [searchParams, setSearchParams]);

  const eligibleQuery = useQuery({
    queryKey: queryKeys.collectionEligibleNe(nePage, neKeyword),
    queryFn: () =>
      fetchEligibleNe({ page: nePage, pageSize: ELIGIBLE_PAGE_SIZE, keyword: neKeyword }),
    staleTime: 5000,
    enabled: createOpen,
  });

  const jobsQuery = useQuery({
    queryKey: queryKeys.neCollections(jobPage, jobPageSize, jobStatus, debouncedJobKeyword),
    queryFn: () =>
      fetchNeCollections({
        page: jobPage,
        pageSize: jobPageSize,
        status: jobStatus,
        keyword: debouncedJobKeyword,
      }),
    staleTime: 1000,
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? [];
      return items.some((j) => j.status === "running" || j.status === "pending") ? POLL_MS : false;
    },
  });

  const dashQuery = useQuery({
    queryKey: queryKeys.neCollectionDashboard,
    queryFn: fetchCollectionDashboard,
    staleTime: 1000,
    refetchInterval: (q) => {
      const running = q.state.data?.running_job;
      return running &&
        (running.status === "running" || running.status === "pending" || running.status === "paused")
        ? POLL_MS
        : false;
    },
  });

  useEffect(() => {
    const p = dashQuery.data?.policy;
    if (!p || policyHydrated) return;
    setPolicyEnabled(Boolean(p.enabled));
    const hours = Math.max(1, Number(p.interval_hours || Number(p.interval_days || 1) * 24));
    if (hours % 24 === 0) {
      setPolicyIntervalUnit("days");
      setPolicyIntervalValue(Math.max(1, Math.min(365, hours / 24)));
    } else {
      setPolicyIntervalUnit("hours");
      setPolicyIntervalValue(Math.max(1, Math.min(8760, hours)));
    }
    setPolicyHistoryKeep(Math.max(0, Math.min(200, Number(p.history_keep ?? 3))));
    setPolicyScopeMode(p.scope_mode === "selected" ? "selected" : "all");
    setPolicyTitle(String(p.title || ""));
    setPolicyCommands(String(p.commands || ""));
    const map: Record<string, CollectionTargetRef> = {};
    for (const ref of p.selected_targets || []) {
      map[targetKey(ref)] = { source: ref.source, id: ref.id };
    }
    setPolicySelectedMap(map);
    setPolicyHydrated(true);
  }, [dashQuery.data?.policy, policyHydrated]);

  const policyTargetsQuery = useQuery({
    queryKey: queryKeys.collectionEligibleNe(policyTargetPage, debouncedPolicyTargetKeyword),
    queryFn: () =>
      fetchEligibleNe({
        page: policyTargetPage,
        pageSize: POLICY_TARGET_PAGE_SIZE,
        keyword: debouncedPolicyTargetKeyword,
      }),
    staleTime: 5000,
    enabled: policyScopeMode === "selected",
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.neCollectionDetail(expandedJobId),
    queryFn: () => fetchCollectionJob(expandedJobId),
    enabled: Boolean(expandedJobId),
    staleTime: 500,
    refetchInterval: (q) => (q.state.data?.job.status === "running" ? POLL_MS : false),
  });

  const jobs = jobsQuery.data?.items ?? [];
  const dash = dashQuery.data;
  const running = dash?.running_job;
  const last = dash?.last_job;
  const jobActive = Boolean(
    running && (running.status === "running" || running.status === "pending" || running.status === "paused"),
  );
  const expandedJobRunning =
    Boolean(expandedJobId) &&
    (jobs.find((j) => j.id === expandedJobId)?.status === "running" || detailQuery.data?.job.status === "running");
  const autoPolling = jobActive || expandedJobRunning;

  const invalidateOpsTasks = () => queryClient.invalidateQueries({ queryKey: ["opsTasks"] });

  const refreshAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionsAll }),
      queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionDashboard }),
      invalidateOpsTasks(),
      expandedJobId
        ? queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionDetail(expandedJobId) })
        : Promise.resolve(),
      expandedJobId
        ? queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionRunsAll })
        : Promise.resolve(),
    ]);
  };

  const savePolicyMutation = useMutation({
    mutationFn: () => {
      const hours =
        policyIntervalUnit === "days"
          ? Math.max(1, Math.min(365, policyIntervalValue)) * 24
          : Math.max(1, Math.min(8760, policyIntervalValue));
      return updateCollectionPolicy({
        enabled: policyEnabled,
        interval_hours: hours,
        history_keep: policyHistoryKeep,
        scope_mode: policyScopeMode,
        title: policyTitle.trim(),
        commands: policyCommands,
        selected_targets: Object.values(policySelectedMap),
      });
    },
    onSuccess: async (saved) => {
      setPolicyEnabled(Boolean(saved.enabled));
      const hours = Math.max(1, Number(saved.interval_hours || Number(saved.interval_days || 1) * 24));
      if (hours % 24 === 0) {
        setPolicyIntervalUnit("days");
        setPolicyIntervalValue(Math.max(1, Math.min(365, hours / 24)));
      } else {
        setPolicyIntervalUnit("hours");
        setPolicyIntervalValue(Math.max(1, Math.min(8760, hours)));
      }
      setPolicyHistoryKeep(Math.max(0, Math.min(200, Number(saved.history_keep ?? 3))));
      setPolicyScopeMode(saved.scope_mode === "selected" ? "selected" : "all");
      setPolicyTitle(String(saved.title || ""));
      setPolicyCommands(String(saved.commands || ""));
      const map: Record<string, CollectionTargetRef> = {};
      for (const ref of saved.selected_targets || []) {
        map[targetKey(ref)] = { source: ref.source, id: ref.id };
      }
      setPolicySelectedMap(map);
      setPolicyHydrated(true);
      queryClient.setQueryData(queryKeys.neCollectionDashboard, (prev: unknown) => {
        if (!prev || typeof prev !== "object") return prev;
        return { ...(prev as object), policy: saved };
      });
      showOk(t("collect.policySaved"));
      await refreshAll();
    },
    onError: (err) => showError(collectionErrorMessage(err, t)),
  });

  const startFromPolicyMutation = useMutation({
    mutationFn: startCollectionFromPolicy,
    onSuccess: async (job) => {
      showOk(t("collect.started", { id: job.id }));
      setExpandedJobId(job.id);
      await refreshAll();
    },
    onError: (err) => showError(collectionErrorMessage(err, t)),
  });

  const invalidateJobs = async (jobId?: string) => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionsAll });
    await queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionDashboard });
    await invalidateOpsTasks();
    if (jobId) {
      await queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionDetail(jobId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionRunsAll });
    }
  };

  const pauseMutation = useMutation({
    mutationFn: pauseCollectionJob,
    onSuccess: async (job) => {
      showOk(t("collect.paused"));
      await invalidateJobs(job.id);
    },
    onError: (err) => showError(collectionErrorMessage(err, t)),
  });

  const startJobMutation = useMutation({
    mutationFn: startCollectionJob,
    onSuccess: async (job) => {
      showOk(t("collect.started", { id: job.id }));
      setExpandedJobId(job.id);
      await invalidateJobs(job.id);
    },
    onError: (err) => showError(collectionErrorMessage(err, t)),
  });

  const retryFailedMutation = useMutation({
    mutationFn: retryFailedCollectionJob,
    onSuccess: async (job) => {
      showOk(t("collect.retryFailedDone"));
      setExpandedJobId(job.id);
      await invalidateJobs(job.id);
    },
    onError: (err) => showError(String(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteCollectionJob,
    onSuccess: async (_, jobId) => {
      showOk(t("collect.deleted"));
      if (expandedJobId === jobId) setExpandedJobId("");
      await invalidateJobs();
    },
    onError: (err) => showError(String(err)),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const ne_ids: string[] = [];
      const ume_ne_ids: string[] = [];
      for (const row of selectedList) {
        if (String(row.source || "managed").toLowerCase() === "ume") ume_ne_ids.push(row.id);
        else ne_ids.push(row.id);
      }
      return createNeCollection({
        title: title.trim(),
        commands,
        ne_ids,
        ume_ne_ids,
      });
    },
    onSuccess: async (job) => {
      showOk(t("collect.created", { id: job.id }));
      setCreateOpen(false);
      setExpandedJobId(job.id);
      await invalidateJobs(job.id);
    },
    onError: (err) => showError(String(err)),
  });

  const addNe = (row: EligibleNeItem) => {
    const key = eligibleKey(row);
    setSelectedMap((prev) => (prev[key] ? prev : { ...prev, [key]: row }));
  };

  const addBatchNe = (rows: EligibleNeItem[]) => {
    if (rows.length === 0) return;
    setSelectedMap((prev) => {
      const next = { ...prev };
      for (const row of rows) {
        const key = eligibleKey(row);
        if (!next[key]) next[key] = row;
      }
      return next;
    });
  };

  const removeNe = (key: string) => {
    setSelectedMap((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const clearSelected = () => setSelectedMap({});

  const eligibleItems = eligibleQuery.data?.items ?? [];
  const selectablePickItems = eligibleItems.filter((row) => !selectedMap[eligibleKey(row)]);
  const allPickSelected =
    selectablePickItems.length > 0 &&
    selectablePickItems.every((row) => pickSelectedIds.includes(eligibleKey(row)));
  const batchPickCount = pickSelectedIds.length;
  const neTotal = eligibleQuery.data?.total ?? 0;
  const nePages = pageCount(neTotal, ELIGIBLE_PAGE_SIZE);

  const jobTotal = jobsQuery.data?.total ?? 0;
  const jobPages = pageCount(jobTotal, jobPageSize);
  const hasJobFilters = Boolean(jobKeyword.trim() || jobStatus);

  const exportJobsCsv = async () => {
    setExportingJobs(true);
    try {
      const rows = await fetchAllPages<CollectionJobItem>({
        pageSize: 100,
        maxRows: 2000,
        fetchPage: (page, pageSize) =>
          fetchNeCollections({
            page,
            pageSize,
            status: jobStatus,
            keyword: debouncedJobKeyword,
          }),
      });
      downloadCsv(
        `${t("collect.exportJobsName")}-${new Date().toISOString().slice(0, 10)}.csv`,
        rows,
        [
          { key: "title", header: t("collect.jobs.col.title") },
          { key: "status", header: t("collect.jobs.col.status") },
          {
            key: "progress",
            header: t("collect.jobs.col.progress"),
            value: (r) =>
              `${r.success_count}/${r.ne_count} ${t("collect.jobs.ok")}, ${r.fail_count} ${t("collect.jobs.fail")}`,
          },
          {
            key: "started_at",
            header: "started_at",
            value: (r) => (r.started_at ? formatSystemTime(r.started_at) : ""),
          },
          {
            key: "ended_at",
            header: "ended_at",
            value: (r) => (r.ended_at ? formatSystemTime(r.ended_at) : ""),
          },
        ],
      );
      if (rows.length < jobTotal) {
        showOk(t("common.exportTruncated", { count: String(rows.length), total: String(jobTotal) }));
      } else {
        showOk(t("common.exportOk", { count: String(rows.length) }));
      }
    } catch (err) {
      showError(t("common.exportFailed") + ": " + formatErr(err));
    } finally {
      setExportingJobs(false);
    }
  };

  const commandLines = useMemo(
    () =>
      commands
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#")).length,
    [commands],
  );

  const actionPending =
    pauseMutation.isPending ||
    startJobMutation.isPending ||
    retryFailedMutation.isPending ||
    deleteMutation.isPending;

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel__toolbar">
          <div>
            <h2>{t("collect.jobs.title")}</h2>
            {autoPolling ? (
              <p className="panel__hint panel__hint--live">
                {t("collect.jobs.autoRefresh")}
                {jobsQuery.isFetching || dashQuery.isFetching ? ` · ${t("common.refreshing")}` : ""}
              </p>
            ) : null}
          </div>
          <div className="btn-row">
            <button
              type="button"
              disabled={exportingJobs || jobTotal === 0}
              onClick={() => void exportJobsCsv()}
            >
              {exportingJobs ? t("common.exporting") : t("common.exportCsv")}
            </button>
            <button type="button" onClick={refreshAll} disabled={jobsQuery.isFetching || dashQuery.isFetching}>
              {jobsQuery.isFetching || dashQuery.isFetching ? t("common.refreshing") : t("common.refresh")}
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={startFromPolicyMutation.isPending || jobActive}
              onClick={() => startFromPolicyMutation.mutate()}
            >
              {startFromPolicyMutation.isPending ? t("collect.form.starting") : t("collect.collectNow")}
            </button>
            <button type="button" onClick={() => setCreateOpen(true)}>
              {t("collect.create.expand")}
            </button>
            {running?.status === "running" || running?.status === "pending" ? (
              <button
                type="button"
                onClick={() => pauseMutation.mutate(running.id)}
                disabled={pauseMutation.isPending}
              >
                {t("collect.jobs.pause")}
              </button>
            ) : null}
          </div>
        </div>

        <div className="pt-list" style={{ marginBottom: 16 }}>
          <div className="pt-list-kpis">
            <div className="pt-list-kpi">
              <div className="pt-list-kpi__label">{t("collect.kpi.jobs")}</div>
              <div className="pt-list-kpi__value">{dash?.job_count ?? "—"}</div>
            </div>
            <div className={`pt-list-kpi${running ? " pt-list-kpi--live" : ""}`}>
              <div className="pt-list-kpi__label">{t("collect.kpi.running")}</div>
              <div className="pt-list-kpi__value" style={{ fontSize: running ? 15 : 22 }}>
                {running
                  ? `${running.status} · ${running.success_count}/${running.ne_count}`
                  : t("collect.kpi.idle")}
              </div>
            </div>
            <div className="pt-list-kpi">
              <div className="pt-list-kpi__label">{t("collect.kpi.last")}</div>
              <div className="pt-list-kpi__value" style={{ fontSize: 15 }}>
                {last
                  ? `${last.title} · ${last.status} · ok ${last.success_count} / fail ${last.fail_count}`
                  : t("common.empty")}
              </div>
            </div>
            <div className="pt-list-kpi">
              <div className="pt-list-kpi__label">{t("collect.kpi.nextDue")}</div>
              <div className="pt-list-kpi__value" style={{ fontSize: 15 }}>
                {dash?.next_due_at ? formatSystemTime(dash.next_due_at) : t("common.empty")}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="panel" style={{ marginBottom: 16 }}>
        <h3>{t("collect.policyTitle")}</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          {t("collect.policyHint")}
        </p>
        <div className="config-sync-policy-row">
          <label className="config-sync-policy-check">
            <input
              type="checkbox"
              checked={policyEnabled}
              onChange={(e) => setPolicyEnabled(e.target.checked)}
            />
            <span>{t("collect.enabled")}</span>
          </label>
          <label className="config-sync-policy-field">
            <span>{t("collect.interval")}</span>
            <input
              type="number"
              min={1}
              max={policyIntervalUnit === "days" ? 365 : 8760}
              value={policyIntervalValue}
              onChange={(e) => {
                const max = policyIntervalUnit === "days" ? 365 : 8760;
                setPolicyIntervalValue(Math.max(1, Math.min(max, Number(e.target.value) || 1)));
              }}
            />
            <select
              value={policyIntervalUnit}
              onChange={(e) => {
                const next = e.target.value === "hours" ? "hours" : "days";
                if (next === policyIntervalUnit) return;
                if (next === "hours") {
                  setPolicyIntervalValue(Math.max(1, Math.min(8760, policyIntervalValue * 24)));
                } else {
                  setPolicyIntervalValue(
                    Math.max(1, Math.min(365, Math.round(policyIntervalValue / 24) || 1)),
                  );
                }
                setPolicyIntervalUnit(next);
              }}
            >
              <option value="days">{t("collect.intervalUnitDays")}</option>
              <option value="hours">{t("collect.intervalUnitHours")}</option>
            </select>
          </label>
          <label className="config-sync-policy-field">
            <span>{t("collect.historyKeep")}</span>
            <input
              type="number"
              min={0}
              max={200}
              value={policyHistoryKeep}
              onChange={(e) =>
                setPolicyHistoryKeep(Math.max(0, Math.min(200, Number(e.target.value) || 0)))
              }
            />
          </label>
          <label className="config-sync-policy-field">
            <span>{t("collect.scope")}</span>
            <select
              value={policyScopeMode}
              onChange={(e) => setPolicyScopeMode(e.target.value === "selected" ? "selected" : "all")}
            >
              <option value="all">{t("collect.scopeAll")}</option>
              <option value="selected">{t("collect.scopeSelected")}</option>
            </select>
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={savePolicyMutation.isPending}
            onClick={() => savePolicyMutation.mutate()}
          >
            {t("collect.savePolicy")}
          </button>
        </div>

        <div className="filter-inline" style={{ marginTop: 12 }}>
          <label className="config-sync-policy-field" style={{ flex: 1 }}>
            <span>{t("collect.form.jobTitle")}</span>
            <input
              value={policyTitle}
              placeholder={t("collect.form.jobTitlePh")}
              onChange={(e) => setPolicyTitle(e.target.value)}
            />
          </label>
        </div>
        <label className="config-sync-policy-field" style={{ display: "block", marginTop: 12 }}>
          <span>{t("collect.form.commands")}</span>
          <textarea
            rows={5}
            value={policyCommands}
            placeholder={t("collect.form.commandsPh")}
            onChange={(e) => setPolicyCommands(e.target.value)}
            style={{ width: "100%", fontFamily: "var(--font-mono, monospace)" }}
          />
          <span className="muted">{t("collect.form.commandsHint")}</span>
        </label>

        {policyScopeMode === "selected" ? (
          <div style={{ marginTop: 12 }}>
            <p className="muted">
              {t("collect.selectedCount", { count: String(Object.keys(policySelectedMap).length) })}
            </p>
            <div className="filter-inline">
              <input
                value={policyTargetKeyword}
                placeholder={t("collect.create.filterKeywordPh")}
                onChange={(e) => {
                  setPolicyTargetKeyword(e.target.value);
                  setPolicyTargetPage(1);
                }}
              />
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th />
                  <th>{t("collect.create.pickTitle")}</th>
                  <th>IP</th>
                  <th>{t("collect.colSource")}</th>
                </tr>
              </thead>
              <tbody>
                {(policyTargetsQuery.data?.items ?? []).map((row) => {
                  const key = eligibleKey(row);
                  const checked = Boolean(policySelectedMap[key]);
                  return (
                    <tr key={key}>
                      <td>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => {
                            setPolicySelectedMap((prev) => {
                              const next = { ...prev };
                              if (next[key]) delete next[key];
                              else next[key] = { source: row.source || "managed", id: row.id };
                              return next;
                            });
                          }}
                        />
                      </td>
                      <td>{row.name || row.id}</td>
                      <td>{row.ip_address}</td>
                      <td>{row.source}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <ListPager
              page={policyTargetPage}
              pages={pageCount(policyTargetsQuery.data?.total ?? 0, POLICY_TARGET_PAGE_SIZE)}
              total={policyTargetsQuery.data?.total ?? 0}
              pageSize={POLICY_TARGET_PAGE_SIZE}
              onPageChange={setPolicyTargetPage}
            />
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel__toolbar">
          <div>
            <h2>{t("collect.jobs.listTitle")}</h2>
          </div>
        </div>
        <div className="pt-list">
          <div className="filter-inline">
            <input
              type="search"
              value={jobKeyword}
              placeholder={t("collect.keywordPh")}
              onChange={(e) => {
                setJobKeyword(e.target.value);
                setJobPage(1);
              }}
            />
            <select
              value={jobStatus}
              onChange={(e) => {
                setJobStatus(e.target.value);
                setJobPage(1);
              }}
            >
              <option value="">{t("collect.statusAll")}</option>
              {JOB_STATUS_OPTIONS.map((st) => (
                <option key={st} value={st}>
                  {st}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!hasJobFilters}
              onClick={() => {
                setJobKeyword("");
                setJobStatus("");
                setJobPage(1);
              }}
            >
              {t("common.clearFilters")}
            </button>
          </div>

          {jobsQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
          {jobsQuery.isError ? <p className="error-text">{t("common.opFailed")}</p> : null}

          {!(jobsQuery.data?.items ?? []).length && !jobsQuery.isLoading ? (
            <div className="pt-list-empty">
              <p>{t("common.empty")}</p>
            </div>
          ) : (
            <div className="pt-list-table-wrap">
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th>{t("collect.jobs.col.title")}</th>
                    <th>{t("collect.jobs.col.status")}</th>
                    <th>{t("collect.jobs.col.progress")}</th>
                    <th>{t("collect.jobs.col.created")}</th>
                    <th>{t("collect.jobs.col.lastRun")}</th>
                    <th>{t("collect.jobs.col.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(jobsQuery.data?.items ?? []).map((job) => (
                    <JobRow
                      key={job.id}
                      job={job}
                      onOpenDetail={() => setExpandedJobId(job.id)}
                      onPause={() => pauseMutation.mutate(job.id)}
                      onStart={() => startJobMutation.mutate(job.id)}
                      onRetryFailed={() => retryFailedMutation.mutate(job.id)}
                      onDelete={() => {
                        if (window.confirm(t("collect.confirmDelete"))) deleteMutation.mutate(job.id);
                      }}
                      actionPending={actionPending}
                      startPending={startJobMutation.isPending}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <ListPager
            page={jobPage}
            pages={jobPages}
            total={jobTotal}
            pageSize={jobPageSize}
            pageSizeOptions={JOB_PAGE_SIZE_OPTIONS}
            onPageChange={setJobPage}
            onPageSizeChange={(size) => {
              setJobPageSize(size);
              setJobPage(1);
            }}
            disabled={jobsQuery.isLoading}
          />
        </div>
      </section>

      {createOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setCreateOpen(false)}>
          <div
            className="modal modal--wide ops-detail-modal ops-detail-modal--xl"
            role="dialog"
            aria-modal="true"
            aria-label={t("collect.create.title")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="ops-detail-modal__head">
              <div className="ops-detail-modal__title">
                <h3>{t("collect.create.title")}</h3>
                <p className="muted">{t("collect.create.hint")}</p>
              </div>
              <div className="btn-row ops-detail-modal__actions">
                <button type="button" onClick={() => setCreateOpen(false)}>
                  {t("networkConfigs.close")}
                </button>
              </div>
            </div>

            <div className="ops-detail-modal__scroll ops-detail-modal__scroll--pad collect-create-modal">
              <div className="form-grid form-grid--single">
                <label className="form-grid__full">
                  {t("collect.form.jobTitle")}
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder={t("collect.form.jobTitlePh")}
                  />
                </label>
                <label className="form-grid__full">
                  {t("collect.form.commands")}
                  <textarea
                    className="collect-commands"
                    rows={6}
                    value={commands}
                    onChange={(e) => setCommands(e.target.value)}
                    placeholder={t("collect.form.commandsPh")}
                  />
                </label>
              </div>
              <p className="panel__hint">{t("collect.form.commandsHint")}</p>

              <div className="collect-selected-block">
                <div className="collect-selected-block__head">
                  <h3>{t("collect.create.selectedTitle")}</h3>
                  <span className="collect-meta">
                    {t("collect.create.selectedCount", { count: selectedList.length })}
                  </span>
                  {selectedList.length > 0 ? (
                    <button type="button" className="link-btn" onClick={clearSelected}>
                      {t("collect.create.clearSelected")}
                    </button>
                  ) : null}
                </div>
                {selectedList.length === 0 ? (
                  <p className="panel__hint">{t("collect.create.selectedEmpty")}</p>
                ) : (
                  <div className="collect-selected-list">
                    {selectedList.map((row) => {
                      const key = eligibleKey(row);
                      const src = String(row.source || "managed").toLowerCase();
                      return (
                        <div key={key} className="collect-selected-chip">
                          <span className="collect-selected-chip__main">
                            <strong>{row.name || row.ip_address}</strong>
                            <span className="collect-selected-chip__meta">
                              {src}
                              {row.ip_address ? ` · ${row.ip_address}` : ""}
                              {row.vendor ? ` · ${row.vendor}` : ""}
                            </span>
                          </span>
                          <button type="button" className="link-btn" onClick={() => removeNe(key)}>
                            {t("collect.create.remove")}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="collect-pick-block">
                <div className="panel__toolbar">
                  <div>
                    <h3>{t("collect.create.pickTitle")}</h3>
                    <p className="panel__hint">{t("collect.create.pickHint")}</p>
                  </div>
                  <div className="table-actions">
                    <button
                      type="button"
                      className="link-btn"
                      disabled={batchPickCount === 0}
                      onClick={() => {
                        const rows = eligibleItems.filter(
                          (x) => pickSelectedIds.includes(eligibleKey(x)) && !selectedMap[eligibleKey(x)],
                        );
                        addBatchNe(rows);
                        setPickSelectedIds([]);
                      }}
                    >
                      {t("collect.create.addBatch", { count: batchPickCount })}
                    </button>
                    <button
                      type="button"
                      onClick={() => eligibleQuery.refetch()}
                      disabled={eligibleQuery.isFetching}
                    >
                      {eligibleQuery.isFetching ? t("common.refreshing") : t("common.refresh")}
                    </button>
                  </div>
                </div>
                <div className="ops-detail-modal__toolbar filter-inline">
                  <input
                    type="search"
                    value={neKeyword}
                    placeholder={t("collect.create.filterKeywordPh")}
                    onChange={(e) => {
                      setNeKeyword(e.target.value);
                      setNePage(1);
                      setPickSelectedIds([]);
                    }}
                  />
                </div>
                {eligibleQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
                <div className="pt-list-table-wrap">
                  <table className="data-table pt-list-table">
                    <thead>
                      <tr>
                        <th>
                          <input
                            type="checkbox"
                            checked={allPickSelected}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setPickSelectedIds(selectablePickItems.map((row) => eligibleKey(row)));
                                return;
                              }
                              setPickSelectedIds([]);
                            }}
                            aria-label="pick all"
                          />
                        </th>
                        <th>{t("managedNe.col.source")}</th>
                        <th>{t("managedNe.col.name")}</th>
                        <th>{t("managedNe.col.vendor")}</th>
                        <th>{t("managedNe.col.ip")}</th>
                        <th>{t("managedNe.col.connect")}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {eligibleItems.map((row) => {
                        const key = eligibleKey(row);
                        const picked = Boolean(selectedMap[key]);
                        const src = String(row.source || "managed").toLowerCase();
                        return (
                          <tr key={key}>
                            <td>
                              <input
                                type="checkbox"
                                checked={pickSelectedIds.includes(key)}
                                disabled={picked}
                                onChange={(e) => {
                                  if (picked) return;
                                  setPickSelectedIds((prev) =>
                                    e.target.checked
                                      ? [...new Set([...prev, key])]
                                      : prev.filter((id) => id !== key),
                                  );
                                }}
                              />
                            </td>
                            <td>
                              <span className="table-tag">{src}</span>
                            </td>
                            <td>{row.name || row.ip_address}</td>
                            <td>{row.vendor}</td>
                            <td>{row.ip_address}</td>
                            <td>
                              <span className="conn-pill conn-pill--up">{row.connect_status}</span>
                            </td>
                            <td className="table-actions">
                              <button
                                type="button"
                                className="link-btn"
                                disabled={picked}
                                onClick={() => {
                                  addNe(row);
                                  setPickSelectedIds((prev) => prev.filter((id) => id !== key));
                                }}
                              >
                                {picked ? t("collect.create.added") : t("collect.create.add")}
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                      {!eligibleQuery.isLoading && eligibleItems.length === 0 ? (
                        <tr>
                          <td colSpan={7} className="muted">
                            {t("collect.create.pickEmpty")}
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                {neTotal > 0 ? (
                  <div className="ops-detail-modal__foot" style={{ marginTop: 10 }}>
                    <span className="muted">
                      {t("common.pagerMeta", {
                        total: String(neTotal),
                        page: String(nePage),
                        pages: String(nePages),
                      })}
                    </span>
                    <div className="btn-row">
                      <button
                        type="button"
                        disabled={nePage <= 1}
                        onClick={() => {
                          setNePage(nePage - 1);
                          setPickSelectedIds([]);
                        }}
                      >
                        {t("common.prevPage")}
                      </button>
                      <button
                        type="button"
                        disabled={nePage >= nePages}
                        onClick={() => {
                          setNePage(nePage + 1);
                          setPickSelectedIds([]);
                        }}
                      >
                        {t("common.nextPage")}
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="ops-detail-modal__foot">
              <span className="muted">
                {t("collect.create.meta", { ne: selectedIds.length, cmd: commandLines })}
              </span>
              <div className="btn-row">
                <button type="button" onClick={() => setCreateOpen(false)}>
                  {t("networkConfigs.close")}
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={selectedIds.length === 0 || commandLines === 0 || createMutation.isPending}
                  onClick={() => createMutation.mutate()}
                >
                  {createMutation.isPending ? t("collect.create.creating") : t("collect.create.create")}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {expandedJobId ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setExpandedJobId("")}
        >
          <div
            className="modal modal--wide ops-detail-modal ops-detail-modal--xl"
            role="dialog"
            aria-modal="true"
            aria-label={t("collect.jobs.detailTitle")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="ops-detail-modal__head">
              <div className="ops-detail-modal__title">
                <h3>{t("collect.jobs.detailTitle")}</h3>
                <p className="muted">
                  {(() => {
                    const job = jobs.find((j) => j.id === expandedJobId) || detailQuery.data?.job;
                    return job
                      ? `${job.title} · ${job.status} · ${job.success_count}/${job.ne_count}`
                      : expandedJobId.slice(0, 8);
                  })()}
                </p>
              </div>
              <div className="btn-row ops-detail-modal__actions">
                <button type="button" onClick={() => setExpandedJobId("")}>
                  {t("networkConfigs.close")}
                </button>
              </div>
            </div>
            {detailQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
            <JobRunsPanel
              jobId={expandedJobId}
              jobStatus={
                jobs.find((j) => j.id === expandedJobId)?.status ||
                detailQuery.data?.job.status ||
                ""
              }
              failCount={
                jobs.find((j) => j.id === expandedJobId)?.fail_count ??
                detailQuery.data?.job.fail_count ??
                0
              }
              commands={detailQuery.data?.job.commands ?? ""}
              onRetryFailed={() => retryFailedMutation.mutate(expandedJobId)}
              retryPending={actionPending}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function JobRow({
  job,
  onOpenDetail,
  onPause,
  onStart,
  onRetryFailed,
  onDelete,
  actionPending,
  startPending,
}: {
  job: CollectionJobItem;
  onOpenDetail: () => void;
  onPause: () => void;
  onStart: () => void;
  onRetryFailed: () => void;
  onDelete: () => void;
  actionPending: boolean;
  startPending: boolean;
}) {
  const { t } = useI18n();
  const canPause = job.status === "running";
  const canStart = job.status !== "running";
  const canRetryFailed = job.status !== "running" && job.fail_count > 0;
  const canDelete = job.status !== "running";
  const hasResults = (job.output_count ?? 0) > 0;

  const downloadResults = () => {
    window.location.assign(collectionJobDownloadUrl(job.id));
  };
  return (
    <tr>
      <td>{job.title}</td>
      <td>{job.status}</td>
      <td>
        {job.success_count}/{job.ne_count} {t("collect.jobs.ok")}, {job.fail_count} {t("collect.jobs.fail")}
      </td>
      <td>{formatSystemTime(job.created_at)}</td>
      <td>{job.last_run_at ? formatSystemTime(job.last_run_at) : t("common.empty")}</td>
      <td className="table-actions">
        <button type="button" className="link-btn" onClick={onOpenDetail}>
          {t("collect.jobs.expand")}
        </button>
        {canPause ? (
          <button type="button" className="link-btn" disabled={actionPending} onClick={onPause}>
            {t("collect.jobs.pause")}
          </button>
        ) : null}
        {canStart ? (
          <button type="button" className="link-btn" disabled={actionPending} onClick={onStart}>
            {startPending ? t("collect.jobs.starting") : t("collect.jobs.start")}
          </button>
        ) : null}
        {canRetryFailed ? (
          <button type="button" className="link-btn" disabled={actionPending} onClick={onRetryFailed}>
            {t("collect.jobs.retryFailed")}
          </button>
        ) : null}
        <button
          type="button"
          className="link-btn"
          disabled={actionPending || !hasResults}
          onClick={downloadResults}
        >
          {t("collect.jobs.downloadResults")}
        </button>
        {canDelete ? (
          <button type="button" className="link-btn link-btn--danger" disabled={actionPending} onClick={onDelete}>
            {t("collect.jobs.delete")}
          </button>
        ) : null}
      </td>
    </tr>
  );
}

const RUN_STATUS_OPTIONS = ["pending", "running", "success", "fail", "cancelled"] as const;

function JobRunsPanel({
  jobId,
  jobStatus,
  failCount,
  commands,
  onRetryFailed,
  retryPending,
}: {
  jobId: string;
  jobStatus: string;
  failCount: number;
  commands: string;
  onRetryFailed: () => void;
  retryPending: boolean;
}) {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [runPage, setRunPage] = useState(1);
  const [runPageSize, setRunPageSize] = useState(20);
  const [runStatus, setRunStatus] = useState("");
  const [runKeyword, setRunKeyword] = useState("");
  const [exportingRuns, setExportingRuns] = useState(false);
  const debouncedRunKeyword = useDebouncedValue(runKeyword, 300);

  const runsQuery = useQuery({
    queryKey: queryKeys.neCollectionRuns(jobId, runPage, runPageSize, runStatus, debouncedRunKeyword),
    queryFn: () =>
      fetchCollectionRuns({
        jobId,
        page: runPage,
        pageSize: runPageSize,
        status: runStatus,
        keyword: debouncedRunKeyword,
      }),
    staleTime: 500,
    refetchInterval: jobStatus === "running" ? 2000 : false,
  });

  const clearRunFilters = () => {
    setRunStatus("");
    setRunKeyword("");
    setRunPage(1);
  };

  const runTotal = runsQuery.data?.total ?? 0;
  const runPages = pageCount(runTotal, runPageSize);
  const runs = runsQuery.data?.items ?? [];
  const hasRunFilters = Boolean(runStatus || runKeyword.trim());

  const exportRunsCsv = async () => {
    setExportingRuns(true);
    try {
      const rows = await fetchAllPages<CollectionRunItem>({
        pageSize: 100,
        maxRows: 2000,
        fetchPage: (page, pageSize) =>
          fetchCollectionRuns({
            jobId,
            page,
            pageSize,
            status: runStatus,
            keyword: debouncedRunKeyword,
          }),
      });
      downloadCsv(
        `${t("collect.exportRunsName")}-${jobId.slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.csv`,
        rows,
        [
          { key: "ne_source", header: t("managedNe.col.source"), value: (r) => r.ne_source || "managed" },
          { key: "ne_name", header: t("managedNe.col.name") },
          { key: "ne_ip", header: t("managedNe.col.ip") },
          { key: "status", header: t("collect.runs.status") },
          { key: "message", header: t("collect.runs.message") },
          {
            key: "started_at",
            header: "started_at",
            value: (r) => (r.started_at ? formatSystemTime(r.started_at) : ""),
          },
          {
            key: "ended_at",
            header: "ended_at",
            value: (r) => (r.ended_at ? formatSystemTime(r.ended_at) : ""),
          },
        ],
      );
      if (rows.length < runTotal) {
        showOk(t("common.exportTruncated", { count: String(rows.length), total: String(runTotal) }));
      } else {
        showOk(t("common.exportOk", { count: String(rows.length) }));
      }
    } catch (err) {
      showError(t("common.exportFailed") + ": " + formatErr(err));
    } finally {
      setExportingRuns(false);
    }
  };

  return (
    <>
      {jobStatus === "running" ? (
        <p className="panel__hint panel__hint--live">{t("collect.jobs.runsInProgress")}</p>
      ) : null}
      {commands ? <pre className="collect-cmd-preview collect-cmd-preview--modal">{commands}</pre> : null}
      <div className="ops-detail-modal__toolbar filter-inline collect-runs-toolbar">
        <input
          type="search"
          value={runKeyword}
          placeholder={t("collect.runs.deviceKeywordPh")}
          onChange={(e) => {
            setRunKeyword(e.target.value);
            setRunPage(1);
          }}
        />
        <select
          value={runStatus}
          onChange={(e) => {
            setRunStatus(e.target.value);
            setRunPage(1);
          }}
        >
          <option value="">{t("collect.runs.allStatus")}</option>
          {RUN_STATUS_OPTIONS.map((st) => (
            <option key={st} value={st}>
              {st}
            </option>
          ))}
        </select>
        {hasRunFilters ? (
          <button type="button" onClick={clearRunFilters}>
            {t("common.clearFilters")}
          </button>
        ) : null}
        <button type="button" disabled={exportingRuns || runTotal === 0} onClick={() => void exportRunsCsv()}>
          {exportingRuns ? t("common.exporting") : t("common.exportCsv")}
        </button>
        {jobStatus !== "running" && failCount > 0 ? (
          <button type="button" disabled={retryPending} onClick={onRetryFailed}>
            {t("collect.jobs.retryFailed")}
          </button>
        ) : null}
      </div>
      {runsQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
      <div className="ops-detail-modal__scroll">
        <div className="pt-list-table-wrap">
          <table className="data-table pt-list-table">
            <thead>
              <tr>
                <th>{t("managedNe.col.source")}</th>
                <th>{t("managedNe.col.name")}</th>
                <th>{t("managedNe.col.ip")}</th>
                <th>{t("collect.runs.status")}</th>
                <th>{t("collect.runs.message")}</th>
                <th>{t("collect.runs.download")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <span className="table-tag">{run.ne_source || "managed"}</span>
                  </td>
                  <td>{run.ne_name}</td>
                  <td>{run.ne_ip}</td>
                  <td>{run.status}</td>
                  <td>
                    {run.message ? (
                      <div className="collect-run-message" title={run.message}>
                        {run.message}
                      </div>
                    ) : (
                      t("common.empty")
                    )}
                  </td>
                  <td>
                    {run.has_output ? (
                      <a className="link-btn" href={collectionRunDownloadUrl(run.id)} target="_blank" rel="noreferrer">
                        {t("collect.runs.downloadFile")}
                      </a>
                    ) : (
                      t("common.empty")
                    )}
                  </td>
                </tr>
              ))}
              {!runsQuery.isLoading && runs.length === 0 ? (
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
          page={runPage}
          pages={runPages}
          total={runTotal}
          pageSize={runPageSize}
          pageSizeOptions={RUN_PAGE_SIZE_OPTIONS}
          onPageChange={setRunPage}
          onPageSizeChange={(size) => {
            setRunPageSize(size);
            setRunPage(1);
          }}
          disabled={runsQuery.isLoading}
        />
      </div>
    </>
  );
}
