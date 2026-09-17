import { Button, Input } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizStateCollectNow,
  bizStateCreateTask,
  bizStateDeleteTask,
  bizStateDiscover,
  bizStateDownloadExport,
  bizStateGetBatch,
  bizStateGetTask,
  bizStateListBatches,
  bizStateListProfiles,
  bizStateListTasks,
  bizStatePatchTask,
  bizStateSetBindings,
  fetchCliTargets,
  formatErr,
} from "../../services/api";
import type { CliTargetItem } from "../../types";
import { formatSystemTime } from "../../utils/time";
import { jobChipColor, NmStatusChip } from "./nmChips";

type TaskRow = {
  id: string;
  ne_name: string;
  ne_ip: string;
  vendor: string;
  status: string;
  collect_running: boolean;
  last_error: string;
  last_collect_ended_at?: string | null;
};

type Placeholder = {
  name: string;
  bind_mode?: string;
  discover_profile_id?: string;
};

type Profile = {
  profile_id: string;
  title: string;
  command_template: string;
  description: string;
  metric_id: string;
  kind?: string;
  placeholders?: Placeholder[];
};

type BatchRow = {
  id: string;
  status: string;
  row_count: number;
  command_count: number;
  started_at?: string | null;
};

type Candidate = { value: string; label: string; rd?: string };

function fmtTime(v?: string | null) {
  if (!v) return "—";
  return formatSystemTime(v) || v;
}

