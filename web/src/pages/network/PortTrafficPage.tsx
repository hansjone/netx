import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  createPortTrafficDevice,
  deletePortTrafficDevice,
  discoverPortTrafficPorts,
  fetchCliTargets,
  fetchPortTrafficDashboard,
  fetchPortTrafficDevices,
  fetchPortTrafficEvents,
  fetchPortTrafficTargets,
  pausePortTrafficDevice,
  putPortTrafficInterfaces,
  rebindPortTrafficDevice,
  startPortTrafficDevice,
  stopPortTrafficDevice,
  updatePortTrafficDevice,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import type {
  CliTargetItem,
  PortTrafficDevice,
  PortTrafficDiscoverPort,
  PortTrafficIfaceIn,
} from "../../types";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";

const POLL_MS = 5000;
const TARGET_PAGE_SIZE = 20;

type ViewMode = "list" | "wizard" | "edit";

function statusTone(status: string): "running" | "paused" | "stopped" | "other" {
  if (status === "running") return "running";
  if (status === "paused") return "paused";
  if (status === "stopped") return "stopped";
  return "other";
}

function formatPortTrafficLogMessage(
  raw: string,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  const s = String(raw || "").trim();
  if (!s) return "";
  if (s.includes("managed_ne_not_found") || s.includes("ume_ne_not_found")) {
    return t("portTraffic.err.managedNeNotFound");
  }
  const m = s.match(/^(\d+)_target_errors$/);
  if (m) return t("portTraffic.err.targetErrors", { count: m[1] });
  if (s === "unsupported_vendor") return t("portTraffic.err.unsupportedVendor");
  if (s === "invalid_source") return t("portTraffic.err.invalidSource");
  return s;
}

function needsNeRebind(row: PortTrafficDevice): boolean {
  const err = String(row.last_error || "");
  if (err.includes("managed_ne_not_found") || err.includes("ume_ne_not_found")) return true;
  // Older rounds only stored "N_target_errors"; still offer rebind when IP is known.
  if (/^\d+_target_errors$/.test(err.trim()) && String(row.ne_ip || "").trim()) return true;
  return false;
}

function formatBw(bps: number) {
  if (!bps) return "—";
  if (bps >= 1e9) return `${bps / 1e9}G`;
  if (bps >= 1e6) return `${bps / 1e6}M`;
  return `${bps}`;
}

function deviceLabel(d: PortTrafficDevice) {
  return d.ne_name || d.ne_ip || d.ne_id || "—";
}

