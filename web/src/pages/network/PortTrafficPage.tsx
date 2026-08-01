import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  collectPortTrafficNow,
  createPortTrafficTask,
  deletePortTrafficTask,
  discoverPortTrafficPorts,
  fetchCliTargets,
  fetchPortTrafficCompare,
  fetchPortTrafficDashboard,
  fetchPortTrafficTargets,
  fetchPortTrafficTasks,
  pausePortTrafficTask,
  startPortTrafficTask,
  stopPortTrafficTask,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import type { CliTargetItem, PortTrafficDiscoverPort, PortTrafficSamplePoint, PortTrafficTargetIn } from "../../types";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";
import { PortTrafficWall } from "./PortTrafficWall";

const POLL_MS = 5000;
const TARGET_PAGE_SIZE = 20;
const EMPTY_COMPARE_POINTS: PortTrafficSamplePoint[] = [];

type PortPick = PortTrafficTargetIn & { key: string };
type BaselineMode = "off" | "shift" | "day" | "week" | "custom";

function neKey(source: string, id: string) {
  return `${source}:${id}`;
}

function portKey(source: string, id: string, ifname: string) {
  return `${source}:${id}:${ifname}`;
}

function formatBw(bps: number) {
  if (!bps) return "—";
  if (bps >= 1e9) return `${bps / 1e9}G`;
  if (bps >= 1e6) return `${bps / 1e6}M`;
  return `${bps}`;
}

