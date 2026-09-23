import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListPager } from "../../components/ListPager";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizStateBulkDeleteBatches,
  bizStateCollectNow,
  bizStateCollectStop,
  bizStateCreateTask,
  bizStateDeleteBatch,
  bizStateDeleteTask,
  bizStateDiscover,
  bizStateDownloadCommandRaw,
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
import { cutoverCachedGet, cutoverCachedGetSWR, invalidateCutoverCache } from "./cutoverDataCache";
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
  last_collect_started_at?: string | null;
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
  /** light | heavy — collect dual-lane */
  collect_lane?: string;
  placeholders?: Placeholder[];
  aux_commands?: Array<{
    key: string;
    profile_id: string;
    title?: string;
    command_template?: string;
  }>;
};

type BatchRow = {
  id: string;
  status: string;
  row_count: number;
  command_count: number;
  message?: string;
  started_at?: string | null;
  ended_at?: string | null;
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
  raw_line_count?: number;
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

function fmtDuration(started?: string | null, ended?: string | null) {
  if (!started || !ended) return "";
  const a = Date.parse(started);
  const b = Date.parse(ended);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return "";
  const sec = Math.round((b - a) / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return s ? `${m}m${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h${rm}m` : `${h}h`;
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
  /** Track that we observed collect_running=true so we don't clear the chip before enqueue lands. */
  const seenCollectRunningRef = useRef<Record<string, boolean>>({});
  const collectStartedAtRef = useRef<Record<string, number>>({});
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
  const [rawLogCommandId, setRawLogCommandId] = useState("");
  const [rawLogLines, setRawLogLines] = useState(0);
  const [rawLogRows, setRawLogRows] = useState(0);
  const [rawLogMessage, setRawLogMessage] = useState("");
  /** Collect status / errors detail (lighter than workbook). */
  const [collectDetail, setCollectDetail] = useState<any>(null);
  const [collectDetailLoading, setCollectDetailLoading] = useState(false);

  const refreshTasks = useCallback(async () => {
    const purpose =
      purposeFilter === "all" ? "" : purposeFilter === "portrait" ? "portrait" : "cutover_hf";
    const key = `bizState:tasks:${purpose || "all"}`;
    const res = await cutoverCachedGet(key, () => bizStateListTasks(purpose), { force: true });
    const items = (res.items || []) as TaskRow[];
    setTasks(items);
    return items;
  }, [purposeFilter]);

  /** Lightweight poll: task flags + batch counters only (no profile reload). */
  const refreshTaskProgress = useCallback(async (id: string) => {
    const task = await bizStateGetTask(id);
    setDetail((prev: any) => {
      if (!prev || prev.id !== id) return prev;
      return {
        ...prev,
        collect_running: Boolean(task.collect_running),
        last_error: task.last_error,
        last_collect_started_at: task.last_collect_started_at,
        last_collect_ended_at: task.last_collect_ended_at,
        status: task.status,
      };
    });
    const b = await bizStateListBatches(id, 50);
    setBatches((b.items || []) as BatchRow[]);
    return task as TaskRow;
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const purpose =
          purposeFilter === "all" ? "" : purposeFilter === "portrait" ? "portrait" : "cutover_hf";
        const key = `bizState:tasks:${purpose || "all"}`;
        const apply = (res: Awaited<ReturnType<typeof bizStateListTasks>>) => {
          setTasks((res.items || []) as TaskRow[]);
        };
        apply(await cutoverCachedGetSWR(key, () => bizStateListTasks(purpose), apply));
      } catch (e) {
        showError(formatErr(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh when purpose filter changes
  }, [purposeFilter]);

  // Progress poll while any collect is running (list chips and/or open task).
  // Does NOT block navigation; cleans up on unmount / when nothing is collecting.
  useEffect(() => {
    const watching = new Set(Object.keys(collectingIds));
    if (taskId && detail?.collect_running) watching.add(taskId);
    if (!watching.size) return;

    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      try {
        const items = await refreshTasks();
        if (cancelled) return;
        setCollectingIds((prev) => {
          let changed = false;
          const next = { ...prev };
          const now = Date.now();
          for (const id of Object.keys(next)) {
            const row = items.find((x) => x.id === id);
            if (row?.collect_running) {
              seenCollectRunningRef.current[id] = true;
              continue;
            }
            const seen = Boolean(seenCollectRunningRef.current[id]);
            const started = collectStartedAtRef.current[id] || 0;
            // Clear after we saw running→idle, or enqueue never landed (~20s).
            if (seen || (started && now - started > 20_000)) {
              delete next[id];
              delete seenCollectRunningRef.current[id];
              delete collectStartedAtRef.current[id];
              changed = true;
            }
          }
          return changed ? next : prev;
        });
        if (taskId && watching.has(taskId)) {
          await refreshTaskProgress(taskId);
        }
      } catch {
        /* ignore transient poll errors */
      }
    };
    const timer = window.setInterval(() => void tick(), 4000);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [collectingIds, taskId, detail?.collect_running, refreshTasks, refreshTaskProgress]);

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

  const profileSelectionStats = useMemo(() => {
    const items = (detail?.items || []) as Array<{
      source_profile_id?: string;
      enabled?: boolean;
      bindings?: unknown[];
    }>;
    const byId = new Map(items.map((it) => [String(it.source_profile_id || ""), it]));
    let enabled = 0;
    let unbound = 0;
    for (const prof of collectProfiles) {
      const it = byId.get(prof.profile_id);
      if (!it?.enabled) continue;
      enabled += 1;
      if ((prof.placeholders || []).length > 0) {
        const binds = (it.bindings || []) as Array<{ value?: string }>;
        const hasVal = binds.some((b) => String(b.value || "").trim());
        if (!hasVal) unbound += 1;
      }
    }
    return { total: collectProfiles.length, enabled, unbound };
  }, [collectProfiles, detail?.items]);

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
      { key: "_actions", header: t("bizState.colActions") },
      { key: "raw_command", header: t("bizState.colCommand") },
      { key: "metric_id", header: "metric" },
      { key: "parse_status", header: t("bizState.colStatus") },
      { key: "raw_line_count", header: t("bizState.colRawLines") },
      { key: "row_count", header: t("bizState.colRows") },
      { key: "message", header: t("bizState.colMessage") },
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
              : ["raw_command", "metric_id", "parse_status", "raw_line_count", "row_count", "message"];
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
          : ["raw_command", "metric_id", "parse_status", "raw_line_count", "row_count", "message"];
        return keys.some((k) => cellText(row[k]).toLowerCase().includes(kw));
      }).length;
    }
    return sheetTotal;
  }, [activeSheet, batchDetail, debouncedSheetKw, sheetColumn, sheetTotal]);

  const collectDetailCmdSummary = useMemo(() => {
    const cmds = (collectDetail?.commands || []) as SheetCmd[];
    let ok = 0;
    let fail = 0;
    let other = 0;
    for (const c of cmds) {
      const st = String(c.parse_status || "").toLowerCase();
      if (st === "ok" || st === "success" || st === "aux" || st === "aux_ok" || st === "aux_cached") ok += 1;
      else if (st === "failed" || st === "error" || st === "fail") fail += 1;
      else other += 1;
    }
    return { ok, fail, other, total: cmds.length };
  }, [collectDetail]);

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
      invalidateCutoverCache("bizCompare:");
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
        collectStartedAtRef.current[id] = Date.now();
        seenCollectRunningRef.current[id] = false;
        if (prev[id]) return prev;
        return { ...prev, [id]: true };
      }
      delete seenCollectRunningRef.current[id];
      delete collectStartedAtRef.current[id];
      if (!prev[id]) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const collectNowForTask = async (id: string, fromModal = false) => {
    setTaskCollecting(id, true);
    try {
      const out = await bizStateCollectNow(id);
      if (out && out.started === false) {
        setTaskCollecting(id, false);
        const reason = String(out.reason || "");
        if (reason === "already_collecting") {
          setTaskCollecting(id, true);
          showOk(t("bizState.collecting"));
        } else {
          showError(reason || t("common.opFailed"));
          return;
        }
      } else {
        showOk(t("bizState.collecting"));
      }
      if (fromModal && taskId === id) {
        setTaskTab("batches");
        try {
          await refreshTaskProgress(id);
        } catch {
          /* progress poll will retry */
        }
      } else {
        try {
          await refreshTasks();
        } catch {
          /* list poll will retry */
        }
      }
      // Do NOT block UI for the full collect duration. Progress is driven by
      // the collectingIds / collect_running effect (cleans up on unmount).
    } catch (e) {
      setTaskCollecting(id, false);
      showError(formatErr(e));
    }
  };

  const collectNow = async () => {
    if (!taskId) return;
    await collectNowForTask(taskId, true);
  };

  const stopCollectForTask = async (id: string) => {
    setBusy(true);
    try {
      const out = await bizStateCollectStop(id);
      if (out.stopped) {
        showOk(t("bizState.stopCollectOk"));
      } else {
        showOk(t("bizState.stopCollectIdle"));
      }
      setTaskCollecting(id, false);
      try {
        await refreshTasks();
      } catch {
        /* ignore */
      }
      if (taskId === id) {
        try {
          await refreshTaskProgress(id);
        } catch {
          /* ignore */
        }
      }
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
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
      invalidateCutoverCache("bizCompare:");
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

  const setAllProfilesEnabled = async (enable: boolean) => {
    if (!taskId || !detail || !collectProfiles.length) return;
    const items = [...(detail.items || [])] as any[];
    const byId = new Map(items.map((it, i) => [String(it.source_profile_id || ""), i]));
    for (const prof of collectProfiles) {
      const idx = byId.get(prof.profile_id);
      if (idx != null) {
        items[idx] = { ...items[idx], enabled: enable };
      } else if (enable) {
        items.push({
          source_profile_id: prof.profile_id,
          kind: "catalog",
          enabled: true,
          title: prof.title,
          bindings: [],
        });
      }
    }
    setBusy(true);
    try {
      await bizStatePatchTask(taskId, { items });
      await loadTask(taskId);
      showOk(
        enable
          ? t("bizState.profilesSelectAllOk", { n: collectProfiles.length })
          : t("bizState.profilesDeselectAllOk"),
      );
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
    setRawLogCommandId("");
  };

  const openCollectDetail = async (batchId: string) => {
    setCollectDetailLoading(true);
    setCollectDetail(null);
    try {
      const d = await bizStateGetBatch(batchId);
      setCollectDetail(d);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setCollectDetailLoading(false);
    }
  };

  const closeCollectDetail = () => {
    setCollectDetail(null);
    setCollectDetailLoading(false);
  };

  const openRawLog = async (commandId: string, fromBatchId?: string) => {
    const bid = fromBatchId || (batchDetail?.id ? String(batchDetail.id) : "") || (collectDetail?.id ? String(collectDetail.id) : "");
    if (!bid || !commandId) return;
    setRawLogOpen(true);
    setRawLogLoading(true);
    setRawLogText("");
    setRawLogCmd("");
    setRawLogMeta("");
    setRawLogCommandId(commandId);
    setRawLogLines(0);
    setRawLogRows(0);
    setRawLogMessage("");
    try {
      const d = await bizStateGetBatchCommand(bid, commandId);
      setRawLogCmd(String(d.raw_command || ""));
      setRawLogText(String(d.raw_text || ""));
      const lines = Number(d.raw_line_count ?? 0);
      const rows = Number(d.row_count ?? 0);
      setRawLogLines(lines);
      setRawLogRows(rows);
      setRawLogMessage(String(d.message || ""));
      const bits = [
        d.parse_status,
        d.metric_id,
        t("bizState.rawLogStats", { lines, rows }),
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

  const exportRawLog = async () => {
    const bid =
      (batchDetail?.id && String(batchDetail.id)) ||
      (collectDetail?.id && String(collectDetail.id)) ||
      "";
    if (!bid || !rawLogCommandId) {
      // Fallback: download already-loaded text
      if (!rawLogText) return;
      const safe = (rawLogCmd || "command").replace(/[^\w.-]+/g, "_").slice(0, 80);
      const blob = new Blob([rawLogText], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      try {
        const a = document.createElement("a");
        a.href = url;
        a.download = `${safe || "command"}.txt`;
        a.click();
      } finally {
        URL.revokeObjectURL(url);
      }
      return;
    }
    try {
      await bizStateDownloadCommandRaw(bid, rawLogCommandId);
      showOk(t("bizState.exportRawLogOk"));
    } catch (e) {
      showError(formatErr(e));
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
                <th>{t("bizState.colStarted")}</th>
                <th>{t("bizState.colEnded")}</th>
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
                      {row.collect_running || collectingIds[row.id] ? (
                        <NmStatusChip color="accent">{t("bizState.statusCollecting")}</NmStatusChip>
                      ) : row.status === "running" ? (
                        <NmStatusChip color="success">{t("bizState.scheduleOn")}</NmStatusChip>
                      ) : row.status === "paused" ? (
                        <NmStatusChip color="warning">{t("bizState.statusPaused")}</NmStatusChip>
                      ) : (
                        <NmStatusChip color="default">{t("bizState.scheduleOff")}</NmStatusChip>
                      )}
                    </div>
                  </td>
                  <td className="pt-list-time">{fmtTime(row.last_collect_started_at)}</td>
                  <td className="pt-list-time">
                    {fmtTime(row.last_collect_ended_at)}
                    {fmtDuration(row.last_collect_started_at, row.last_collect_ended_at) ? (
                      <div className="muted">
                        {fmtDuration(row.last_collect_started_at, row.last_collect_ended_at)}
                      </div>
                    ) : null}
                  </td>
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
                      {row.collect_running || collectingIds[row.id] ? (
                        <Button
                          size="sm"
                          variant="danger"
                          isDisabled={busy}
                          onPress={() => void stopCollectForTask(row.id)}
                        >
                          {t("bizState.stopCollect")}
                        </Button>
                      ) : null}
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
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {detail ? (
            <div className="config-sync-policy-row bs-schedule-row">
              {detail.collect_running || (taskId && collectingIds[taskId]) ? (
                <NmStatusChip color="accent">{t("bizState.statusCollecting")}</NmStatusChip>
              ) : detail.status === "running" ? (
                <NmStatusChip color="success">{t("bizState.scheduleOn")}</NmStatusChip>
              ) : detail.status === "paused" ? (
                <NmStatusChip color="warning">{t("bizState.statusPaused")}</NmStatusChip>
              ) : (
                <NmStatusChip color="default">{t("bizState.scheduleOff")}</NmStatusChip>
              )}
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
            <div className="bs-profiles-panel">
              <div className="btn-row bs-profiles-toolbar">
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={busy || !collectProfiles.length}
                  onPress={() => void setAllProfilesEnabled(true)}
                >
                  {t("bizState.profilesSelectAll")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={busy || !profileSelectionStats.enabled}
                  onPress={() => void setAllProfilesEnabled(false)}
                >
                  {t("bizState.profilesDeselectAll")}
                </Button>
                <span className="muted bs-profiles-stats">
                  {t("bizState.profilesSelectionStats", {
                    enabled: profileSelectionStats.enabled,
                    total: profileSelectionStats.total,
                  })}
                  {profileSelectionStats.unbound
                    ? ` · ${t("bizState.profilesUnboundStats", { n: profileSelectionStats.unbound })}`
                    : ""}
                </span>
              </div>
              <div className="pt-list-table-wrap bs-profiles-table-wrap">
                <table className="data-table pt-list-table bs-profiles-table">
                  <thead>
                    <tr>
                      <th>{t("bizState.enable")}</th>
                      <th>{t("bizState.profiles")}</th>
                      <th>{t("bizState.colLane")}</th>
                      <th>{t("bizState.colActions")}</th>
                      <th>{t("bizState.params")}</th>
                      <th>{t("bizState.command")}</th>
                      <th>{t("bizState.colAux")}</th>
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
                      const auxList = prof.aux_commands || [];
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
                          <td className="bs-profiles-sticky-col bs-profiles-sticky-col--1">
                            <input
                              type="checkbox"
                              checked={enabled}
                              disabled={busy}
                              onChange={(e) => void toggleProfileItem(prof, e.target.checked)}
                            />
                          </td>
                          <td className="bs-profiles-sticky-col bs-profiles-sticky-col--2">
                            <div className="pt-list-task-name">{prof.title}</div>
                            {prof.description ? <div className="muted">{prof.description}</div> : null}
                            {needsBind ? (
                              <div className="bs-bind-hint muted">{t("bizState.bindHintRequired")}</div>
                            ) : null}
                          </td>
                          <td>
                            {String(prof.collect_lane || "light").toLowerCase() === "heavy" ? (
                              <span title={t("bizState.laneHeavyHint")}>
                                <NmStatusChip color="warning">{t("bizState.laneHeavy")}</NmStatusChip>
                              </span>
                            ) : (
                              <span title={t("bizState.laneLightHint")}>
                                <NmStatusChip color="default">{t("bizState.laneLight")}</NmStatusChip>
                              </span>
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
                          <td>
                            <code className="bs-cmd-cell" title={prof.command_template}>
                              {prof.command_template}
                            </code>
                          </td>
                          <td className="bs-aux-cell">
                            {auxList.length ? (
                              <ul className="bs-aux-list">
                                {auxList.map((a) => {
                                  const label = a.title || a.key || a.profile_id;
                                  const tmpl = String(a.command_template || "").trim();
                                  return (
                                    <li key={`${a.key}:${a.profile_id}`} title={tmpl || a.profile_id}>
                                      <span className="bs-aux-key">{a.key}</span>
                                      <span className="muted"> · {label}</span>
                                      {tmpl ? (
                                        <code className="bs-aux-cmd">{tmpl}</code>
                                      ) : null}
                                    </li>
                                  );
                                })}
                              </ul>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
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
                    <th>{t("bizState.colStarted")}</th>
                    <th>{t("bizState.colEnded")}</th>
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
                        <td className="pt-list-time">
                          {fmtTime(b.ended_at)}
                          {fmtDuration(b.started_at, b.ended_at) ? (
                            <div className="muted">{fmtDuration(b.started_at, b.ended_at)}</div>
                          ) : null}
                        </td>
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
                            <Button size="sm" variant="secondary" onPress={() => void openCollectDetail(b.id)}>
                              {t("bizState.batchDetail")}
                            </Button>
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
          {detail?.collect_running || (taskId && collectingIds[taskId]) ? (
            <Button
              size="sm"
              variant="danger"
              isDisabled={busy || !taskId}
              onPress={() => void stopCollectForTask(taskId)}
            >
              {t("bizState.stopCollect")}
            </Button>
          ) : null}
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
                {t("bizState.colStarted")} {fmtTime(batchDetail.started_at)} ·{" "}
                {t("bizState.colEnded")} {fmtTime(batchDetail.ended_at)}
                {fmtDuration(batchDetail.started_at, batchDetail.ended_at)
                  ? ` · ${fmtDuration(batchDetail.started_at, batchDetail.ended_at)}`
                  : ""}
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
              {batchDetail.message ? (
                <div
                  className={`bs-batch-message${
                    ["failed", "partial", "error", "cancelled"].includes(
                      String(batchDetail.status || "").toLowerCase(),
                    )
                      ? " bs-batch-message--alert"
                      : ""
                  }`}
                  role="status"
                >
                  <span className="bs-batch-message__label">{t("bizState.colMessage")}</span>
                  <pre className="bs-batch-message__body">{String(batchDetail.message)}</pre>
                </div>
              ) : null}
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
                          <Button
                            size="sm"
                            variant="secondary"
                            isDisabled={!c.has_raw}
                            onPress={() => void openRawLog(c.id)}
                          >
                            {t("bizState.viewRawLog")}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            isDisabled={!c.has_raw || !batchDetail?.id}
                            onPress={() =>
                              void bizStateDownloadCommandRaw(String(batchDetail.id), c.id).catch((e) =>
                                showError(formatErr(e)),
                              )
                            }
                          >
                            {t("bizState.exportRawLog")}
                          </Button>
                          <span className="muted bs-cmd-stat">
                            {t("bizState.rawLogStats", {
                              lines: Number(c.raw_line_count ?? 0),
                              rows: Number(c.row_count ?? 0),
                            })}
                          </span>
                          {c.parse_status ? (
                            <NmStatusChip color={jobChipColor(String(c.parse_status))}>
                              {c.parse_status}
                            </NmStatusChip>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {activeSheet.id === "commands" ? (
                <div className="bs-sheet-cmd-list bs-commands-card-list">
                  {displayRows.map((row, i) => (
                    <div key={String(row.id || i)} className="bs-sheet-cmd-row bs-commands-card">
                      <code className="bs-sheet-cmd-code" title={cellText(row.raw_command)}>
                        {cellText(row.raw_command) || "—"}
                      </code>
                      <div className="bs-sheet-cmd-actions">
                        <Button
                          size="sm"
                          variant="secondary"
                          isDisabled={!row.has_raw}
                          onPress={() => void openRawLog(String(row.id || ""))}
                        >
                          {t("bizState.viewRawLog")}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          isDisabled={!row.has_raw || !batchDetail?.id}
                          onPress={() =>
                            void bizStateDownloadCommandRaw(
                              String(batchDetail.id),
                              String(row.id || ""),
                            ).catch((e) => showError(formatErr(e)))
                          }
                        >
                          {t("bizState.exportRawLog")}
                        </Button>
                        <span className="muted bs-cmd-stat">
                          {t("bizState.rawLogStats", {
                            lines: Number(row.raw_line_count ?? 0),
                            rows: Number(row.row_count ?? 0),
                          })}
                        </span>
                        {cellText(row.parse_status) ? (
                          <NmStatusChip color={jobChipColor(cellText(row.parse_status))}>
                            {cellText(row.parse_status)}
                          </NmStatusChip>
                        ) : null}
                        {cellText(row.metric_id) ? (
                          <span className="muted bs-cmd-metric">{cellText(row.metric_id)}</span>
                        ) : null}
                      </div>
                      {cellText(row.message) ? (
                        <div className="bs-commands-card__msg" title={cellText(row.message)}>
                          {cellText(row.message)}
                        </div>
                      ) : null}
                    </div>
                  ))}
                  {!displayRows.length ? (
                    <div className="pt-list-empty">{t("bizState.sheetEmpty")}</div>
                  ) : null}
                </div>
              ) : null}

              {activeSheet.id !== "commands" ? (
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
              ) : (
              <div className="filter-inline bs-sheet-filter">
                <Input
                  value={sheetKeyword}
                  placeholder={t("bizState.sheetFilterPh")}
                  onChange={(e) => {
                    setSheetKeyword(e.target.value);
                    setSheetPage(1);
                  }}
                />
                <span className="muted bs-sheet-count">
                  {`${displayTotal} ${t("bizState.colRows")}`}
                </span>
              </div>
              )}

              {activeSheet.id !== "commands" ? (
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
                              <div className="pt-list-actions">
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  isDisabled={!row.has_raw}
                                  onPress={() => void openRawLog(String(row.id || ""))}
                                >
                                  {t("bizState.viewRawLog")}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  isDisabled={!row.has_raw || !batchDetail?.id}
                                  onPress={() =>
                                    void bizStateDownloadCommandRaw(
                                      String(batchDetail.id),
                                      String(row.id || ""),
                                    ).catch((e) => showError(formatErr(e)))
                                  }
                                >
                                  {t("bizState.exportRawLog")}
                                </Button>
                              </div>
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
                    {sheetLoading && !displayRows.length ? (
                      <tr>
                        <td colSpan={Math.max(1, displayColumns.length)}>
                          <div className="pt-list-empty muted">{t("bizState.sheetLoading")}</div>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              ) : null}

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
          <div className="bs-rawlog-stats">
            <NmStatusChip color="accent">
              {t("bizState.rawLogStats", { lines: rawLogLines, rows: rawLogRows })}
            </NmStatusChip>
            {rawLogMeta ? <span className="muted">{rawLogMeta}</span> : null}
          </div>
          {rawLogMessage ? (
            <div className="bs-batch-message bs-batch-message--alert" role="status">
              <span className="bs-batch-message__label">{t("bizState.colMessage")}</span>
              <pre className="bs-batch-message__body">{rawLogMessage}</pre>
            </div>
          ) : null}
          {rawLogLoading ? (
            <div className="pt-list-empty muted">{t("bizState.sheetLoading")}</div>
          ) : (
            <pre className="bs-rawlog-pre">{rawLogText || t("bizState.rawLogEmpty")}</pre>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button
            size="sm"
            variant="secondary"
            isDisabled={rawLogLoading || (!rawLogText && !rawLogCommandId)}
            onPress={() => void exportRawLog()}
          >
            {t("bizState.exportRawLog")}
          </Button>
          <Button size="sm" variant="ghost" onPress={() => setRawLogOpen(false)}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Batch collect status / errors detail */}
      <AppModalShell
        open={Boolean(collectDetail) || collectDetailLoading}
        onClose={closeCollectDetail}
        size="lg"
        className="app-heroui-modal--lg bs-collect-detail-modal"
      >
        <Modal.Header>
          <Modal.Heading>{t("bizState.batchCollectDetail")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-2">
          {collectDetailLoading ? (
            <div className="pt-list-empty muted">{t("bizState.sheetLoading")}</div>
          ) : collectDetail ? (
            <>
              <div className="bs-workbook-meta">
                <NmStatusChip color={jobChipColor(String(collectDetail.status || ""))}>
                  {String(collectDetail.status || "—")}
                </NmStatusChip>
                {collectDetail.alias ? (
                  <NmStatusChip color="accent">{String(collectDetail.alias)}</NmStatusChip>
                ) : null}
                <span className="muted">
                  {collectDetail.command_count} cmd · {collectDetail.row_count} rows ·{" "}
                  {t("bizState.colStarted")} {fmtTime(collectDetail.started_at)} ·{" "}
                  {t("bizState.colEnded")} {fmtTime(collectDetail.ended_at)}
                  {fmtDuration(collectDetail.started_at, collectDetail.ended_at)
                    ? ` · ${fmtDuration(collectDetail.started_at, collectDetail.ended_at)}`
                    : ""}
                </span>
              </div>
              <div
                className={`bs-batch-message${
                  ["failed", "partial", "error", "cancelled"].includes(
                    String(collectDetail.status || "").toLowerCase(),
                  )
                    ? " bs-batch-message--alert"
                    : ""
                }`}
                role="status"
              >
                <span className="bs-batch-message__label">{t("bizState.colMessage")}</span>
                <pre className="bs-batch-message__body">
                  {String(collectDetail.message || "").trim() || t("bizState.batchMessageEmpty")}
                </pre>
              </div>
              <p className="muted">
                {t("bizState.batchCmdSummary", {
                  ok: collectDetailCmdSummary.ok,
                  fail: collectDetailCmdSummary.fail,
                  other: collectDetailCmdSummary.other,
                  total: collectDetailCmdSummary.total,
                })}
              </p>
              <div className="pt-list-table-wrap bs-sheet-table">
                <table className="data-table pt-list-table">
                  <thead>
                    <tr>
                      <th>{t("bizState.colCommand")}</th>
                      <th>{t("bizState.colStatus")}</th>
                      <th>{t("bizState.colRawLines")}</th>
                      <th>{t("bizState.colRows")}</th>
                      <th>{t("bizState.colMessage")}</th>
                      <th>{t("bizState.colActions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {((collectDetail.commands || []) as SheetCmd[]).map((c) => (
                      <tr key={c.id}>
                        <td>
                          <code className="bs-inline-cmd">{c.raw_command || "—"}</code>
                        </td>
                        <td>
                          <NmStatusChip color={jobChipColor(String(c.parse_status || ""))}>
                            {c.parse_status || "—"}
                          </NmStatusChip>
                        </td>
                        <td className="pt-list-num">{Number(c.raw_line_count ?? 0)}</td>
                        <td className="pt-list-num">{Number(c.row_count ?? 0)}</td>
                        <td className="bs-msg-cell" title={c.message || ""}>
                          {c.message?.trim() ? c.message : "—"}
                        </td>
                        <td>
                          <div className="pt-list-actions">
                            <Button
                              size="sm"
                              variant="ghost"
                              isDisabled={!c.has_raw}
                              onPress={() => void openRawLog(c.id, String(collectDetail.id))}
                            >
                              {t("bizState.viewRawLog")}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              isDisabled={!c.has_raw}
                              onPress={() =>
                                void bizStateDownloadCommandRaw(String(collectDetail.id), c.id).catch((e) =>
                                  showError(formatErr(e)),
                                )
                              }
                            >
                              {t("bizState.exportRawLog")}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {!((collectDetail.commands || []) as SheetCmd[]).length ? (
                      <tr>
                        <td colSpan={6}>
                          <div className="pt-list-empty">{t("bizState.sheetEmpty")}</div>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          {collectDetail?.id ? (
            <>
              <Button
                size="sm"
                variant="primary"
                onPress={() => {
                  const id = String(collectDetail.id);
                  closeCollectDetail();
                  void openBatch(id);
                }}
              >
                {t("bizState.openWorkbook")}
              </Button>
              <Button size="sm" variant="secondary" onPress={() => void bizStateDownloadExport(String(collectDetail.id))}>
                {t("bizState.export")}
              </Button>
            </>
          ) : null}
          <Button size="sm" variant="ghost" onPress={closeCollectDetail}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
