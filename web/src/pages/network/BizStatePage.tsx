import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ListPager } from "../../components/ListPager";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizStateBulkDeleteBatches,
  bizStateCollectNow,
  bizStateCreateTask,
  bizStateDeleteBatch,
  bizStateDeleteTask,
  bizStateDiscover,
  bizStateDownloadExport,
  bizStateDownloadTaskCommands,
  bizStateGetBatch,
  bizStateGetBatchCommand,
  bizStateGetTask,
  bizStateListBatches,
  bizStateListBatchMetricRows,
  bizStateListProfiles,
  bizStateListTasks,
  bizStatePatchTask,
  bizStatePauseTask,
  bizStatePurgeTask,
  bizStateSetBatchBaseline,
  bizStateSetBatchAlias,
  bizStateSetBindings,
  bizStateStartTask,
  fetchCliTargets,
  formatErr,
} from "../../services/api";

import type { CliTargetItem } from "../../types";
import { pageCount } from "../../utils/display";
import { writeClipboardText } from "../../utils/clipboard";
import { formatSystemTime } from "../../utils/time";
import { jobChipColor, NmStatusChip, sourceChipColor } from "./nmChips";

type TaskRow = {
  id: string;
  source?: string;
  ne_name: string;
  ne_ip: string;
  vendor: string;
  note?: string;
  purpose?: string;
  status: string;
  collect_running: boolean;
  last_error: string;
  last_collect_ended_at?: string | null;
  interval_sec?: number;
};

type Placeholder = {
  name: string;
  required?: boolean;
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
  is_baseline?: boolean;
  protected?: boolean;
  protect_reasons?: string[];
  alias?: string;
};

type Candidate = {
  value: string;
  label: string;
  rd?: string;
  bindings?: Record<string, string>;
};

type SheetCol = { key: string; header: string };

type SheetCmd = {
  id: string;
  raw_command: string;
  parse_status?: string;
  row_count?: number;
  message?: string;
  has_raw?: boolean;
  profile_id?: string;
};

type SheetTab = {
  id: string;
  title: string;
  rowCount: number;
  commands: SheetCmd[];
};

type TaskTab = "profiles" | "batches";
type NeSourceFilter = "all" | "managed" | "ume";

const NE_PAGE_SIZE = 10;
const SHEET_PAGE_SIZE_OPTIONS = [20, 50, 100, 200];
const DEFAULT_SHEET_PAGE_SIZE = 50;

function fmtIntervalLabel(
  sec: number,
  daysLabel: string,
  hoursLabel: string,
  secondsLabel: string,
) {
  const s = Math.max(1, Math.round(Number(sec || 0)) || 1);
  if (s % 86400 === 0) return `${s / 86400} ${daysLabel}`;
  if (s % 3600 === 0) return `${s / 3600} ${hoursLabel}`;
  return `${s} ${secondsLabel}`;
}

function secToIntervalUi(sec: number): { value: number; unit: "days" | "hours" | "seconds" } {
  const s = Math.max(1, Math.round(Number(sec || 3600)) || 3600);
  if (s % 86400 === 0) return { value: s / 86400, unit: "days" };
  if (s % 3600 === 0) return { value: s / 3600, unit: "hours" };
  return { value: s, unit: "seconds" };
}

function intervalUiToSec(value: number, unit: "days" | "hours" | "seconds") {
  const n = Math.max(1, Number(value) || 1);
  if (unit === "days") return n * 86400;
  if (unit === "hours") return n * 3600;
  return Math.max(60, n);
}

function intervalUnitMax(unit: "days" | "hours" | "seconds") {
  if (unit === "days") return 365;
  if (unit === "hours") return 8760;
  return 604800; // up to 7 days in seconds
}

function convertIntervalValue(
  value: number,
  from: "days" | "hours" | "seconds",
  to: "days" | "hours" | "seconds",
) {
  const sec = intervalUiToSec(value, from);
  if (to === "days") return Math.max(1, Math.min(365, Math.round(sec / 86400) || 1));
  if (to === "hours") return Math.max(1, Math.min(8760, Math.round(sec / 3600) || 1));
  return Math.max(60, Math.min(604800, sec));
}

function fmtTime(v?: string | null) {
  if (!v) return "—";
  return formatSystemTime(v) || v;
}

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function neSourceOf(row: CliTargetItem): "managed" | "ume" {
  return row.source === "ume" ? "ume" : "managed";
}

function columnsFromRows(rows: Record<string, unknown>[]): SheetCol[] {
  const keys: string[] = [];
  for (const row of rows) {
    for (const k of Object.keys(row || {})) {
      if (!keys.includes(k)) keys.push(k);
    }
  }
  return keys.map((k) => ({ key: k, header: k }));
}

function buildSheetTabs(batch: any, t: (k: string) => string): SheetTab[] {
  const tabs: SheetTab[] = [];
  const cmds = (batch?.commands || []) as SheetCmd[];
  if (cmds.length) {
    tabs.push({
      id: "commands",
      title: "Commands",
      rowCount: cmds.length,
      commands: cmds,
    });
  }
  for (const s of (batch?.sheets || []) as any[]) {
    const mid = String(s?.metric_id || "").trim();
    if (!mid || mid === "vrf_list") continue;
    const title = String(s?.title || "").trim() || mid;
    tabs.push({
      id: mid,
      title,
      rowCount: Number(s?.row_count || 0),
      commands: Array.isArray(s?.commands) ? (s.commands as SheetCmd[]) : [],
    });
  }
  return tabs;
}