export function PortTrafficPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkAppliedRef = useRef(false);

  const [view, setView] = useState<"list" | "wizard" | "wall">("list");
  const [taskPage, setTaskPage] = useState(1);
  const [wallTaskId, setWallTaskId] = useState("");
  const [wallTargetId, setWallTargetId] = useState("");
  const [mapBaselineTaskId, setMapBaselineTaskId] = useState("");
  const [mapBaselineTargetId, setMapBaselineTargetId] = useState("");
  const [rangeHours, setRangeHours] = useState(24);
  const [baseline, setBaseline] = useState<BaselineMode>("off");
  const [customOffsetHours, setCustomOffsetHours] = useState(48);

  // Wizard state
  const [step, setStep] = useState(1);
  const [title, setTitle] = useState("");
  const [intervalSec, setIntervalSec] = useState(60);
  const [retentionDays, setRetentionDays] = useState(7);
  const [concurrency, setConcurrency] = useState(5);
  const [startNow, setStartNow] = useState(true);
  const [neKeyword, setNeKeyword] = useState("");
  const [nePage, setNePage] = useState(1);
  const [selectedNes, setSelectedNes] = useState<Record<string, CliTargetItem>>({});
  const [portsByNe, setPortsByNe] = useState<
    Record<string, { loading?: boolean; error?: string; ports: PortTrafficDiscoverPort[]; meta?: { ne_name: string; ne_ip: string; vendor: string } }>
  >({});
  const [pickedPorts, setPickedPorts] = useState<Record<string, PortPick>>({});
  const [deepLinkHint, setDeepLinkHint] = useState("");
  const [deepLinkSource, setDeepLinkSource] = useState<"managed" | "ume" | "">("");
  const [deepLinkNeId, setDeepLinkNeId] = useState("");

  const dashQuery = useQuery({
    queryKey: queryKeys.portTrafficDashboard,
    queryFn: fetchPortTrafficDashboard,
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const tasksQuery = useQuery({
    queryKey: queryKeys.portTrafficTasks(taskPage),
    queryFn: () => fetchPortTrafficTasks({ page: taskPage, pageSize: 10 }),
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const wallTasksQuery = useQuery({
    queryKey: queryKeys.portTrafficTasks(0),
    queryFn: () => fetchPortTrafficTasks({ page: 1, pageSize: 100 }),
    enabled: view === "wall",
    staleTime: 5000,
  });

  const nesQuery = useQuery({
    queryKey: queryKeys.cliTargets(neKeyword, nePage, TARGET_PAGE_SIZE),
    queryFn: () =>
      fetchCliTargets({ source: "all", keyword: neKeyword, page: nePage, pageSize: TARGET_PAGE_SIZE }),
    enabled: view === "wizard" && step === 2,
    staleTime: 5000,
  });

  const wallTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(wallTaskId),
    queryFn: () => fetchPortTrafficTargets(wallTaskId),
    enabled: view === "wall" && Boolean(wallTaskId),
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const mapBaselineTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(mapBaselineTaskId),
    queryFn: () => fetchPortTrafficTargets(mapBaselineTaskId),
    enabled: view === "wall" && Boolean(mapBaselineTaskId),
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const compareQuery = useQuery({
    queryKey: queryKeys.portTrafficCompare(
      wallTargetId,
      rangeHours,
      baseline,
      baseline === "custom" ? customOffsetHours : 0,
      mapBaselineTargetId,
    ),
    queryFn: () =>
      fetchPortTrafficCompare({
        targetId: wallTargetId,
        rangeHours,
        baseline,
        offsetHours: baseline === "custom" ? customOffsetHours : undefined,
        baselineTargetId: mapBaselineTargetId || undefined,
      }),
    enabled: view === "wall" && Boolean(wallTargetId),
    staleTime: 1000,
    placeholderData: keepPreviousData,
    refetchInterval: (q) => {
      const n = q.state.data?.current?.length ?? 0;
      return n === 0 ? 2500 : POLL_MS;
    },
  });

  const wallPoints = compareQuery.data?.current ?? EMPTY_COMPARE_POINTS;
  const wallBaselinePoints =
    baseline === "off" && !mapBaselineTargetId
      ? EMPTY_COMPARE_POINTS
      : (compareQuery.data?.baseline ?? EMPTY_COMPARE_POINTS);

  useEffect(() => {
    if (deepLinkAppliedRef.current) return;
    const neId = (searchParams.get("ne_id") || "").trim();
    if (!neId) return;
    deepLinkAppliedRef.current = true;
    const source = searchParams.get("source") === "ume" ? "ume" : "managed";
    const ifname = (searchParams.get("ifname") || "").trim();
    setDeepLinkNeId(neId);
    setDeepLinkSource(source);
    setView("wizard");
    setStep(2);
    setNeKeyword(neId);
    setNePage(1);
    setDeepLinkHint(
      t("portTraffic.deepLinkHint")
        .replace("{{ne}}", neId)
        .replace(
          "{{ifname}}",
          ifname ? t("portTraffic.deepLinkIfname").replace("{{ifname}}", ifname) : "",
        ),
    );
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams, t]);

  useEffect(() => {
    if (!deepLinkNeId || view !== "wizard" || step !== 2) return;
    const items = nesQuery.data?.items || [];
    const hit = items.find((row) => {
      const src = row.source === "ume" ? "ume" : "managed";
      if (deepLinkSource && src !== deepLinkSource) return false;
      return row.id === deepLinkNeId;
    });
    if (!hit) return;
    const k = neKey(hit.source, hit.id);
    setSelectedNes((prev) => (prev[k] ? prev : { ...prev, [k]: hit }));
    setDeepLinkNeId("");
  }, [deepLinkNeId, deepLinkSource, nesQuery.data, view, step]);

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficTasksAll });
    void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficDashboard });
  };

  const createMut = useMutation({
    mutationFn: createPortTrafficTask,
    onSuccess: () => {
      showOk(t("portTraffic.created"));
      invalidateAll();
      resetWizard();
      setView("list");
    },
    onError: (e: Error) => showError(e.message || t("portTraffic.createFailed")),
  });

  const startMut = useMutation({
    mutationFn: startPortTrafficTask,
    onSuccess: () => {
      showOk(t("portTraffic.started"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });
  const pauseMut = useMutation({
    mutationFn: pausePortTrafficTask,
    onSuccess: () => {
      showOk(t("portTraffic.paused"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });
  const stopMut = useMutation({
    mutationFn: stopPortTrafficTask,
    onSuccess: () => {
      showOk(t("portTraffic.stopped"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });
  const collectMut = useMutation({
    mutationFn: collectPortTrafficNow,
    onSuccess: (res) => {
      showOk(res.started ? t("portTraffic.collectStarted") : t("portTraffic.collectBusy"));
      invalidateAll();
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficTargets(wallTaskId) });
      void queryClient.invalidateQueries({ queryKey: ["portTrafficCompare"] });
    },
    onError: (e: Error) => showError(e.message),
  });
  const deleteMut = useMutation({
    mutationFn: deletePortTrafficTask,
    onSuccess: () => {
      showOk(t("portTraffic.deleted"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });

  const resetWizard = () => {
    setStep(1);
    setTitle("");
    setIntervalSec(60);
    setRetentionDays(7);
    setConcurrency(5);
    setStartNow(true);
    setSelectedNes({});
    setPortsByNe({});
    setPickedPorts({});
    setNeKeyword("");
    setNePage(1);
    setDeepLinkHint("");
    setDeepLinkSource("");
    setDeepLinkNeId("");
  };

  const openWizard = () => {
    resetWizard();
    setView("wizard");
  };

  const openWall = (taskId: string) => {
    setWallTaskId(taskId);
    setWallTargetId("");
    setMapBaselineTaskId("");
    setMapBaselineTargetId("");
    setBaseline("off");
    setView("wall");
  };

  const toggleNe = (row: CliTargetItem) => {
    const k = neKey(row.source, row.id);
    setSelectedNes((prev) => {
      const next = { ...prev };
      if (next[k]) delete next[k];
      else next[k] = row;
      return next;
    });
  };

  const discoverNe = async (row: CliTargetItem) => {
    const k = neKey(row.source, row.id);
    setPortsByNe((prev) => ({ ...prev, [k]: { loading: true, ports: prev[k]?.ports || [] } }));
    try {
      const source = row.source === "ume" ? "ume" : "managed";
      const res = await discoverPortTrafficPorts({ source, id: row.id });
      setPortsByNe((prev) => ({
        ...prev,
        [k]: {
          ports: res.ports,
          meta: { ne_name: res.ne_name || row.name, ne_ip: res.ne_ip || row.ip_address, vendor: res.vendor || row.vendor || "" },
        },
      }));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "discover_failed";
      setPortsByNe((prev) => ({ ...prev, [k]: { ports: [], error: msg } }));
      showError(msg);
    }
  };

  const togglePort = (ne: CliTargetItem, port: PortTrafficDiscoverPort, meta?: { ne_name: string; ne_ip: string; vendor: string }) => {
    const source = (ne.source === "ume" ? "ume" : "managed") as "managed" | "ume";
    const k = portKey(source, ne.id, port.ifname);
    setPickedPorts((prev) => {
      const next = { ...prev };
      if (next[k]) {
        delete next[k];
        return next;
      }
      next[k] = {
        key: k,
        source,
        target_id: ne.id,
        ne_name: meta?.ne_name || ne.name,
        ne_ip: meta?.ne_ip || ne.ip_address,
        vendor: meta?.vendor || ne.vendor || "",
        ifname: port.ifname,
        if_description: port.description,
        bw_bps: port.bw_bps,
      };
      return next;
    });
  };

  const selectedNeList = useMemo(() => Object.values(selectedNes), [selectedNes]);
  const pickedList = useMemo(() => Object.values(pickedPorts), [pickedPorts]);
  const wallTaskOptions = wallTasksQuery.data?.items || tasksQuery.data?.items || [];
  const wallTargets = useMemo(
    () => (wallTargetsQuery.data?.items || []).filter((x) => x.status === "active"),
    [wallTargetsQuery.data?.items],
  );
  const selectedWallTarget = wallTargets.find((x) => x.id === wallTargetId) || null;
  const mapBaselineOptions = useMemo(
    () =>
      (mapBaselineTargetsQuery.data?.items || []).filter(
        (x) => x.status === "active" && x.id !== wallTargetId,
      ),
    [mapBaselineTargetsQuery.data?.items, wallTargetId],
  );

  useEffect(() => {
    if (view !== "wall" || wallTargetId || !wallTargets.length) return;
    setWallTargetId(wallTargets[0].id);
  }, [view, wallTargetId, wallTargets]);

  useEffect(() => {
    if (!mapBaselineTargetId) return;
    if (mapBaselineTargetId === wallTargetId) {
      setMapBaselineTargetId("");
      return;
    }
    if (
      mapBaselineTaskId &&
      mapBaselineTargetsQuery.isFetched &&
      !mapBaselineOptions.some((x) => x.id === mapBaselineTargetId)
    ) {
      setMapBaselineTargetId("");
    }
  }, [
    mapBaselineTargetId,
    wallTargetId,
    mapBaselineTaskId,
    mapBaselineOptions,
    mapBaselineTargetsQuery.isFetched,
  ]);

  const submitWizard = () => {
    if (!title.trim()) {
      showError(t("portTraffic.titleRequired"));
      return;
    }
    if (!pickedList.length) {
      showError(t("portTraffic.portsRequired"));
      return;
    }
    createMut.mutate({
      title: title.trim(),
      interval_sec: intervalSec,
      retention_days: retentionDays,
      concurrency,
      start_now: startNow,
      targets: pickedList.map(({ key: _k, ...rest }) => rest),
    });
  };

  const dash = dashQuery.data;
  const tasks = tasksQuery.data?.items || [];
  const taskPages = pageCount(tasksQuery.data?.total || 0, 10);

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("portTraffic.title")}</h2>
        <div className="btn-row">
          {view !== "list" ? (
            <button type="button" onClick={() => setView("list")}>
              {t("portTraffic.backList")}
            </button>
          ) : (
            <button type="button" className="btn-primary" onClick={openWizard}>
              {t("portTraffic.create")}
            </button>
          )}
        </div>
      </div>

      {view === "list" ? (
        <>
          <div className="stat-grid" style={{ marginBottom: 16 }}>
            <div className="stat-card">
              <div className="muted">{t("portTraffic.kpi.tasks")}</div>
              <div>{dash?.task_count ?? "—"}</div>
            </div>
            <div className="stat-card">
              <div className="muted">{t("portTraffic.kpi.running")}</div>
              <div>{dash?.running_task_count ?? "—"}</div>
            </div>
            <div className="stat-card">
              <div className="muted">{t("portTraffic.kpi.ports")}</div>
              <div>{dash?.active_target_count ?? "—"}</div>
            </div>
            <div className="stat-card">
              <div className="muted">{t("portTraffic.kpi.samples24h")}</div>
              <div>{dash?.sample_count_24h ?? "—"}</div>
            </div>
          </div>

          <table className="data-table">
            <thead>
              <tr>
                <th>{t("portTraffic.col.title")}</th>
                <th>{t("portTraffic.col.status")}</th>
                <th>{t("portTraffic.col.ports")}</th>
                <th>{t("portTraffic.col.interval")}</th>
                <th>{t("portTraffic.col.last")}</th>
                <th>{t("portTraffic.col.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {!tasks.length ? (
                <tr>
                  <td colSpan={6} className="muted">
                    {t("portTraffic.empty")}
                  </td>
                </tr>
              ) : (
                tasks.map((row) => (
                  <tr key={row.id}>
                    <td>{row.title}</td>
                    <td>
                      {row.status}
                      {row.collect_running ? " · collecting" : ""}
                    </td>
                    <td>
                      {row.active_target_count}/{row.target_count}
                    </td>
                    <td>{row.interval_sec}s</td>
                    <td>{formatSystemTime(row.last_collect_ended_at) || "—"}</td>
                    <td>
                      <div className="btn-row">
                        <button type="button" onClick={() => openWall(row.id)}>
                          {t("portTraffic.wall")}
                        </button>
                        {row.status !== "running" ? (
                          <button type="button" disabled={startMut.isPending} onClick={() => startMut.mutate(row.id)}>
                            {t("portTraffic.start")}
                          </button>
                        ) : (
                          <button type="button" disabled={pauseMut.isPending} onClick={() => pauseMut.mutate(row.id)}>
                            {t("portTraffic.pause")}
                          </button>
                        )}
                        {row.status !== "stopped" ? (
                          <button type="button" disabled={stopMut.isPending} onClick={() => stopMut.mutate(row.id)}>
                            {t("portTraffic.stop")}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          disabled={deleteMut.isPending}
                          onClick={() => {
                            if (window.confirm(t("portTraffic.confirmDelete"))) deleteMut.mutate(row.id);
                          }}
                        >
                          {t("portTraffic.delete")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          <div className="pager">
            <button type="button" className="pager__btn" disabled={taskPage <= 1} onClick={() => setTaskPage((p) => p - 1)}>
              ‹
            </button>
            <span className="muted">
              {taskPage}/{Math.max(1, taskPages)}
            </span>
            <button
              type="button"
              className="pager__btn"
              disabled={taskPage >= taskPages}
              onClick={() => setTaskPage((p) => p + 1)}
            >
              ›
            </button>
          </div>
        </>
      ) : null}

      {view === "wizard" ? (
        <div className="pt-wizard">
          <div className="pt-wizard__steps">
            {[1, 2, 3, 4].map((n) => (
              <button
                key={n}
                type="button"
                className={`pt-wizard__step${step === n ? " is-active" : ""}`}
                onClick={() => setStep(n)}
              >
                {n}. {t(`portTraffic.step${n}`)}
              </button>
            ))}
          </div>

          {step === 1 ? (
            <div className="config-sync-policy-row">
              <label className="config-sync-policy-field">
                {t("portTraffic.fieldTitle")}
                <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("portTraffic.titlePh")} />
              </label>
              <label className="config-sync-policy-field">
                {t("portTraffic.interval")}
                <input
                  type="number"
                  min={15}
                  max={3600}
                  value={intervalSec}
                  onChange={(e) => setIntervalSec(Number(e.target.value) || 60)}
                />
              </label>
              <label className="config-sync-policy-field">
                {t("portTraffic.retention")}
                <input
                  type="number"
                  min={1}
                  max={90}
                  value={retentionDays}
                  onChange={(e) => setRetentionDays(Number(e.target.value) || 7)}
                />
                <span className="muted" style={{ fontSize: 12 }}>
                  {t("portTraffic.retentionHintWizard")}
                </span>
              </label>
              <label className="config-sync-policy-field">
                {t("portTraffic.concurrency")}
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={concurrency}
                  onChange={(e) => setConcurrency(Number(e.target.value) || 5)}
                />
              </label>
              <label className="config-sync-policy-check">
                <input type="checkbox" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} />
                {t("portTraffic.startNow")}
              </label>
            </div>
          ) : null}

          {step === 2 ? (
            <>
              {deepLinkHint ? <p className="panel__hint panel__hint--live">{deepLinkHint}</p> : null}
              <div className="filter-inline">
                <input
                  value={neKeyword}
                  onChange={(e) => {
                    setNeKeyword(e.target.value);
                    setNePage(1);
                  }}
                  placeholder={t("portTraffic.neKeywordPh")}
                />
                <span className="muted">{t("portTraffic.selectedNe", { count: selectedNeList.length })}</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th />
                    <th>{t("portTraffic.col.source")}</th>
                    <th>{t("portTraffic.col.name")}</th>
                    <th>IP</th>
                    <th>{t("portTraffic.col.vendor")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(nesQuery.data?.items || []).map((row) => {
                    const k = neKey(row.source, row.id);
                    return (
                      <tr key={k}>
                        <td>
                          <input type="checkbox" checked={Boolean(selectedNes[k])} onChange={() => toggleNe(row)} />
                        </td>
                        <td>{row.source}</td>
                        <td>{row.name}</td>
                        <td>{row.ip_address}</td>
                        <td>{row.vendor || "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          ) : null}

          {step === 3 ? (
            <div className="pt-wizard__ports">
              {!selectedNeList.length ? <p>{t("portTraffic.pickNeFirst")}</p> : null}
              {selectedNeList.map((ne) => {
                const k = neKey(ne.source, ne.id);
                const bucket = portsByNe[k];
                return (
                  <div key={k} className="pt-wizard__ne-block">
                    <div className="btn-row" style={{ marginBottom: 8 }}>
                      <strong>
                        {ne.name} ({ne.ip_address})
                      </strong>
                      <button type="button" disabled={bucket?.loading} onClick={() => void discoverNe(ne)}>
                        {bucket?.loading ? "…" : t("portTraffic.discover")}
                      </button>
                      {bucket?.error ? <span className="muted">{bucket.error}</span> : null}
                    </div>
                    {bucket?.ports?.length ? (
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th />
                            <th>Interface</th>
                            <th>BW</th>
                            <th>Admin/Phy/Prot</th>
                            <th>Description</th>
                          </tr>
                        </thead>
                        <tbody>
                          {bucket.ports.map((p) => {
                            const pk = portKey(ne.source === "ume" ? "ume" : "managed", ne.id, p.ifname);
                            return (
                              <tr key={pk}>
                                <td>
                                  <input
                                    type="checkbox"
                                    checked={Boolean(pickedPorts[pk])}
                                    onChange={() => togglePort(ne, p, bucket.meta)}
                                  />
                                </td>
                                <td>{p.ifname}</td>
                                <td>{p.bw_raw || formatBw(p.bw_bps)}</td>
                                <td>
                                  {p.admin}/{p.phy}/{p.prot}
                                </td>
                                <td>{p.description || "—"}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    ) : null}
                  </div>
                );
              })}
              <p>{t("portTraffic.selectedPorts", { count: String(pickedList.length) })}</p>
            </div>
          ) : null}

          {step === 4 ? (
            <div className="pt-wizard__confirm">
              <p>
                <strong>{title || "—"}</strong> · {intervalSec}s · {retentionDays}d · ×{concurrency}
              </p>
              <p>{t("portTraffic.selectedPorts", { count: String(pickedList.length) })}</p>
              <ul className="pt-wizard__confirm-list">
                {pickedList.slice(0, 40).map((p) => (
                  <li key={p.key}>
                    {p.ne_name} / {p.ifname} ({formatBw(p.bw_bps || 0)})
                  </li>
                ))}
                {pickedList.length > 40 ? <li>… +{pickedList.length - 40}</li> : null}
              </ul>
              <button type="button" className="btn-primary" disabled={createMut.isPending} onClick={submitWizard}>
                {t("portTraffic.confirmCreate")}
              </button>
            </div>
          ) : null}

          <div className="btn-row" style={{ marginTop: 12 }}>
            <button type="button" disabled={step <= 1} onClick={() => setStep((s) => s - 1)}>
              {t("portTraffic.prev")}
            </button>
            <button type="button" disabled={step >= 4} onClick={() => setStep((s) => s + 1)}>
              {t("portTraffic.next")}
            </button>
          </div>
        </div>
      ) : null}

      {view === "wall" ? (
        <div className="pt-wall-page">
          <div className="filter-inline pt-wall-page__filters">
            <label>
              {t("portTraffic.wallTask")}
              <select
                value={wallTaskId}
                onChange={(e) => {
                  setWallTaskId(e.target.value);
                  setWallTargetId("");
                }}
              >
                {wallTaskOptions.map((task) => (
                  <option key={task.id} value={task.id}>
                    {task.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("portTraffic.wallPort")}
              <select
                value={wallTargetId}
                onChange={(e) => setWallTargetId(e.target.value)}
              >
                <option value="">{t("portTraffic.pickPort")}</option>
                {wallTargets.map((tgt) => (
                  <option key={tgt.id} value={tgt.id}>
                    {tgt.ne_name || tgt.ne_ip || "—"} / {tgt.ifname}
                    {tgt.ne_ip ? ` (${tgt.ne_ip})` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("portTraffic.range")}
              <select value={rangeHours} onChange={(e) => setRangeHours(Number(e.target.value))}>
                <option value={1}>1h</option>
                <option value={6}>6h</option>
                <option value={24}>24h</option>
              </select>
            </label>
            <label>
              {t("portTraffic.compare")}
              <select
                value={baseline}
                onChange={(e) => setBaseline(e.target.value as BaselineMode)}
              >
                <option value="off">{t("portTraffic.compareOff")}</option>
                <option value="day">{t("portTraffic.compareDay")}</option>
                <option value="week">{t("portTraffic.compareWeek")}</option>
                <option value="shift">{t("portTraffic.compareShift")}</option>
                <option value="custom">{t("portTraffic.compareCustom")}</option>
              </select>
            </label>
            {baseline === "custom" ? (
              <label>
                {t("portTraffic.offsetHours")}
                <input
                  type="number"
                  min={1}
                  max={24 * 90}
                  value={customOffsetHours}
                  onChange={(e) => setCustomOffsetHours(Number(e.target.value) || 24)}
                  style={{ width: 88 }}
                />
              </label>
            ) : null}
            <label>
              {t("portTraffic.mapBaselineTask")}
              <select
                value={mapBaselineTaskId}
                onChange={(e) => {
                  setMapBaselineTaskId(e.target.value);
                  setMapBaselineTargetId("");
                }}
                disabled={!wallTargetId}
              >
                <option value="">{t("portTraffic.mapBaselineNone")}</option>
                {wallTaskOptions.map((task) => (
                  <option key={task.id} value={task.id}>
                    {task.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("portTraffic.mapBaselinePort")}
              <select
                value={mapBaselineTargetId}
                onChange={(e) => setMapBaselineTargetId(e.target.value)}
                disabled={!mapBaselineTaskId}
              >
                <option value="">{t("portTraffic.pickPort")}</option>
                {mapBaselineOptions.map((tgt) => (
                  <option key={tgt.id} value={tgt.id}>
                    {tgt.ne_name || tgt.ne_ip || "—"} / {tgt.ifname}
                    {tgt.ne_ip ? ` (${tgt.ne_ip})` : ""}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={!wallTaskId || collectMut.isPending}
              onClick={() => collectMut.mutate(wallTaskId)}
            >
              {t("portTraffic.collectNow")}
            </button>
          </div>
          {baseline === "week" ? (
            <p className="muted" style={{ marginBottom: 8 }}>
              {t("portTraffic.retentionHint")}
            </p>
          ) : null}
          {selectedWallTarget?.last_error ? (
            <p className="muted" style={{ marginBottom: 8 }}>
              {t("portTraffic.targetError")}: {selectedWallTarget.last_error}
            </p>
          ) : null}
          <PortTrafficWall
            target={selectedWallTarget}
            baselineTarget={
              compareQuery.data?.meta?.baseline_target ||
              (baseline !== "off" && wallBaselinePoints.length ? selectedWallTarget : null)
            }
            points={wallPoints}
            baselinePoints={wallBaselinePoints}
            rangeLabel={`${rangeHours}h${
              baseline === "off"
                ? ""
                : baseline === "day"
                  ? ` · ${t("portTraffic.compareDay")}`
                  : baseline === "week"
                    ? ` · ${t("portTraffic.compareWeek")}`
                    : baseline === "shift"
                      ? ` · ${t("portTraffic.compareShift")}`
                      : ` · ${t("portTraffic.compareCustom")}`
            }${mapBaselineTargetId ? ` · ${t("portTraffic.mapBaselinePort")}` : ""}`}
            loading={compareQuery.isLoading || compareQuery.isFetching}
            hint={
              !wallTargetId
                ? t("portTraffic.pickPort")
                : selectedWallTarget?.last_error
                  ? t("portTraffic.waitAfterError")
                  : t("portTraffic.waitSamples")
            }
          />
        </div>
      ) : null}
    </section>
  );
}
