import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
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
import { formatSystemTime } from "../../utils/time";
import { jobChipColor, NmStatusChip } from "./nmChips";

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
  note?: string;
  created_at?: string | null;
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

type DetailTab = "setup" | "batches" | "board";

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

function fmtTime(v?: string | null) {
  if (!v) return "—";
  return formatSystemTime(v) || v;
}

function taskLabel(x: TaskOpt) {
  const base = `${x.ne_name || x.id} (${x.ne_ip || "-"})`;
  const note = x.note ? ` · ${x.note}` : "";
  const iv = x.interval_sec ? ` · ${x.interval_sec}s` : "";
  return `${base}${note}${iv}`;
}

export function BizMigrationPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();

  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<TaskOpt[]>([]);
  const [mappings, setMappings] = useState<{ id: string; name: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [listKeyword, setListKeyword] = useState("");
  const debouncedListKw = useDebouncedValue(listKeyword, 250);

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createOldTaskId, setCreateOldTaskId] = useState("");
  const [createNewTaskId, setCreateNewTaskId] = useState("");
  const [createMappingId, setCreateMappingId] = useState("");
  const [createOldBaselineId, setCreateOldBaselineId] = useState("");
  const [createNewBaselineId, setCreateNewBaselineId] = useState("");
  const [createOldBatches, setCreateOldBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [createNewBatches, setCreateNewBatches] = useState<{ id: string; started_at?: string | null }[]>([]);

  const [projectId, setProjectId] = useState("");
  const [detailTab, setDetailTab] = useState<DetailTab>("setup");
  const [mappingId, setMappingId] = useState("");
  const [oldBaselineId, setOldBaselineId] = useState("");
  const [newBaselineId, setNewBaselineId] = useState("");
  const [oldBatches, setOldBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [newBatches, setNewBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
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
  const [onlyExpect, setOnlyExpect] = useState(true);
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

  const filteredProjects = useMemo(() => {
    const kw = debouncedListKw.trim().toLowerCase();
    if (!kw) return projects;
    return projects.filter((p) => {
      const blob = [
        p.name,
        p.status,
        p.note || "",
        p.old_task?.ne_name || "",
        p.old_task?.ne_ip || "",
        p.new_task?.ne_name || "",
        p.new_task?.ne_ip || "",
        p.old_task_id,
        p.new_task_id,
      ]
        .join(" ")
        .toLowerCase();
      return blob.includes(kw);
    });
  }, [projects, debouncedListKw]);

  const activeCount = useMemo(
    () => projects.filter((p) => String(p.status || "").toLowerCase() === "active").length,
    [projects],
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
      setRedTickets([]);
      setOpenRedCount(0);
      setBoard(null);
      setDiffs([]);
      setAcceptInfo(null);
      return;
    }
    void (async () => {
      try {
        const res = await bizMigrationListBatches(projectId);
        const items = (res.items || []) as MigBatch[];
        setBatches(items);
        setBatchId((prev) => (items.find((b) => b.id === prev) ? prev : items[0]?.id || ""));
        await loadBaselinePorts(projectId);
        await loadRedTickets(projectId);
      } catch (e) {
        showError(formatErr(e));
      }
    })();
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
    if (!createOldTaskId) {
      setCreateOldBatches([]);
      return;
    }
    void bizStateListBatches(createOldTaskId, 30).then((r) => {
      setCreateOldBatches(
        ((r.items || []) as Record<string, unknown>[]).map((x) => ({
          id: String(x.id || ""),
          started_at: (x.started_at as string) || null,
        })),
      );
    });
  }, [createOldTaskId]);

  useEffect(() => {
    if (!createNewTaskId) {
      setCreateNewBatches([]);
      return;
    }
    void bizStateListBatches(createNewTaskId, 30).then((r) => {
      setCreateNewBatches(
        ((r.items || []) as Record<string, unknown>[]).map((x) => ({
          id: String(x.id || ""),
          started_at: (x.started_at as string) || null,
        })),
      );
    });
  }, [createNewTaskId]);

  useEffect(() => {
    if (!project?.old_task_id) {
      setOldBatches([]);
      return;
    }
    void bizStateListBatches(project.old_task_id, 30).then((r) => {
      setOldBatches(
        ((r.items || []) as Record<string, unknown>[]).map((x) => ({
          id: String(x.id || ""),
          started_at: (x.started_at as string) || null,
        })),
      );
    });
  }, [project?.old_task_id]);

  useEffect(() => {
    if (!project?.new_task_id) {
      setNewBatches([]);
      return;
    }
    void bizStateListBatches(project.new_task_id, 30).then((r) => {
      setNewBatches(
        ((r.items || []) as Record<string, unknown>[]).map((x) => ({
          id: String(x.id || ""),
          started_at: (x.started_at as string) || null,
        })),
      );
    });
  }, [project?.new_task_id]);

  useEffect(() => {
    if (project) {
      setMappingId(project.mapping_id || "");
      setOldBaselineId(project.old_baseline_batch_id || "");
      setNewBaselineId(project.new_baseline_batch_id || "");
    }
  }, [project]);

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

  function resetCreateForm() {
    setCreateName("");
    setCreateOldTaskId("");
    setCreateNewTaskId("");
    setCreateMappingId("");
    setCreateOldBaselineId("");
    setCreateNewBaselineId("");
  }

  const openCreate = () => {
    closeProject();
    resetCreateForm();
    setCreateOpen(true);
  };

  const closeCreate = () => {
    setCreateOpen(false);
    resetCreateForm();
  };

  const openProject = (id: string, tab: DetailTab = "setup") => {
    setProjectId(id);
    setDetailTab(tab);
    setSelectedPorts(new Set());
    setPortFilter("");
    setBatchLabel("");
    setOnlyExpect(true);
  };

  const closeProject = () => {
    setProjectId("");
    setDetailTab("setup");
  };

  async function onCreateProject() {
    if (!createName.trim() || !createOldTaskId || !createNewTaskId) {
      showError(t("bizMigration.needProjectFields"));
      return;
    }
    setBusy(true);
    try {
      const p = (await bizMigrationCreateProject({
        name: createName.trim(),
        old_task_id: createOldTaskId,
        new_task_id: createNewTaskId,
        mapping_id: createMappingId || "",
        old_baseline_batch_id: createOldBaselineId || "",
        new_baseline_batch_id: createNewBaselineId || "",
        status: "active",
      })) as Project;
      await reloadProjects();
      closeCreate();
      showOk(t("bizMigration.projectCreated"));
      openProject(p.id, "setup");
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
        showOk(
          t("bizMigration.batchCreatedWithRed", {
            n: String((b as { open_red_count?: number }).open_red_count),
          }),
        );
      } else {
        showOk(t("bizMigration.batchCreated"));
      }
      await loadRedTickets(projectId);
      setDetailTab("board");
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
        setDetailTab("board");
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
      setDetailTab("board");
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
      await bizMigrationEnsurePortHighfreq(projectId, {
        interval_sec: 60,
        collect_now: true,
      });
      await reloadProjects();
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

  function statusLabel(status: string) {
    const s = String(status || "").toLowerCase();
    if (s === "active") return t("bizMigration.statusActive");
    if (s === "done" || s === "completed") return t("bizMigration.statusDone");
    if (s === "paused") return t("bizMigration.statusPaused");
    if (s === "pending") return t("bizMigration.statusPending");
    if (s === "review") return t("bizMigration.statusReview");
    return status || "—";
  }

  const taskOptions = (
    <>
      <option value="">—</option>
      {tasks.map((x) => (
        <option key={x.id} value={x.id}>
          {taskLabel(x)}
        </option>
      ))}
    </>
  );

  const batchOptions = (items: { id: string; started_at?: string | null }[]) => (
    <>
      <option value="">—</option>
      {items.map((x) => (
        <option key={x.id} value={x.id}>
          {fmtTime(x.started_at) !== "—" ? fmtTime(x.started_at) : x.id.slice(0, 12)}
        </option>
      ))}
    </>
  );

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("bizMigration.title")}</h2>
        <div className="btn-row">
          <Button size="sm" variant="primary" onPress={openCreate}>
            {t("bizMigration.create")}
          </Button>
        </div>
      </div>
      <p className="panel__hint muted">{t("bizMigration.hintPort")}</p>

      <div className="pt-list">
        <div className="pt-list-kpis">
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("bizMigration.kpiProjects")}</div>
            <div className="pt-list-kpi__value">{projects.length}</div>
          </div>
          <div className="pt-list-kpi pt-list-kpi--live">
            <div className="pt-list-kpi__label">{t("bizMigration.kpiActive")}</div>
            <div className="pt-list-kpi__value">{activeCount}</div>
          </div>
        </div>

        <div className="filter-inline">
          <Input
            value={listKeyword}
            placeholder={t("bizMigration.listFilterPh")}
            onChange={(e) => setListKeyword(e.target.value)}
          />
        </div>

        <div className="pt-list-table-wrap">
          <table className="data-table pt-list-table">
            <thead>
              <tr>
                <th>{t("bizMigration.colName")}</th>
                <th>{t("bizMigration.colPair")}</th>
                <th>{t("bizMigration.colStatus")}</th>
                <th>{t("bizMigration.colBaseline")}</th>
                <th>{t("bizMigration.colCreated")}</th>
                <th>{t("bizMigration.colActions")}</th>
              </tr>
            </thead>
            <tbody>
              {filteredProjects.map((row) => (
                <tr key={row.id}>
                  <td>
                    <div className="pt-list-task-name">{row.name}</div>
                    {row.note ? <div className="muted">{row.note}</div> : null}
                  </td>
                  <td>
                    <div className="pt-list-task-name">
                      {row.old_task?.ne_name || row.old_task_id.slice(0, 8)}
                    </div>
                    <div className="muted">
                      → {row.new_task?.ne_name || row.new_task_id.slice(0, 8)}
                      {row.old_task?.ne_ip || row.new_task?.ne_ip
                        ? ` · ${row.old_task?.ne_ip || "—"} / ${row.new_task?.ne_ip || "—"}`
                        : ""}
                    </div>
                  </td>
                  <td>
                    <NmStatusChip color={jobChipColor(row.status)}>{statusLabel(row.status)}</NmStatusChip>
                  </td>
                  <td>
                    {row.old_baseline_batch_id ? (
                      <NmStatusChip color="success">{t("bizMigration.baselineSet")}</NmStatusChip>
                    ) : (
                      <NmStatusChip color="warning">{t("bizMigration.baselineUnset")}</NmStatusChip>
                    )}
                  </td>
                  <td className="pt-list-time">{fmtTime(row.created_at)}</td>
                  <td>
                    <div className="pt-list-actions">
                      <Button size="sm" variant="primary" onPress={() => openProject(row.id, "setup")}>
                        {t("bizMigration.detail")}
                      </Button>
                      <Button size="sm" variant="ghost" onPress={() => openProject(row.id, "batches")}>
                        {t("bizMigration.batches")}
                      </Button>
                      <Button size="sm" variant="secondary" onPress={() => openProject(row.id, "board")}>
                        {t("bizMigration.board")}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!filteredProjects.length ? (
                <tr>
                  <td colSpan={6}>
                    <div className="pt-list-empty">{t("bizMigration.empty")}</div>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create task */}
      <AppModalShell open={createOpen} onClose={closeCreate} size="lg">
        <Modal.Header>
          <Modal.Heading>{t("bizMigration.create")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-4">
          <p className="muted" style={{ margin: 0 }}>
            {t("bizMigration.createHint")}
          </p>

          <div className="flex flex-col gap-2">
            <strong style={{ fontSize: 13 }}>{t("bizMigration.sectionName")}</strong>
            <Input
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder={t("bizMigration.projectNamePh")}
              aria-label={t("bizMigration.projectName")}
            />
          </div>

          <div className="flex flex-col gap-2">
            <strong style={{ fontSize: 13 }}>{t("bizMigration.sectionTasks")}</strong>
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              {t("bizMigration.sectionTasksHint")}
            </p>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
                alignItems: "start",
              }}
            >
              <FieldSelect
                label={t("bizMigration.oldTask")}
                value={createOldTaskId}
                onChange={(e) => {
                  setCreateOldTaskId(e.target.value);
                  setCreateOldBaselineId("");
                }}
                fullWidth
              >
                {taskOptions}
              </FieldSelect>
              <FieldSelect
                label={t("bizMigration.newTask")}
                value={createNewTaskId}
                onChange={(e) => {
                  setCreateNewTaskId(e.target.value);
                  setCreateNewBaselineId("");
                }}
                fullWidth
              >
                {taskOptions}
              </FieldSelect>
            </div>
            {!tasks.length ? (
              <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                {t("bizMigration.needBizStateTasks")}{" "}
                <Link to="/network/tasks/biz-state" onClick={closeCreate}>
                  {t("bizMigration.openBizState")}
                </Link>
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <strong style={{ fontSize: 13 }}>{t("bizMigration.sectionOptional")}</strong>
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              {t("bizMigration.sectionOptionalHint")}
            </p>
            <FieldSelect
              label={t("bizMigration.portMapping")}
              value={createMappingId}
              onChange={(e) => setCreateMappingId(e.target.value)}
              fullWidth
              hint={t("bizMigration.portMappingHint")}
            >
              <option value="">{t("bizMigration.optionalNone")}</option>
              {mappings.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </FieldSelect>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
                alignItems: "start",
              }}
            >
              <FieldSelect
                label={t("bizMigration.oldBaseline")}
                value={createOldBaselineId}
                onChange={(e) => setCreateOldBaselineId(e.target.value)}
                fullWidth
                disabled={!createOldTaskId}
                hint={
                  createOldTaskId
                    ? t("bizMigration.baselinePickHint")
                    : t("bizMigration.baselineNeedTask")
                }
              >
                {batchOptions(createOldBatches)}
              </FieldSelect>
              <FieldSelect
                label={t("bizMigration.newBaseline")}
                value={createNewBaselineId}
                onChange={(e) => setCreateNewBaselineId(e.target.value)}
                fullWidth
                disabled={!createNewTaskId}
                hint={
                  createNewTaskId
                    ? t("bizMigration.baselinePickHint")
                    : t("bizMigration.baselineNeedTask")
                }
              >
                {batchOptions(createNewBatches)}
              </FieldSelect>
            </div>
          </div>
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="secondary" onPress={closeCreate}>
            {t("bizMigration.cancel")}
          </Button>
          <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onCreateProject()}>
            {t("bizMigration.create")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Project detail */}
      <AppModalShell open={Boolean(projectId && project)} onClose={closeProject} size="cover">
        <Modal.Header>
          <Modal.Heading>
            {project?.name || t("bizMigration.detail")}
            {project ? (
              <span style={{ marginLeft: 10 }}>
                <NmStatusChip color={jobChipColor(project.status)}>{statusLabel(project.status)}</NmStatusChip>
              </span>
            ) : null}
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {project ? (
            <>
              <div className="btn-row" style={{ flexWrap: "wrap", gap: 8 }}>
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
                <Link to="/network/tasks/biz-state" style={{ fontSize: 12, alignSelf: "center" }}>
                  {t("bizMigration.openBizState")}
                </Link>
                {openRedCount > 0 ? (
                  <span className="form-error" style={{ marginLeft: "auto", fontSize: 12 }}>
                    {t("bizMigration.openRedHint", { n: String(openRedCount) })}
                  </span>
                ) : null}
              </div>
              <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                {t("bizMigration.boundTasks")}:{" "}
                {project.old_task?.ne_name || project.old_task_id.slice(0, 8)}
                {project.old_task?.note ? ` (${project.old_task.note})` : ""} /{" "}
                {project.new_task?.ne_name || project.new_task_id.slice(0, 8)}
                {project.new_task?.note ? ` (${project.new_task.note})` : ""}
                {project.old_task?.interval_sec ? ` · ${project.old_task.interval_sec}s` : ""}
              </p>
              <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                {t("bizMigration.highfreqHint")}
              </p>

              <div className="btn-row nm-config-modal__tabs">
                <Button
                  size="sm"
                  variant={detailTab === "setup" ? "primary" : "secondary"}
                  onPress={() => setDetailTab("setup")}
                >
                  {t("bizMigration.tabSetup")}
                </Button>
                <Button
                  size="sm"
                  variant={detailTab === "batches" ? "primary" : "secondary"}
                  onPress={() => setDetailTab("batches")}
                >
                  {t("bizMigration.tabBatches")}
                </Button>
                <Button
                  size="sm"
                  variant={detailTab === "board" ? "primary" : "secondary"}
                  onPress={() => setDetailTab("board")}
                >
                  {t("bizMigration.tabBoard")}
                </Button>
              </div>

              {detailTab === "setup" ? (
                <div className="flex flex-col gap-4">
                  <div className="flex flex-col gap-2">
                    <strong style={{ fontSize: 13 }}>{t("bizMigration.sectionPair")}</strong>
                    <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                      {t("bizMigration.sectionPairHint")}
                    </p>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: 12,
                      }}
                    >
                      <div>
                        <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                          {t("bizMigration.oldTask")}
                        </div>
                        <div className="pt-list-task-name">
                          {project.old_task?.ne_name || project.old_task_id.slice(0, 8)}
                        </div>
                        <div className="muted" style={{ fontSize: 12 }}>
                          {project.old_task?.ne_ip || "—"}
                          {project.old_task?.note ? ` · ${project.old_task.note}` : ""}
                        </div>
                      </div>
                      <div>
                        <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                          {t("bizMigration.newTask")}
                        </div>
                        <div className="pt-list-task-name">
                          {project.new_task?.ne_name || project.new_task_id.slice(0, 8)}
                        </div>
                        <div className="muted" style={{ fontSize: 12 }}>
                          {project.new_task?.ne_ip || "—"}
                          {project.new_task?.note ? ` · ${project.new_task.note}` : ""}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col gap-2">
                    <strong style={{ fontSize: 13 }}>{t("bizMigration.portMapping")}</strong>
                    <FieldSelect
                      value={mappingId}
                      onChange={(e) => setMappingId(e.target.value)}
                      fullWidth
                      hint={t("bizMigration.portMappingHint")}
                    >
                      <option value="">{t("bizMigration.optionalNone")}</option>
                      {mappings.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name}
                        </option>
                      ))}
                    </FieldSelect>
                  </div>

                  <div className="flex flex-col gap-2">
                    <strong style={{ fontSize: 13 }}>{t("bizMigration.sectionBaseline")}</strong>
                    <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                      {t("bizMigration.sectionBaselineHint")}
                    </p>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: 12,
                        alignItems: "start",
                      }}
                    >
                      <FieldSelect
                        label={t("bizMigration.oldBaseline")}
                        value={oldBaselineId}
                        onChange={(e) => setOldBaselineId(e.target.value)}
                        fullWidth
                        hint={t("bizMigration.baselinePickHint")}
                      >
                        {batchOptions(oldBatches)}
                      </FieldSelect>
                      <FieldSelect
                        label={t("bizMigration.newBaseline")}
                        value={newBaselineId}
                        onChange={(e) => setNewBaselineId(e.target.value)}
                        fullWidth
                        hint={t("bizMigration.baselinePickHint")}
                      >
                        {batchOptions(newBatches)}
                      </FieldSelect>
                    </div>
                  </div>

                  <div className="btn-row">
                    <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onSaveBaseline()}>
                      {t("bizMigration.saveBaseline")}
                    </Button>
                  </div>
                </div>
              ) : null}

              {detailTab === "batches" ? (
                <div className="flex flex-col gap-3">
                  <div>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>
                      {t("bizMigration.pickExpectPorts")} ({selectedPorts.size})
                    </div>
                    <div className="filter-inline" style={{ marginBottom: 8 }}>
                      <Input
                        size="sm"
                        value={portFilter}
                        onChange={(e) => setPortFilter(e.target.value)}
                        placeholder={t("bizMigration.portFilterPh")}
                      />
                    </div>
                    {!project.old_baseline_batch_id ? (
                      <div className="form-error" style={{ marginBottom: 6, fontSize: 12 }}>
                        {t("bizMigration.needBaselineFirst")}
                      </div>
                    ) : null}
                    <div className="pt-list-table-wrap" style={{ maxHeight: 260, overflow: "auto" }}>
                      <table className="data-table pt-list-table">
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
                            <tr
                              key={p.interface}
                              onClick={() => togglePort(p.interface)}
                              style={{ cursor: "pointer" }}
                            >
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
                              <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                                {p.description || ""}
                              </td>
                            </tr>
                          ))}
                          {!filteredBaselinePorts.length ? (
                            <tr>
                              <td colSpan={5}>
                                <div className="pt-list-empty">{t("bizMigration.emptyBaselinePorts")}</div>
                              </td>
                            </tr>
                          ) : null}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div className="btn-row" style={{ flexWrap: "wrap", gap: 8, alignItems: "end" }}>
                    <Input
                      value={batchLabel}
                      onChange={(e) => setBatchLabel(e.target.value)}
                      size="sm"
                      placeholder={t("bizMigration.defaultBatchLabel")}
                      aria-label={t("bizMigration.batchLabel")}
                      style={{ minWidth: 160 }}
                    />
                    <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onCreateBatch()}>
                      {t("bizMigration.createBatch")}
                    </Button>
                    <FieldSelect
                      label={t("bizMigration.batches")}
                      value={batchId}
                      onChange={(e) => setBatchId(e.target.value)}
                      aria-label={t("bizMigration.batches")}
                    >
                      {batches.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.batch_label} ({statusLabel(b.status)})
                        </option>
                      ))}
                    </FieldSelect>
                    {batch ? (
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
                    ) : null}
                  </div>
                </div>
              ) : null}

              {detailTab === "board" ? (
                <div className="flex flex-col gap-3">
                  <div className="btn-row" style={{ flexWrap: "wrap", gap: 8, alignItems: "end" }}>
                    <FieldSelect
                      label={t("bizMigration.batches")}
                      value={batchId}
                      onChange={(e) => setBatchId(e.target.value)}
                      aria-label={t("bizMigration.batches")}
                    >
                      {batches.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.batch_label} ({statusLabel(b.status)})
                        </option>
                      ))}
                    </FieldSelect>
                    <Button size="sm" variant="primary" isDisabled={busy || !batchId} onPress={() => void onEvaluate()}>
                      {t("bizMigration.evaluate")}
                    </Button>
                    <label className="config-sync-policy-check">
                      <input
                        type="checkbox"
                        checked={onlyExpect}
                        onChange={(e) => setOnlyExpect(e.target.checked)}
                      />
                      <span>{t("bizMigration.onlyExpect")}</span>
                    </label>
                  </div>

                  {acceptInfo ? (
                    <div className="flex flex-col gap-2">
                      <strong>{t("bizMigration.acceptTitle")}</strong>
                      <div
                        className="pt-list-kpis"
                        style={{ display: "flex", gap: 16, flexWrap: "wrap", fontVariantNumeric: "tabular-nums" }}
                      >
                        <div style={{ color: acceptInfo.passed ? COLOR.green : COLOR.red, fontWeight: 600 }}>
                          {acceptInfo.passed
                            ? t("bizMigration.acceptPassedShort")
                            : t("bizMigration.acceptFailedShort")}
                        </div>
                        <div>
                          {t("bizMigration.progress")}: {acceptInfo.progress_ok ?? 0}/
                          {acceptInfo.progress_total ?? 0}
                        </div>
                        <div style={{ color: COLOR.red }}>
                          {t("bizMigration.anomaly")}: {acceptInfo.anomaly ?? 0}
                        </div>
                      </div>
                      <div className="muted" style={{ fontSize: 12 }}>
                        {t("bizMigration.acceptCarryHint")}
                      </div>
                    </div>
                  ) : null}

                  {redTickets.length > 0 ? (
                    <div className="flex flex-col gap-2">
                      <strong>
                        {t("bizMigration.redTitle")} ({openRedCount} {t("bizMigration.redOpen")})
                      </strong>
                      <div className="pt-list-table-wrap">
                        <table className="data-table pt-list-table">
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
                                  {r.status !== "resolved" ? (
                                    <Button
                                      size="sm"
                                      variant="secondary"
                                      onPress={() => void onResolveTicket(r.id)}
                                    >
                                      {t("bizMigration.resolveRed")}
                                    </Button>
                                  ) : null}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : null}

                  {board?.run ? (
                    <div className="flex flex-col gap-2">
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
                        {sheetCards[0] ? (
                          <div className="muted" style={{ fontSize: 12 }}>
                            {sheetCards[0].title || sheetCards[0].metric_id}
                          </div>
                        ) : null}
                      </div>
                      <div className="pt-list-table-wrap">
                        <table className="data-table pt-list-table">
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
                            {!visibleDiffs.length ? (
                              <tr>
                                <td colSpan={5}>
                                  <div className="pt-list-empty">{t("bizMigration.emptyDiffs")}</div>
                                </td>
                              </tr>
                            ) : null}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : (
                    <div className="pt-list-empty">{t("bizMigration.emptyDiffs")}</div>
                  )}
                </div>
              ) : null}
            </>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="secondary" onPress={closeProject}>
            {t("bizMigration.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