export function BizStatePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();

  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [collectingIds, setCollectingIds] = useState<Record<string, true>>({});
  const [listKeyword, setListKeyword] = useState("");
  const debouncedListKw = useDebouncedValue(listKeyword, 250);
  const [purposeFilter, setPurposeFilter] = useState<"all" | "portrait" | "cutover_hf">("all");

  // create-task modal (all valid CLI targets)
  const [createOpen, setCreateOpen] = useState(false);
  const [neKeyword, setNeKeyword] = useState("");
  const debouncedNeKw = useDebouncedValue(neKeyword, 300);
  const [neSource, setNeSource] = useState<NeSourceFilter>("all");
  const [nePage, setNePage] = useState(1);
  const [neTotal, setNeTotal] = useState(0);
  const [neItems, setNeItems] = useState<CliTargetItem[]>([]);
  const [neLoading, setNeLoading] = useState(false);
  const [selectedNe, setSelectedNe] = useState<CliTargetItem | null>(null);

  // task modal
  const [taskId, setTaskId] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [batches, setBatches] = useState<BatchRow[]>([]);
  const [taskTab, setTaskTab] = useState<TaskTab>("profiles");
  const [intervalValue, setIntervalValue] = useState(1);
  const [intervalUnit, setIntervalUnit] = useState<"days" | "hours" | "seconds">("hours");
  const [retentionDays, setRetentionDays] = useState(30);
  const [dailyKeepEnabled, setDailyKeepEnabled] = useState(false);
  const [dailyKeepCount, setDailyKeepCount] = useState(10);
  const [selectedBatchIds, setSelectedBatchIds] = useState<string[]>([]);

  // VRF bind modal (blocking)
  const [bindItemId, setBindItemId] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedVrfs, setSelectedVrfs] = useState<string[]>([]);
  const [discoverCmd, setDiscoverCmd] = useState("");
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const [discoverError, setDiscoverError] = useState("");
  const [discoverCacheHit, setDiscoverCacheHit] = useState(false);

  // Bound-params detail modal (double-click params cell)
  const [paramsDetail, setParamsDetail] = useState<{
    title: string;
    lines: string[];
  } | null>(null);

  // batch workbook modal (summary + lazy-paged metric sheets)
  const [batchDetail, setBatchDetail] = useState<any>(null);
  const [sheetId, setSheetId] = useState("");
  const [sheetKeyword, setSheetKeyword] = useState("");
  const [sheetColumn, setSheetColumn] = useState("");
  const debouncedSheetKw = useDebouncedValue(sheetKeyword, 250);
  const [sheetPage, setSheetPage] = useState(1);
  const [sheetPageSize, setSheetPageSize] = useState(DEFAULT_SHEET_PAGE_SIZE);
  const [sheetRows, setSheetRows] = useState<Record<string, unknown>[]>([]);
  const [sheetColumns, setSheetColumns] = useState<SheetCol[]>([]);
  const [sheetTotal, setSheetTotal] = useState(0);
  const [sheetLoading, setSheetLoading] = useState(false);
  const [rawLogOpen, setRawLogOpen] = useState(false);
  const [rawLogLoading, setRawLogLoading] = useState(false);
  const [rawLogCmd, setRawLogCmd] = useState("");
  const [rawLogText, setRawLogText] = useState("");
  const [rawLogMeta, setRawLogMeta] = useState("");

  const refreshTasks = useCallback(async () => {
    const purpose =
      purposeFilter === "all" ? "" : purposeFilter === "portrait" ? "portrait" : "cutover_hf";
    const res = await bizStateListTasks(purpose);
    const items = (res.items || []) as TaskRow[];
    setTasks(items);
    return items;
  }, [purposeFilter]);

  useEffect(() => {
    void (async () => {
      try {
        await refreshTasks();
      } catch (e) {
        showError(formatErr(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh when purpose filter / refreshTasks changes
  }, [refreshTasks]);

  // While a collect is running, refresh batch counters so cmd/row progress is visible.
  useEffect(() => {
    if (!taskId || !detail?.collect_running) return;
    let cancelled = false;
    const tick = async () => {
      try {
        if (cancelled) return;
        await loadTask(taskId);
      } catch {
        /* ignore transient poll errors */
      }
    };
    const timer = window.setInterval(() => void tick(), 3000);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll while collect_running
  }, [taskId, detail?.collect_running]);

  useEffect(() => {
    if (!createOpen) return;
    let cancelled = false;
    setNeLoading(true);
    void (async () => {
      try {
        const res = await fetchCliTargets({
          source: neSource,
          keyword: debouncedNeKw,
          page: nePage,
          pageSize: NE_PAGE_SIZE,
        });
        if (cancelled) return;
        setNeItems(res.items || []);
        setNeTotal(Number(res.total || 0));
      } catch (e) {
        if (!cancelled) showError(formatErr(e));
      } finally {
        if (!cancelled) setNeLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [createOpen, neSource, debouncedNeKw, nePage]);

  const nePages = pageCount(neTotal, NE_PAGE_SIZE);

  const filteredTasks = useMemo(() => {
    const kw = debouncedListKw.trim().toLowerCase();
    if (!kw) return tasks;
    return tasks.filter((row) => {
      const blob =
        `${row.ne_name} ${row.ne_ip} ${row.vendor} ${row.note || ""} ${row.purpose || ""} ${row.status} ${row.source || ""} ${row.last_error}`.toLowerCase();
      return blob.includes(kw);
    });
  }, [tasks, debouncedListKw]);

  const collectProfiles = useMemo(
    () => profiles.filter((p) => (p.kind || "collect") === "collect"),
    [profiles],
  );

  const sheetTabs = useMemo(
    () => (batchDetail ? buildSheetTabs(batchDetail, t) : []),
    [batchDetail, t],
  );

  const activeSheet = useMemo(() => {
    if (!sheetTabs.length) return null;
    return sheetTabs.find((s) => s.id === sheetId) || sheetTabs[0];
  }, [sheetTabs, sheetId]);

  const commandsSheetColumns = useMemo<SheetCol[]>(
    () => [
      { key: "raw_command", header: t("bizState.colCommand") },
      { key: "metric_id", header: "metric" },
      { key: "parse_status", header: t("bizState.colStatus") },
      { key: "row_count", header: t("bizState.colRows") },
      { key: "message", header: t("bizState.colMessage") },
      { key: "_actions", header: t("bizState.colActions") },
    ],
    [t],
  );

  const displayColumns = useMemo(() => {
    if (!activeSheet) return [];
    if (activeSheet.id === "commands") return commandsSheetColumns;
    return sheetColumns.length ? sheetColumns : [{ key: "_empty", header: "—" }];
  }, [activeSheet, commandsSheetColumns, sheetColumns]);

  const displayRows = useMemo(() => {
    if (!activeSheet) return [];
    if (activeSheet.id === "commands") {
      const cmds = (batchDetail?.commands || []) as Record<string, unknown>[];
      const kw = debouncedSheetKw.trim().toLowerCase();
      const filtered = !kw
        ? cmds
        : cmds.filter((row) => {
            const keys = sheetColumn
              ? [sheetColumn]
              : ["raw_command", "metric_id", "parse_status", "row_count", "message"];
            return keys.some((k) => cellText(row[k]).toLowerCase().includes(kw));
          });
      const start = (sheetPage - 1) * sheetPageSize;
      return filtered.slice(start, start + sheetPageSize).map((c) => ({ ...c }));
    }
    return sheetRows;
  }, [
    activeSheet,
    batchDetail,
    debouncedSheetKw,
    sheetColumn,
    sheetPage,
    sheetPageSize,
    sheetRows,
  ]);

  const displayTotal = useMemo(() => {
    if (!activeSheet) return 0;
    if (activeSheet.id === "commands") {
      const cmds = (batchDetail?.commands || []) as Record<string, unknown>[];
      const kw = debouncedSheetKw.trim().toLowerCase();
      if (!kw) return cmds.length;
      return cmds.filter((row) => {
        const keys = sheetColumn
          ? [sheetColumn]
          : ["raw_command", "metric_id", "parse_status", "row_count", "message"];
        return keys.some((k) => cellText(row[k]).toLowerCase().includes(kw));
      }).length;
    }
    return sheetTotal;
  }, [activeSheet, batchDetail, debouncedSheetKw, sheetColumn, sheetTotal]);

  const loadSheetPage = useCallback(
    async (batchId: string, metricId: string, page: number, pageSize: number, kw: string, column: string) => {
      if (!batchId || !metricId || metricId === "commands") return;
      setSheetLoading(true);
      try {
        const res = await bizStateListBatchMetricRows({
          batchId,
          metricId,
          page,
          pageSize,
          kw,
          column,
        });
        setSheetRows(res.items || []);
        setSheetTotal(Number(res.total || 0));
        const cols = (res.columns || []).map((c) => ({
          key: c.key,
          // Always show original field key; never localized display_name.
          header: c.key || c.header || "",
        }));
        setSheetColumns(cols.length ? cols : columnsFromRows(res.items || []));
      } catch (e) {
        showError(formatErr(e));
        setSheetRows([]);
        setSheetTotal(0);
      } finally {
        setSheetLoading(false);
      }
    },
    [showError],
  );

  useEffect(() => {
    if (!batchDetail?.id || !activeSheet || activeSheet.id === "commands") return;
    void loadSheetPage(
      String(batchDetail.id),
      activeSheet.id,
      sheetPage,
      sheetPageSize,
      debouncedSheetKw,
      sheetColumn,
    );
  }, [
    batchDetail?.id,
    activeSheet,
    sheetPage,
    sheetPageSize,
    debouncedSheetKw,
    sheetColumn,
    loadSheetPage,
  ]);

  const openCreate = () => {
    setCreateOpen(true);
    setSelectedNe(null);
    setNeKeyword("");
    setNeSource("all");
    setNePage(1);
  };

  const closeCreate = () => {
    setCreateOpen(false);
    setSelectedNe(null);
  };

  const loadTask = async (id: string) => {
    const task = await bizStateGetTask(id);
    setDetail(task);
    const ui = secToIntervalUi(Number(task.interval_sec || 3600));
    setIntervalValue(ui.value);
    setIntervalUnit(ui.unit);
    setRetentionDays(Math.max(1, Number(task.retention_days || 30)));
    setDailyKeepEnabled(Boolean(task.daily_keep_enabled));
    setDailyKeepCount(Math.max(1, Number(task.daily_keep_count || 10)));
    const b = await bizStateListBatches(id, 200);
    setBatches((b.items || []) as BatchRow[]);
    setSelectedBatchIds([]);
    const p = await bizStateListProfiles({
      vendor: task.vendor || "",
      device_type: task.device_type || "",
    });
    setProfiles((p.items || []) as Profile[]);
  };

  const openTask = async (id: string, tab: TaskTab = "profiles") => {
    setTaskId(id);
    setTaskTab(tab);
    setBindItemId("");
    setCandidates([]);
    setBatchDetail(null);
    try {
      await loadTask(id);
    } catch (e) {
      showError(formatErr(e));
      setTaskId("");
    }
  };

  const closeTask = () => {
    setTaskId("");
    setDetail(null);
    setBindItemId("");
    setCandidates([]);
  };

  const createTask = async () => {
    if (!selectedNe) return;
    const source = neSourceOf(selectedNe);
    setBusy(true);
    try {
      const task = await bizStateCreateTask({
        source,
        ne_id: selectedNe.id,
        ne_name: selectedNe.name,
        ne_ip: selectedNe.ip_address,
        vendor: selectedNe.vendor || "",
        device_type: selectedNe.device_type || "",
      });
      showOk(t("bizState.created"));
      closeCreate();
      await refreshTasks();
      await openTask(String(task.id), "profiles");
    } catch (e) {
      showError(t("bizState.createFailed") + ": " + formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const setScheduleEnabled = async (id: string, enabled: boolean) => {
    setBusy(true);
    try {
      if (enabled) {
        await bizStateStartTask(id);
      } else {
        await bizStatePauseTask(id);
      }
      showOk(enabled ? t("bizState.started") : t("bizState.paused"));
      if (taskId === id) await loadTask(id);
      await refreshTasks();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const saveSchedule = async () => {
    if (!taskId) return;
    setBusy(true);
    try {
      await bizStatePatchTask(taskId, {
        interval_sec: intervalUiToSec(intervalValue, intervalUnit),
        retention_days: Math.max(1, Number(retentionDays) || 30),
        daily_keep_enabled: dailyKeepEnabled,
        daily_keep_count: Math.max(1, Number(dailyKeepCount) || 10),
      });
      showOk(t("bizState.scheduleSaved"));
      await loadTask(taskId);
      await refreshTasks();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleBatchSelect = (id: string, protectedBatch: boolean) => {
    if (protectedBatch) return;
    setSelectedBatchIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const selectDeletableBatches = () => {
    setSelectedBatchIds(batches.filter((b) => !b.protected).map((b) => b.id));
  };

  const markBaseline = async (batchId: string, marked: boolean) => {
    setBusy(true);
    try {
      await bizStateSetBatchBaseline(batchId, marked);
      if (taskId) await loadTask(taskId);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const editBatchAlias = async (batchId: string, current?: string) => {
    const next = window.prompt(t("bizState.aliasPrompt"), String(current || ""));
    if (next === null) return;
    setBusy(true);
    try {
      await bizStateSetBatchAlias(batchId, next.trim());
      showOk(t("bizState.aliasSaved"));
      if (taskId) await loadTask(taskId);
      if (batchDetail?.id === batchId) {
        setBatchDetail((prev: any) => (prev ? { ...prev, alias: next.trim() } : prev));
      }
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const removeBatch = async (batchId: string) => {
    if (!window.confirm(t("bizState.confirmDeleteBatch"))) return;
    setBusy(true);
    try {
      await bizStateDeleteBatch(batchId);
      showOk(t("bizState.batchDeleted"));
      if (taskId) await loadTask(taskId);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const bulkRemoveBatches = async () => {
    if (!selectedBatchIds.length) return;
    if (
      !window.confirm(
        t("bizState.confirmBulkDelete").replace("{{count}}", String(selectedBatchIds.length)),
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const res = await bizStateBulkDeleteBatches(selectedBatchIds);
      showOk(
        t("bizState.bulkDeleted")
          .replace("{{deleted}}", String(res.deleted_count || 0))
          .replace("{{skipped}}", String((res.skipped || []).length)),
      );
      if (taskId) await loadTask(taskId);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const purgeNow = async () => {
    if (!taskId) return;
    setBusy(true);
    try {
      const res = await bizStatePurgeTask(taskId);
      showOk(t("bizState.purgeOk").replace("{{dropped}}", String(res.dropped || 0)));
      await loadTask(taskId);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const setTaskCollecting = (id: string, on: boolean) => {
    setCollectingIds((prev) => {
      if (on) {
        if (prev[id]) return prev;
        return { ...prev, [id]: true };
      }
      if (!prev[id]) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const collectNowForTask = async (id: string, fromModal = false) => {
    setTaskCollecting(id, true);
    try {
      await bizStateCollectNow(id);
      showOk(t("bizState.collecting"));
      if (fromModal && taskId === id) {
        setTaskTab("batches");
      }
      // Heavy show-interface can take ~20 minutes; poll long enough and refresh batches.
      const deadline = Date.now() + 32 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 3000));
        if (fromModal && taskId === id) {
          try {
            await loadTask(id);
            const task = await bizStateGetTask(id);
            setDetail(task);
            if (!task.collect_running) break;
          } catch {
            break;
          }
          continue;
        }
        const items = await refreshTasks();
        const latest = items.find((x) => x.id === id);
        if (!latest?.collect_running) break;
      }
      await refreshTasks();
      if (fromModal && taskId === id) {
        await loadTask(id);
      }
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setTaskCollecting(id, false);
    }
  };

  const collectNow = async () => {
    if (!taskId) return;
    await collectNowForTask(taskId, true);
  };

  const exportTaskCommands = async () => {
    if (!taskId) return;
    setBusy(true);
    try {
      await bizStateDownloadTaskCommands(taskId);
      showOk(t("bizState.exportCommandsOk"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const removeTask = async (id: string) => {
    if (!window.confirm(t("bizState.confirmDelete"))) return;
    setBusy(true);
    try {
      await bizStateDeleteTask(id);
      showOk(t("bizState.deleted"));
      if (taskId === id) closeTask();
      await refreshTasks();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleProfileItem = async (profile: Profile, enable: boolean) => {
    if (!taskId || !detail) return;
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
      await bizStatePatchTask(taskId, { items });
      await loadTask(taskId);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const closeBindModal = () => {
    setBindItemId("");
    setCandidates([]);
    setSelectedVrfs([]);
    setDiscoverCmd("");
    setDiscoverError("");
    setDiscoverCacheHit(false);
    setDiscoverLoading(false);
  };

  const startDiscover = async (item: any, forceRefresh = false) => {
    if (!taskId) return;
    const prof = profiles.find((p) => p.profile_id === item.source_profile_id);
    const phs = prof?.placeholders || [];
    const ph = phs[0];
    if (!ph) {
      showError(t("bizState.noNeedBind"));
      return;
    }
    setBindItemId(item.id);
    setCandidates([]);
    setSelectedVrfs([]);
    setDiscoverCmd("");
    setDiscoverError("");
    setDiscoverCacheHit(false);
    setDiscoverLoading(true);
    setBusy(true);
    try {
      // Shared discover_profile_id → omit placeholder so API returns pair candidates.
      const sharedDisc = phs.length > 1 && phs.every((p) => p.discover_profile_id === ph.discover_profile_id);
      const res = await bizStateDiscover({
        task_id: taskId,
        collect_profile_id: item.source_profile_id,
        placeholder: sharedDisc ? "" : ph.name,
        force_refresh: forceRefresh,
      });
      if (!res.ok) {
        const err = res.error || t("bizState.discoverFailed");
        setDiscoverError(err);
        showError(err);
        return;
      }
      setDiscoverCmd(res.command || "");
      setDiscoverCacheHit(Boolean(res.cache_hit));
      const cand = (res.candidates || []) as Candidate[];
      setCandidates(cand);
      const existing = (item.bindings || []) as { placeholder?: string; value?: string }[];
      let keep: string[] = [];
      if (res.pair_mode || sharedDisc) {
        const phNames = phs.map((p) => p.name);
        const byPh: Record<string, string[]> = {};
        for (const b of existing) {
          const name = String(b.placeholder || "");
          const val = String(b.value || "");
          if (!name || !val) continue;
          (byPh[name] ||= []).push(val);
        }
        const counts = phNames.map((n) => (byPh[n] || []).length);
        const n = counts.length ? Math.min(...counts) : 0;
        const existingKeys = new Set<string>();
        for (let i = 0; i < n; i++) {
          const parts = phNames
            .slice()
            .sort()
            .map((name) => `${name}=${(byPh[name] || [])[i] || ""}`);
          existingKeys.add(parts.join("|"));
        }
        keep = cand.filter((c) => existingKeys.has(c.value)).map((c) => c.value);
      } else {
        const vals = existing
          .filter((b) => b.placeholder === ph.name)
          .map((b) => String(b.value));
        keep = vals.filter((v) => cand.some((c) => c.value === v));
      }
      setSelectedVrfs(keep);
      if (!cand.length) {
        setDiscoverError(t("bizState.discoverEmpty"));
      }
    } catch (e) {
      const err = formatErr(e);
      setDiscoverError(err);
      showError(err);
    } finally {
      setDiscoverLoading(false);
      setBusy(false);
    }
  };

  const saveBindings = async (values?: string[]) => {
    if (!taskId || !bindItemId) return;
    const item = (detail?.items || []).find((it: any) => it.id === bindItemId);
    const prof = profiles.find((p) => p.profile_id === item?.source_profile_id);
    const phName = (prof?.placeholders || [])[0]?.name || "vrf";
    const picked = values !== undefined ? values : selectedVrfs;
    if (!picked.length) return;
    const candByVal = new Map(candidates.map((c) => [c.value, c]));
    const rows: { placeholder: string; value: string }[] = [];
    for (const v of picked) {
      const c = candByVal.get(v);
      const binds = c?.bindings;
      if (binds && Object.keys(binds).length) {
        for (const [k, val] of Object.entries(binds)) {
          if (k && val) rows.push({ placeholder: k, value: String(val) });
        }
      } else {
        rows.push({ placeholder: phName, value: v });
      }
    }
    setBusy(true);
    try {
      await bizStateSetBindings(taskId, bindItemId, rows);
      showOk(t("bizState.bindingsSaved"));
      await loadTask(taskId);
      closeBindModal();
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
      setSheetKeyword("");
      setSheetColumn("");
      setSheetPage(1);
      setSheetRows([]);
      setSheetColumns([]);
      setSheetTotal(0);
      const built = buildSheetTabs(d, t);
      const prefer =
        built.find((s) => s.id !== "commands" && s.rowCount > 0) ||
        built.find((s) => s.id !== "commands") ||
        built[0];
      setSheetId(prefer?.id || "");
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const closeBatch = () => {
    setBatchDetail(null);
    setSheetId("");
    setSheetKeyword("");
    setSheetColumn("");
    setSheetPage(1);
    setSheetRows([]);
    setSheetColumns([]);
    setSheetTotal(0);
    setRawLogOpen(false);
    setRawLogText("");
  };

  const openRawLog = async (commandId: string) => {
    if (!batchDetail?.id || !commandId) return;
    setRawLogOpen(true);
    setRawLogLoading(true);
    setRawLogText("");
    setRawLogCmd("");
    setRawLogMeta("");
    try {
      const d = await bizStateGetBatchCommand(String(batchDetail.id), commandId);
      setRawLogCmd(String(d.raw_command || ""));
      setRawLogText(String(d.raw_text || ""));
      const bits = [
        d.parse_status,
        d.metric_id,
        d.row_count != null ? `${d.row_count} rows` : "",
        d.collected_at ? fmtTime(d.collected_at) : "",
      ].filter(Boolean);
      setRawLogMeta(bits.join(" · "));
    } catch (e) {
      showError(formatErr(e));
      setRawLogOpen(false);
    } finally {
      setRawLogLoading(false);
    }
  };

  const selectSheet = (id: string) => {
    setSheetId(id);
    setSheetKeyword("");
    setSheetColumn("");
    setSheetPage(1);
    setSheetRows([]);
    setSheetColumns([]);
    setSheetTotal(0);
  };

  const runningCount = tasks.filter((x) => x.status === "running").length;

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("bizState.title")}</h2>
        <div className="btn-row">
          <Button size="sm" variant="primary" onPress={openCreate}>
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
            <div className="pt-list-kpi__label">{t("bizState.scheduleOn")}</div>
            <div className="pt-list-kpi__value">{runningCount}</div>
          </div>
        </div>

        <div className="filter-inline">
          <Input
            value={listKeyword}
            placeholder={t("bizState.listFilterPh")}
            onChange={(e) => setListKeyword(e.target.value)}
          />
          <FieldSelect
            value={purposeFilter}
            onChange={(e) =>
              setPurposeFilter(e.target.value as "all" | "portrait" | "cutover_hf")
            }
            aria-label={t("bizState.purposeFilter")}
          >
            <option value="all">{t("bizState.purposeAll")}</option>
            <option value="portrait">{t("bizState.purposePortrait")}</option>
            <option value="cutover_hf">{t("bizState.purposeCutoverHf")}</option>
          </FieldSelect>
        </div>

        <div className="pt-list-table-wrap">
          <table className="data-table pt-list-table">
            <thead>
              <tr>
                <th>{t("bizState.colNe")}</th>
                <th>{t("bizState.colSource")}</th>
                <th>{t("bizState.colPurpose")}</th>
                <th>{t("bizState.colInterval")}</th>
                <th>{t("bizState.scheduleEnabled")}</th>
                <th>{t("bizState.colStatus")}</th>
                <th>{t("bizState.colLast")}</th>
                <th>{t("bizState.colActions")}</th>
              </tr>
            </thead>
            <tbody>
              {filteredTasks.map((row) => (
                <tr key={row.id}>
                  <td>
                    <div className="pt-list-task-name">{row.ne_name || row.ne_ip || "—"}</div>
                    <div className="muted">
                      {row.vendor || "—"} · {row.ne_ip || "—"}
                      {row.note ? ` · ${row.note}` : ""}
                    </div>
                    {row.last_error ? <div className="form-error">{row.last_error}</div> : null}
                  </td>
                  <td>
                    <NmStatusChip color={sourceChipColor(row.source)}>
                      {row.source || "managed"}
                    </NmStatusChip>
                  </td>
                  <td>
                    <NmStatusChip
                      color={row.purpose === "cutover_hf" ? "accent" : "default"}
                    >
                      {row.purpose === "cutover_hf"
                        ? t("bizState.purposeCutoverHf")
                        : t("bizState.purposePortrait")}
                    </NmStatusChip>
                  </td>
                  <td className="pt-list-num">
                    {fmtIntervalLabel(
                      Number(row.interval_sec || 3600),
                      t("bizState.intervalUnitDays"),
                      t("bizState.intervalUnitHours"),
                      t("bizState.intervalUnitSeconds"),
                    )}
                  </td>
                  <td>
                    <label className="config-sync-policy-check">
                      <input
                        type="checkbox"
                        checked={row.status === "running"}
                        disabled={busy}
                        onChange={(e) => void setScheduleEnabled(row.id, e.target.checked)}
                      />
                      <span>
                        {row.status === "running" ? t("bizState.scheduleOn") : t("bizState.scheduleOff")}
                      </span>
                    </label>
                  </td>
                  <td>
                    <div className="pt-list-actions" style={{ flexWrap: "wrap", gap: 4 }}>
                      {row.collect_running ? (
                        <NmStatusChip color="accent">{t("bizState.collecting")}</NmStatusChip>
                      ) : row.status === "running" ? (
                        <NmStatusChip color="success">{t("bizState.scheduleOn")}</NmStatusChip>
                      ) : row.status === "paused" ? (
                        <NmStatusChip color="warning">{t("bizState.statusPaused")}</NmStatusChip>
                      ) : (
                        <NmStatusChip color="default">{t("bizState.scheduleOff")}</NmStatusChip>
                      )}
                    </div>
                  </td>
                  <td className="pt-list-time">{fmtTime(row.last_collect_ended_at)}</td>
                  <td>
                    <div className="pt-list-actions">
                      <Button size="sm" variant="primary" onPress={() => void openTask(row.id, "profiles")}>
                        {t("bizState.detail")}
                      </Button>
                      <Button size="sm" variant="ghost" onPress={() => void openTask(row.id, "batches")}>
                        {t("bizState.batches")}
                      </Button>
                      {row.status === "running" ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          isDisabled={busy}
                          onPress={() => void setScheduleEnabled(row.id, false)}
                        >
                          {t("bizState.pause")}
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          isDisabled={busy}
                          onPress={() => void setScheduleEnabled(row.id, true)}
                        >
                          {t("bizState.start")}
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="secondary"
                        isDisabled={Boolean(row.collect_running || collectingIds[row.id])}
                        onPress={() => void collectNowForTask(row.id, false)}
                      >
                        {t("bizState.collectNow")}
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        isDisabled={busy}
                        onPress={() => void removeTask(row.id)}
                      >
                        {t("bizState.delete")}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
                {!filteredTasks.length ? (
                  <tr>
                    <td colSpan={8}>
                      <div className="pt-list-empty">{t("bizState.empty")}</div>
                    </td>
                  </tr>
                ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create task: pick any CLI target with search */}
      <AppModalShell open={createOpen} onClose={closeCreate} size="lg">
        <Modal.Header>
          <Modal.Heading>{t("bizState.create")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="muted">{t("bizState.createHint")}</p>
          <div className="filter-inline">
            <Input
              value={neKeyword}
              placeholder={t("bizState.neKeywordPh")}
              onChange={(e) => {
                setNeKeyword(e.target.value);
                setNePage(1);
              }}
            />
            <FieldSelect
              value={neSource}
              onChange={(e) => {
                setNeSource(e.target.value as NeSourceFilter);
                setNePage(1);
              }}
              aria-label={t("bizState.colSource")}
            >
              <option value="all">{t("bizState.allSource")}</option>
              <option value="managed">managed</option>
              <option value="ume">ume</option>
            </FieldSelect>
            {selectedNe ? (
              <span className="muted">
                {t("bizState.selectedNe")}: {selectedNe.name} ({selectedNe.ip_address}) · {neSourceOf(selectedNe)}
              </span>
            ) : null}
          </div>
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th />
                  <th>{t("bizState.colSource")}</th>
                  <th>{t("bizState.colNe")}</th>
                  <th>IP</th>
                  <th>{t("bizState.colVendor")}</th>
                  <th>{t("bizState.colConnect")}</th>
                </tr>
              </thead>
              <tbody>
                {neItems.map((row) => {
                  const checked =
                    selectedNe?.id === row.id && neSourceOf(selectedNe) === neSourceOf(row);
                  return (
                    <tr key={`${row.source}:${row.id}`}>
                      <td>
                        <input
                          type="radio"
                          name="bs-ne"
                          checked={checked}
                          onChange={() => setSelectedNe(row)}
                        />
                      </td>
                      <td>
                        <NmStatusChip color={sourceChipColor(row.source)}>{row.source}</NmStatusChip>
                      </td>
                      <td>{row.name || "—"}</td>
                      <td>{row.ip_address || "—"}</td>
                      <td>{row.vendor || "—"}</td>
                      <td>
                        <NmStatusChip color={jobChipColor(row.connect_status)}>
                          {row.connect_status || "—"}
                        </NmStatusChip>
                      </td>
                    </tr>
                  );
                })}
                {!neItems.length ? (
                  <tr>
                    <td colSpan={6}>
                      <div className="pt-list-empty">
                        {neLoading ? t("common.refreshing") : t("bizState.neEmpty")}
                      </div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <ListPager
            page={nePage}
            pages={nePages}
            total={neTotal}
            pageSize={NE_PAGE_SIZE}
            onPageChange={setNePage}
            disabled={neLoading}
          />
        </Modal.Body>
        <Modal.Footer>
          <Button
            size="sm"
            variant="primary"
            isDisabled={busy || !selectedNe}
            onPress={() => void createTask()}
          >
            {t("bizState.create")}
          </Button>
          <Button size="sm" variant="ghost" onPress={closeCreate}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Task detail modal */}
      <AppModalShell open={Boolean(taskId)} onClose={closeTask} size="lg" className="app-heroui-modal--xl">
        <Modal.Header>
          <Modal.Heading>
            {detail?.ne_name || detail?.ne_ip || t("bizState.detail")}
            {detail ? ` · ${detail.status}` : ""}
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {detail ? (
            <div className="config-sync-policy-row bs-schedule-row">
              <label className="config-sync-policy-check">
                <input
                  type="checkbox"
                  checked={detail.status === "running"}
                  disabled={busy}
                  onChange={(e) => void setScheduleEnabled(taskId, e.target.checked)}
                />
                <span>{t("bizState.scheduleEnabled")}</span>
              </label>
              {detail.status === "running" ? (
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={busy}
                  onPress={() => void setScheduleEnabled(taskId, false)}
                >
                  {t("bizState.pause")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={busy}
                  onPress={() => void setScheduleEnabled(taskId, true)}
                >
                  {t("bizState.start")}
                </Button>
              )}
              <label className="config-sync-policy-field">
                <span>{t("bizState.interval")}</span>
                <Input
                  type="number"
                  min={intervalUnit === "seconds" ? 60 : 1}
                  max={intervalUnitMax(intervalUnit)}
                  value={String(intervalValue)}
                  onChange={(e) => {
                    const min = intervalUnit === "seconds" ? 60 : 1;
                    const max = intervalUnitMax(intervalUnit);
                    setIntervalValue(Math.max(min, Math.min(max, Number(e.target.value) || min)));
                  }}
                />
                <select
                  value={intervalUnit}
                  onChange={(e) => {
                    const next =
                      e.target.value === "seconds"
                        ? "seconds"
                        : e.target.value === "hours"
                          ? "hours"
                          : "days";
                    if (next === intervalUnit) return;
                    setIntervalValue(convertIntervalValue(intervalValue, intervalUnit, next));
                    setIntervalUnit(next);
                  }}
                >
                  <option value="seconds">{t("bizState.intervalUnitSeconds")}</option>
                  <option value="hours">{t("bizState.intervalUnitHours")}</option>
                  <option value="days">{t("bizState.intervalUnitDays")}</option>
                </select>
              </label>
              <label className="config-sync-policy-field">
                <span>{t("bizState.retention")}</span>
                <Input
                  type="number"
                  min={1}
                  max={3650}
                  value={String(retentionDays)}
                  onChange={(e) => setRetentionDays(Math.max(1, Number(e.target.value) || 1))}
                />
              </label>
              <label className="config-sync-policy-field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={dailyKeepEnabled}
                  disabled={busy}
                  onChange={(e) => setDailyKeepEnabled(e.target.checked)}
                />
                <span>{t("bizState.dailyKeepEnabled")}</span>
              </label>
              {dailyKeepEnabled ? (
                <label className="config-sync-policy-field">
                  <span>{t("bizState.dailyKeepCount")}</span>
                  <Input
                    type="number"
                    min={1}
                    max={1000}
                    value={String(dailyKeepCount)}
                    onChange={(e) => setDailyKeepCount(Math.max(1, Number(e.target.value) || 1))}
                  />
                </label>
              ) : null}
              <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void saveSchedule()}>
                {t("bizState.saveSchedule")}
              </Button>
              <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => void purgeNow()}>
                {t("bizState.purgeNow")}
              </Button>
              <span className="muted">
                {detail.vendor || "—"} · {detail.ne_ip || "—"} · {t("bizState.retentionHint")}
                {dailyKeepEnabled ? ` · ${t("bizState.dailyKeepHint")}` : ""}
              </span>
            </div>
          ) : null}
          {detail?.last_error ? <p className="form-error">{detail.last_error}</p> : null}

          <div className="btn-row nm-config-modal__tabs">
            <Button
              size="sm"
              variant={taskTab === "profiles" ? "primary" : "secondary"}
              onPress={() => setTaskTab("profiles")}
            >
              {t("bizState.profiles")}
            </Button>
            <Button
              size="sm"
              variant={taskTab === "batches" ? "primary" : "secondary"}
              onPress={() => setTaskTab("batches")}
            >
              {t("bizState.batches")}
            </Button>
          </div>

          {taskTab === "profiles" ? (
            <>
              <div className="pt-list-table-wrap bs-profiles-table-wrap">
                <table className="data-table pt-list-table bs-profiles-table">
                  <thead>
                    <tr>
                      <th>{t("bizState.enable")}</th>
                      <th>{t("bizState.profiles")}</th>
                      <th>{t("bizState.params")}</th>
                      <th>{t("bizState.colActions")}</th>
                      <th>{t("bizState.command")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {collectProfiles.map((prof) => {
                      const it = (detail?.items || []).find(
                        (x: any) => x.source_profile_id === prof.profile_id,
                      );
                      const enabled = Boolean(it?.enabled);
                      const binds = (it?.bindings || []) as {
                        placeholder?: string;
                        value?: string;
                      }[];
                      const needsBind = (prof.placeholders || []).length > 0;
                      const phNames = (prof.placeholders || []).map((p) => p.name);
                      let bindLines: string[] = [];
                      if (needsBind && binds.length) {
                        if (phNames.length >= 2) {
                          const byPh: Record<string, string[]> = {};
                          for (const b of binds) {
                            const name = String(b.placeholder || "");
                            const val = String(b.value || "");
                            if (!name || !val) continue;
                            (byPh[name] ||= []).push(val);
                          }
                          const counts = phNames.map((n) => (byPh[n] || []).length);
                          const n = counts.length ? Math.min(...counts) : 0;
                          for (let i = 0; i < n; i++) {
                            bindLines.push(
                              phNames.map((name) => `${name}=${(byPh[name] || [])[i] || ""}`).join(" · "),
                            );
                          }
                        } else {
                          const ph = phNames[0] || "value";
                          bindLines = binds
                            .filter((b) => String(b.value || "").trim())
                            .map((b) => `${ph}=${String(b.value)}`);
                        }
                      }
                      const bindHint = needsBind
                        ? bindLines.length
                          ? bindLines.length <= 2
                            ? bindLines.join("; ")
                            : `${bindLines.slice(0, 2).join("; ")} … (+${bindLines.length - 2})`
                          : t("bizState.unbound")
                        : "—";
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
                            {needsBind ? (
                              <div className="bs-bind-hint muted">{t("bizState.bindHintRequired")}</div>
                            ) : null}
                          </td>
                          <td className="bs-params-cell">
                            {needsBind ? (
                              <div
                                className={`bs-params-scroll${
                                  !bindLines.length ? " bs-params-warn" : ""
                                }${bindLines.length ? " bs-params-scroll--clickable" : ""}`}
                                title={
                                  bindLines.length
                                    ? `${bindHint}\n${t("bizState.paramsClickHint")}`
                                    : bindHint
                                }
                                onClick={() => {
                                  if (!bindLines.length) return;
                                  setParamsDetail({
                                    title: prof.title,
                                    lines: bindLines,
                                  });
                                }}
                              >
                                {bindHint}
                              </div>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td className="bs-actions-cell">
                            {needsBind && enabled && it ? (
                              <Button
                                size="sm"
                                variant="secondary"
                                isDisabled={busy || discoverLoading}
                                onPress={() => void startDiscover(it)}
                              >
                                {t("bizState.discoverBind")}
                              </Button>
                            ) : needsBind ? (
                              <span className="muted">—</span>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td>
                            <code className="bs-cmd-cell">{prof.command_template}</code>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="pt-list-table-wrap">
              <div className="btn-row" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
                <Button size="sm" variant="ghost" isDisabled={busy || !batches.length} onPress={selectDeletableBatches}>
                  {t("bizState.selectAll")}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  isDisabled={busy || !selectedBatchIds.length}
                  onPress={() => void bulkRemoveBatches()}
                >
                  {t("bizState.bulkDelete")}
                  {selectedBatchIds.length ? ` (${selectedBatchIds.length})` : ""}
                </Button>
              </div>
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }} />
                    <th>{t("bizState.colTime")}</th>
                    <th>{t("bizState.colAlias")}</th>
                    <th>{t("bizState.colStatus")}</th>
                    <th>{t("bizState.colProtect")}</th>
                    <th>{t("bizState.colRows")}</th>
                    <th>{t("bizState.colActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {batches.map((b) => {
                    const locked = Boolean(b.protected);
                    return (
                      <tr key={b.id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedBatchIds.includes(b.id)}
                            disabled={locked || busy}
                            onChange={() => toggleBatchSelect(b.id, locked)}
                          />
                        </td>
                        <td className="pt-list-time">{fmtTime(b.started_at)}</td>
                        <td>
                          <button
                            type="button"
                            className="bs-alias-link"
                            disabled={busy}
                            title={t("bizState.setAlias")}
                            onClick={() => void editBatchAlias(b.id, b.alias)}
                          >
                            {b.alias?.trim() ? b.alias : t("bizState.aliasEmpty")}
                          </button>
                        </td>
                        <td>
                          <NmStatusChip color={jobChipColor(b.status)}>{b.status}</NmStatusChip>
                        </td>
                        <td>
                          {b.is_baseline ? (
                            <NmStatusChip color="accent">{t("bizState.baseline")}</NmStatusChip>
                          ) : locked ? (
                            <span className="muted">{t("bizState.protected")}</span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="pt-list-num">
                          {b.row_count}
                          <span className="muted"> / {b.command_count} cmd</span>
                        </td>
                        <td>
                          <div className="pt-list-actions">
                            <Button size="sm" variant="primary" onPress={() => void openBatch(b.id)}>
                              {t("bizState.viewBatch")}
                            </Button>
                            <Button size="sm" variant="ghost" onPress={() => void bizStateDownloadExport(b.id)}>
                              {t("bizState.export")}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              isDisabled={busy}
                              onPress={() => void editBatchAlias(b.id, b.alias)}
                            >
                              {t("bizState.setAlias")}
                            </Button>
                            {b.is_baseline ? (
                              <Button
                                size="sm"
                                variant="secondary"
                                isDisabled={busy}
                                onPress={() => void markBaseline(b.id, false)}
                              >
                                {t("bizState.unmarkBaseline")}
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                variant="secondary"
                                isDisabled={busy}
                                onPress={() => void markBaseline(b.id, true)}
                              >
                                {t("bizState.markBaseline")}
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant="danger"
                              isDisabled={busy || locked}
                              onPress={() => void removeBatch(b.id)}
                            >
                              {t("bizState.deleteBatch")}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {!batches.length ? (
                    <tr>
                      <td colSpan={7}>
                        <div className="pt-list-empty">{t("bizState.noBatches")}</div>
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button
            size="sm"
            variant="primary"
            isDisabled={Boolean(detail?.collect_running || (taskId && collectingIds[taskId]))}
            onPress={() => void collectNow()}
          >
            {t("bizState.collectNow")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            isDisabled={busy || !taskId}
            onPress={() => void exportTaskCommands()}
          >
            {t("bizState.exportCommands")}
          </Button>
          <Button size="sm" variant="danger" isDisabled={busy} onPress={() => void removeTask(taskId)}>
            {t("bizState.delete")}
          </Button>
          <Button size="sm" variant="ghost" onPress={closeTask}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Bound params detail — double-click params cell */}
      <AppModalShell
        open={Boolean(paramsDetail)}
        onClose={() => setParamsDetail(null)}
        dismissible
        size="md"
        className="bs-bind-modal"
      >
        <Modal.Header>
          <Modal.Heading>
            {t("bizState.paramsDetailTitle")}
            {paramsDetail?.title ? ` · ${paramsDetail.title}` : ""}
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-2">
          {paramsDetail?.lines?.length ? (
            <>
              <p className="muted">
                {t("bizState.paramsDetailCount", { n: paramsDetail.lines.length })}
              </p>
              <div className="bs-bind-list bs-bind-list--modal bs-params-detail-list">
                {paramsDetail.lines.map((line, idx) => (
                  <div key={`${idx}-${line}`} className="bs-params-detail-item">
                    <code>{line}</code>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">{t("bizState.unbound")}</p>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="ghost" onPress={() => setParamsDetail(null)}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* VRF discover / bind — blocking modal above task dialog */}
      <AppModalShell
        open={Boolean(bindItemId)}
        onClose={closeBindModal}
        dismissible={!discoverLoading && !busy}
        size="md"
        className="bs-bind-modal"
      >
        <Modal.Header>
          <Modal.Heading>{t("bizState.bindTitle")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {discoverLoading ? (
            <p className="muted">{t("bizState.discoverLoading")}</p>
          ) : null}
          {discoverCmd ? (
            <p className="muted">
              <code>{discoverCmd}</code>
              {candidates.length ? ` · ${selectedVrfs.length}/${candidates.length}` : null}
              {discoverCacheHit ? ` · ${t("bizState.discoverCacheHit")}` : null}
            </p>
          ) : null}
          {discoverError ? <p className="bs-params-warn">{discoverError}</p> : null}
          {!discoverLoading && candidates.length ? (
            <>
              <div className="btn-row" style={{ gap: 8 }}>
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={busy}
                  onPress={() => {
                    const allSelected =
                      candidates.length > 0 &&
                      candidates.every((c) => selectedVrfs.includes(c.value));
                    setSelectedVrfs(allSelected ? [] : candidates.map((c) => c.value));
                  }}
                >
                  {candidates.length > 0 &&
                  candidates.every((c) => selectedVrfs.includes(c.value))
                    ? t("bizState.deselectAllVrfs")
                    : t("bizState.selectAllVrfs")}
                </Button>
                {bindItemId ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    isDisabled={busy || discoverLoading}
                    onPress={() => {
                      const item = (detail?.items || []).find((it: any) => it.id === bindItemId);
                      if (item) void startDiscover(item, true);
                    }}
                  >
                    {t("bizState.discoverRefresh")}
                  </Button>
                ) : null}
              </div>
              <div className="bs-bind-list bs-bind-list--modal">
                {candidates.map((c) => (
                  <label key={c.value} className="bs-bind-item">
                    <input
                      type="checkbox"
                      checked={selectedVrfs.includes(c.value)}
                      disabled={busy}
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
            </>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          <Button
            size="sm"
            variant="primary"
            isDisabled={busy || discoverLoading || !selectedVrfs.length}
            onPress={() => void saveBindings()}
          >
            {t("bizState.saveBindings")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            isDisabled={discoverLoading}
            onPress={closeBindModal}
          >
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Batch workbook: summary + lazy-paged metric sheets */}
      <AppModalShell
        open={Boolean(batchDetail)}
        onClose={closeBatch}
        size="lg"
        className="app-heroui-modal--xl bs-workbook-modal"
      >
        <Modal.Header>
          <Modal.Heading>{t("bizState.batchWorkbook")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-2 bs-workbook-body">
          {batchDetail ? (
            <div className="bs-workbook-meta">
              <NmStatusChip color={jobChipColor(String(batchDetail.status || ""))}>
                {String(batchDetail.status || "—")}
              </NmStatusChip>
              {batchDetail.alias ? (
                <NmStatusChip color="accent">{String(batchDetail.alias)}</NmStatusChip>
              ) : null}
              <span className="muted">
                {t("bizState.workbookStats", {
                  compare: Number(batchDetail.compare_item_count ?? batchDetail.sheet_count ?? 0),
                  withData: Number(batchDetail.sheets_with_data ?? 0),
                  sheets: Number(batchDetail.sheet_count ?? 0),
                })}
                {" · "}
                {batchDetail.command_count} cmd · {batchDetail.row_count} rows ·{" "}
                {fmtTime(batchDetail.started_at)}
              </span>
              <div className="bs-id-row" title={String(batchDetail.id || "")}>
                <span className="bs-id-row__label">{t("bizState.batchId")}</span>
                <code className="bs-id-row__value">{String(batchDetail.id || "")}</code>
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={!batchDetail.id}
                  onPress={() => {
                    void (async () => {
                      const ok = await writeClipboardText(String(batchDetail.id || ""));
                      if (ok) showOk(t("common.copied"));
                      else showError(t("common.opFailed"));
                    })();
                  }}
                >
                  {t("bizState.copyBatchId")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={busy}
                  onPress={() => void editBatchAlias(String(batchDetail.id || ""), String(batchDetail.alias || ""))}
                >
                  {t("bizState.setAlias")}
                </Button>
              </div>
            </div>
          ) : null}

          <div className="bs-sheet-tabs bs-sheet-tabs--top" role="tablist" aria-label={t("bizState.batchWorkbook")}>
            {sheetTabs.map((s) => (
              <button
                key={s.id}
                type="button"
                role="tab"
                aria-selected={activeSheet?.id === s.id}
                className={`bs-sheet-tab${activeSheet?.id === s.id ? " is-active" : ""}`}
                onClick={() => selectSheet(s.id)}
              >
                {s.title}
                <span className="bs-sheet-tab__count">{s.rowCount}</span>
              </button>
            ))}
            {!sheetTabs.length ? <span className="muted">{t("bizState.sheetEmpty")}</span> : null}
          </div>

          {activeSheet ? (
            <>
              {activeSheet.id !== "commands" && activeSheet.commands.length ? (
                <div className="bs-sheet-cmd-bar">
                  <div className="bs-sheet-cmd-bar__label">{t("bizState.collectCommand")}</div>
                  <div className="bs-sheet-cmd-list">
                    {activeSheet.commands.map((c) => (
                      <div key={c.id} className="bs-sheet-cmd-row">
                        <code className="bs-sheet-cmd-code" title={c.raw_command}>
                          {c.raw_command || "—"}
                        </code>
                        <div className="bs-sheet-cmd-actions">
                          {c.parse_status ? (
                            <NmStatusChip color={jobChipColor(String(c.parse_status))}>
                              {c.parse_status}
                            </NmStatusChip>
                          ) : null}
                          <Button
                            size="sm"
                            variant="secondary"
                            isDisabled={!c.has_raw}
                            onPress={() => void openRawLog(c.id)}
                          >
                            {t("bizState.viewRawLog")}
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              <div className="filter-inline bs-sheet-filter">
                <FieldSelect
                  value={sheetColumn}
                  onChange={(e) => {
                    setSheetColumn(e.target.value);
                    setSheetPage(1);
                  }}
                  aria-label={t("bizState.filterColumn")}
                >
                  <option value="">{t("bizState.filterAllCols")}</option>
                  {displayColumns
                    .filter((c) => c.key !== "_actions" && c.key !== "_empty")
                    .map((c) => (
                      <option key={c.key} value={c.key}>
                        {c.header}
                      </option>
                    ))}
                </FieldSelect>
                <Input
                  value={sheetKeyword}
                  placeholder={t("bizState.sheetFilterPh")}
                  onChange={(e) => {
                    setSheetKeyword(e.target.value);
                    setSheetPage(1);
                  }}
                />
                <span className="muted bs-sheet-count">
                  {sheetLoading ? t("bizState.sheetLoading") : `${displayTotal} ${t("bizState.colRows")}`}
                </span>
              </div>

              <div className="pt-list-table-wrap bs-sheet-table">
                <table className="data-table pt-list-table">
                  <thead>
                    <tr>
                      {displayColumns.map((c) => (
                        <th key={c.key}>{c.header}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {displayRows.map((row, i) => (
                      <tr key={String(row.id || i)}>
                        {displayColumns.map((c) => (
                          <td key={c.key}>
                            {c.key === "_actions" ? (
                              <Button
                                size="sm"
                                variant="ghost"
                                isDisabled={!row.has_raw}
                                onPress={() => void openRawLog(String(row.id || ""))}
                              >
                                {t("bizState.viewRawLog")}
                              </Button>
                            ) : c.key === "parse_status" ? (
                              <NmStatusChip color={jobChipColor(cellText(row[c.key]))}>
                                {cellText(row[c.key]) || "—"}
                              </NmStatusChip>
                            ) : c.key === "raw_command" ? (
                              <code className="bs-inline-cmd">{cellText(row[c.key]) || "—"}</code>
                            ) : (
                              cellText(row[c.key]) || "—"
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {!displayRows.length && !sheetLoading ? (
                      <tr>
                        <td colSpan={Math.max(1, displayColumns.length)}>
                          <div className="pt-list-empty">{t("bizState.sheetEmpty")}</div>
                        </td>
                      </tr>
                    ) : null}
                    {sheetLoading && activeSheet.id !== "commands" && !displayRows.length ? (
                      <tr>
                        <td colSpan={Math.max(1, displayColumns.length)}>
                          <div className="pt-list-empty muted">{t("bizState.sheetLoading")}</div>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

              <ListPager
                page={sheetPage}
                pages={pageCount(displayTotal, sheetPageSize)}
                total={displayTotal}
                pageSize={sheetPageSize}
                pageSizeOptions={SHEET_PAGE_SIZE_OPTIONS}
                disabled={sheetLoading}
                onPageChange={setSheetPage}
                onPageSizeChange={(n) => {
                  setSheetPageSize(n);
                  setSheetPage(1);
                }}
              />
            </>
          ) : (
            <div className="pt-list-empty">{t("bizState.sheetEmpty")}</div>
          )}
        </Modal.Body>
        <Modal.Footer>
          {batchDetail ? (
            <Button size="sm" variant="secondary" onPress={() => void bizStateDownloadExport(batchDetail.id)}>
              {t("bizState.export")}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onPress={closeBatch}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Raw CLI log viewer */}
      <AppModalShell
        open={rawLogOpen}
        onClose={() => setRawLogOpen(false)}
        size="lg"
        className="app-heroui-modal--lg bs-rawlog-modal"
      >
        <Modal.Header>
          <Modal.Heading>{t("bizState.rawLogTitle")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-2 bs-rawlog-body">
          {rawLogCmd ? <code className="bs-sheet-cmd-code bs-sheet-cmd-code--block">{rawLogCmd}</code> : null}
          {rawLogMeta ? <p className="muted">{rawLogMeta}</p> : null}
          {rawLogLoading ? (
            <div className="pt-list-empty muted">{t("bizState.sheetLoading")}</div>
          ) : (
            <pre className="bs-rawlog-pre">{rawLogText || t("bizState.rawLogEmpty")}</pre>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="ghost" onPress={() => setRawLogOpen(false)}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