export function BizStatePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [batches, setBatches] = useState<BatchRow[]>([]);
  const [batchDetail, setBatchDetail] = useState<any>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [nes, setNes] = useState<CliTargetItem[]>([]);
  const [neId, setNeId] = useState("");
  const [busy, setBusy] = useState(false);
  const [bindItemId, setBindItemId] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedVrfs, setSelectedVrfs] = useState<string[]>([]);
  const [discoverCmd, setDiscoverCmd] = useState("");

  const refreshTasks = useCallback(async () => {
    const res = await bizStateListTasks();
    setTasks((res.items || []) as TaskRow[]);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await refreshTasks();
        const neRes = await fetchCliTargets({
          source: "managed",
          keyword: "",
          page: 1,
          pageSize: 200,
        });
        setNes(neRes.items || []);
      } catch (e) {
        showError(formatErr(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once
  }, [refreshTasks]);

  const collectProfiles = useMemo(
    () => profiles.filter((p) => (p.kind || "collect") === "collect"),
    [profiles],
  );

  const openTask = async (id: string) => {
    setSelectedId(id);
    setBatchDetail(null);
    setBindItemId("");
    setCandidates([]);
    setSelectedVrfs([]);
    try {
      const task = await bizStateGetTask(id);
      setDetail(task);
      const b = await bizStateListBatches(id);
      setBatches((b.items || []) as BatchRow[]);
      const p = await bizStateListProfiles({
        vendor: task.vendor || "",
        device_type: task.device_type || "",
      });
      setProfiles((p.items || []) as Profile[]);
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const createTask = async () => {
    if (!neId) return;
    const ne = nes.find((n) => n.id === neId);
    if (!ne) return;
    setBusy(true);
    try {
      const task = await bizStateCreateTask({
        source: "managed",
        ne_id: ne.id,
        ne_name: ne.name,
        ne_ip: ne.ip_address,
        vendor: ne.vendor,
        device_type: ne.device_type,
      });
      showOk(t("bizState.created"));
      await refreshTasks();
      await openTask(String(task.id));
      setNeId("");
    } catch (e) {
      showError(t("bizState.createFailed") + ": " + formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const setStatus = async (status: string) => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await bizStatePatchTask(selectedId, { status });
      showOk(status === "running" ? t("bizState.started") : t("bizState.paused"));
      await openTask(selectedId);
      await refreshTasks();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const collectNow = async () => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await bizStateCollectNow(selectedId);
      await openTask(selectedId);
      await refreshTasks();
      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const task = await bizStateGetTask(selectedId);
        setDetail(task);
        if (!task.collect_running) {
          const b = await bizStateListBatches(selectedId);
          setBatches((b.items || []) as BatchRow[]);
          break;
        }
      }
      await refreshTasks();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const openBatch = async (batchId: string) => {
    try {
      const d = await bizStateGetBatch(batchId);
      setBatchDetail(d);
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const removeTask = async () => {
    if (!selectedId) return;
    if (!window.confirm(t("bizState.confirmDelete"))) return;
    setBusy(true);
    try {
      await bizStateDeleteTask(selectedId);
      showOk(t("bizState.deleted"));
      setSelectedId("");
      setDetail(null);
      setBatches([]);
      setBatchDetail(null);
      await refreshTasks();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleProfileItem = async (profile: Profile, enable: boolean) => {
    if (!selectedId || !detail) return;
    const items = [...(detail.items || [])];
    const idx = items.findIndex((it: any) => it.source_profile_id === profile.profile_id);
    if (enable) {
      if (idx >= 0) items[idx] = { ...items[idx], enabled: true };
      else {
        items.push({
          source_profile_id: profile.profile_id,
          kind: "catalog",
          enabled: true,
          title: profile.title,
          bindings: [],
        });
      }
    } else if (idx >= 0) {
      items[idx] = { ...items[idx], enabled: false };
    }
    setBusy(true);
    try {
      await bizStatePatchTask(selectedId, { items });
      await openTask(selectedId);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const startDiscover = async (item: any) => {
    if (!selectedId || !detail) return;
    const prof = profiles.find((p) => p.profile_id === item.source_profile_id);
    const ph = (prof?.placeholders || [])[0];
    if (!ph) {
      showError(t("bizState.noNeedBind"));
      return;
    }
    setBusy(true);
    setBindItemId(item.id);
    try {
      const res = await bizStateDiscover({
        task_id: selectedId,
        collect_profile_id: item.source_profile_id,
        placeholder: ph.name,
      });
      if (!res.ok) {
        showError(res.error || t("bizState.discoverFailed"));
        setCandidates([]);
        return;
      }
      setDiscoverCmd(res.command || "");
      const cand = (res.candidates || []) as Candidate[];
      setCandidates(cand);
      const existing = (item.bindings || [])
        .filter((b: any) => b.placeholder === ph.name)
        .map((b: any) => String(b.value));
      setSelectedVrfs(existing.length ? existing : cand.map((c) => c.value));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const saveBindings = async () => {
    if (!selectedId || !bindItemId) return;
    const item = (detail?.items || []).find((it: any) => it.id === bindItemId);
    const prof = profiles.find((p) => p.profile_id === item?.source_profile_id);
    const phName = (prof?.placeholders || [])[0]?.name || "vrf";
    setBusy(true);
    try {
      await bizStateSetBindings(
        selectedId,
        bindItemId,
        selectedVrfs.map((v) => ({ placeholder: phName, value: v })),
      );
      showOk(t("bizState.bindingsSaved"));
      await openTask(selectedId);
      setBindItemId("");
      setCandidates([]);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const runningCount = tasks.filter((x) => x.status === "running").length;

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("bizState.title")}</h2>
        <div className="btn-row">
          <FieldSelect
            value={neId}
            onChange={(e) => setNeId(e.target.value)}
            aria-label={t("bizState.pickNe")}
          >
            <option value="">{t("bizState.pickNe")}</option>
            {nes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name || n.ip_address} ({n.vendor || "-"})
              </option>
            ))}
          </FieldSelect>
          <Button size="sm" variant="primary" isDisabled={busy || !neId} onPress={() => void createTask()}>
            {t("bizState.create")}
          </Button>
        </div>
      </div>

      <div className="pt-list">
        <div className="pt-list-kpis">
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("bizState.colNe")}</div>
            <div className="pt-list-kpi__value">{tasks.length}</div>
          </div>
          <div className="pt-list-kpi pt-list-kpi--live">
            <div className="pt-list-kpi__label">{t("bizState.start")}</div>
            <div className="pt-list-kpi__value">{runningCount}</div>
          </div>
        </div>

        <div className="nm-split" style={{ display: "grid", gridTemplateColumns: "1fr 1.45fr", gap: 16 }}>
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("bizState.colNe")}</th>
                  <th>{t("bizState.colStatus")}</th>
                  <th>{t("bizState.colLast")}</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((row) => (
                  <tr
                    key={row.id}
                    className={selectedId === row.id ? "is-selected" : undefined}
                    style={{ cursor: "pointer" }}
                    onClick={() => void openTask(row.id)}
                  >
                    <td>
                      <div className="pt-list-task-name">{row.ne_name || row.ne_ip || "—"}</div>
                      <div className="muted">{row.vendor || "—"}</div>
                    </td>
                    <td>
                      <div className="pt-list-actions" style={{ flexWrap: "wrap", gap: 4 }}>
                        <NmStatusChip color={jobChipColor(row.status)}>{row.status}</NmStatusChip>
                        {row.collect_running ? (
                          <NmStatusChip color="accent">{t("bizState.collecting")}</NmStatusChip>
                        ) : null}
                      </div>
                    </td>
                    <td className="pt-list-time">{fmtTime(row.last_collect_ended_at)}</td>
                  </tr>
                ))}
                {!tasks.length ? (
                  <tr>
                    <td colSpan={3}>
                      <div className="pt-list-empty">{t("bizState.empty")}</div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <div>
            {!detail ? (
              <div className="pt-list-empty">{t("bizState.pickTask")}</div>
            ) : (
              <>
                <div className="panel__toolbar" style={{ padding: 0, marginBottom: 8 }}>
                  <h3 style={{ margin: 0 }}>
                    {detail.ne_name || detail.ne_ip} · {detail.status}
                  </h3>
                  <div className="btn-row">
                    <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void setStatus("running")}>
                      {t("bizState.start")}
                    </Button>
                    <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void setStatus("paused")}>
                      {t("bizState.pause")}
                    </Button>
                    <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void collectNow()}>
                      {t("bizState.collectNow")}
                    </Button>
                    <Button size="sm" variant="danger" isDisabled={busy} onPress={() => void removeTask()}>
                      {t("bizState.delete")}
                    </Button>
                  </div>
                </div>
                {detail.last_error ? <p className="form-error">{detail.last_error}</p> : null}

                <h4 style={{ margin: "12px 0 8px" }}>{t("bizState.profiles")}</h4>
                <div className="pt-list-table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{t("bizState.enable")}</th>
                        <th>{t("bizState.profiles")}</th>
                        <th>{t("bizState.command")}</th>
                        <th>{t("bizState.params")}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {collectProfiles.map((prof) => {
                        const it = (detail.items || []).find(
                          (x: any) => x.source_profile_id === prof.profile_id,
                        );
                        const enabled = Boolean(it?.enabled);
                        const binds = it?.bindings || [];
                        const needsBind = (prof.placeholders || []).length > 0;
                        return (
                          <tr key={prof.profile_id}>
                            <td>
                              <input
                                type="checkbox"
                                checked={enabled}
                                disabled={busy}
                                onChange={(e) => void toggleProfileItem(prof, e.target.checked)}
                              />
                            </td>
                            <td>
                              <div className="pt-list-task-name">{prof.title}</div>
                              {prof.description ? <div className="muted">{prof.description}</div> : null}
                            </td>
                            <td>
                              <code>{prof.command_template}</code>
                            </td>
                            <td>
                              {needsBind
                                ? binds.length
                                  ? binds.map((b: any) => b.value).join(", ")
                                  : t("bizState.unbound")
                                : "—"}
                            </td>
                            <td>
                              {needsBind && enabled && it ? (
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  isDisabled={busy}
                                  onPress={() => void startDiscover(it)}
                                >
                                  {t("bizState.discoverVrf")}
                                </Button>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {bindItemId && candidates.length ? (
                  <div className="panel" style={{ marginTop: 12, padding: 12 }}>
                    <h4 style={{ marginTop: 0 }}>{t("bizState.bindTitle")}</h4>
                    <p className="muted">
                      <code>{discoverCmd}</code> · {selectedVrfs.length}
                    </p>
                    <div style={{ maxHeight: 220, overflow: "auto", marginBottom: 8 }}>
                      {candidates.map((c) => (
                        <label key={c.value} style={{ display: "block", marginBottom: 4 }}>
                          <input
                            type="checkbox"
                            checked={selectedVrfs.includes(c.value)}
                            onChange={(e) => {
                              setSelectedVrfs((prev) =>
                                e.target.checked
                                  ? [...prev, c.value]
                                  : prev.filter((x) => x !== c.value),
                              );
                            }}
                          />{" "}
                          {c.label}
                          {c.rd ? <span className="muted"> · RD {c.rd}</span> : null}
                        </label>
                      ))}
                    </div>
                    <div className="btn-row">
                      <Button
                        size="sm"
                        variant="primary"
                        isDisabled={busy || !selectedVrfs.length}
                        onPress={() => void saveBindings()}
                      >
                        {t("bizState.saveBindings")}
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        isDisabled={busy}
                        onPress={() => {
                          setBindItemId("");
                          setCandidates([]);
                        }}
                      >
                        {t("bizState.cancel")}
                      </Button>
                    </div>
                  </div>
                ) : null}

                <h4 style={{ margin: "16px 0 8px" }}>{t("bizState.batches")}</h4>
                <div className="pt-list-table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{t("bizState.colTime")}</th>
                        <th>{t("bizState.colStatus")}</th>
                        <th>{t("bizState.colRows")}</th>
                        <th>{t("bizState.colActions")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {batches.map((b) => (
                        <tr key={b.id}>
                          <td className="pt-list-time">{fmtTime(b.started_at)}</td>
                          <td>
                            <NmStatusChip color={jobChipColor(b.status)}>{b.status}</NmStatusChip>
                          </td>
                          <td className="pt-list-num">{b.row_count}</td>
                          <td>
                            <div className="pt-list-actions">
                              <Button size="sm" variant="secondary" onPress={() => void openBatch(b.id)}>
                                {t("bizState.viewBatch")}
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                onPress={() => void bizStateDownloadExport(b.id)}
                              >
                                {t("bizState.export")}
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))}
                      {!batches.length ? (
                        <tr>
                          <td colSpan={4}>
                            <div className="pt-list-empty">{t("bizState.noBatches")}</div>
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>

                {batchDetail ? (
                  <div style={{ marginTop: 16 }}>
                    <h4 style={{ marginBottom: 4 }}>
                      {t("bizState.batchDetail")} {batchDetail.id}
                    </h4>
                    <p className="muted">
                      {batchDetail.command_count} · {batchDetail.row_count} · {batchDetail.status}
                    </p>
                    {(batchDetail.lldp_neighbors || []).length ? (
                      <div className="pt-list-table-wrap" style={{ marginTop: 8 }}>
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th>local_if</th>
                              <th>remote_sys</th>
                              <th>remote_if</th>
                              <th>remote_ip</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(batchDetail.lldp_neighbors || []).slice(0, 200).map((n: any, i: number) => (
                              <tr key={i}>
                                <td>{n.local_if}</td>
                                <td>{n.remote_sys}</td>
                                <td>{n.remote_if}</td>
                                <td>{n.remote_ip}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                    {(batchDetail.vrf_route_summary || []).length ? (
                      <div className="pt-list-table-wrap" style={{ marginTop: 8 }}>
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th>vrf</th>
                              <th>source</th>
                              <th>networks</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(batchDetail.vrf_route_summary || []).map((r: any, i: number) => (
                              <tr key={i}>
                                <td>{r.vrf}</td>
                                <td>{r.source}</td>
                                <td className="pt-list-num">{r.networks}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
