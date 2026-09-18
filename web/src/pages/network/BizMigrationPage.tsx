import { Button, Input } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizCompareListMappings,
  bizMigrationCollectNow,
  bizMigrationCreateBatch,
  bizMigrationCreateProject,
  bizMigrationEnsurePortHighfreq,
  bizMigrationEvaluate,
  bizMigrationFinishBatch,
  bizMigrationGetBoard,
  bizMigrationListBaselinePorts,
  bizMigrationListBatches,
  bizMigrationListDiffs,
  bizMigrationListProjects,
  bizMigrationListRedTickets,
  bizMigrationPatchBatch,
  bizMigrationPatchProject,
  bizMigrationResolveRedTicket,
  bizStateListBatches,
  bizStateListTasks,
  formatErr,
} from "../../services/api";
import { Link } from "react-router-dom";

type TaskOpt = { id: string; ne_name: string; ne_ip: string; note?: string; interval_sec?: number };
type Project = {
  id: string;
  name: string;
  old_task_id: string;
  new_task_id: string;
  old_baseline_batch_id: string;
  new_baseline_batch_id: string;
  mapping_id: string;
  status: string;
  old_task?: TaskOpt;
  new_task?: TaskOpt;
};
type MigBatch = {
  id: string;
  batch_label: string;
  status: string;
  expect_set?: { ports?: string[] };
  accept_status?: string;
  accept_run_id?: string;
  accept_summary?: {
    passed?: boolean;
    progress_ok?: number;
    progress_total?: number;
    anomaly?: number;
    verdict_counts?: Record<string, number>;
  };
};
type RedTicket = {
  id: string;
  batch_id: string;
  key_str: string;
  new_key_str?: string;
  verdict: string;
  old_status?: string;
  new_status?: string;
  status: string;
};
type SheetCard = {
  metric_id: string;
  title?: string;
  progress_ok: number;
  progress_total: number;
  anomaly: number;
};
type DiffRow = {
  id: string;
  metric_id: string;
  verdict: string;
  color: string;
  old_kind: string;
  new_kind: string;
  in_expect: boolean;
  key_str?: string;
  new_key_str?: string;
  old_status?: string;
  new_status?: string;
};
type BaselinePort = {
  interface: string;
  admin?: string;
  phy?: string;
  prot?: string;
  description?: string;
  mapped_to?: string;
};

const COLOR: Record<string, string> = {
  green: "#16a34a",
  yellow: "#ca8a04",
  red: "#dc2626",
  gray: "#6b7280",
};

  const VERDICT_I18N: Record<string, string> = {
  migrated: "bizMigration.verdictMigrated",
  migrating: "bizMigration.verdictMigrating",
  lost: "bizMigration.verdictLost",
  anomaly: "bizMigration.verdictAnomaly",
  not_involved: "bizMigration.verdictNotInvolved",
  unexpected_new: "bizMigration.verdictUnexpected",
  unfinished: "bizMigration.verdictUnfinished",
  ok: "bizMigration.verdictOk",
};