export function PortTrafficPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkAppliedRef = useRef(false);

  const [view, setView] = useState<ViewMode>("list");
  const [listPage, setListPage] = useState(1);

  // Wizard / edit
  const [editDeviceId, setEditDeviceId] = useState("");
  const [editDeviceSnap, setEditDeviceSnap] = useState<PortTrafficDevice | null>(null);
  const [intervalSec, setIntervalSec] = useState(60);
  const [retentionDays, setRetentionDays] = useState(7);
  const [concurrency, setConcurrency] = useState(1);
  const [note, setNote] = useState("");
  const [startNow, setStartNow] = useState(true);
  const [neKeyword, setNeKeyword] = useState("");
  const [nePage, setNePage] = useState(1);
  const [selectedNe, setSelectedNe] = useState<CliTargetItem | null>(null);
  const [ports, setPorts] = useState<PortTrafficDiscoverPort[]>([]);
  const [portsLoading, setPortsLoading] = useState(false);
  const [portsError, setPortsError] = useState("");
  const [pickedIfnames, setPickedIfnames] = useState<Record<string, PortTrafficIfaceIn>>({});
  const [deepLinkHint, setDeepLinkHint] = useState("");
  const [deepLinkSource, setDeepLinkSource] = useState<"managed" | "ume" | "">("");
  const [deepLinkNeId, setDeepLinkNeId] = useState("");
  const [deepLinkIfname, setDeepLinkIfname] = useState("");
  const [wizardStep, setWizardStep] = useState<1 | 2 | 3>(1);
  const [logDeviceId, setLogDeviceId] = useState("");
  const [logDeviceLabel, setLogDeviceLabel] = useState("");
  const [rebindDevice, setRebindDevice] = useState<PortTrafficDevice | null>(null);
  const [rebindKeyword, setRebindKeyword] = useState("");
  const [rebindPage, setRebindPage] = useState(1);
  const [rebindSelectedNe, setRebindSelectedNe] = useState<CliTargetItem | null>(null);

  const dashQuery = useQuery({
    queryKey: queryKeys.portTrafficDashboard,
    queryFn: fetchPortTrafficDashboard,
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const devicesQuery = useQuery({
    queryKey: queryKeys.portTrafficDevices(listPage),
    queryFn: () => fetchPortTrafficDevices({ page: listPage, pageSize: 10 }),
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const nesQuery = useQuery({
    queryKey: queryKeys.cliTargets(neKeyword, nePage, TARGET_PAGE_SIZE),
    queryFn: () =>
      fetchCliTargets({ source: "all", keyword: neKeyword, page: nePage, pageSize: TARGET_PAGE_SIZE }),
    enabled: view === "wizard" && wizardStep === 1,
    staleTime: 5000,
  });

  const rebindSource = rebindDevice?.source === "ume" ? "ume" : "managed";
  const rebindNesQuery = useQuery({
    queryKey: ["cliTargets", "rebind", rebindSource, rebindKeyword, rebindPage, TARGET_PAGE_SIZE],
    queryFn: () =>
      fetchCliTargets({
        source: rebindSource,
        keyword: rebindKeyword,
        page: rebindPage,
        pageSize: TARGET_PAGE_SIZE,
      }),
    enabled: Boolean(rebindDevice),
    staleTime: 5000,
  });

  const editTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(editDeviceId),
    queryFn: () => fetchPortTrafficTargets(editDeviceId),
    enabled: view === "edit" && Boolean(editDeviceId),
    staleTime: 2000,
  });

  const logQuery = useQuery({
    queryKey: queryKeys.portTrafficEvents(logDeviceId),
    queryFn: () => fetchPortTrafficEvents(logDeviceId, 200),
    enabled: Boolean(logDeviceId),
    staleTime: 2000,
    refetchInterval: logDeviceId ? POLL_MS : false,
  });

  useEffect(() => {
    if (deepLinkAppliedRef.current) return;
    const neId = (searchParams.get("ne_id") || "").trim();
    if (!neId) return;
    deepLinkAppliedRef.current = true;
    const source = searchParams.get("source") === "ume" ? "ume" : "managed";
    const ifname = (searchParams.get("ifname") || "").trim();
    setDeepLinkNeId(neId);
    setDeepLinkSource(source);
    setDeepLinkIfname(ifname);
    setView("wizard");
    setWizardStep(1);
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
    if (!deepLinkNeId || view !== "wizard" || wizardStep !== 1) return;
    const items = nesQuery.data?.items || [];
    const hit = items.find((row) => {
      const src = row.source === "ume" ? "ume" : "managed";
      if (deepLinkSource && src !== deepLinkSource) return false;
      return row.id === deepLinkNeId;
    });
    if (!hit) return;
    setSelectedNe(hit);
    setDeepLinkNeId("");
  }, [deepLinkNeId, deepLinkSource, nesQuery.data, view, wizardStep]);

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficDevicesAll });
    void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficDashboard });
  };

  const createMut = useMutation({
    mutationFn: createPortTrafficDevice,
    onSuccess: () => {
      showOk(t("portTraffic.created"));
      invalidateAll();
      resetWizard();
      setView("list");
    },
    onError: (e: Error) => showError(e.message || t("portTraffic.createFailed")),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof updatePortTrafficDevice>[1] }) =>
      updatePortTrafficDevice(id, body),
    onSuccess: () => {
      showOk(t("portTraffic.updated"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });

  const putIfacesMut = useMutation({
    mutationFn: ({ id, ifaces }: { id: string; ifaces: PortTrafficIfaceIn[] }) =>
      putPortTrafficInterfaces(id, ifaces),
    onSuccess: () => {
      showOk(t("portTraffic.interfacesSaved"));
      invalidateAll();
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficTargets(editDeviceId) });
    },
    onError: (e: Error) => showError(e.message),
  });

  const startMut = useMutation({
    mutationFn: startPortTrafficDevice,
    onSuccess: () => {
      showOk(t("portTraffic.started"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });
  const pauseMut = useMutation({
    mutationFn: pausePortTrafficDevice,
    onSuccess: () => {
      showOk(t("portTraffic.paused"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });
  const stopMut = useMutation({
    mutationFn: stopPortTrafficDevice,
    onSuccess: () => {
      showOk(t("portTraffic.stopped"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });
  const deleteMut = useMutation({
    mutationFn: deletePortTrafficDevice,
    onSuccess: () => {
      showOk(t("portTraffic.deleted"));
      invalidateAll();
    },
    onError: (e: Error) => showError(e.message),
  });

  const rebindMut = useMutation({
    mutationFn: ({ id, neId }: { id: string; neId: string }) =>
      rebindPortTrafficDevice(id, { ne_id: neId }),
    onSuccess: () => {
      showOk(t("portTraffic.rebindOk"));
      setRebindDevice(null);
      setRebindSelectedNe(null);
      setRebindKeyword("");
      setRebindPage(1);
      invalidateAll();
    },
    onError: (e: Error) => {
      const msg = String(e.message || "");
      if (msg.includes("managed_ne_not_found") || msg.includes("ume_ne_not_found")) {
        showError(t("portTraffic.err.rebindNeMissing"));
      } else if (msg.includes("device_already_monitored")) {
        showError(t("portTraffic.err.alreadyMonitored"));
      } else if (msg.includes("ne_id_required")) {
        showError(t("portTraffic.err.rebindPickFirst"));
      } else {
        showError(msg || t("portTraffic.rebindFailed"));
      }
    },
  });

  const openRebind = (row: PortTrafficDevice) => {
    setRebindDevice(row);
    setRebindSelectedNe(null);
    setRebindKeyword(row.ne_ip || row.ne_name || "");
    setRebindPage(1);
  };

  const closeRebind = () => {
    if (rebindMut.isPending) return;
    setRebindDevice(null);
    setRebindSelectedNe(null);
    setRebindKeyword("");
    setRebindPage(1);
  };

  const confirmRebind = () => {
    if (!rebindDevice || !rebindSelectedNe) {
      showError(t("portTraffic.err.rebindPickFirst"));
      return;
    }
    const src = rebindSelectedNe.source === "ume" ? "ume" : "managed";
    if (src !== rebindSource) {
      showError(t("portTraffic.err.rebindSourceMismatch"));
      return;
    }
    const label = `${rebindSelectedNe.name} (${rebindSelectedNe.ip_address})`;
    if (!window.confirm(t("portTraffic.rebindConfirm", { ne: label }))) return;
    rebindMut.mutate({ id: rebindDevice.id, neId: rebindSelectedNe.id });
  };

  const resetWizard = () => {
    setWizardStep(1);
    setIntervalSec(60);
    setRetentionDays(7);
    setConcurrency(1);
    setNote("");
    setStartNow(true);
    setSelectedNe(null);
    setPorts([]);
    setPortsError("");
    setPickedIfnames({});
    setNeKeyword("");
    setNePage(1);
    setDeepLinkHint("");
    setDeepLinkSource("");
    setDeepLinkNeId("");
    setDeepLinkIfname("");
    setEditDeviceId("");
    setEditDeviceSnap(null);
  };

  const openWizard = () => {
    resetWizard();
    setView("wizard");
  };

  const openWallList = () => {
    navigate("/network/tasks/port-traffic/wall");
  };

  const openEdit = (row: PortTrafficDevice) => {
    setEditDeviceId(row.id);
    setEditDeviceSnap(row);
    setIntervalSec(row.interval_sec);
    setRetentionDays(row.retention_days);
    setConcurrency(row.concurrency || 1);
    setNote(row.note || "");
    setPickedIfnames({});
    setPorts([]);
    setPortsError("");
    setView("edit");
  };

  useEffect(() => {
    if (view !== "edit" || !editTargetsQuery.data) return;
    const next: Record<string, PortTrafficIfaceIn> = {};
    for (const tgt of editTargetsQuery.data.items) {
      if (tgt.status !== "active") continue;
      next[tgt.ifname] = {
        ifname: tgt.ifname,
        if_description: tgt.if_description,
        bw_bps: tgt.bw_bps,
      };
    }
    setPickedIfnames(next);
  }, [view, editTargetsQuery.data]);

  const discoverSelected = async () => {
    const source =
      view === "edit"
        ? editDeviceSnap?.source === "ume"
          ? "ume"
          : "managed"
        : selectedNe?.source === "ume"
          ? "ume"
          : "managed";
    const id = view === "edit" ? editDeviceSnap?.ne_id || "" : selectedNe?.id || "";
    if (!id) return;
    setPortsLoading(true);
    setPortsError("");
    try {
      const res = await discoverPortTrafficPorts({ source, id });
      setPorts(res.ports);
      if (deepLinkIfname) {
        const hit = res.ports.find((p) => p.ifname === deepLinkIfname);
        if (hit) {
          setPickedIfnames((prev) => ({
            ...prev,
            [hit.ifname]: {
              ifname: hit.ifname,
              if_description: hit.description,
              bw_bps: hit.bw_bps,
            },
          }));
        }
        setDeepLinkIfname("");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "discover_failed";
      setPorts([]);
      setPortsError(msg);
      showError(msg);
    } finally {
      setPortsLoading(false);
    }
  };

  const toggleIfname = (port: PortTrafficDiscoverPort) => {
    setPickedIfnames((prev) => {
      const next = { ...prev };
      if (next[port.ifname]) {
        delete next[port.ifname];
        return next;
      }
      next[port.ifname] = {
        ifname: port.ifname,
        if_description: port.description,
        bw_bps: port.bw_bps,
      };
      return next;
    });
  };

  const pickedList = useMemo(() => Object.values(pickedIfnames), [pickedIfnames]);

  const submitWizard = () => {
    if (!selectedNe) {
      showError(t("portTraffic.pickNeFirst"));
      return;
    }
    if (!pickedList.length) {
      showError(t("portTraffic.portsRequired"));
      return;
    }
    const source = selectedNe.source === "ume" ? "ume" : "managed";
    createMut.mutate({
      source,
      ne_id: selectedNe.id,
      ne_name: selectedNe.name,
      ne_ip: selectedNe.ip_address,
      vendor: selectedNe.vendor || "",
      note: note.trim(),
      interval_sec: intervalSec,
      retention_days: retentionDays,
      concurrency,
      start_now: startNow,
      interfaces: pickedList,
    });
  };

  const saveEdit = async () => {
    if (!editDeviceId) return;
    try {
      await updateMut.mutateAsync({
        id: editDeviceId,
        body: {
          note: note.trim(),
          interval_sec: intervalSec,
          retention_days: retentionDays,
          concurrency,
        },
      });
      await putIfacesMut.mutateAsync({ id: editDeviceId, ifaces: pickedList });
      setView("list");
    } catch {
      /* toast already shown */
    }
  };

  const dash = dashQuery.data;
  const devices = devicesQuery.data?.items || [];
  const pages = pageCount(devicesQuery.data?.total || 0, 10);
  const editDevice = editDeviceSnap;

  const statusText = (status: string) => {
    const tone = statusTone(status);
    if (tone === "running") return t("portTraffic.statusRunning");
    if (tone === "paused") return t("portTraffic.statusPaused");
    if (tone === "stopped") return t("portTraffic.statusStopped");
    return status;
  };

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("portTraffic.title")}</h2>
        <div className="btn-row">
          {view !== "list" ? (
            <button
              type="button"
              onClick={() => {
                resetWizard();
                setView("list");
              }}
            >
              {t("portTraffic.backList")}
            </button>
          ) : (
            <>
              <button type="button" onClick={() => navigate("/network/tasks/port-traffic/wall")}>
                {t("portTraffic.wall")}
              </button>
              <button type="button" className="btn-primary" onClick={openWizard}>
                {t("portTraffic.create")}
              </button>
            </>
          )}
        </div>
      </div>

      {view === "list" ? (
        <div className="pt-list">
          <div className="pt-list-kpis">
            <div className="pt-list-kpi">
              <div className="pt-list-kpi__label">{t("portTraffic.kpi.devices")}</div>
              <div className="pt-list-kpi__value">
                {dash?.device_count ?? dash?.task_count ?? "—"}
              </div>
            </div>
            <div className="pt-list-kpi pt-list-kpi--live">
              <div className="pt-list-kpi__label">{t("portTraffic.kpi.running")}</div>
              <div className="pt-list-kpi__value">
                {dash?.running_device_count ?? dash?.running_task_count ?? "—"}
              </div>
            </div>
            <div className="pt-list-kpi">
              <div className="pt-list-kpi__label">{t("portTraffic.kpi.ports")}</div>
              <div className="pt-list-kpi__value">{dash?.active_target_count ?? "—"}</div>
            </div>
            <div className="pt-list-kpi">
              <div className="pt-list-kpi__label">{t("portTraffic.kpi.samples24h")}</div>
              <div className="pt-list-kpi__value">{dash?.sample_count_24h ?? "—"}</div>
            </div>
          </div>

          {!devices.length ? (
            <div className="pt-list-empty">
              <p>{t("portTraffic.empty")}</p>
              <button type="button" className="btn-primary" onClick={openWizard}>
                {t("portTraffic.create")}
              </button>
            </div>
          ) : (
            <>
              <div className="pt-list-table-wrap">
                <table className="data-table pt-list-table">
                  <thead>
                    <tr>
                      <th>{t("portTraffic.col.device")}</th>
                      <th>{t("portTraffic.col.status")}</th>
                      <th>{t("portTraffic.col.ports")}</th>
                      <th>{t("portTraffic.col.interval")}</th>
                      <th>{t("portTraffic.col.last")}</th>
                      <th>{t("portTraffic.col.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {devices.map((row) => {
                      const tone = statusTone(row.status);
                      return (
                        <tr key={row.id}>
                          <td>
                            <div className="pt-list-task-name">{deviceLabel(row)}</div>
                            <div className="muted" style={{ fontSize: 12 }}>
                              {row.ne_ip || row.ne_id}
                              {row.vendor ? ` · ${row.vendor}` : ""}
                              {row.note ? ` · ${row.note}` : ""}
                            </div>
                          </td>
                          <td>
                            <span className={`pt-list-status pt-list-status--${tone}`}>
                              {statusText(row.status)}
                            </span>
                            {row.collect_running ? (
                              <span className="pt-list-status pt-list-status--collect">
                                {t("portTraffic.collecting")}
                              </span>
                            ) : null}
                          </td>
                          <td className="pt-list-num">
                            {row.active_target_count}/{row.target_count}
                          </td>
                          <td className="pt-list-num">{row.interval_sec}s</td>
                          <td className="pt-list-time">
                            {formatSystemTime(row.last_collect_ended_at) || "—"}
                          </td>
                          <td>
                            <div className="pt-list-actions">
                              <button
                                type="button"
                                className="btn-primary"
                                onClick={() => openWallList()}
                              >
                                {t("portTraffic.wall")}
                              </button>
                              <button type="button" onClick={() => openEdit(row)}>
                                {t("portTraffic.edit")}
                              </button>
                              {needsNeRebind(row) ? (
                                <button
                                  type="button"
                                  title={t("portTraffic.rebindHint")}
                                  onClick={() => openRebind(row)}
                                >
                                  {t("portTraffic.rebind")}
                                </button>
                              ) : null}
                              {row.status !== "running" ? (
                                <button
                                  type="button"
                                  disabled={startMut.isPending}
                                  onClick={() => startMut.mutate(row.id)}
                                >
                                  {t("portTraffic.start")}
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  disabled={pauseMut.isPending}
                                  onClick={() => pauseMut.mutate(row.id)}
                                >
                                  {t("portTraffic.pause")}
                                </button>
                              )}
                              {row.status !== "stopped" ? (
                                <button
                                  type="button"
                                  disabled={stopMut.isPending}
                                  onClick={() => stopMut.mutate(row.id)}
                                >
                                  {t("portTraffic.stop")}
                                </button>
                              ) : null}
                              <button
                                type="button"
                                className={row.last_error ? "pt-list-log-btn--error" : undefined}
                                onClick={() => {
                                  setLogDeviceId(row.id);
                                  setLogDeviceLabel(deviceLabel(row));
                                }}
                              >
                                {t("portTraffic.log")}
                              </button>
                              <button
                                type="button"
                                className="btn--danger"
                                disabled={deleteMut.isPending}
                                onClick={() => {
                                  if (window.confirm(t("portTraffic.confirmDelete"))) {
                                    deleteMut.mutate(row.id);
                                  }
                                }}
                              >
                                {t("portTraffic.delete")}
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="pager pt-list-pager">
                <button
                  type="button"
                  className="pager__btn"
                  disabled={listPage <= 1}
                  onClick={() => setListPage((p) => p - 1)}
                >
                  ‹
                </button>
                <span className="muted">
                  {listPage}/{Math.max(1, pages)}
                </span>
                <button
                  type="button"
                  className="pager__btn"
                  disabled={listPage >= pages}
                  onClick={() => setListPage((p) => p + 1)}
                >
                  ›
                </button>
              </div>
            </>
          )}
        </div>
      ) : null}

      {view === "wizard" ? (
        <div className="pt-wizard">
          <div className="pt-wizard__steps">
            {[1, 2, 3].map((n) => (
              <button
                key={n}
                type="button"
                className={`pt-wizard__step${wizardStep === n ? " is-active" : ""}`}
                onClick={() => setWizardStep(n as 1 | 2 | 3)}
              >
                {n}. {t(`portTraffic.wizStep${n}`)}
              </button>
            ))}
          </div>

          {wizardStep === 1 ? (
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
                {selectedNe ? (
                  <span className="muted">
                    {t("portTraffic.selectedOneNe")}: {selectedNe.name} ({selectedNe.ip_address})
                  </span>
                ) : null}
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
                    const checked =
                      selectedNe?.id === row.id &&
                      (selectedNe.source === "ume" ? "ume" : "managed") ===
                        (row.source === "ume" ? "ume" : "managed");
                    return (
                      <tr key={`${row.source}:${row.id}`}>
                        <td>
                          <input
                            type="radio"
                            name="pt-ne"
                            checked={checked}
                            onChange={() => {
                              setSelectedNe(row);
                              setPorts([]);
                              setPickedIfnames({});
                            }}
                          />
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

          {wizardStep === 2 ? (
            <div className="pt-wizard__ports">
              {!selectedNe ? <p>{t("portTraffic.pickNeFirst")}</p> : null}
              {selectedNe ? (
                <>
                  <div className="btn-row" style={{ marginBottom: 8 }}>
                    <strong>
                      {selectedNe.name} ({selectedNe.ip_address})
                    </strong>
                    <button type="button" disabled={portsLoading} onClick={() => void discoverSelected()}>
                      {portsLoading ? "…" : t("portTraffic.discover")}
                    </button>
                    {portsError ? <span className="muted">{portsError}</span> : null}
                  </div>
                  {ports.length ? (
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
                        {ports.map((p) => (
                          <tr key={p.ifname}>
                            <td>
                              <input
                                type="checkbox"
                                checked={Boolean(pickedIfnames[p.ifname])}
                                onChange={() => toggleIfname(p)}
                              />
                            </td>
                            <td>{p.ifname}</td>
                            <td>{p.bw_raw || formatBw(p.bw_bps)}</td>
                            <td>
                              {p.admin}/{p.phy}/{p.prot}
                            </td>
                            <td>{p.description || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : null}
                  <p>{t("portTraffic.selectedPorts", { count: String(pickedList.length) })}</p>
                </>
              ) : null}
            </div>
          ) : null}

          {wizardStep === 3 ? (
            <div className="pt-wizard__confirm">
              <div className="config-sync-policy-row">
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
                </label>
                <label className="config-sync-policy-field">
                  {t("portTraffic.concurrency")}
                  <input
                    type="number"
                    min={1}
                    max={5}
                    value={concurrency}
                    onChange={(e) => setConcurrency(Number(e.target.value) || 1)}
                  />
                </label>
                <label className="config-sync-policy-field">
                  {t("portTraffic.note")}
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder={t("portTraffic.notePh")}
                  />
                </label>
                <label className="config-sync-policy-check">
                  <input type="checkbox" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} />
                  {t("portTraffic.startNow")}
                </label>
              </div>
              <p>
                <strong>
                  {selectedNe?.name || "—"} ({selectedNe?.ip_address || "—"})
                </strong>
              </p>
              <p>{t("portTraffic.selectedPorts", { count: String(pickedList.length) })}</p>
              <ul className="pt-wizard__confirm-list">
                {pickedList.slice(0, 40).map((p) => (
                  <li key={p.ifname}>
                    {p.ifname} ({formatBw(p.bw_bps || 0)})
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
            <button
              type="button"
              disabled={wizardStep <= 1}
              onClick={() => setWizardStep((s) => (s > 1 ? ((s - 1) as 1 | 2 | 3) : s))}
            >
              {t("portTraffic.prev")}
            </button>
            <button
              type="button"
              disabled={wizardStep >= 3}
              onClick={() => {
                if (wizardStep === 1 && !selectedNe) {
                  showError(t("portTraffic.pickNeFirst"));
                  return;
                }
                if (wizardStep === 2 && !pickedList.length) {
                  showError(t("portTraffic.portsRequired"));
                  return;
                }
                setWizardStep((s) => (s < 3 ? ((s + 1) as 1 | 2 | 3) : s));
              }}
            >
              {t("portTraffic.next")}
            </button>
          </div>
        </div>
      ) : null}

      {view === "edit" && editDevice ? (
        <div className="pt-wizard">
          <h3 style={{ marginTop: 0 }}>
            {t("portTraffic.edit")}: {deviceLabel(editDevice)}
          </h3>
          <div className="config-sync-policy-row">
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
            </label>
            <label className="config-sync-policy-field">
              {t("portTraffic.concurrency")}
              <input
                type="number"
                min={1}
                max={5}
                value={concurrency}
                onChange={(e) => setConcurrency(Number(e.target.value) || 1)}
              />
            </label>
            <label className="config-sync-policy-field">
              {t("portTraffic.note")}
              <input value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
          </div>
          <div className="btn-row" style={{ margin: "12px 0" }}>
            <button type="button" disabled={portsLoading} onClick={() => void discoverSelected()}>
              {portsLoading ? "…" : t("portTraffic.discover")}
            </button>
            <span className="muted">{t("portTraffic.selectedPorts", { count: String(pickedList.length) })}</span>
          </div>
          {portsError ? <p className="muted">{portsError}</p> : null}
          {ports.length ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th />
                  <th>Interface</th>
                  <th>BW</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {ports.map((p) => (
                  <tr key={p.ifname}>
                    <td>
                      <input
                        type="checkbox"
                        checked={Boolean(pickedIfnames[p.ifname])}
                        onChange={() => toggleIfname(p)}
                      />
                    </td>
                    <td>{p.ifname}</td>
                    <td>{p.bw_raw || formatBw(p.bw_bps)}</td>
                    <td>{p.description || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <ul className="pt-wizard__confirm-list">
              {pickedList.map((p) => (
                <li key={p.ifname}>{p.ifname}</li>
              ))}
            </ul>
          )}
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn-primary"
              disabled={updateMut.isPending || putIfacesMut.isPending}
              onClick={() => void saveEdit()}
            >
              {t("portTraffic.save")}
            </button>
          </div>
        </div>
      ) : null}

      {logDeviceId ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => {
            setLogDeviceId("");
            setLogDeviceLabel("");
          }}
        >
          <div
            className="modal modal--wide pt-log-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pt-log-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="pt-log-modal__head">
              <div>
                <h3 id="pt-log-title">{t("portTraffic.logTitle")}</h3>
                <p className="muted pt-log-modal__sub">
                  {t("portTraffic.logDevice")}: {logDeviceLabel || "—"}
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setLogDeviceId("");
                  setLogDeviceLabel("");
                }}
              >
                {t("portTraffic.logClose")}
              </button>
            </div>
            {logQuery.isLoading ? (
              <p className="muted">…</p>
            ) : !(logQuery.data?.items || []).length ? (
              <p className="muted">{t("portTraffic.logEmpty")}</p>
            ) : (
              <div className="pt-log-modal__list">
                {(logQuery.data?.items || []).map((ev) => {
                  const level = String(ev.level || "error").toLowerCase();
                  const tone =
                    level === "warn" || level === "warning"
                      ? "warn"
                      : level === "info"
                        ? "info"
                        : "error";
                  return (
                    <article key={ev.id} className={`pt-log-item pt-log-item--${tone}`}>
                      <div className="pt-log-item__meta">
                        <span className="pt-log-item__level">{level}</span>
                        <span className="pt-log-item__time">
                          {formatSystemTime(ev.created_at) || "—"}
                        </span>
                        {ev.ifname ? (
                          <span className="pt-log-item__if" title={ev.ifname}>
                            {ev.ifname}
                          </span>
                        ) : null}
                      </div>
                      <pre className="pt-log-item__msg">{formatPortTrafficLogMessage(ev.message, t)}</pre>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      ) : null}

      {rebindDevice ? (
        <div className="modal-backdrop" role="presentation" onClick={closeRebind}>
          <div
            className="modal modal--wide pt-rebind-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pt-rebind-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="pt-log-modal__head">
              <div>
                <h3 id="pt-rebind-title">{t("portTraffic.rebindTitle")}</h3>
                <p className="muted pt-log-modal__sub">
                  {t("portTraffic.rebindCurrent")}: {deviceLabel(rebindDevice)}
                  {rebindDevice.ne_ip ? ` · ${rebindDevice.ne_ip}` : ""}
                  {` · ${rebindSource}`}
                  {rebindDevice.ne_id ? ` · ID ${rebindDevice.ne_id.slice(0, 8)}…` : ""}
                </p>
                <p className="muted pt-log-modal__sub">{t("portTraffic.rebindHint")}</p>
              </div>
              <button type="button" onClick={closeRebind} disabled={rebindMut.isPending}>
                {t("portTraffic.logClose")}
              </button>
            </div>

            <div className="filter-inline" style={{ marginBottom: 10 }}>
              <input
                value={rebindKeyword}
                onChange={(e) => {
                  setRebindKeyword(e.target.value);
                  setRebindPage(1);
                }}
                placeholder={t("portTraffic.neKeywordPh")}
              />
              {rebindSelectedNe ? (
                <span className="muted">
                  {t("portTraffic.selectedOneNe")}: {rebindSelectedNe.name} (
                  {rebindSelectedNe.ip_address})
                </span>
              ) : null}
            </div>

            {rebindNesQuery.isLoading ? (
              <p className="muted">…</p>
            ) : !(rebindNesQuery.data?.items || []).length ? (
              <p className="muted">{t("portTraffic.rebindEmpty")}</p>
            ) : (
              <div className="pt-rebind-modal__table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th />
                      <th>{t("portTraffic.col.name")}</th>
                      <th>IP</th>
                      <th>{t("portTraffic.col.vendor")}</th>
                      <th>ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(rebindNesQuery.data?.items || []).map((row) => {
                      const checked = rebindSelectedNe?.id === row.id;
                      const sameIp =
                        Boolean(rebindDevice.ne_ip) &&
                        String(row.ip_address || "").trim() ===
                          String(rebindDevice.ne_ip || "").trim();
                      return (
                        <tr
                          key={`${row.source}:${row.id}`}
                          className={sameIp ? "pt-rebind-row--same-ip" : undefined}
                          title={sameIp ? t("portTraffic.rebindSameIpHint") : undefined}
                        >
                          <td>
                            <input
                              type="radio"
                              name="pt-rebind-ne"
                              checked={checked}
                              onChange={() => setRebindSelectedNe(row)}
                            />
                          </td>
                          <td>{row.name}</td>
                          <td>
                            {row.ip_address}
                            {sameIp ? (
                              <span className="pt-rebind-same-ip">{t("portTraffic.rebindSameIp")}</span>
                            ) : null}
                          </td>
                          <td>{row.vendor || "—"}</td>
                          <td className="muted" style={{ fontSize: 12 }}>
                            {row.id.slice(0, 8)}…
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="pager pt-list-pager" style={{ marginTop: 8 }}>
              <button
                type="button"
                className="pager__btn"
                disabled={rebindPage <= 1}
                onClick={() => setRebindPage((p) => p - 1)}
              >
                ‹
              </button>
              <span className="muted">
                {rebindPage}/
                {Math.max(1, pageCount(rebindNesQuery.data?.total || 0, TARGET_PAGE_SIZE))}
              </span>
              <button
                type="button"
                className="pager__btn"
                disabled={
                  rebindPage >=
                  Math.max(1, pageCount(rebindNesQuery.data?.total || 0, TARGET_PAGE_SIZE))
                }
                onClick={() => setRebindPage((p) => p + 1)}
              >
                ›
              </button>
            </div>

            <div className="modal__actions" style={{ marginTop: 12 }}>
              <button type="button" onClick={closeRebind} disabled={rebindMut.isPending}>
                {t("portTraffic.boardCancel")}
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!rebindSelectedNe || rebindMut.isPending}
                onClick={confirmRebind}
              >
                {rebindMut.isPending ? "…" : t("portTraffic.rebindSubmit")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
