import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createNeCollection,
  deleteCollectionJob,
  fetchCollectionJob,
  fetchCollectionRuns,
  fetchEligibleNe,
  fetchNeCollections,
  pauseCollectionJob,
  restartCollectionJob,
  retryFailedCollectionJob,
  collectionJobDownloadUrl,
  collectionRunDownloadUrl,
} from "../services/api";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { CollectionJobDetail, CollectionJobItem, EligibleNeItem } from "../types";
import { pageCount } from "../utils/display";
import { formatSystemTime } from "../utils/time";

export function CollectPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();

  const [commands, setCommands] = useState("");
  const [title, setTitle] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [nePage, setNePage] = useState(1);
  const [jobPage, setJobPage] = useState(1);
  const [expandedJobId, setExpandedJobId] = useState("");

  const POLL_MS = 2000;
  const ELIGIBLE_PAGE_SIZE = 20;

  const eligibleQuery = useQuery({
    queryKey: queryKeys.collectionEligibleNe(nePage),
    queryFn: () => fetchEligibleNe({ page: nePage, pageSize: ELIGIBLE_PAGE_SIZE }),
    staleTime: 5000,
  });

  const jobsQuery = useQuery({
    queryKey: queryKeys.neCollections(jobPage),
    queryFn: () => fetchNeCollections({ page: jobPage, pageSize: 20 }),
    staleTime: 1000,
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? [];
      return items.some((j) => j.status === "running") ? POLL_MS : false;
    },
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.neCollectionDetail(expandedJobId),
    queryFn: () => fetchCollectionJob(expandedJobId),
    enabled: Boolean(expandedJobId),
    staleTime: 500,
    refetchInterval: (q) => (q.state.data?.job.status === "running" ? POLL_MS : false),
  });

  const jobs = jobsQuery.data?.items ?? [];
  const jobActive = jobs.some((j) => j.status === "running");
  const expandedJobRunning =
    Boolean(expandedJobId) &&
    (jobs.find((j) => j.id === expandedJobId)?.status === "running" || detailQuery.data?.job.status === "running");
  const autoPolling = jobActive || expandedJobRunning;

  const refreshAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionsAll }),
      expandedJobId
        ? queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionDetail(expandedJobId) })
        : Promise.resolve(),
      expandedJobId
        ? queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionRunsAll })
        : Promise.resolve(),
    ]);
  };

  const invalidateJobs = async (jobId?: string) => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionsAll });
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
    onError: (err) => showError(String(err)),
  });

  const restartMutation = useMutation({
    mutationFn: restartCollectionJob,
    onSuccess: async (job) => {
      showOk(t("collect.restarted"));
      setExpandedJobId(job.id);
      await invalidateJobs(job.id);
    },
    onError: (err) => showError(String(err)),
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

  const startMutation = useMutation({
    mutationFn: () =>
      createNeCollection({
        title: title.trim(),
        commands,
        ne_ids: selected,
      }),
    onSuccess: async (job) => {
      showOk(t("collect.started", { id: job.id }));
      setExpandedJobId(job.id);
      await queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionsAll });
      await queryClient.invalidateQueries({ queryKey: queryKeys.neCollectionDetail(job.id) });
    },
    onError: (err) => showError(String(err)),
  });

  const items = eligibleQuery.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((x) => selected.includes(x.id));

  const toggleAll = () => {
    if (allSelected) {
      const ids = new Set(items.map((x) => x.id));
      setSelected((prev) => prev.filter((id) => !ids.has(id)));
    } else {
      setSelected((prev) => [...new Set([...prev, ...items.map((x) => x.id)])]);
    }
  };

  const neTotal = eligibleQuery.data?.total ?? 0;
  const nePages = pageCount(neTotal, ELIGIBLE_PAGE_SIZE);

  const jobTotal = jobsQuery.data?.total ?? 0;
  const jobPages = pageCount(jobTotal, 20);

  const commandLines = useMemo(
    () =>
      commands
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#")).length,
    [commands],
  );

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel__toolbar">
          <div>
            <h2>{t("collect.eligible.title")}</h2>
            <p className="panel__hint">{t("collect.eligible.hint")}</p>
          </div>
          <button type="button" onClick={() => eligibleQuery.refetch()} disabled={eligibleQuery.isFetching}>
            {eligibleQuery.isFetching ? t("common.refreshing") : t("common.refresh")}
          </button>
        </div>
        {eligibleQuery.isLoading ? <p>{t("common.refreshing")}</p> : null}
        {!eligibleQuery.isLoading && items.length === 0 ? (
          <p>{t("collect.eligible.empty")}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="select all" />
                </th>
                <th>{t("managedNe.col.name")}</th>
                <th>{t("managedNe.col.vendor")}</th>
                <th>{t("managedNe.col.ip")}</th>
                <th>{t("managedNe.col.connect")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row: EligibleNeItem) => (
                <tr key={row.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.includes(row.id)}
                      onChange={() =>
                        setSelected((prev) =>
                          prev.includes(row.id) ? prev.filter((x) => x !== row.id) : [...prev, row.id],
                        )
                      }
                    />
                  </td>
                  <td>{row.name || row.ip_address}</td>
                  <td>{row.vendor}</td>
                  <td>{row.ip_address}</td>
                  <td>
                    <span className="conn-pill conn-pill--up">{row.connect_status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {neTotal > 0 ? (
          <div className="pager">
            <div className="pager__meta">{t("common.pagerMeta", { total: neTotal, page: nePage, pages: nePages })}</div>
            <div className="pager__controls">
              <button className="pager__btn" disabled={nePage <= 1} onClick={() => setNePage(nePage - 1)}>
                {t("common.prevPage")}
              </button>
              <button className="pager__btn" disabled={nePage >= nePages} onClick={() => setNePage(nePage + 1)}>
                {t("common.nextPage")}
              </button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h2>{t("collect.form.title")}</h2>
        <p className="panel__hint">{t("collect.form.commandsHint")}</p>
        <div className="form-grid form-grid--single">
          <label className="form-grid__full">
            {t("collect.form.jobTitle")}
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("collect.form.jobTitlePh")} />
          </label>
          <label className="form-grid__full">
            {t("collect.form.commands")}
            <textarea
              className="collect-commands"
              rows={10}
              value={commands}
              onChange={(e) => setCommands(e.target.value)}
              placeholder={t("collect.form.commandsPh")}
            />
          </label>
        </div>
        <div className="panel__actions">
          <span className="collect-meta">
            {t("collect.form.meta", { ne: selected.length, cmd: commandLines })}
          </span>
          <button
            type="button"
            disabled={selected.length === 0 || commandLines === 0 || startMutation.isPending}
            onClick={() => startMutation.mutate()}
          >
            {startMutation.isPending ? t("collect.form.starting") : t("collect.form.start")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel__toolbar">
          <div>
            <h2>{t("collect.jobs.title")}</h2>
            {autoPolling ? (
              <p className="panel__hint panel__hint--live">
                {t("collect.jobs.autoRefresh")}
                {jobsQuery.isFetching ? ` · ${t("common.refreshing")}` : ""}
              </p>
            ) : null}
          </div>
          <button type="button" onClick={refreshAll} disabled={jobsQuery.isFetching}>
            {jobsQuery.isFetching ? t("common.refreshing") : t("common.refresh")}
          </button>
        </div>
        <table>
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
                expanded={expandedJobId === job.id}
                detail={expandedJobId === job.id ? detailQuery.data : undefined}
                onToggle={() => setExpandedJobId(expandedJobId === job.id ? "" : job.id)}
                onPause={() => pauseMutation.mutate(job.id)}
                onRestart={() => restartMutation.mutate(job.id)}
                onRetryFailed={() => retryFailedMutation.mutate(job.id)}
                onDelete={() => {
                  if (window.confirm(t("collect.confirmDelete"))) deleteMutation.mutate(job.id);
                }}
                actionPending={
                  pauseMutation.isPending ||
                  restartMutation.isPending ||
                  retryFailedMutation.isPending ||
                  deleteMutation.isPending
                }
              />
            ))}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">{t("common.pagerMeta", { total: jobTotal, page: jobPage, pages: jobPages })}</div>
          <div className="pager__controls">
            <button className="pager__btn" disabled={jobPage <= 1} onClick={() => setJobPage(jobPage - 1)}>
              {t("common.prevPage")}
            </button>
            <button className="pager__btn" disabled={jobPage >= jobPages} onClick={() => setJobPage(jobPage + 1)}>
              {t("common.nextPage")}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function JobRow({
  job,
  expanded,
  detail,
  onToggle,
  onPause,
  onRestart,
  onRetryFailed,
  onDelete,
  actionPending,
}: {
  job: CollectionJobItem;
  expanded: boolean;
  detail?: CollectionJobDetail;
  onToggle: () => void;
  onPause: () => void;
  onRestart: () => void;
  onRetryFailed: () => void;
  onDelete: () => void;
  actionPending: boolean;
}) {
  const { t } = useI18n();
  const canPause = job.status === "running";
  const canRestart = job.status !== "running";
  const canRetryFailed = job.status !== "running" && job.fail_count > 0;
  const canDelete = job.status !== "running";
  const hasResults = (job.output_count ?? 0) > 0;

  const downloadResults = () => {
    window.location.assign(collectionJobDownloadUrl(job.id));
  };
  return (
    <>
      <tr>
        <td>{job.title}</td>
        <td>{job.status}</td>
        <td>
          {job.success_count}/{job.ne_count} {t("collect.jobs.ok")}, {job.fail_count} {t("collect.jobs.fail")}
        </td>
        <td>{formatSystemTime(job.created_at)}</td>
        <td>{job.last_run_at ? formatSystemTime(job.last_run_at) : t("common.empty")}</td>
        <td className="table-actions">
          <button type="button" className="link-btn" onClick={onToggle}>
            {expanded ? t("collect.jobs.collapse") : t("collect.jobs.expand")}
          </button>
          {canPause ? (
            <button type="button" className="link-btn" disabled={actionPending} onClick={onPause}>
              {t("collect.jobs.pause")}
            </button>
          ) : null}
          {canRestart ? (
            <button type="button" className="link-btn" disabled={actionPending} onClick={onRestart}>
              {t("collect.jobs.restart")}
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
      {expanded ? (
        <tr>
          <td colSpan={6}>
            <JobRunsPanel
              jobId={job.id}
              jobStatus={job.status}
              failCount={job.fail_count}
              commands={detail?.job.commands ?? ""}
              onRetryFailed={onRetryFailed}
              retryPending={actionPending}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

const RUN_PAGE_SIZE = 20;
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
  const [runPage, setRunPage] = useState(1);
  const [runStatus, setRunStatus] = useState("");
  const [runKeyword, setRunKeyword] = useState("");

  const runsQuery = useQuery({
    queryKey: queryKeys.neCollectionRuns(jobId, runPage, runStatus, runKeyword),
    queryFn: () =>
      fetchCollectionRuns({
        jobId,
        page: runPage,
        pageSize: RUN_PAGE_SIZE,
        status: runStatus,
        keyword: runKeyword,
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
  const runPages = pageCount(runTotal, RUN_PAGE_SIZE);
  const runs = runsQuery.data?.items ?? [];

  return (
    <div className="collect-runs-panel">
      {jobStatus === "running" ? (
        <p className="panel__hint panel__hint--live">{t("collect.jobs.runsInProgress")}</p>
      ) : null}
      {commands ? <pre className="collect-cmd-preview">{commands}</pre> : null}
      <div className="collect-runs-toolbar">
        <label className="collect-runs-filter">
          {t("collect.runs.filterDevice")}
          <input
            type="search"
            value={runKeyword}
            placeholder={t("collect.runs.deviceKeywordPh")}
            onChange={(e) => {
              setRunKeyword(e.target.value);
              setRunPage(1);
            }}
          />
        </label>
        <label className="collect-runs-filter">
          {t("collect.runs.filterStatus")}
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
        </label>
        {runStatus || runKeyword ? (
          <button type="button" className="link-btn" onClick={clearRunFilters}>
            {t("common.clearFilters")}
          </button>
        ) : null}
        {jobStatus !== "running" && failCount > 0 ? (
          <button type="button" className="link-btn" disabled={retryPending} onClick={onRetryFailed}>
            {t("collect.jobs.retryFailed")}
          </button>
        ) : null}
      </div>
      {runsQuery.isLoading ? <p>{t("common.refreshing")}</p> : null}
      {!runsQuery.isLoading && runs.length === 0 ? <p>{t("common.empty")}</p> : null}
      {runs.length > 0 ? (
        <table>
          <thead>
            <tr>
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
          </tbody>
        </table>
      ) : null}
      {runTotal > 0 ? (
        <div className="pager">
          <div className="pager__meta">{t("common.pagerMeta", { total: runTotal, page: runPage, pages: runPages })}</div>
          <div className="pager__controls">
            <button className="pager__btn" disabled={runPage <= 1} onClick={() => setRunPage(runPage - 1)}>
              {t("common.prevPage")}
            </button>
            <button className="pager__btn" disabled={runPage >= runPages} onClick={() => setRunPage(runPage + 1)}>
              {t("common.nextPage")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