export function BizMigrationPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();

  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<TaskOpt[]>([]);
  const [mappings, setMappings] = useState<{ id: string; name: string }[]>([]);
  const [projectId, setProjectId] = useState("");
  const [batches, setBatches] = useState<MigBatch[]>([]);
  const [batchId, setBatchId] = useState("");
  const [board, setBoard] = useState<{
    run?: {
      id: string;
      summary?: {
        sheet_cards?: SheetCard[];
        progress?: { ok: number; total: number };
        anomaly?: number;
        expect_ports?: string[];
      };
    } | null;
  } | null>(null);
  const [diffs, setDiffs] = useState<DiffRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [onlyExpect, setOnlyExpect] = useState(true);

  const [newName, setNewName] = useState("");
  const [oldTaskId, setOldTaskId] = useState("");
  const [newTaskId, setNewTaskId] = useState("");
  const [mappingId, setMappingId] = useState("");
  const [oldBaselineId, setOldBaselineId] = useState("");
  const [newBaselineId, setNewBaselineId] = useState("");
  const [oldBatches, setOldBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [newBatches, setNewBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [batchLabel, setBatchLabel] = useState("");
  const [baselinePorts, setBaselinePorts] = useState<BaselinePort[]>([]);
  const [selectedPorts, setSelectedPorts] = useState<Set<string>>(new Set());
  const [portFilter, setPortFilter] = useState("");
  const [redTickets, setRedTickets] = useState<RedTicket[]>([]);
  const [openRedCount, setOpenRedCount] = useState(0);
  const [acceptInfo, setAcceptInfo] = useState<MigBatch["accept_summary"] | null>(null);

  const project = useMemo(
    () => projects.find((p) => p.id === projectId) || null,
    [projects, projectId],
  );
  const batch = useMemo(() => batches.find((b) => b.id === batchId) || null, [batches, batchId]);
  const sheetCards = board?.run?.summary?.sheet_cards || [];
  const visibleDiffs = useMemo(
    () => (onlyExpect ? diffs.filter((d) => d.in_expect) : diffs),
    [diffs, onlyExpect],
  );

  useEffect(() => {
    if (batch?.accept_summary && Object.keys(batch.accept_summary).length) {
      setAcceptInfo(batch.accept_summary);
    } else if (batch && batch.accept_status === "none") {
      setAcceptInfo(null);
    }
  }, [batch]);

  const filteredBaselinePorts = useMemo(() => {
    const kw = portFilter.trim().toLowerCase();
    if (!kw) return baselinePorts;
    return baselinePorts.filter(
      (p) =>
        p.interface.toLowerCase().includes(kw) ||
        String(p.description || "")
          .toLowerCase()
          .includes(kw),
    );
  }, [baselinePorts, portFilter]);

  const reloadProjects = useCallback(async () => {
    const res = await bizMigrationListProjects();
    setProjects((res.items || []) as Project[]);
  }, []);

  const loadBaselinePorts = useCallback(async (pid: string) => {
    if (!pid) {
      setBaselinePorts([]);
      return;
    }
    try {
      const res = await bizMigrationListBaselinePorts(pid);
      setBaselinePorts(res.ports || []);
    } catch {
      setBaselinePorts([]);
    }
  }, []);

  const loadRedTickets = useCallback(async (pid: string) => {
    if (!pid) {
      setRedTickets([]);
      setOpenRedCount(0);
      return;
    }
    try {
      const res = await bizMigrationListRedTickets(pid);
      setRedTickets((res.items || []) as RedTicket[]);
      setOpenRedCount(Number(res.open_count || 0));
    } catch {
      setRedTickets([]);
      setOpenRedCount(0);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const [pr, tk, mp] = await Promise.all([
          bizMigrationListProjects(),
          bizStateListTasks(),
          bizCompareListMappings(),
        ]);
        setProjects((pr.items || []) as Project[]);
        setTasks(
          ((tk.items || []) as Record<string, unknown>[]).map((x) => ({
            id: String(x.id || ""),
            ne_name: String(x.ne_name || ""),
            ne_ip: String(x.ne_ip || ""),
            note: String(x.note || ""),
            interval_sec: Number(x.interval_sec || 0) || undefined,
          })),
        );
        setMappings(
          ((mp.items || []) as Record<string, unknown>[]).map((x) => ({
            id: String(x.id || ""),
            name: String(x.name || x.id || ""),
          })),
        );
      } catch (e) {
        showError(formatErr(e));
      }
    })();
  }, [showError]);

  useEffect(() => {
    if (!projectId) {
      setBatches([]);
      setBatchId("");
      setBaselinePorts([]);
      return;
    }
    void (async () => {
      try {
        const res = await bizMigrationListBatches(projectId);
        const items = (res.items || []) as MigBatch[];
        setBatches(items);
        if (!items.find((b) => b.id === batchId)) {
          setBatchId(items[0]?.id || "");
        }
        await loadBaselinePorts(projectId);
        await loadRedTickets(projectId);
      } catch (e) {
        showError(formatErr(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, showError, loadBaselinePorts, loadRedTickets]);

  useEffect(() => {
    if (!batchId) {
      setBoard(null);
      setDiffs([]);
      return;
    }
    void loadBoard(batchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  useEffect(() => {
    if (!oldTaskId) {
      setOldBatches([]);
      return;
    }
    void bizStateListBatches(oldTaskId, 30).then((r) => {
      setOldBatches(
        ((r.items || []) as Record<string, unknown>[]).map((x) => ({
          id: String(x.id || ""),
          started_at: (x.started_at as string) || null,
        })),
      );
    });
  }, [oldTaskId]);

  useEffect(() => {
    if (!newTaskId) {
      setNewBatches([]);
      return;
    }
    void bizStateListBatches(newTaskId, 30).then((r) => {
      setNewBatches(
        ((r.items || []) as Record<string, unknown>[]).map((x) => ({
          id: String(x.id || ""),
          started_at: (x.started_at as string) || null,
        })),
      );
    });
  }, [newTaskId]);

  async function loadBoard(bid: string, runId = "") {
    try {
      const b = await bizMigrationGetBoard(bid, runId);
      setBoard(b as typeof board);
      const rid = String((b as { run?: { id?: string } })?.run?.id || "");
      if (rid) {
        const d = await bizMigrationListDiffs({ runId: rid, limit: 500 });
        setDiffs((d.items || []) as DiffRow[]);
      } else {
        setDiffs([]);
      }
    } catch (e) {
      showError(formatErr(e));
    }
  }

  async function onCreateProject() {
    if (!newName.trim() || !oldTaskId || !newTaskId) {
      showError(t("bizMigration.needProjectFields"));
      return;
    }
    setBusy(true);
    try {
      const p = (await bizMigrationCreateProject({
        name: newName.trim(),
        old_task_id: oldTaskId,
        new_task_id: newTaskId,
        mapping_id: mappingId || "",
        old_baseline_batch_id: oldBaselineId || "",
        new_baseline_batch_id: newBaselineId || "",
        status: "active",
      })) as Project;
      await reloadProjects();
      setProjectId(p.id);
      setNewName("");
      showOk(t("bizMigration.projectCreated"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSaveBaseline() {
    if (!projectId) return;
    setBusy(true);
    try {
      await bizMigrationPatchProject(projectId, {
        old_baseline_batch_id: oldBaselineId || project?.old_baseline_batch_id || "",
        new_baseline_batch_id: newBaselineId || project?.new_baseline_batch_id || "",
        mapping_id: mappingId || project?.mapping_id || "",
      });
      await reloadProjects();
      await loadBaselinePorts(projectId);
      showOk(t("bizMigration.baselineSaved"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateBatch() {
    if (!projectId) return;
    const ports = [...selectedPorts];
    if (!ports.length) {
      showError(t("bizMigration.needExpectPorts"));
      return;
    }
    setBusy(true);
    try {
      const b = (await bizMigrationCreateBatch(projectId, {
        batch_label: batchLabel.trim() || t("bizMigration.defaultBatchLabel"),
        expect_set: { ports },
        status: "pending",
      })) as MigBatch;
      const res = await bizMigrationListBatches(projectId);
      setBatches((res.items || []) as MigBatch[]);
      setBatchId(b.id);
      setBatchLabel("");
      setSelectedPorts(new Set());
      if (Number((b as { open_red_count?: number }).open_red_count || 0) > 0) {
        showOk(t("bizMigration.batchCreatedWithRed", { n: String((b as { open_red_count?: number }).open_red_count) }));
      } else {
        showOk(t("bizMigration.batchCreated"));
      }
      await loadRedTickets(projectId);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function setBatchStatus(status: string) {
    if (!batchId) return;
    setBusy(true);
    try {
      if (status === "review" || status === "done") {
        const res = (await bizMigrationFinishBatch(batchId, status === "done")) as {
          batch?: MigBatch;
          accept_summary?: MigBatch["accept_summary"];
          open_red_count?: number;
        };
        const list = await bizMigrationListBatches(projectId);
        setBatches((list.items || []) as MigBatch[]);
        setAcceptInfo(res.accept_summary || res.batch?.accept_summary || null);
        await loadRedTickets(projectId);
        if (res.accept_summary?.passed) {
          showOk(t("bizMigration.acceptPassed"));
        } else {
          showOk(t("bizMigration.acceptFailed", { n: String(res.open_red_count ?? openRedCount) }));
        }
        if (res.batch?.accept_run_id) {
          await loadBoard(batchId, res.batch.accept_run_id);
        }
      } else {
        await bizMigrationPatchBatch(batchId, { status });
        const res = await bizMigrationListBatches(projectId);
        setBatches((res.items || []) as MigBatch[]);
        showOk(t("bizMigration.statusUpdated"));
        if (status === "active") await loadRedTickets(projectId);
      }
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onResolveTicket(id: string) {
    try {
      await bizMigrationResolveRedTicket(id);
      await loadRedTickets(projectId);
      showOk(t("bizMigration.redResolved"));
    } catch (e) {
      showError(formatErr(e));
    }
  }

  async function onEvaluate() {
    if (!batchId) return;
    setBusy(true);
    try {
      const run = await bizMigrationEvaluate(batchId, {});
      await loadBoard(batchId, String((run as { id?: string }).id || ""));
      showOk(t("bizMigration.evaluated"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onEnsureHighfreq() {
    if (!projectId) return;
    setBusy(true);
    try {
      const res = await bizMigrationEnsurePortHighfreq(projectId, {
        interval_sec: 60,
        collect_now: true,
      });
      await reloadProjects();
      const pr = await bizMigrationListProjects();
      setProjects((pr.items || []) as Project[]);
      showOk(t("bizMigration.highfreqReady"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCollectNow() {
    if (!projectId) return;
    setBusy(true);
    try {
      await bizMigrationCollectNow(projectId);
      showOk(t("bizMigration.collectTriggered"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (project) {
      setOldTaskId(project.old_task_id);
      setNewTaskId(project.new_task_id);
      setMappingId(project.mapping_id || "");
      setOldBaselineId(project.old_baseline_batch_id || "");
      setNewBaselineId(project.new_baseline_batch_id || "");
    }
  }, [project]);

  function togglePort(name: string) {
    setSelectedPorts((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function verdictLabel(v: string) {
    const key = VERDICT_I18N[v];
    return key ? t(key) : v;
  }

  return (
    <div className="nm-page" style={{ display: "grid", gap: 16 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: "1.25rem" }}>{t("bizMigration.title")}</h1>
        <p style={{ margin: "4px 0 0", opacity: 0.75, fontSize: 13 }}>{t("bizMigration.hintPort")}</p>
      </div>

      <section className="nm-card" style={{ display: "grid", gap: 10, padding: 12 }}>
        <strong>{t("bizMigration.createProject")}</strong>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
          <Input
            label={t("bizMigration.projectName")}
            value={newName}
            onValueChange={setNewName}
            size="sm"
          />
          <FieldSelect
            label={t("bizMigration.oldTask")}
            value={oldTaskId}
            onChange={(e) => setOldTaskId(e.target.value)}
            fullWidth
          >
            <option value="">—</option>
            {tasks.map((x) => (
              <option key={x.id} value={x.id}>
                {x.ne_name || x.id} ({x.ne_ip || "-"})
                {x.note ? ` · ${x.note}` : ""}
                {x.interval_sec ? ` · ${x.interval_sec}s` : ""}
              </option>
            ))}
          </FieldSelect>
          <FieldSelect
            label={t("bizMigration.newTask")}
            value={newTaskId}
            onChange={(e) => setNewTaskId(e.target.value)}
            fullWidth
          >
            <option value="">—</option>
            {tasks.map((x) => (
              <option key={x.id} value={x.id}>
                {x.ne_name || x.id} ({x.ne_ip || "-"})
                {x.note ? ` · ${x.note}` : ""}
                {x.interval_sec ? ` · ${x.interval_sec}s` : ""}
              </option>
            ))}
          </FieldSelect>
          <FieldSelect
            label={t("bizMigration.portMapping")}
            value={mappingId}
            onChange={(e) => setMappingId(e.target.value)}
            fullWidth
          >
            <option value="">—</option>
            {mappings.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </FieldSelect>
          <FieldSelect
            label={t("bizMigration.oldBaseline")}
            value={oldBaselineId}
            onChange={(e) => setOldBaselineId(e.target.value)}
            fullWidth
          >
            <option value="">—</option>
            {oldBatches.map((x) => (
              <option key={x.id} value={x.id}>
                {String(x.started_at || x.id).slice(0, 32)}
              </option>
            ))}
          </FieldSelect>
          <FieldSelect
            label={t("bizMigration.newBaseline")}
            value={newBaselineId}
            onChange={(e) => setNewBaselineId(e.target.value)}
            fullWidth
          >
            <option value="">—</option>
            {newBatches.map((x) => (
              <option key={x.id} value={x.id}>
                {String(x.started_at || x.id).slice(0, 32)}
              </option>
            ))}
          </FieldSelect>
        </div>
        <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onCreateProject()}>
          {t("bizMigration.createProject")}
        </Button>
      </section>

      <section
        style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 12, alignItems: "start" }}
      >
        <div className="nm-card" style={{ padding: 10, display: "grid", gap: 6 }}>
          <strong>{t("bizMigration.projects")}</strong>
          {projects.length === 0 && (
            <span style={{ fontSize: 12, opacity: 0.7 }}>{t("bizMigration.emptyProjects")}</span>
          )}
          {projects.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setProjectId(p.id)}
              style={{
                textAlign: "left",
                padding: "6px 8px",
                borderRadius: 6,
                border: projectId === p.id ? "1px solid #2563eb" : "1px solid transparent",
                background: projectId === p.id ? "rgba(37,99,235,.08)" : "transparent",
                cursor: "pointer",
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</div>
              <div style={{ fontSize: 11, opacity: 0.7 }}>
                {p.old_task?.ne_name || p.old_task_id.slice(0, 6)} →{" "}
                {p.new_task?.ne_name || p.new_task_id.slice(0, 6)}
              </div>
            </button>
          ))}
        </div>

        <div style={{ display: "grid", gap: 12 }}>
          {project && (
            <div className="nm-card" style={{ padding: 12, display: "grid", gap: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <strong>{project.name}</strong>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <Button
                    size="sm"
                    variant="primary"
                    isDisabled={busy}
                    onPress={() => void onEnsureHighfreq()}
                  >
                    {t("bizMigration.enableHighfreq")}
                  </Button>
                  <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void onCollectNow()}>
                    {t("bizMigration.collectNow")}
                  </Button>
                  <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void onSaveBaseline()}>
                    {t("bizMigration.saveBaseline")}
                  </Button>
                  <Link
                    to="/network/tasks/biz-state"
                    style={{ fontSize: 12, alignSelf: "center" }}
                  >
                    {t("bizMigration.openBizState")}
                  </Link>
                </div>
              </div>
              {(project.old_task || project.new_task) && (
                <div style={{ fontSize: 12, opacity: 0.8 }}>
                  {t("bizMigration.boundTasks")}:{" "}
                  {project.old_task?.ne_name || project.old_task_id.slice(0, 8)}
                  {project.old_task?.note ? ` (${project.old_task.note})` : ""} /
                  {project.new_task?.ne_name || project.new_task_id.slice(0, 8)}
                  {project.new_task?.note ? ` (${project.new_task.note})` : ""}
                  {project.old_task?.interval_sec
                    ? ` · ${project.old_task.interval_sec}s`
                    : ""}
                </div>
              )}
              {openRedCount > 0 && (
                <div style={{ fontSize: 12, color: COLOR.red }}>
                  {t("bizMigration.openRedHint", { n: String(openRedCount) })}
                </div>
              )}
              <p style={{ margin: 0, fontSize: 12, opacity: 0.7 }}>{t("bizMigration.highfreqHint")}</p>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 6 }}>
                  {t("bizMigration.pickExpectPorts")} ({selectedPorts.size})
                </div>
                <Input
                  size="sm"
                  value={portFilter}
                  onValueChange={setPortFilter}
                  placeholder={t("bizMigration.portFilterPh")}
                  style={{ marginBottom: 8 }}
                />
                {!project.old_baseline_batch_id && (
                  <div style={{ fontSize: 12, color: COLOR.yellow, marginBottom: 6 }}>
                    {t("bizMigration.needBaselineFirst")}
                  </div>
                )}
                <div
                  style={{
                    maxHeight: 220,
                    overflow: "auto",
                    border: "1px solid rgba(0,0,0,.08)",
                    borderRadius: 8,
                  }}
                >
                  <table className="nm-table" style={{ width: "100%", fontSize: 12 }}>
                    <thead>
                      <tr>
                        <th style={{ width: 36 }} />
                        <th>{t("bizMigration.colPort")}</th>
                        <th>admin/phy/prot</th>
                        <th>{t("bizMigration.colMapped")}</th>
                        <th>{t("bizMigration.colDesc")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredBaselinePorts.map((p) => (
                        <tr key={p.interface} onClick={() => togglePort(p.interface)} style={{ cursor: "pointer" }}>
                          <td>
                            <input
                              type="checkbox"
                              checked={selectedPorts.has(p.interface)}
                              onChange={() => togglePort(p.interface)}
                            />
                          </td>
                          <td>{p.interface}</td>
                          <td>
                            {p.admin || "-"}/{p.phy || "-"}/{p.prot || "-"}
                          </td>
                          <td>{p.mapped_to || "—"}</td>
                          <td style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis" }}>
                            {p.description || ""}
                          </td>
                        </tr>
                      ))}
                      {filteredBaselinePorts.length === 0 && (
                        <tr>
                          <td colSpan={5} style={{ opacity: 0.6 }}>
                            {t("bizMigration.emptyBaselinePorts")}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
                <Input
                  label={t("bizMigration.batchLabel")}
                  value={batchLabel}
                  onValueChange={setBatchLabel}
                  size="sm"
                  placeholder={t("bizMigration.defaultBatchLabel")}
                  style={{ minWidth: 160 }}
                />
                <Button size="sm" isDisabled={busy} onPress={() => void onCreateBatch()}>
                  {t("bizMigration.createBatch")}
                </Button>
                <FieldSelect
                  label={t("bizMigration.batches")}
                  value={batchId}
                  onChange={(e) => setBatchId(e.target.value)}
                >
                  {batches.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.batch_label} ({b.status})
                    </option>
                  ))}
                </FieldSelect>
                {batch && (
                  <>
                    <Button
                      size="sm"
                      variant="secondary"
                      isDisabled={busy}
                      onPress={() => void setBatchStatus("active")}
                    >
                      {t("bizMigration.startBatch")}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      isDisabled={busy}
                      onPress={() => void setBatchStatus("review")}
                    >
                      {t("bizMigration.finishBatch")}
                    </Button>
                    <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onEvaluate()}>
                      {t("bizMigration.evaluate")}
                    </Button>
                  </>
                )}
              </div>
            </div>
          )}

          {acceptInfo && (
            <div className="nm-card" style={{ padding: 12, display: "grid", gap: 8 }}>
              <strong>{t("bizMigration.acceptTitle")}</strong>
              <div style={{ display: "flex", gap: 20, flexWrap: "wrap", fontVariantNumeric: "tabular-nums" }}>
                <div style={{ color: acceptInfo.passed ? COLOR.green : COLOR.red, fontWeight: 600 }}>
                  {acceptInfo.passed ? t("bizMigration.acceptPassedShort") : t("bizMigration.acceptFailedShort")}
                </div>
                <div>
                  {t("bizMigration.progress")}: {acceptInfo.progress_ok ?? 0}/{acceptInfo.progress_total ?? 0}
                </div>
                <div style={{ color: COLOR.red }}>
                  {t("bizMigration.anomaly")}: {acceptInfo.anomaly ?? 0}
                </div>
              </div>
              <div style={{ fontSize: 12, opacity: 0.75 }}>{t("bizMigration.acceptCarryHint")}</div>
            </div>
          )}

          {redTickets.length > 0 && (
            <div className="nm-card" style={{ padding: 12, display: "grid", gap: 8 }}>
              <strong>
                {t("bizMigration.redTitle")} ({openRedCount} {t("bizMigration.redOpen")})
              </strong>
              <div style={{ overflowX: "auto" }}>
                <table className="nm-table" style={{ width: "100%", fontSize: 12 }}>
                  <thead>
                    <tr>
                      <th>{t("bizMigration.colOldPort")}</th>
                      <th>{t("bizMigration.colNewPort")}</th>
                      <th>{t("bizMigration.colOldStatus")}</th>
                      <th>{t("bizMigration.colNewStatus")}</th>
                      <th>{t("bizMigration.colVerdict")}</th>
                      <th>{t("bizMigration.colStatus")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {redTickets.map((r) => (
                      <tr key={r.id}>
                        <td>{r.key_str}</td>
                        <td>{r.new_key_str || "—"}</td>
                        <td>{r.old_status || "—"}</td>
                        <td>{r.new_status || "—"}</td>
                        <td style={{ color: COLOR.red, fontWeight: 600 }}>{verdictLabel(r.verdict)}</td>
                        <td>{r.status}</td>
                        <td>
                          {r.status !== "resolved" && (
                            <Button size="sm" variant="secondary" onPress={() => void onResolveTicket(r.id)}>
                              {t("bizMigration.resolveRed")}
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {board?.run && (
            <div className="nm-card" style={{ padding: 12, display: "grid", gap: 10 }}>
              <strong>
                {t("bizMigration.board")} · {t("bizMigration.metricPort")}
              </strong>
              <div style={{ display: "flex", gap: 24, fontVariantNumeric: "tabular-nums", flexWrap: "wrap" }}>
                <div>
                  {t("bizMigration.progress")}:{" "}
                  <b>
                    {board.run.summary?.progress?.ok ?? 0}/{board.run.summary?.progress?.total ?? 0}
                  </b>
                </div>
                <div style={{ color: COLOR.red }}>
                  {t("bizMigration.anomaly")}: <b>{board.run.summary?.anomaly ?? 0}</b>
                </div>
                {sheetCards[0] && (
                  <div style={{ fontSize: 12, opacity: 0.75 }}>
                    {sheetCards[0].title || sheetCards[0].metric_id}
                  </div>
                )}
                <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={onlyExpect}
                    onChange={(e) => setOnlyExpect(e.target.checked)}
                  />
                  {t("bizMigration.onlyExpect")}
                </label>
              </div>

              <div style={{ overflowX: "auto" }}>
                <table className="nm-table" style={{ width: "100%", fontSize: 12 }}>
                  <thead>
                    <tr>
                      <th>{t("bizMigration.colOldPort")}</th>
                      <th>{t("bizMigration.colNewPort")}</th>
                      <th>{t("bizMigration.colOldStatus")}</th>
                      <th>{t("bizMigration.colNewStatus")}</th>
                      <th>{t("bizMigration.colVerdict")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleDiffs.map((d) => (
                      <tr key={d.id}>
                        <td>{d.key_str || "—"}</td>
                        <td>{d.new_key_str || "—"}</td>
                        <td>{d.old_status || "—"}</td>
                        <td>{d.new_status || "—"}</td>
                        <td style={{ color: COLOR[d.color] || COLOR.gray, fontWeight: 600 }}>
                          {verdictLabel(d.verdict)}
                        </td>
                      </tr>
                    ))}
                    {visibleDiffs.length === 0 && (
                      <tr>
                        <td colSpan={5} style={{ opacity: 0.6 }}>
                          {t("bizMigration.emptyDiffs")}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
