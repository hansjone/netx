import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchCliTargets,
  fetchFabricEdges,
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

  const [edgeStatus, setEdgeStatus] = useState<"all" | "active" | "missing">("all");
  const [edgeKeyword, setEdgeKeyword] = useState("");
  const [edgePage, setEdgePage] = useState(1);
  const EDGE_PAGE_SIZE = 20;

  const [enabled, setEnabled] = useState(false);
  const [intervalValue, setIntervalValue] = useState(1);
  const [intervalUnit, setIntervalUnit] = useState<"days" | "hours">("days");
  const [concurrency, setConcurrency] = useState(4);
  const [historyKeep, setHistoryKeep] = useState(30);
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
    const hours = Math.max(1, Number(p.interval_hours || (Number(p.interval_days || 1) * 24)));
    if (hours % 24 === 0) {
      setIntervalUnit("days");
      setIntervalValue(Math.max(1, Math.min(365, hours / 24)));
    } else {
      setIntervalUnit("hours");
      setIntervalValue(Math.max(1, Math.min(8760, hours)));
    }
    setConcurrency(Number(p.concurrency || 4));
    setHistoryKeep(Math.max(0, Math.min(200, Number(p.history_keep ?? 30))));
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

  const edgesQuery = useQuery({
    queryKey: queryKeys.fabricEdges(edgeStatus, edgeKeyword, edgePage),
    queryFn: () =>
      fetchFabricEdges({
        status: edgeStatus === "all" ? "" : edgeStatus,
        keyword: edgeKeyword,
        page: edgePage,
        pageSize: EDGE_PAGE_SIZE,
      }),
    staleTime: 2000,
    refetchInterval: () => (dashQuery.data?.running_job ? POLL_MS : false),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.lldpCollectDashboard }),
      queryClient.invalidateQueries({ queryKey: queryKeys.lldpCollectJobsAll }),
      queryClient.invalidateQueries({ queryKey: queryKeys.lldpCollectJobAll }),
      queryClient.invalidateQueries({ queryKey: queryKeys.fabricEdgesAll }),
    ]);
  };

  const savePolicyMut = useMutation({
    mutationFn: () => {
      const hours =
        intervalUnit === "days"
          ? Math.max(1, Math.min(365, intervalValue)) * 24
          : Math.max(1, Math.min(8760, intervalValue));
      return updateLldpCollectPolicy({
        enabled,
        interval_hours: hours,
        concurrency,
        history_keep: historyKeep,
        scope_mode: scopeMode,
        auto_add_unmatched: autoAdd,
        selected_targets: Object.values(selectedMap),
      });
    },
    onSuccess: async (saved) => {
      // Apply server response immediately — do NOT flip policyHydrated false then
      // rehydrate from a possibly-stale dashboard cache (checkbox "pops back" bug).
      setEnabled(Boolean(saved.enabled));
      const hours = Math.max(1, Number(saved.interval_hours || (Number(saved.interval_days || 1) * 24)));
      if (hours % 24 === 0) {
        setIntervalUnit("days");
        setIntervalValue(Math.max(1, Math.min(365, hours / 24)));
      } else {
        setIntervalUnit("hours");
        setIntervalValue(Math.max(1, Math.min(8760, hours)));
      }
      setConcurrency(Number(saved.concurrency || 4));
      setHistoryKeep(Math.max(0, Math.min(200, Number(saved.history_keep ?? 30))));
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
  const edgeItems = edgesQuery.data?.items ?? [];
  const edgeTotal = Number(edgesQuery.data?.total || 0);
  const edgePages = pageCount(edgeTotal, EDGE_PAGE_SIZE);

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
            <div className="pt-list-kpi__value">
              {dash?.fabric_edge_missing ?? dash?.fabric_edge_stale ?? "—"}
            </div>
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
            <span>{t("lldpLinks.interval")}</span>
            <input
              type="number"
              min={1}
              max={intervalUnit === "days" ? 365 : 8760}
              value={intervalValue}
              onChange={(e) => {
                const max = intervalUnit === "days" ? 365 : 8760;
                setIntervalValue(Math.max(1, Math.min(max, Number(e.target.value) || 1)));
              }}
            />
            <select
              value={intervalUnit}
              onChange={(e) => {
                const next = e.target.value === "hours" ? "hours" : "days";
                if (next === intervalUnit) return;
                if (next === "hours") {
                  setIntervalValue(Math.max(1, Math.min(8760, intervalValue * 24)));
                } else {
                  setIntervalValue(Math.max(1, Math.min(365, Math.round(intervalValue / 24) || 1)));
                }
                setIntervalUnit(next);
              }}
            >
              <option value="days">{t("lldpLinks.intervalUnitDays")}</option>
              <option value="hours">{t("lldpLinks.intervalUnitHours")}</option>
            </select>
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
            <span>{t("lldpLinks.historyKeep")}</span>
            <input
              type="number"
              min={0}
              max={200}
              value={historyKeep}
              onChange={(e) => setHistoryKeep(Math.max(0, Math.min(200, Number(e.target.value) || 0)))}
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

      <h3>{t("lldpLinks.edgesTitle")}</h3>
      <div className="filter-inline" style={{ marginBottom: 8 }}>
        <select
          value={edgeStatus}
          onChange={(e) => {
            const v = e.target.value;
            setEdgeStatus(v === "active" || v === "missing" ? v : "all");
            setEdgePage(1);
          }}
        >
          <option value="all">{t("lldpLinks.edgeStatusAll")}</option>
          <option value="active">{t("lldpLinks.edgeStatusActive")}</option>
          <option value="missing">{t("lldpLinks.edgeStatusMissing")}</option>
        </select>
        <input
          value={edgeKeyword}
          placeholder={t("lldpLinks.edgeKeywordPh")}
          onChange={(e) => {
            setEdgeKeyword(e.target.value);
            setEdgePage(1);
          }}
        />
      </div>
      <div className="pt-list-table-wrap">
        <table className="data-table pt-list-table">
          <thead>
            <tr>
              <th>{t("lldpLinks.col.status")}</th>
              <th>{t("lldpLinks.col.aSide")}</th>
              <th>{t("lldpLinks.col.aPort")}</th>
              <th>{t("lldpLinks.col.bSide")}</th>
              <th>{t("lldpLinks.col.bPort")}</th>
              <th>{t("lldpLinks.col.source")}</th>
              <th>{t("lldpLinks.missCount")}</th>
              <th>{t("lldpLinks.col.lastSeen")}</th>
            </tr>
          </thead>
          <tbody>
            {edgeItems.map((e) => {
              const attrs = (e.attrs || {}) as Record<string, unknown>;
              const missCount = Number(attrs.miss_count || 0) || 0;
              const replaced = String(attrs.replaced_by_edge_id || "").trim();
              const aLabel = e.a_name || e.a_ip || e.a_node_id.slice(0, 8);
              const bLabel = e.b_name || e.b_ip || e.b_node_id.slice(0, 8);
              const st = e.status === "stale" ? "missing" : e.status;
              return (
                <tr key={e.id}>
                  <td>{st}</td>
                  <td>
                    <div>{aLabel}</div>
                    {e.a_ip && e.a_name ? <div className="muted">{e.a_ip}</div> : null}
                  </td>
                  <td>{e.a_port || "—"}</td>
                  <td>
                    <div>{bLabel}</div>
                    {e.b_ip && e.b_name ? <div className="muted">{e.b_ip}</div> : null}
                  </td>
                  <td>{e.b_port || "—"}</td>
                  <td>{e.source}</td>
                  <td>
                    {missCount || (st === "missing" ? 1 : 0)}
                    {replaced ? (
                      <div className="muted" title={replaced}>
                        {t("lldpLinks.replacedBy")}
                      </div>
                    ) : null}
                  </td>
                  <td>{e.last_seen_at ? formatSystemTime(e.last_seen_at) : "—"}</td>
                </tr>
              );
            })}
            {!edgeItems.length ? (
              <tr>
                <td colSpan={8} className="muted">
                  {edgesQuery.isLoading ? "…" : t("common.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button type="button" disabled={edgePage <= 1} onClick={() => setEdgePage((p) => p - 1)}>
          {t("common.prevPage")}
        </button>
        <span className="muted">
          {t("common.pagerMeta", {
            total: String(edgeTotal),
            page: String(edgePage),
            pages: String(edgePages),
          })}
        </span>
        <button
          type="button"
          disabled={edgePage >= edgePages}
          onClick={() => setEdgePage((p) => p + 1)}
        >
          {t("common.nextPage")}
        </button>
      </div>

      <h3 style={{ marginTop: 24 }}>{t("lldpLinks.jobsTitle")}</h3>
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
                  <td>{job.edges_missing ?? job.edges_stale ?? 0}</td>
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
                {itemDetail.error === "vendor_or_device_type_required"
                  ? t("topology.discoverVendorRequired")
                  : itemDetail.error || t("topology.discoverNeFail")}
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
