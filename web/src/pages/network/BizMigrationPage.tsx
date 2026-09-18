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
  bizCompareCreateMapping,
  bizCompareUpdateMapping,
  bizMigrationCollectNow,
  bizMigrationCreateBatch,
  bizMigrationCreateProject,
  bizMigrationEnsureHighfreq,
  bizMigrationEvaluate,
  bizMigrationFinishBatch,
  bizMigrationGetBoard,
  bizMigrationListBaselineExpect,
  bizMigrationListBatches,
  bizMigrationListDiffs,
  bizMigrationListProjects,
  bizMigrationListRedTickets,
  bizMigrationListRuns,
  bizMigrationPatchBatch,
  bizMigrationPatchProject,
  bizMigrationResolveRedTicket,
  bizMonitorListTemplates,
  bizStateListBatches,
  bizStateListTasks,
  ApiRequestError,
  formatErr,
} from "../../services/api";
import { formatSystemTime } from "../../utils/time";
import { jobChipColor, NmStatusChip } from "./nmChips";

type TaskOpt = { id: string; ne_name: string; ne_ip: string; note?: string; interval_sec?: number };
type PortMapping = {
  id: string;
  name: string;
  rows: Array<{ before_if: string; after_if: string }>;
};
type MonitorTplOpt = {
  id: string;
  name: string;
  compare_template_id?: string;
  compare_template_name?: string;
  collect_metric_ids?: string[];
};
type Project = {
  id: string;
  name: string;
  old_task_id: string;
  new_task_id: string;
  old_baseline_batch_id: string;
  new_baseline_batch_id: string;
  mapping_id: string;
  monitor_template_id?: string;
  monitor_template?: MonitorTplOpt;
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
  expect_set?: { ports?: string[]; items?: Array<{ metric_id: string; key?: string; keys?: string[] }> };
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
  new_baseline_missing?: boolean;
  new_baseline_mode?: string;
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
  rule_hit?: string;
};
type EvalRun = {
  id: string;
  created_at?: string | null;
  purpose?: string;
  old_batch_id?: string;
  new_batch_id?: string;
  summary?: { anomaly?: number; progress?: { ok?: number; total?: number } };
};
type ExpectSheetItem = {
  key: string;
  keys: string[];
  mapped_to?: string;
  label?: string;
  row?: Record<string, unknown>;
};
type ExpectSheet = {
  metric_id: string;
  key_fields: string[];
  iface_fields: string[];
  items: ExpectSheetItem[];
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
  const [mappings, setMappings] = useState<PortMapping[]>([]);
  const [busy, setBusy] = useState(false);
  const [listKeyword, setListKeyword] = useState("");
  const debouncedListKw = useDebouncedValue(listKeyword, 250);

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createOldTaskId, setCreateOldTaskId] = useState("");
  const [createNewTaskId, setCreateNewTaskId] = useState("");
  const [createMonitorTplId, setCreateMonitorTplId] = useState("");
  const [createOldBaselineId, setCreateOldBaselineId] = useState("");
  const [createNewBaselineId, setCreateNewBaselineId] = useState("");
  const [createOldBatches, setCreateOldBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [createNewBatches, setCreateNewBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [createShowMore, setCreateShowMore] = useState(false);
  const [monitorTpls, setMonitorTpls] = useState<MonitorTplOpt[]>([]);
  const [boardSubTab, setBoardSubTab] = useState<"diffs" | "red">("diffs");

  const [projectId, setProjectId] = useState("");
  const [detailTab, setDetailTab] = useState<DetailTab>("setup");
  const [mappingId, setMappingId] = useState("");
  const [mapName, setMapName] = useState("");
  const [mapText, setMapText] = useState("");
  const [monitorTplId, setMonitorTplId] = useState("");
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
  const [onlyExpect, setOnlyExpect] = useState(false);
  const [boardMetricId, setBoardMetricId] = useState("");
  const [batchLabel, setBatchLabel] = useState("");
  const [expectSheets, setExpectSheets] = useState<ExpectSheet[]>([]);
  const [expectMetricId, setExpectMetricId] = useState("");
  const [selectedExpectKeys, setSelectedExpectKeys] = useState<Set<string>>(new Set());
  const [portFilter, setPortFilter] = useState("");
  const [redTickets, setRedTickets] = useState<RedTicket[]>([]);
  const [openRedCount, setOpenRedCount] = useState(0);
  const [acceptInfo, setAcceptInfo] = useState<MigBatch["accept_summary"] | null>(null);
  const [pinOldBatchId, setPinOldBatchId] = useState("");
  const [pinNewBatchId, setPinNewBatchId] = useState("");
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");

  const project = useMemo(
    () => projects.find((p) => p.id === projectId) || null,
    [projects, projectId],
  );
  const batch = useMemo(() => batches.find((b) => b.id === batchId) || null, [batches, batchId]);
  const sheetCards = board?.run?.summary?.sheet_cards || [];
  const visibleDiffs = useMemo(() => {
    let rows = diffs;
    if (boardMetricId) rows = rows.filter((d) => d.metric_id === boardMetricId);
    if (onlyExpect) rows = rows.filter((d) => d.in_expect);
    return rows;
  }, [diffs, onlyExpect, boardMetricId]);

  const boardMetricOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const c of sheetCards) {
      if (c.metric_id) ids.add(c.metric_id);
    }
    for (const d of diffs) {
      if (d.metric_id) ids.add(d.metric_id);
    }
    return [...ids];
  }, [sheetCards, diffs]);

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

  const filteredExpectItems = useMemo(() => {
    const sheet = expectSheets.find((s) => s.metric_id === expectMetricId) || expectSheets[0];
    const items = sheet?.items || [];
    const kw = portFilter.trim().toLowerCase();
    if (!kw) return items;
    return items.filter((it) => {
      const blob = `${it.key} ${it.label || ""} ${JSON.stringify(it.row || {})}`.toLowerCase();
      return blob.includes(kw);
    });
  }, [expectSheets, expectMetricId, portFilter]);

  const reloadProjects = useCallback(async () => {
    const res = await bizMigrationListProjects();
    setProjects((res.items || []) as Project[]);
  }, []);

  const reloadMappings = useCallback(async () => {
    const mp = await bizCompareListMappings();
    setMappings(
      ((mp.items || []) as Record<string, unknown>[]).map((x) => ({
        id: String(x.id || ""),
        name: String(x.name || x.id || ""),
        rows: Array.isArray(x.rows)
          ? (x.rows as Array<{ before_if?: string; after_if?: string }>).map((r) => ({
              before_if: String(r.before_if || ""),
              after_if: String(r.after_if || ""),
            }))
          : [],
      })),
    );
  }, []);

  const parseMapRows = () => {
    const rows: { before_if: string; after_if: string }[] = [];
    for (const line of mapText.split(/\r?\n/)) {
      const s = line.trim();
      if (!s || s.startsWith("#") || s.toLowerCase().startsWith("old") || s.toLowerCase().startsWith("before")) {
        continue;
      }
      const parts = s.split(/[,|\t]+/).map((x) => x.trim());
      if (parts.length >= 2 && parts[0] && parts[1]) {
        rows.push({ before_if: parts[0], after_if: parts[1] });
      }
    }
    return rows;
  };

  const loadMappingText = (id: string, list?: PortMapping[]) => {
    const m = (list || mappings).find((x) => x.id === id);
    if (!m) {
      setMappingId(id);
      return;
    }
    setMappingId(id);
    setMapName(m.name);
    setMapText(m.rows.map((r) => `${r.before_if},${r.after_if}`).join("\n"));
  };

  const resetMappingEditor = () => {
    setMappingId("");
    setMapName(t("bizCompare.mapping"));
    setMapText("");
  };

  const saveMapping = async (): Promise<string> => {
    const rows = parseMapRows();
    if (mappingId) {
      await bizCompareUpdateMapping(mappingId, { name: mapName.trim() || t("bizCompare.mapping"), rows });
      await reloadMappings();
      return mappingId;
    }
    const m = await bizCompareCreateMapping({
      name: mapName.trim() || t("bizCompare.mapping"),
      rows,
    });
    const id = String(m.id || "");
    setMappingId(id);
    await reloadMappings();
    return id;
  };

  const loadBaselineExpect = useCallback(async (pid: string) => {
    if (!pid) {
      setExpectSheets([]);
      return;
    }
    try {
      const res = await bizMigrationListBaselineExpect(pid);
      const sheets = (res.sheets || []) as ExpectSheet[];
      setExpectSheets(sheets);
      setExpectMetricId((prev) => {
        if (prev && sheets.some((s) => s.metric_id === prev)) return prev;
        return sheets[0]?.metric_id || "";
      });
    } catch {
      setExpectSheets([]);
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
        const [pr, tk, mp, mt] = await Promise.all([
          bizMigrationListProjects(),
          bizStateListTasks(),
          bizCompareListMappings(),
          bizMonitorListTemplates(),
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
            rows: Array.isArray(x.rows)
              ? (x.rows as Array<{ before_if?: string; after_if?: string }>).map((r) => ({
                  before_if: String(r.before_if || ""),
                  after_if: String(r.after_if || ""),
                }))
              : [],
          })),
        );
        const mts = ((mt.items || []) as Record<string, unknown>[]).map((x) => ({
          id: String(x.id || ""),
          name: String(x.name || x.id || ""),
          compare_template_id: String(x.compare_template_id || ""),
          compare_template_name: String(x.compare_template_name || ""),
          collect_metric_ids: Array.isArray(x.collect_metric_ids)
            ? (x.collect_metric_ids as string[])
            : [],
        }));
        setMonitorTpls(mts);
        if (!createMonitorTplId && mts.length) {
          const port = mts.find((x) => x.name.includes("端口") || x.name.toLowerCase().includes("port"));
          setCreateMonitorTplId(port?.id || mts[0].id);
        }
      } catch (e) {
        showError(formatErr(e));
      }
    })();
  }, [showError]);

  useEffect(() => {
    if (!projectId) {
      setBatches([]);
      setBatchId("");
      setExpectSheets([]);
      setExpectMetricId("");
      setSelectedExpectKeys(new Set());
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
        await loadBaselineExpect(projectId);
        await loadRedTickets(projectId);
      } catch (e) {
        showError(formatErr(e));
      }
    })();
  }, [projectId, showError, loadBaselineExpect, loadRedTickets]);

  useEffect(() => {
    if (!batchId) {
      setBoard(null);
      setDiffs([]);
      setEvalRuns([]);
      setSelectedRunId("");
      return;
    }
    void loadRuns(batchId);
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
    if (!project) return;
    setMonitorTplId(project.monitor_template_id || project.monitor_template?.id || "");
    setOldBaselineId(project.old_baseline_batch_id || "");
    setNewBaselineId(project.new_baseline_batch_id || "");
    if (project.mapping_id) loadMappingText(project.mapping_id);
    else resetMappingEditor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, project?.mapping_id, mappings.length]);

  async function loadRuns(bid: string) {
    try {
      const res = await bizMigrationListRuns(bid, 20);
      const items = (res.items || []) as EvalRun[];
      setEvalRuns(items);
      setSelectedRunId((prev) => {
        if (prev && items.some((r) => r.id === prev)) return prev;
        return items[0]?.id || "";
      });
    } catch {
      setEvalRuns([]);
    }
  }

  async function loadBoard(bid: string, runId = "") {
    try {
      const b = await bizMigrationGetBoard(bid, runId);
      setBoard(b as typeof board);
      const rid = String((b as { run?: { id?: string } })?.run?.id || runId || "");
      if (rid) setSelectedRunId(rid);
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
    setCreateOldBaselineId("");
    setCreateNewBaselineId("");
    setCreateShowMore(false);
    resetMappingEditor();
    const port = monitorTpls.find((x) => x.name.includes("端口") || x.name.toLowerCase().includes("port"));
    setCreateMonitorTplId(port?.id || monitorTpls[0]?.id || "");
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
    setSelectedExpectKeys(new Set());
    setPortFilter("");
    setBatchLabel("");
    setOnlyExpect(true);
    setBoardSubTab("diffs");
    setBoardMetricId("");
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
      let mid = mappingId;
      if (parseMapRows().length) {
        mid = await saveMapping();
      }
      const p = (await bizMigrationCreateProject({
        name: createName.trim(),
        old_task_id: createOldTaskId,
        new_task_id: createNewTaskId,
        mapping_id: mid || "",
        monitor_template_id: createMonitorTplId || "",
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

  async function onSaveMappingOnly() {
    if (!projectId) return;
    setBusy(true);
    try {
      const mid = await saveMapping();
      if (mid) {
        await bizMigrationPatchProject(projectId, { mapping_id: mid });
        await reloadProjects();
      }
      showOk(t("bizMigration.mappingBound"));
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
      let mid = mappingId;
      if (mappingId || parseMapRows().length) {
        mid = await saveMapping();
      } else {
        mid = "";
      }
      await bizMigrationPatchProject(projectId, {
        old_baseline_batch_id: oldBaselineId || project?.old_baseline_batch_id || "",
        new_baseline_batch_id: newBaselineId || project?.new_baseline_batch_id || "",
        mapping_id: mid || "",
        monitor_template_id: monitorTplId || project?.monitor_template_id || "",
      });
      await reloadProjects();
      await loadBaselineExpect(projectId);
      showOk(t("bizMigration.baselineSaved"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateBatch() {
    if (!projectId) return;
    // One item per selected key. A flat `keys: [a, b]` is parsed as one composite key.
    const items: Array<{ metric_id: string; key: string }> = [];
    const ports: string[] = [];
    for (const sheet of expectSheets) {
      const keys = sheet.items.map((it) => it.key).filter((k) => selectedExpectKeys.has(k));
      for (const key of keys) items.push({ metric_id: sheet.metric_id, key });
      if (sheet.metric_id === "interface_brief") ports.push(...keys);
    }
    if (!items.length && !ports.length) {
      showError(t("bizMigration.needExpectPorts"));
      return;
    }
    setBusy(true);
    try {
      const b = (await bizMigrationCreateBatch(projectId, {
        batch_label: batchLabel.trim() || t("bizMigration.defaultBatchLabel"),
        expect_set: { ports, items },
        status: "pending",
      })) as MigBatch;
      const res = await bizMigrationListBatches(projectId);
      setBatches((res.items || []) as MigBatch[]);
      setBatchId(b.id);
      setBatchLabel("");
      setSelectedExpectKeys(new Set());
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
    if (!project?.old_baseline_batch_id || !project?.new_baseline_batch_id) {
      showError(t("bizMigration.needBothBaselines"));
      return;
    }
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      if (pinOldBatchId) body.old_batch_id = pinOldBatchId;
      if (pinNewBatchId) body.new_batch_id = pinNewBatchId;
      const run = await bizMigrationEvaluate(batchId, body);
      const rid = String((run as { id?: string }).id || "");
      await loadRuns(batchId);
      await loadBoard(batchId, rid);
      showOk(t("bizMigration.evaluated"));
      setDetailTab("board");
    } catch (e) {
      const detail = e instanceof ApiRequestError ? String(e.detail || "") : "";
      if (detail === "old_baseline_required") showError(t("bizMigration.errOldBaselineRequired"));
      else if (detail === "new_baseline_required") showError(t("bizMigration.errNewBaselineRequired"));
      else showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onEnsureHighfreq() {
    if (!projectId) return;
    setBusy(true);
    try {
      await bizMigrationEnsureHighfreq(projectId, {
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

  function toggleExpectKey(key: string) {
    setSelectedExpectKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const selectedMonitorTpl = useMemo(() => {
    const id = monitorTplId || project?.monitor_template_id || "";
    return monitorTpls.find((x) => x.id === id) || project?.monitor_template || null;
  }, [monitorTplId, monitorTpls, project]);

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
      <p className="panel__hint muted">
        {t("bizMigration.hintShort")}{" "}
        <Link to="/network/cutover/monitor-templates">{t("network.nav.monitorTemplates")}</Link>
        {" · "}
        <Link to="/network/cutover/compare-templates">{t("network.nav.compareTemplates")}</Link>
      </p>

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
                    {row.old_baseline_batch_id && row.new_baseline_batch_id ? (
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
        <Modal.Body className="flex flex-col gap-3 bm-create">
          <p className="muted bm-hint">{t("bizMigration.createHintShort")}</p>
          <Input
            value={createName}
            onChange={(e) => setCreateName(e.target.value)}
            placeholder={t("bizMigration.projectNamePh")}
            aria-label={t("bizMigration.projectName")}
          />
          <div className="bm-pair-grid">
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
            <p className="muted bm-hint">
              {t("bizMigration.needBizStateTasks")}{" "}
              <Link to="/network/cutover/biz-state" onClick={closeCreate}>
                {t("bizMigration.openBizState")}
              </Link>
            </p>
          ) : null}

          <Button size="sm" variant="ghost" onPress={() => setCreateShowMore((v) => !v)}>
            {createShowMore ? t("bizMigration.hideCreateMore") : t("bizMigration.showCreateMore")}
          </Button>
          {createShowMore ? (
            <div className="flex flex-col gap-3">
              <FieldSelect
                label={t("bizMigration.monitorTemplate")}
                value={createMonitorTplId}
                onChange={(e) => setCreateMonitorTplId(e.target.value)}
                fullWidth
              >
                <option value="">{t("bizMigration.optionalNone")}</option>
                {monitorTpls.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </FieldSelect>
              <div className="bm-mapping">
                <div className="bm-mapping__label">{t("bizMigration.portMapping")}</div>
                <p className="muted bm-hint">{t("bizCompare.mappingOptionalHint")}</p>
                <div className="bm-mapping__row">
                  <FieldSelect
                    value={mappingId}
                    onChange={(e) => {
                      const id = e.target.value;
                      if (id) loadMappingText(id);
                      else resetMappingEditor();
                    }}
                    fullWidth
                  >
                    <option value="">{t("bizCompare.newMapping")}</option>
                    {mappings.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </FieldSelect>
                  <Input
                    value={mapName}
                    placeholder={t("bizCompare.mapName")}
                    onChange={(e) => setMapName(e.target.value)}
                    aria-label={t("bizCompare.mapName")}
                  />
                  <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void onSaveMappingOnly()}>
                    {t("bizCompare.saveMapping")}
                  </Button>
                </div>
                <textarea
                  className="bm-mapping__text"
                  value={mapText}
                  onChange={(e) => setMapText(e.target.value)}
                  placeholder={t("bizCompare.mapHint")}
                  rows={5}
                />
              </div>
              <div className="bm-pair-grid">
                <FieldSelect
                  label={t("bizMigration.oldBaseline")}
                  value={createOldBaselineId}
                  onChange={(e) => setCreateOldBaselineId(e.target.value)}
                  fullWidth
                  disabled={!createOldTaskId}
                >
                  {batchOptions(createOldBatches)}
                </FieldSelect>
                <FieldSelect
                  label={t("bizMigration.newBaseline")}
                  value={createNewBaselineId}
                  onChange={(e) => setCreateNewBaselineId(e.target.value)}
                  fullWidth
                  disabled={!createNewTaskId}
                >
                  {batchOptions(createNewBatches)}
                </FieldSelect>
              </div>
            </div>
          ) : null}
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
      <AppModalShell
        open={Boolean(projectId && project)}
        onClose={closeProject}
        size="cover"
        className={detailTab === "board" ? "bs-cmp-board-modal" : undefined}
      >
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
        <Modal.Body className="flex flex-col gap-3 bm-detail">
          {project ? (
            <>
              <div className="bm-detail__chrome">
                <div className="bm-detail__meta muted">
                  <span>
                    {project.old_task?.ne_name || project.old_task_id.slice(0, 8)}
                    {project.old_task?.ne_ip ? ` (${project.old_task.ne_ip})` : ""}
                  </span>
                  <span className="bm-detail__arrow">→</span>
                  <span>
                    {project.new_task?.ne_name || project.new_task_id.slice(0, 8)}
                    {project.new_task?.ne_ip ? ` (${project.new_task.ne_ip})` : ""}
                  </span>
                  {openRedCount > 0 ? (
                    <span className="form-error bm-detail__red">
                      {t("bizMigration.openRedHint", { n: String(openRedCount) })}
                    </span>
                  ) : null}
                </div>
                <div className="btn-row bm-detail__actions">
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={busy}
                    onPress={() => void onEnsureHighfreq()}
                  >
                    {t("bizMigration.enableHighfreq")}
                  </Button>
                  <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void onCollectNow()}>
                    {t("bizMigration.collectNow")}
                  </Button>
                  <Link to="/network/cutover/biz-state" className="bm-detail__link">
                    {t("bizMigration.openBizState")}
                  </Link>
                </div>
              </div>

              <div className="btn-row nm-config-modal__tabs" role="tablist">
                <Button
                  size="sm"
                  variant={detailTab === "setup" ? "primary" : "secondary"}
                  className={detailTab === "setup" ? "is-active" : undefined}
                  onPress={() => setDetailTab("setup")}
                >
                  {t("bizMigration.tabSetup")}
                </Button>
                <Button
                  size="sm"
                  variant={detailTab === "batches" ? "primary" : "secondary"}
                  className={detailTab === "batches" ? "is-active" : undefined}
                  onPress={() => setDetailTab("batches")}
                >
                  {t("bizMigration.tabBatches")}
                </Button>
                <Button
                  size="sm"
                  variant={detailTab === "board" ? "primary" : "secondary"}
                  className={detailTab === "board" ? "is-active" : undefined}
                  onPress={() => setDetailTab("board")}
                >
                  {t("bizMigration.tabBoard")}
                </Button>
              </div>

              {detailTab === "setup" ? (
                <div className="bm-setup flex flex-col gap-3">
                  <p className="muted bm-hint">{t("bizMigration.setupHintShort")}</p>
                  <div className="bm-pair-grid">
                    <div className="bm-pair-card">
                      <div className="bm-pair-card__label">{t("bizMigration.oldTask")}</div>
                      <div className="pt-list-task-name">
                        {project.old_task?.ne_name || project.old_task_id.slice(0, 8)}
                      </div>
                      <div className="muted bm-hint">{project.old_task?.ne_ip || "—"}</div>
                    </div>
                    <div className="bm-pair-card">
                      <div className="bm-pair-card__label">{t("bizMigration.newTask")}</div>
                      <div className="pt-list-task-name">
                        {project.new_task?.ne_name || project.new_task_id.slice(0, 8)}
                      </div>
                      <div className="muted bm-hint">{project.new_task?.ne_ip || "—"}</div>
                    </div>
                  </div>
                  <div className="bm-pair-grid">
                    <FieldSelect
                      label={t("bizMigration.monitorTemplate")}
                      value={monitorTplId}
                      onChange={(e) => setMonitorTplId(e.target.value)}
                      fullWidth
                    >
                      <option value="">{t("bizMigration.optionalNone")}</option>
                      {monitorTpls.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name}
                        </option>
                      ))}
                    </FieldSelect>
                    <div />
                  </div>
                  {selectedMonitorTpl?.compare_template_name || selectedMonitorTpl?.compare_template_id ? (
                    <p className="muted bm-hint">
                      {t("bizMigration.compareTemplate")}:{" "}
                      <Link to="/network/cutover/compare-templates">
                        {selectedMonitorTpl.compare_template_name ||
                          selectedMonitorTpl.compare_template_id}
                      </Link>
                    </p>
                  ) : null}
                  <div className="bm-mapping">
                    <div className="bm-mapping__label">{t("bizMigration.portMapping")}</div>
                    <p className="muted bm-hint">{t("bizCompare.mappingOptionalHint")}</p>
                    <div className="bm-mapping__row">
                      <FieldSelect
                        value={mappingId}
                        onChange={(e) => {
                          const id = e.target.value;
                          if (id) loadMappingText(id);
                          else resetMappingEditor();
                        }}
                        fullWidth
                      >
                        <option value="">{t("bizCompare.newMapping")}</option>
                        {mappings.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.name}
                          </option>
                        ))}
                      </FieldSelect>
                      <Input
                        value={mapName}
                        placeholder={t("bizCompare.mapName")}
                        onChange={(e) => setMapName(e.target.value)}
                        aria-label={t("bizCompare.mapName")}
                      />
                      <Button
                        size="sm"
                        variant="secondary"
                        isDisabled={busy}
                        onPress={() => void onSaveMappingOnly()}
                      >
                        {t("bizCompare.saveMapping")}
                      </Button>
                    </div>
                    <textarea
                      className="bm-mapping__text"
                      value={mapText}
                      onChange={(e) => setMapText(e.target.value)}
                      placeholder={t("bizCompare.mapHint")}
                      rows={5}
                    />
                  </div>
                  <div className="bm-pair-grid">
                    <FieldSelect
                      label={t("bizMigration.oldBaseline")}
                      value={oldBaselineId}
                      onChange={(e) => setOldBaselineId(e.target.value)}
                      fullWidth
                    >
                      {batchOptions(oldBatches)}
                    </FieldSelect>
                    <FieldSelect
                      label={t("bizMigration.newBaseline")}
                      value={newBaselineId}
                      onChange={(e) => setNewBaselineId(e.target.value)}
                      fullWidth
                    >
                      {batchOptions(newBatches)}
                    </FieldSelect>
                  </div>
                  <div className="btn-row">
                    <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onSaveBaseline()}>
                      {t("bizMigration.saveBaseline")}
                    </Button>
                  </div>
                </div>
              ) : null}

              {detailTab === "batches" ? (
                <div className="bm-batches">
                  <div className="bm-batches__toolbar btn-row">
                    <Input
                      value={batchLabel}
                      onChange={(e) => setBatchLabel(e.target.value)}
                      size="sm"
                      placeholder={t("bizMigration.defaultBatchLabel")}
                      aria-label={t("bizMigration.batchLabel")}
                      style={{ minWidth: 140 }}
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
                        <Button
                          size="sm"
                          variant="primary"
                          isDisabled={
                            busy ||
                            !project?.old_baseline_batch_id ||
                            !project?.new_baseline_batch_id
                          }
                          onPress={() => void onEvaluate()}
                        >
                          {t("bizMigration.evaluate")}
                        </Button>
                      </>
                    ) : null}
                  </div>

                  {!project.old_baseline_batch_id ? (
                    <div className="form-error bm-hint">{t("bizMigration.needBaselineFirst")}</div>
                  ) : null}

                  <div className="ct-editor__main bm-batches__main">
                    <aside className="ct-editor__nav" aria-label={t("bizMigration.pickExpectPorts")}>
                      <div className="ct-editor__nav-head">
                        <span>
                          {t("bizMigration.pickExpectPorts")} ({selectedExpectKeys.size})
                        </span>
                      </div>
                      <div className="ct-editor__nav-list" role="tablist">
                        {(expectSheets.length
                          ? expectSheets
                          : [{ metric_id: "—", items: [] as ExpectSheetItem[], key_fields: [] as string[], iface_fields: [] as string[] }]
                        ).map((s) => {
                          const active =
                            (expectMetricId || expectSheets[0]?.metric_id) === s.metric_id;
                          return (
                            <button
                              key={s.metric_id}
                              type="button"
                              role="tab"
                              aria-selected={active}
                              className={`ct-editor__nav-item${active ? " is-active" : ""}`}
                              onClick={() => setExpectMetricId(s.metric_id)}
                            >
                              <span className="ct-editor__nav-name">{s.metric_id}</span>
                              <span className="ct-editor__nav-tag">{s.items.length}</span>
                            </button>
                          );
                        })}
                      </div>
                    </aside>
                    <div className="ct-editor__pane">
                      <div className="filter-inline" style={{ marginBottom: 8 }}>
                        <Input
                          size="sm"
                          value={portFilter}
                          onChange={(e) => setPortFilter(e.target.value)}
                          placeholder={t("bizMigration.portFilterPh")}
                        />
                      </div>
                      <div className="pt-list-table-wrap bm-expect-table">
                        <table className="data-table pt-list-table">
                          <thead>
                            <tr>
                              <th style={{ width: 36 }} />
                              <th>{t("bizMigration.colKey")}</th>
                              <th>{t("bizMigration.colMapped")}</th>
                              <th>{t("bizMigration.colDesc")}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {filteredExpectItems.map((it) => {
                              const row = it.row || {};
                              const statusBits = ["admin", "phy", "prot"]
                                .map((k) => (row[k] != null ? String(row[k]) : ""))
                                .filter(Boolean);
                              const desc =
                                it.label ||
                                String(row.description || row.desc || row.peer || row.neighbor || "") ||
                                (statusBits.length ? statusBits.join("/") : "");
                              return (
                                <tr
                                  key={it.key}
                                  onClick={() => toggleExpectKey(it.key)}
                                  style={{ cursor: "pointer" }}
                                >
                                  <td>
                                    <input
                                      type="checkbox"
                                      checked={selectedExpectKeys.has(it.key)}
                                      onChange={() => toggleExpectKey(it.key)}
                                    />
                                  </td>
                                  <td>
                                    <code>{it.key}</code>
                                  </td>
                                  <td>{it.mapped_to || "—"}</td>
                                  <td className="bm-expect-desc">{desc || "—"}</td>
                                </tr>
                              );
                            })}
                            {!filteredExpectItems.length ? (
                              <tr>
                                <td colSpan={4}>
                                  <div className="pt-list-empty">{t("bizMigration.emptyBaselinePorts")}</div>
                                </td>
                              </tr>
                            ) : null}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>
              ) : null}

              {detailTab === "board" ? (
                <div className="bs-cmp-board bm-board">
                  <div className="bs-cmp-board__toolbar">
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
                    <FieldSelect
                      label={t("bizMigration.pinOldBatch")}
                      value={pinOldBatchId}
                      onChange={(e) => setPinOldBatchId(e.target.value)}
                      aria-label={t("bizMigration.pinOldBatch")}
                    >
                      <option value="">{t("bizMigration.pinLatest")}</option>
                      {batchOptions(oldBatches)}
                    </FieldSelect>
                    <FieldSelect
                      label={t("bizMigration.pinNewBatch")}
                      value={pinNewBatchId}
                      onChange={(e) => setPinNewBatchId(e.target.value)}
                      aria-label={t("bizMigration.pinNewBatch")}
                    >
                      <option value="">{t("bizMigration.pinLatest")}</option>
                      {batchOptions(newBatches)}
                    </FieldSelect>
                    <FieldSelect
                      label={t("bizMigration.runHistory")}
                      value={selectedRunId}
                      onChange={(e) => {
                        const rid = e.target.value;
                        setSelectedRunId(rid);
                        if (batchId && rid) void loadBoard(batchId, rid);
                      }}
                      aria-label={t("bizMigration.runHistory")}
                    >
                      {!evalRuns.length ? (
                        <option value="">{t("bizMigration.runLatest")}</option>
                      ) : null}
                      {evalRuns.map((r) => (
                        <option key={r.id} value={r.id}>
                          {fmtTime(r.created_at)} · {r.purpose || "manual"}
                          {r.summary?.anomaly != null ? ` · ⚠${r.summary.anomaly}` : ""}
                        </option>
                      ))}
                    </FieldSelect>
                    <div className="bs-cmp-board__actions btn-row">
                      <Button
                        size="sm"
                        variant="primary"
                        isDisabled={
                          busy ||
                          !batchId ||
                          !project?.old_baseline_batch_id ||
                          !project?.new_baseline_batch_id
                        }
                        onPress={() => void onEvaluate()}
                      >
                        {t("bizMigration.evaluate")}
                      </Button>
                      <label className="config-sync-policy-check" title={t("bizMigration.onlyExpectHint")}>
                        <input
                          type="checkbox"
                          checked={onlyExpect}
                          onChange={(e) => setOnlyExpect(e.target.checked)}
                        />
                        <span>{t("bizMigration.onlyExpect")}</span>
                      </label>
                    </div>
                    {acceptInfo ? (
                      <div className="bm-board__accept muted">
                        <span
                          style={{
                            color: acceptInfo.passed ? COLOR.green : COLOR.red,
                            fontWeight: 600,
                          }}
                        >
                          {acceptInfo.passed
                            ? t("bizMigration.acceptPassedShort")
                            : t("bizMigration.acceptFailedShort")}
                        </span>
                        <span>
                          {t("bizMigration.progress")} {acceptInfo.progress_ok ?? 0}/
                          {acceptInfo.progress_total ?? 0}
                        </span>
                        <span style={{ color: COLOR.red }}>
                          {t("bizMigration.anomaly")} {acceptInfo.anomaly ?? 0}
                        </span>
                      </div>
                    ) : null}
                  </div>

                  {sheetCards.some((c) => c.new_baseline_missing) ? (
                    <div className="form-error bm-hint">
                      {t("bizMigration.newBaselineMissingBanner", {
                        metrics: sheetCards
                          .filter((c) => c.new_baseline_missing)
                          .map((c) => c.metric_id)
                          .join(", "),
                      })}
                    </div>
                  ) : null}

                  <div className="mt-rule-tabs" role="tablist">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={boardSubTab === "diffs"}
                      className={`mt-rule-tab${boardSubTab === "diffs" ? " is-active" : ""}`}
                      onClick={() => setBoardSubTab("diffs")}
                    >
                      {t("bizMigration.board")}
                      <span className="mt-rule-tab__n">{visibleDiffs.length}</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={boardSubTab === "red"}
                      className={`mt-rule-tab mt-rule-tab--anomaly${boardSubTab === "red" ? " is-active" : ""}`}
                      onClick={() => setBoardSubTab("red")}
                    >
                      {t("bizMigration.redTitle")}
                      <span className="mt-rule-tab__n">{openRedCount}</span>
                    </button>
                  </div>

                  {boardSubTab === "red" ? (
                    <div className="pt-list-table-wrap bm-board__table">
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
                              <td>
                                <code>{r.key_str}</code>
                              </td>
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
                          {!redTickets.length ? (
                            <tr>
                              <td colSpan={7}>
                                <div className="pt-list-empty">{t("bizMigration.emptyRed")}</div>
                              </td>
                            </tr>
                          ) : null}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="bs-cmp-board__body">
                      <aside className="bs-cmp-nav" aria-label={t("bizMigration.colMetric")}>
                        <div className="bs-cmp-nav__head">
                          <div className="bs-cmp-nav__head-main">
                            <span className="bs-cmp-nav__title">{t("bizMigration.colMetric")}</span>
                            <span className="bs-cmp-nav__count">{sheetCards.length || 1}</span>
                          </div>
                        </div>
                        <div className="bs-cmp-nav__list" role="tablist">
                          <button
                            type="button"
                            role="tab"
                            aria-selected={!boardMetricId}
                            className={`bs-cmp-nav__item${!boardMetricId ? " is-active" : ""}`}
                            onClick={() => setBoardMetricId("")}
                          >
                            <span className="bs-cmp-nav__dot" />
                            <span className="bs-cmp-nav__name">{t("bizMigration.allMetrics")}</span>
                          </button>
                          {sheetCards.map((c) => {
                            const active = boardMetricId === c.metric_id;
                            const hot = (c.anomaly || 0) > 0 || Boolean(c.new_baseline_missing);
                            return (
                              <button
                                key={c.metric_id}
                                type="button"
                                role="tab"
                                aria-selected={active}
                                className={`bs-cmp-nav__item${active ? " is-active" : ""}${
                                  hot ? " has-diff" : " is-clean"
                                }`}
                                onClick={() => setBoardMetricId(c.metric_id)}
                              >
                                <span className="bs-cmp-nav__dot" />
                                <span className="bs-cmp-nav__name">
                                  {c.metric_id}
                                  {c.new_baseline_missing ? " · baseline" : ""}
                                </span>
                                <span className="bs-cmp-nav__stats">
                                  <span className="bs-cmp-nav__num bs-cmp-nav__num--ok">
                                    {c.progress_ok}/{c.progress_total}
                                  </span>
                                  <span
                                    className={`bs-cmp-nav__num bs-cmp-nav__num--fail${hot ? " is-hot" : ""}`}
                                  >
                                    {c.anomaly}
                                  </span>
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </aside>
                      <div className="bs-cmp-main">
                        {board?.run ? (
                          <div className="pt-list-table-wrap bm-board__table">
                            <table className="data-table pt-list-table">
                              <thead>
                                <tr>
                                  <th>{t("bizMigration.colMetric")}</th>
                                  <th>{t("bizMigration.colKey")}</th>
                                  <th>{t("bizMigration.colMapped")}</th>
                                  <th>{t("bizMigration.colOld")}</th>
                                  <th>{t("bizMigration.colNew")}</th>
                                  <th>{t("bizMigration.colVerdict")}</th>
                                  <th>{t("bizMigration.colRuleHit")}</th>
                                </tr>
                              </thead>
                              <tbody>
                                {visibleDiffs.map((d) => (
                                  <tr key={d.id}>
                                    <td>{d.metric_id || "—"}</td>
                                    <td>
                                      <code>{d.key_str || "—"}</code>
                                    </td>
                                    <td>{d.new_key_str || "—"}</td>
                                    <td>{d.old_status || d.old_kind || "—"}</td>
                                    <td>{d.new_status || d.new_kind || "—"}</td>
                                    <td
                                      style={{
                                        color: COLOR[d.color] || COLOR.gray,
                                        fontWeight: 600,
                                      }}
                                    >
                                      {verdictLabel(d.verdict)}
                                    </td>
                                    <td className="muted">
                                      <code style={{ fontSize: 11 }}>{d.rule_hit || "—"}</code>
                                    </td>
                                  </tr>
                                ))}
                                {!visibleDiffs.length ? (
                                  <tr>
                                    <td colSpan={7}>
                                      <div className="pt-list-empty">{t("bizMigration.emptyDiffs")}</div>
                                    </td>
                                  </tr>
                                ) : null}
                              </tbody>
                            </table>
                          </div>
                        ) : (
                          <div className="pt-list-empty">{t("bizMigration.emptyDiffs")}</div>
                        )}
                      </div>
                    </div>
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
