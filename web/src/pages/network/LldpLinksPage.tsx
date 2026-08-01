import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchCliTargets,
  fetchLldpCollectDashboard,
  fetchLldpCollectJob,
  fetchLldpCollectJobs,
  startLldpCollect,
  updateLldpCollectPolicy,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import type { CliTargetItem, ConfigSyncTargetRef, TopologyDiscoverJobItem } from "../../types";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";

const POLL_MS = 2500;
const TARGET_PAGE_SIZE = 20;
const SEP = " · ";

export function LldpLinksPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();

  const [jobPage, setJobPage] = useState(1);
  const [expandedJobId, setExpandedJobId] = useState("");
  const [itemDetail, setItemDetail] = useState<TopologyDiscoverJobItem | null>(null);

  const [enabled, setEnabled] = useState(false);
  const [intervalDays, setIntervalDays] = useState(1);
  const [concurrency, setConcurrency] = useState(4);
  const [scopeMode, setScopeMode] = useState<"all" | "selected">("all");
  const [autoAdd, setAutoAdd] = useState(true);
  const [selectedMap, setSelectedMap] = useState<Record<string, ConfigSyncTargetRef>>({});
  const [policyHydrated, setPolicyHydrated] = useState(false);

  const [targetKeyword, setTargetKeyword] = useState("");
  const [targetPage, setTargetPage] = useState(1);

  const dashQuery = useQuery({
    queryKey: queryKeys.lldpCollectDashboard,
    queryFn: fetchLldpCollectDashboard,
    staleTime: 1000,
    refetchInterval: (q) => {
      const running = q.state.data?.running_job;
      return running && (running.status === "running" || running.status === "pending") ? POLL_MS : false;
    },
  });

  useEffect(() => {
    if (!dashQuery.data || policyHydrated) return;
    const p = dashQuery.data.policy;
    setEnabled(Boolean(p.enabled));
    setIntervalDays(Number(p.interval_days || 1));
    setConcurrency(Number(p.concurrency || 4));
    setScopeMode(p.scope_mode === "selected" ? "selected" : "all");
    setAutoAdd(Boolean(p.auto_add_unmatched));
    const map: Record<string, ConfigSyncTargetRef> = {};
    for (const ref of p.selected_targets || []) {
      const source = ref.source === "ume" ? "ume" : "managed";
      map[`${source}:${ref.id}`] = { source, id: ref.id };
    }
    setSelectedMap(map);
    setPolicyHydrated(true);
  }, [dashQuery.data, policyHydrated]);

  const jobsQuery = useQuery({
    queryKey: queryKeys.lldpCollectJobs(jobPage),
    queryFn: () => fetchLldpCollectJobs({ page: jobPage, pageSize: 10 }),
    staleTime: 1000,
    refetchInterval: () => (dashQuery.data?.running_job ? POLL_MS : false),
  });

  const jobDetailQuery = useQuery({
    queryKey: queryKeys.lldpCollectJob(expandedJobId),
    queryFn: () => fetchLldpCollectJob(expandedJobId),
    enabled: Boolean(expandedJobId),
    staleTime: 800,
    refetchInterval: () => (dashQuery.data?.running_job?.id === expandedJobId ? POLL_MS : false),
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
      queryClient.invalidateQueries({ queryKey: queryKeys.lldpCollectDashboard }),
      queryClient.invalidateQueries({ queryKey: queryKeys.lldpCollectJobsAll }),
      queryClient.invalidateQueries({ queryKey: queryKeys.lldpCollectJobAll }),
    ]);
  };

  const savePolicyMut = useMutation({
    mutationFn: () =>
      updateLldpCollectPolicy({
        enabled,
        interval_days: intervalDays,
        concurrency,
        scope_mode: scopeMode,
        auto_add_unmatched: autoAdd,
        selected_targets: Object.values(selectedMap),
      }),
    onSuccess: async (saved) => {
      // Apply server response immediately — do NOT flip policyHydrated false then
      // rehydrate from a possibly-stale dashboard cache (checkbox "pops back" bug).
      setEnabled(Boolean(saved.enabled));
      setIntervalDays(Number(saved.interval_days || 1));
      setConcurrency(Number(saved.concurrency || 4));
      setScopeMode(saved.scope_mode === "selected" ? "selected" : "all");
      setAutoAdd(Boolean(saved.auto_add_unmatched));
      const map: Record<string, ConfigSyncTargetRef> = {};
      for (const ref of saved.selected_targets || []) {
        const source = ref.source === "ume" ? "ume" : "managed";
        map[`${source}:${ref.id}`] = { source, id: ref.id };
      }
      setSelectedMap(map);
      setPolicyHydrated(true);
      queryClient.setQueryData(queryKeys.lldpCollectDashboard, (prev: unknown) => {
        if (!prev || typeof prev !== "object") return prev;
        return { ...(prev as object), policy: saved };
      });
      showOk(t("lldpLinks.policySaved"));
      await refresh();
    },
    onError: (err) => showError(String(err)),
  });

  const startMut = useMutation({
    mutationFn: () => startLldpCollect(),
    onSuccess: async () => {
      showOk(t("lldpLinks.started"));
      await refresh();
    },
    onError: (err) => showError(String(err)),
  });

  const dash = dashQuery.data;
  const running = dash?.running_job;
  const last = dash?.last_job;
  const jobs = jobsQuery.data?.items ?? [];
  const jobTotal = Number(jobsQuery.data?.total || 0);
  const jobPages = pageCount(jobTotal, 10);
  const selectedCount = useMemo(() => Object.keys(selectedMap).length, [selectedMap]);
  const detailItems = jobDetailQuery.data?.items ?? [];

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
        <h2>{t("lldpLinks.title")}</h2>
        <div className="btn-row">
          <button type="button" onClick={() => void refresh()} disabled={dashQuery.isFetching}>
            {t("common.refresh")}
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={Boolean(running) || startMut.isPending}
            onClick={() => startMut.mutate()}
          >
            {t("lldpLinks.collectNow")}
          </button>
        </div>
      </div>

      <div className="pt-list" style={{ marginBottom: 16 }}>
        <div className="pt-list-kpis">
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("lldpLinks.kpi.nodes")}</div>
            <div className="pt-list-kpi__value">{dash?.fabric_node_count ?? "—"}</div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("lldpLinks.kpi.edges")}</div>
            <div className="pt-list-kpi__value">{dash?.fabric_edge_active ?? "—"}</div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("lldpLinks.kpi.missing")}</div>
            <div className="pt-list-kpi__value">{dash?.fabric_edge_stale ?? "—"}</div>
          </div>
          <div className={`pt-list-kpi${running ? " pt-list-kpi--live" : ""}`}>
            <div className="pt-list-kpi__label">{t("lldpLinks.kpi.running")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: running ? 15 : 22 }}>
              {running
                ? `${running.status} · ${running.done}/${running.total}`
                : t("lldpLinks.kpi.idle")}
            </div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("lldpLinks.kpi.last")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: 15 }}>
              {last
                ? `${last.status} · +${last.edges_added} / ~${last.edges_updated}`
                : t("common.empty")}
            </div>
          </div>
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("lldpLinks.kpi.nextDue")}</div>
            <div className="pt-list-kpi__value" style={{ fontSize: 15 }}>
              {dash?.next_due_at ? formatSystemTime(dash.next_due_at) : t("common.empty")}
            </div>
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <h3>{t("lldpLinks.policyTitle")}</h3>
        <div className="config-sync-policy-row">
          <label className="config-sync-policy-check">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <span>{t("lldpLinks.enabled")}</span>
          </label>
          <label className="config-sync-policy-check">
            <input type="checkbox" checked={autoAdd} onChange={(e) => setAutoAdd(e.target.checked)} />
            <span>{t("lldpLinks.autoAddUnmatched")}</span>
          </label>
          <label className="config-sync-policy-field">
            <span>{t("lldpLinks.intervalDays")}</span>
            <input
              type="number"
              min={1}
              max={365}
              value={intervalDays}
              onChange={(e) => setIntervalDays(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <label className="config-sync-policy-field">
            <span>{t("lldpLinks.concurrency")}</span>
            <input
              type="number"
              min={1}
              max={32}
              value={concurrency}
              onChange={(e) => setConcurrency(Math.max(1, Math.min(32, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="config-sync-policy-field">
            <span>{t("lldpLinks.scope")}</span>
            <select
              value={scopeMode}
              onChange={(e) => setScopeMode(e.target.value === "selected" ? "selected" : "all")}
            >
              <option value="all">{t("lldpLinks.scopeAll")}</option>
              <option value="selected">{t("lldpLinks.scopeSelected")}</option>
            </select>
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={savePolicyMut.isPending}
            onClick={() => savePolicyMut.mutate()}
          >
            {t("lldpLinks.savePolicy")}
          </button>
        </div>

        {scopeMode === "selected" ? (
          <div style={{ marginTop: 12 }}>
            <p className="muted">{t("lldpLinks.selectedCount", { count: String(selectedCount) })}</p>
            <div className="filter-inline">
              <input
                value={targetKeyword}
                placeholder={t("lldpLinks.targetKeywordPh")}
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
                  <th>{t("lldpLinks.col.source")}</th>
                  <th>{t("lldpLinks.col.name")}</th>
                  <th>IP</th>
                  <th>{t("lldpLinks.col.vendor")}</th>
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

      <h3>{t("lldpLinks.jobsTitle")}</h3>
      <div className="pt-list-table-wrap">
        <table className="data-table pt-list-table">
          <thead>
            <tr>
              <th />
              <th>ID</th>
              <th>{t("lldpLinks.col.trigger")}</th>
              <th>{t("lldpLinks.col.scope")}</th>
              <th>{t("lldpLinks.col.status")}</th>
              <th>{t("lldpLinks.col.progress")}</th>
              <th>{t("lldpLinks.col.edges")}</th>
              <th>{t("lldpLinks.col.missingDelta")}</th>
              <th>{t("lldpLinks.col.started")}</th>
              <th>{t("lldpLinks.col.ended")}</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const open = expandedJobId === job.id;
              return (
                <tr key={job.id} className={open ? "is-expanded" : undefined}>
                  <td>
                    <button
                      type="button"
                      className="btn btn--sm btn--ghost"
                      onClick={() => {
                        setItemDetail(null);
                        setExpandedJobId(open ? "" : job.id);
                      }}
                    >
                      {open ? "−" : "+"}
                    </button>
                  </td>
                  <td className="mono">{job.id.slice(0, 8)}</td>
                  <td>{job.trigger_mode}</td>
                  <td>{job.scope}</td>
                  <td>{job.status}</td>
                  <td>
                    {job.done}/{job.total}
                  </td>
                  <td>
                    +{job.edges_added} / ~{job.edges_updated}
                  </td>
                  <td>{job.edges_stale || 0}</td>
                  <td>{job.started_at ? formatSystemTime(job.started_at) : "—"}</td>
                  <td>{job.ended_at ? formatSystemTime(job.ended_at) : "—"}</td>
                </tr>
              );
            })}
            {!jobs.length ? (
              <tr>
                <td colSpan={10} className="muted">
                  {t("common.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button type="button" disabled={jobPage <= 1} onClick={() => setJobPage((p) => p - 1)}>
          {t("common.prevPage")}
        </button>
        <span className="muted">
          {t("common.pagerMeta", {
            total: String(jobTotal),
            page: String(jobPage),
            pages: String(jobPages),
          })}
        </span>
        <button type="button" disabled={jobPage >= jobPages} onClick={() => setJobPage((p) => p + 1)}>
          {t("common.nextPage")}
        </button>
      </div>

      {expandedJobId ? (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3>{t("lldpLinks.jobDetailTitle")}</h3>
          {jobDetailQuery.isLoading ? <p className="muted">…</p> : null}
          {jobDetailQuery.data?.error ? (
            <p className="muted" style={{ color: "var(--danger, #b00)" }}>
              {jobDetailQuery.data.error}
            </p>
          ) : null}
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("lldpLinks.col.name")}</th>
                <th>IP</th>
                <th>{t("lldpLinks.col.status")}</th>
                <th>{t("lldpLinks.col.neighbors")}</th>
                <th>{t("lldpLinks.col.edges")}</th>
                <th>{t("lldpLinks.col.error")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {detailItems.map((it) => {
                const unmatchedCount = it.unmatched_count ?? (it.unmatched?.length || 0);
                return (
                  <tr key={it.id}>
                    <td>{it.ne_name || it.ne_id || "—"}</td>
                    <td>{it.ne_ip || "—"}</td>
                    <td>
                      {!it.ok ? "fail" : it.parser_stub || unmatchedCount > 0 ? "warn" : "ok"}
                    </td>
                    <td>{it.neighbors}</td>
                    <td>
                      +{it.edges_added} / ~{it.edges_updated}
                      {unmatchedCount > 0
                        ? ` · ${t("topology.discoverUnmatched").replace("{{count}}", String(unmatchedCount))}`
                        : ""}
                    </td>
                    <td className="muted">{it.error || "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        onClick={() => setItemDetail(it)}
                      >
                        {t("topology.discoverViewDetail")}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!detailItems.length && !jobDetailQuery.isLoading ? (
                <tr>
                  <td colSpan={7} className="muted">
                    {t("common.empty")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}

      {itemDetail ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setItemDetail(null)}>
          <div
            className="modal modal--wide topo-discover-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="lldp-item-detail-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="topo-discover-modal__head">
              <div>
                <h3 id="lldp-item-detail-title">
                  {itemDetail.ne_name || itemDetail.ne_ip || itemDetail.ne_id}
                </h3>
                <p className="topo-discover-modal__sub">
                  {[itemDetail.ne_ip, itemDetail.command].filter(Boolean).join(SEP)}
                </p>
              </div>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => setItemDetail(null)}
              >
                {t("topology.discoverClose")}
              </button>
            </div>

            {!itemDetail.ok ? (
              <p className="topo-discover__error">
                {itemDetail.error || t("topology.discoverNeFail")}
              </p>
            ) : null}
            {itemDetail.parser_stub ? (
              <p className="topo-discover__item-warn">
                {t("topology.discoverParserStub").replace(
                  "{{parser}}",
                  itemDetail.parser_key || "unknown",
                )}
              </p>
            ) : null}

            <div className="topo-discover-modal__stats">
              <span>
                {t("topology.discoverNeOk")
                  .replace("{{neighbors}}", String(itemDetail.neighbors || 0))
                  .replace("{{added}}", String(itemDetail.edges_added || 0))
                  .replace("{{updated}}", String(itemDetail.edges_updated || 0))}
              </span>
            </div>

            <h4 className="topo-discover-modal__section">
              {t("topology.discoverUnmatchedTitle").replace(
                "{{count}}",
                String(itemDetail.unmatched_count ?? itemDetail.unmatched?.length ?? 0),
              )}
            </h4>
            {(itemDetail.unmatched || []).length === 0 ? (
              <p className="panel__hint">{t("topology.discoverUnmatchedEmpty")}</p>
            ) : (
              <div className="topo-discover-modal__table-wrap">
                <table className="topo-discover-modal__table">
                  <thead>
                    <tr>
                      <th>{t("topology.discoverColRemote")}</th>
                      <th>{t("topology.discoverColLocalPort")}</th>
                      <th>{t("topology.discoverColRemotePort")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(itemDetail.unmatched || []).map((u, idx) => (
                      <tr key={`${itemDetail.id}-u-${idx}`}>
                        <td>
                          {(u.remote_name || u.remote_ip || "?").trim()}
                          {u.remote_ip && u.remote_name ? ` (${u.remote_ip})` : ""}
                        </td>
                        <td>{u.local_port || "?"}</td>
                        <td>{u.remote_port || "?"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {itemDetail.raw_preview ? (
              <>
                <h4 className="topo-discover-modal__section">{t("topology.discoverRawPreview")}</h4>
                <pre className="topo-discover-modal__raw">{itemDetail.raw_preview}</pre>
              </>
            ) : null}

            <div className="modal__actions">
              <button type="button" className="btn btn--sm" onClick={() => setItemDetail(null)}>
                {t("topology.discoverClose")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
