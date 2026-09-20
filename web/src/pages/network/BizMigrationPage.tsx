import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ListPager } from "../../components/ListPager";
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
  bizMigrationDeleteProject,
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
  bizMigrationNePortrait,
  bizMigrationPatchBatch,
  bizMigrationPatchProject,
  bizMigrationResolveRedTicket,
  bizMonitorListTemplates,
  bizStateListBatches,
  fetchCliTargets,
  ApiRequestError,
  formatErr,
} from "../../services/api";
// bizStateListTasks not needed for create — NEs + auto HF
import type { CliTargetItem } from "../../types";
import { writeClipboardText } from "../../utils/clipboard";
import { pageCount } from "../../utils/display";
import { formatSystemTime, localDatetimeInputToUtcIso, utcIsoToLocalDatetimeInput } from "../../utils/time";
import { jobChipColor, NmStatusChip, sourceChipColor } from "./nmChips";

type NeSourceFilter = "all" | "managed" | "ume";
type CreatePickSide = "old" | "new";
type CreateStep = 0 | 1 | 2 | 3;

const CREATE_NE_PAGE_SIZE_DEFAULT = 20;
const CREATE_NE_PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
const CREATE_STEPS = 4;

function neSourceOf(row: CliTargetItem): "managed" | "ume" {
  return row.source === "ume" ? "ume" : "managed";
}

function nePayload(ne: CliTargetItem) {
  return {
    source: neSourceOf(ne),
    ne_id: ne.id,
    ne_name: ne.name,
    ne_ip: ne.ip_address,
    vendor: ne.vendor || "",
    device_type: ne.device_type || "",
  };
}

function monitorTplMetrics(tpl: MonitorTplOpt | null | undefined): string[] {
  if (!tpl) return ["interface_brief"];
  const raw = tpl.collect_metric_ids?.length
    ? tpl.collect_metric_ids
    : tpl.collect_metric_ids_effective;
  const mids = (raw || []).map((x) => String(x || "").trim()).filter(Boolean);
  return mids.length ? mids : ["interface_brief"];
}

/** Empty override = use monitor-template default (full effective set). */
function collectOverridePayload(selected: string[], available: string[]): string[] {
  if (!available.length) return selected.filter(Boolean);
  const sel = new Set(selected.filter(Boolean));
  if (sel.size === available.length && available.every((m) => sel.has(m))) return [];
  return [...sel];
}

function metricIntervalPayload(
  selected: string[],
  intervals: Record<string, string>,
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const mid of selected) {
    const raw = intervals[mid];
    if (raw && Number(raw) >= 60) out[mid] = Math.max(60, Number(raw));
  }
  return out;
}

type HfCollectSide = Array<{ ok?: boolean; error?: string; task_id?: string }>;

function summarizeHfCollect(out: {
  hf_status?: string;
  old?: HfCollectSide;
  new?: HfCollectSide;
  collect?: { old?: HfCollectSide; new?: HfCollectSide };
}): { kind: "ok" | "inactive" | "missing" | "partial" | "fail"; failN: number; okN: number; err: string } {
  if (out.hf_status && out.hf_status !== "running") {
    return { kind: "inactive", failN: 0, okN: 0, err: "hf_window_inactive" };
  }
  const sides = [
    ...(out.old || out.collect?.old || []),
    ...(out.new || out.collect?.new || []),
  ];
  if (!sides.length) return { kind: "fail", failN: 0, okN: 0, err: "collect_failed" };
  const okN = sides.filter((x) => x.ok).length;
  const fails = sides.filter((x) => !x.ok);
  const err = String(fails[0]?.error || "");
  if (!fails.length) return { kind: "ok", failN: 0, okN, err: "" };
  if (err === "hf_window_inactive") return { kind: "inactive", failN: fails.length, okN, err };
  if (err === "hf_task_missing" && !okN) return { kind: "missing", failN: fails.length, okN, err };
  if (okN > 0) return { kind: "partial", failN: fails.length, okN, err };
  return { kind: "fail", failN: fails.length, okN, err: err || "collect_failed" };
}

function mapMigrationApiErr(
  e: unknown,
  t: (key: string, params?: Record<string, string>) => string,
): string {
  const detail = e instanceof ApiRequestError ? String(e.detail || "") : "";
  if (detail.startsWith("metric_needs_bindings:")) {
    const [, metric = "", ...rest] = detail.split(":");
    return t("bizMigration.errMetricNeedsBindings", {
      metric,
      params: rest.join(":") || "required",
    });
  }
  if (detail.startsWith("no_profile_for_metric:")) {
    const [, metric = ""] = detail.split(":");
    return t("bizMigration.errNoProfileForMetric", { metric });
  }
  if (detail === "old_baseline_required") return t("bizMigration.errOldBaselineRequired");
  if (detail === "new_baseline_required") return t("bizMigration.errNewBaselineRequired");
  return formatErr(e);
}

function formatHfBindLine(
  binds?: Array<{ task_id: string; metric_ids?: string[]; interval_sec?: number }>,
): string {
  if (!binds?.length) return "";
  return binds
    .map((b) => `${b.interval_sec || 60}s·${(b.metric_ids || []).join("+") || "—"}`)
    .join(" · ");
}

type TaskOpt = {
  id: string;
  ne_name: string;
  ne_ip: string;
  note?: string;
  interval_sec?: number;
  purpose?: string;
  status?: string;
};
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
  collect_metric_ids_effective?: string[];
};
type Project = {
  id: string;
  name: string;
  old_task_id: string;
  new_task_id: string;
  old_hf_task_id?: string;
  new_hf_task_id?: string;
  old_hf_bindings?: Array<{ task_id: string; metric_ids?: string[]; interval_sec?: number }>;
  new_hf_bindings?: Array<{ task_id: string; metric_ids?: string[]; interval_sec?: number }>;
  old_baseline_batch_id: string;
  new_baseline_batch_id: string;
  mapping_id: string;
  monitor_template_id?: string;
  monitor_template?: MonitorTplOpt;
  collect_metric_ids?: string[];
  collect_metric_ids_effective?: string[];
  metric_interval_sec?: Record<string, number>;
  hf_interval_sec?: number;
  hf_start_at?: string | null;
  hf_end_at?: string | null;
  status: string;
  note?: string;
  created_at?: string | null;
  old_task?: TaskOpt;
  new_task?: TaskOpt;
  old_hf_task?: TaskOpt;
  new_hf_task?: TaskOpt;
};
type MigBatch = {
  id: string;
  batch_label: string;
  status: string;
  expect_set?: {
    ports?: string[];
    items?: Array<{ metric_id: string; sheet_id?: string; key?: string; keys?: string[] }>;
  };
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
type DiffRow = {
  id: string;
  metric_id: string;
  sheet_id?: string;
  verdict: string;
  color: string;
  old_kind: string;
  new_kind: string;
  in_expect: boolean;
  old_key?: string;
  new_key?: string;
  key_str?: string;
  new_key_str?: string;
  match_old_key?: string;
  match_new_key?: string;
  old_status?: string;
  new_status?: string;
  rule_hit?: string;
  evidence?: {
    old?: {
      device?: { ne_name?: string; ne_ip?: string };
      command?: { raw_command?: string; parse_status?: string };
      collect?: { collected_at?: string; batch_id?: string };
      iface?: Array<{ raw?: string; normalized?: string; mapped?: boolean; map_to?: string }>;
    };
    new?: {
      device?: { ne_name?: string; ne_ip?: string };
      command?: { raw_command?: string; parse_status?: string };
      collect?: { collected_at?: string; batch_id?: string };
      iface?: Array<{ raw?: string; normalized?: string }>;
    };
    port_map?: {
      applied?: boolean;
      match_before?: string;
      match_after?: string;
      display_before?: string;
      display_after?: string;
    };
  };
  old?: Record<string, unknown>;
  new?: Record<string, unknown>;
};
type RedTicket = {
  id: string;
  batch_id: string;
  old_key?: string;
  new_key?: string;
  key_str: string;
  new_key_str?: string;
  verdict: string;
  old_status?: string;
  new_status?: string;
  status: string;
  evidence?: DiffRow["evidence"];
};
type SheetCard = {
  metric_id: string;
  sheet_id?: string;
  title?: string;
  progress_ok: number;
  progress_total: number;
  anomaly: number;
  new_baseline_missing?: boolean;
  new_baseline_mode?: string;
  collect_skipped?: boolean;
  current_missing?: boolean;
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
  sheet_id?: string;
  title?: string;
  key_fields: string[];
  iface_fields: string[];
  items: ExpectSheetItem[];
};

function sheetIdentity(s: { sheet_id?: string; metric_id?: string } | null | undefined): string {
  return String(s?.sheet_id || s?.metric_id || "").trim();
}

function sheetLabel(s: { title?: string; sheet_id?: string; metric_id?: string } | null | undefined): string {
  return String(s?.title || s?.sheet_id || s?.metric_id || "").trim() || "—";
}

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
  const [mappings, setMappings] = useState<PortMapping[]>([]);
  const [busy, setBusy] = useState(false);
  const [listKeyword, setListKeyword] = useState("");
  const debouncedListKw = useDebouncedValue(listKeyword, 250);

  const [createOpen, setCreateOpen] = useState(false);
  const [createStep, setCreateStep] = useState<CreateStep>(0);
  const [createName, setCreateName] = useState("");
  const [createOldNe, setCreateOldNe] = useState<CliTargetItem | null>(null);
  const [createNewNe, setCreateNewNe] = useState<CliTargetItem | null>(null);
  const [createPickSide, setCreatePickSide] = useState<CreatePickSide>("old");
  const [createNePickerOpen, setCreateNePickerOpen] = useState(true);
  const [createNeKeyword, setCreateNeKeyword] = useState("");
  const debouncedCreateNeKw = useDebouncedValue(createNeKeyword, 300);
  const [createNeSource, setCreateNeSource] = useState<NeSourceFilter>("all");
  const [createNePage, setCreateNePage] = useState(1);
  const [createNePageSize, setCreateNePageSize] = useState(CREATE_NE_PAGE_SIZE_DEFAULT);
  const [createNeTotal, setCreateNeTotal] = useState(0);
  const [createNeItems, setCreateNeItems] = useState<CliTargetItem[]>([]);
  const [createNeLoading, setCreateNeLoading] = useState(false);
  const [createMonitorTplId, setCreateMonitorTplId] = useState("");
  const [createCollectMetricIds, setCreateCollectMetricIds] = useState<string[]>([]);
  const [createMetricIntervalSec, setCreateMetricIntervalSec] = useState<Record<string, string>>({});
  const [createHfIntervalSec, setCreateHfIntervalSec] = useState(60);
  const [createHfStartAt, setCreateHfStartAt] = useState("");
  const [createHfEndAt, setCreateHfEndAt] = useState("");
  const [createOldBaselineId, setCreateOldBaselineId] = useState("");
  const [createNewBaselineId, setCreateNewBaselineId] = useState("");
  const [createOldBatches, setCreateOldBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [createNewBatches, setCreateNewBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [createOldHasPortrait, setCreateOldHasPortrait] = useState(false);
  const [createNewHasPortrait, setCreateNewHasPortrait] = useState(false);
  const [monitorTpls, setMonitorTpls] = useState<MonitorTplOpt[]>([]);
  const [boardSubTab, setBoardSubTab] = useState<"diffs" | "red">("diffs");

  const [projectId, setProjectId] = useState("");
  const [detailTab, setDetailTab] = useState<DetailTab>("setup");
  const [mappingId, setMappingId] = useState("");
  const [mapName, setMapName] = useState("");
  const [mapText, setMapText] = useState("");
  const [monitorTplId, setMonitorTplId] = useState("");
  const [collectMetricIds, setCollectMetricIds] = useState<string[]>([]);
  const [metricIntervalSec, setMetricIntervalSec] = useState<Record<string, string>>({});
  const [hfIntervalSec, setHfIntervalSec] = useState(60);
  const [hfStartAt, setHfStartAt] = useState("");
  const [hfEndAt, setHfEndAt] = useState("");
  const [oldBaselineId, setOldBaselineId] = useState("");
  const [newBaselineId, setNewBaselineId] = useState("");
  const [oldBatches, setOldBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [newBatches, setNewBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [hfOldBatches, setHfOldBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
  const [hfNewBatches, setHfNewBatches] = useState<{ id: string; started_at?: string | null }[]>([]);
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
    if (boardMetricId) {
      rows = rows.filter(
        (d) => sheetIdentity(d) === boardMetricId || d.metric_id === boardMetricId,
      );
    }
    if (onlyExpect) rows = rows.filter((d) => d.in_expect);
    return rows;
  }, [diffs, onlyExpect, boardMetricId]);

  const boardMetricOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const c of sheetCards) {
      const id = sheetIdentity(c);
      if (id) ids.add(id);
    }
    for (const d of diffs) {
      const id = sheetIdentity(d);
      if (id) ids.add(id);
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
        p.old_hf_task?.ne_name || "",
        p.old_hf_task?.ne_ip || "",
        p.new_hf_task?.ne_name || "",
        p.new_hf_task?.ne_ip || "",
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
    const sheet =
      expectSheets.find((s) => sheetIdentity(s) === expectMetricId) || expectSheets[0];
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
        if (prev && sheets.some((s) => sheetIdentity(s) === prev)) return prev;
        return sheets[0] ? sheetIdentity(sheets[0]) : "";
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
        const [pr, mp, mt] = await Promise.all([
          bizMigrationListProjects(),
          bizCompareListMappings(),
          bizMonitorListTemplates(),
        ]);
        setProjects((pr.items || []) as Project[]);
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
          collect_metric_ids_effective: Array.isArray(x.collect_metric_ids_effective)
            ? (x.collect_metric_ids_effective as string[])
            : [],
        }));
        setMonitorTpls(mts);
        if (!createMonitorTplId && mts.length) {
          const preferred =
            mts.find((x) => x.name === "默认割接监控") ||
            mts.find((x) => /默认|default|状态|status/i.test(x.name)) ||
            mts[0];
          setCreateMonitorTplId(preferred.id);
          setCreateCollectMetricIds(monitorTplMetrics(preferred));
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
    if (!createOpen) return;
    let cancelled = false;
    setCreateNeLoading(true);
    void (async () => {
      try {
        const res = await fetchCliTargets({
          source: createNeSource,
          keyword: debouncedCreateNeKw,
          page: createNePage,
          pageSize: createNePageSize,
        });
        if (cancelled) return;
        setCreateNeItems(res.items || []);
        setCreateNeTotal(Number(res.total || 0));
      } catch (e) {
        if (!cancelled) showError(formatErr(e));
      } finally {
        if (!cancelled) setCreateNeLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [createOpen, createNeSource, debouncedCreateNeKw, createNePage, createNePageSize, showError]);

  useEffect(() => {
    if (!createOldNe) {
      setCreateOldBatches([]);
      setCreateOldHasPortrait(false);
      setCreateOldBaselineId("");
      return;
    }
    void bizMigrationNePortrait({
      source: neSourceOf(createOldNe),
      ne_id: createOldNe.id,
      limit: 30,
    }).then((r) => {
      setCreateOldHasPortrait(Boolean(r.task?.id));
      setCreateOldBatches(
        (r.batches || []).map((x) => ({
          id: String(x.id || ""),
          started_at: x.started_at || null,
        })),
      );
    });
  }, [createOldNe]);

  useEffect(() => {
    if (!createNewNe) {
      setCreateNewBatches([]);
      setCreateNewHasPortrait(false);
      setCreateNewBaselineId("");
      return;
    }
    void bizMigrationNePortrait({
      source: neSourceOf(createNewNe),
      ne_id: createNewNe.id,
      limit: 30,
    }).then((r) => {
      setCreateNewHasPortrait(Boolean(r.task?.id));
      setCreateNewBatches(
        (r.batches || []).map((x) => ({
          id: String(x.id || ""),
          started_at: x.started_at || null,
        })),
      );
    });
  }, [createNewNe]);

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
    const ids = [
      ...(project?.old_hf_bindings || []).map((b) => b.task_id),
      project?.old_hf_task_id || "",
    ].filter(Boolean);
    const uniq = [...new Set(ids)];
    if (!uniq.length) {
      setHfOldBatches([]);
      return;
    }
    void Promise.all(uniq.map((tid) => bizStateListBatches(tid, 30))).then((results) => {
      const seen = new Set<string>();
      const items: { id: string; started_at?: string | null }[] = [];
      for (const r of results) {
        for (const x of (r.items || []) as Record<string, unknown>[]) {
          const id = String(x.id || "");
          if (!id || seen.has(id)) continue;
          seen.add(id);
          items.push({ id, started_at: (x.started_at as string) || null });
        }
      }
      setHfOldBatches(items);
    });
  }, [project?.old_hf_task_id, project?.old_hf_bindings]);

  useEffect(() => {
    const ids = [
      ...(project?.new_hf_bindings || []).map((b) => b.task_id),
      project?.new_hf_task_id || "",
    ].filter(Boolean);
    const uniq = [...new Set(ids)];
    if (!uniq.length) {
      setHfNewBatches([]);
      return;
    }
    void Promise.all(uniq.map((tid) => bizStateListBatches(tid, 30))).then((results) => {
      const seen = new Set<string>();
      const items: { id: string; started_at?: string | null }[] = [];
      for (const r of results) {
        for (const x of (r.items || []) as Record<string, unknown>[]) {
          const id = String(x.id || "");
          if (!id || seen.has(id)) continue;
          seen.add(id);
          items.push({ id, started_at: (x.started_at as string) || null });
        }
      }
      setHfNewBatches(items);
    });
  }, [project?.new_hf_task_id, project?.new_hf_bindings]);

  useEffect(() => {
    if (!project) return;
    setMonitorTplId(project.monitor_template_id || project.monitor_template?.id || "");
    setOldBaselineId(project.old_baseline_batch_id || "");
    setNewBaselineId(project.new_baseline_batch_id || "");
    const eff =
      project.collect_metric_ids?.length
        ? project.collect_metric_ids
        : project.collect_metric_ids_effective || project.monitor_template?.collect_metric_ids || [];
    setCollectMetricIds([...eff]);
    const ivMap = project.metric_interval_sec || {};
    const nextIv: Record<string, string> = {};
    for (const mid of eff) {
      const n = Number(ivMap[mid] || 0);
      if (n >= 60) nextIv[mid] = String(n);
    }
    setMetricIntervalSec(nextIv);
    setHfIntervalSec(Math.max(60, Number(project.hf_interval_sec || 60)));
    setHfStartAt(utcIsoToLocalDatetimeInput(project.hf_start_at));
    setHfEndAt(utcIsoToLocalDatetimeInput(project.hf_end_at));
    if (project.mapping_id) loadMappingText(project.mapping_id);
    else resetMappingEditor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    project?.id,
    project?.mapping_id,
    project?.collect_metric_ids,
    project?.collect_metric_ids_effective,
    project?.metric_interval_sec,
    project?.hf_interval_sec,
    mappings.length,
  ]);

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
    setCreateStep(0);
    setCreateName("");
    setCreateOldNe(null);
    setCreateNewNe(null);
    setCreatePickSide("old");
    setCreateNePickerOpen(true);
    setCreateNeKeyword("");
    setCreateNeSource("all");
    setCreateNePage(1);
    setCreateNePageSize(CREATE_NE_PAGE_SIZE_DEFAULT);
    setCreateCollectMetricIds([]);
    setCreateMetricIntervalSec({});
    setCreateHfIntervalSec(60);
    setCreateHfStartAt("");
    setCreateHfEndAt("");
    setCreateOldBaselineId("");
    setCreateNewBaselineId("");
    setCreateOldBatches([]);
    setCreateNewBatches([]);
    setCreateOldHasPortrait(false);
    setCreateNewHasPortrait(false);
    resetMappingEditor();
    const preferred =
      monitorTpls.find((x) => x.name === "默认割接监控") ||
      monitorTpls.find((x) => /默认|default|状态|status/i.test(x.name)) ||
      monitorTpls[0];
    const tplId = preferred?.id || "";
    setCreateMonitorTplId(tplId);
    setCreateCollectMetricIds(monitorTplMetrics(preferred));
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

  function canAdvanceCreateStep(step: CreateStep): boolean {
    if (step === 0) return Boolean(createName.trim() && createOldNe && createNewNe);
    if (step === 1) return Boolean(createMonitorTplId && createCollectMetricIds.length);
    if (step === 2) return createHfIntervalSec >= 60;
    return true;
  }

  function createStepBlockReason(step: CreateStep): string | null {
    if (step === 0) {
      if (!createName.trim()) return t("bizMigration.needProjectName");
      if (!createOldNe || !createNewNe) return t("bizMigration.needNePair");
      return null;
    }
    if (step === 1) {
      if (!createMonitorTplId) return t("bizMigration.needMonitorTemplate");
      if (!createCollectMetricIds.length) return t("bizMigration.needMetrics");
      return null;
    }
    return null;
  }

  function onCreateNext() {
    const reason = createStepBlockReason(createStep);
    if (reason) {
      showError(reason);
      return;
    }
    setCreateStep((s) => Math.min(CREATE_STEPS - 1, (s + 1) as CreateStep) as CreateStep);
  }

  function onCreateBack() {
    setCreateStep((s) => Math.max(0, s - 1) as CreateStep);
  }

  function pickCreateNeSide(side: CreatePickSide) {
    setCreatePickSide(side);
    setCreateNePickerOpen(true);
  }

  function selectCreateNe(row: CliTargetItem) {
    if (createPickSide === "old") {
      setCreateOldNe(row);
      setCreateOldBaselineId("");
      // After picking old, auto-focus new side but keep picker open for the other side
      if (!createNewNe) setCreatePickSide("new");
    } else {
      setCreateNewNe(row);
      setCreateNewBaselineId("");
      if (!createOldNe) setCreatePickSide("old");
    }
  }

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
    if (!createName.trim() || !createOldNe || !createNewNe) {
      showError(t("bizMigration.needNePair"));
      return;
    }
    if (!createMonitorTplId) {
      showError(t("bizMigration.needMonitorTemplate"));
      return;
    }
    if (!createCollectMetricIds.length) {
      showError(t("bizMigration.needMetrics"));
      return;
    }
    setBusy(true);
    try {
      const metricIntervals: Record<string, number> = {};
      for (const mid of createCollectMetricIds) {
        const raw = createMetricIntervalSec[mid];
        if (raw && Number(raw) >= 60) metricIntervals[mid] = Math.max(60, Number(raw));
      }
      const mapRows = parseMapRows();
      const available = monitorTplMetrics(monitorTpls.find((m) => m.id === createMonitorTplId));
      const body: Record<string, unknown> = {
        name: createName.trim(),
        old_ne: nePayload(createOldNe),
        new_ne: nePayload(createNewNe),
        monitor_template_id: createMonitorTplId,
        collect_metric_ids: collectOverridePayload(createCollectMetricIds, available),
        metric_interval_sec: metricIntervals,
        hf_interval_sec: Math.max(60, createHfIntervalSec || 60),
        hf_start_at: localDatetimeInputToUtcIso(createHfStartAt),
        hf_end_at: localDatetimeInputToUtcIso(createHfEndAt),
        old_baseline_batch_id: createOldBaselineId || "",
        new_baseline_batch_id: createNewBaselineId || "",
        status: "active",
        collect_now: false,
      };
      if (mappingId) {
        body.mapping_id = mappingId;
        if (mapRows.length) await saveMapping();
      } else if (mapRows.length) {
        body.mapping_name = mapName.trim() || `${createName.trim()}-ports`;
        body.mapping_rows = mapRows;
      }
      const p = (await bizMigrationCreateProject(body)) as Project;
      await reloadProjects();
      closeCreate();
      showOk(t("bizMigration.projectCreated"));
      openProject(p.id, p.old_baseline_batch_id && p.new_baseline_batch_id ? "batches" : "setup");
    } catch (e) {
      showError(mapMigrationApiErr(e, t));
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
    if (!collectMetricIds.length) {
      showError(t("bizMigration.needMetrics"));
      return;
    }
    setBusy(true);
    try {
      let mid = mappingId;
      if (mappingId || parseMapRows().length) {
        mid = await saveMapping();
      } else {
        mid = "";
      }
      const available = collectMetricOptions.length
        ? collectMetricOptions
        : monitorTplMetrics(selectedMonitorTpl as MonitorTplOpt | null);
      const intervals = metricIntervalPayload(collectMetricIds, metricIntervalSec);
      await bizMigrationPatchProject(projectId, {
        old_baseline_batch_id: oldBaselineId || project?.old_baseline_batch_id || "",
        new_baseline_batch_id: newBaselineId || project?.new_baseline_batch_id || "",
        mapping_id: mid || "",
        monitor_template_id: monitorTplId || project?.monitor_template_id || "",
        collect_metric_ids: collectOverridePayload(collectMetricIds, available),
        metric_interval_sec: intervals,
        hf_interval_sec: Math.max(60, hfIntervalSec || 60),
        hf_start_at: localDatetimeInputToUtcIso(hfStartAt),
        hf_end_at: localDatetimeInputToUtcIso(hfEndAt),
      });
      await bizMigrationEnsureHighfreq(projectId, {
        interval_sec: Math.max(60, hfIntervalSec || 60),
        collect_now: false,
        collect_metric_ids: collectOverridePayload(collectMetricIds, available),
        hf_start_at: localDatetimeInputToUtcIso(hfStartAt),
        hf_end_at: localDatetimeInputToUtcIso(hfEndAt),
      });
      await reloadProjects();
      await loadBaselineExpect(projectId);
      showOk(t("bizMigration.baselineSaved"));
    } catch (e) {
      showError(mapMigrationApiErr(e, t));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateBatch() {
    if (!projectId) return;
    // One item per selected key. A flat `keys: [a, b]` is parsed as one composite key.
    const items: Array<{ metric_id: string; sheet_id?: string; key: string }> = [];
    const ports: string[] = [];
    for (const sheet of expectSheets) {
      const keys = sheet.items.map((it) => it.key).filter((k) => selectedExpectKeys.has(k));
      const sid = sheetIdentity(sheet);
      for (const key of keys) {
        items.push({ metric_id: sheet.metric_id, sheet_id: sid, key });
      }
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
      showError(mapMigrationApiErr(e, t));
    } finally {
      setBusy(false);
    }
  }

  async function onEnsureHighfreq() {
    if (!projectId) return;
    if (!collectMetricIds.length) {
      showError(t("bizMigration.needMetrics"));
      return;
    }
    setBusy(true);
    try {
      const available = collectMetricOptions.length
        ? collectMetricOptions
        : monitorTplMetrics(selectedMonitorTpl as MonitorTplOpt | null);
      const intervals = metricIntervalPayload(collectMetricIds, metricIntervalSec);
      await bizMigrationPatchProject(projectId, {
        collect_metric_ids: collectOverridePayload(collectMetricIds, available),
        metric_interval_sec: intervals,
        hf_interval_sec: Math.max(60, hfIntervalSec || 60),
        hf_start_at: localDatetimeInputToUtcIso(hfStartAt),
        hf_end_at: localDatetimeInputToUtcIso(hfEndAt),
      });
      const res = (await bizMigrationEnsureHighfreq(projectId, {
        interval_sec: Math.max(60, hfIntervalSec || 60),
        collect_now: true,
        collect_metric_ids: collectOverridePayload(collectMetricIds, available),
        hf_start_at: localDatetimeInputToUtcIso(hfStartAt),
        hf_end_at: localDatetimeInputToUtcIso(hfEndAt),
      })) as {
        hf_status?: string;
        collect?: { old?: HfCollectSide; new?: HfCollectSide };
      };
      await reloadProjects();
      const sum = summarizeHfCollect(res);
      if (sum.kind === "inactive") {
        showOk(t("bizMigration.highfreqReadyInactive"));
      } else if (sum.kind === "partial") {
        showError(t("bizMigration.collectPartial", { ok: String(sum.okN), fail: String(sum.failN) }));
      } else if (sum.kind === "fail" || sum.kind === "missing") {
        showError(sum.err === "hf_task_missing" ? t("bizMigration.hfNotBound") : sum.err);
      } else {
        showOk(t("bizMigration.highfreqReady"));
      }
    } catch (e) {
      showError(mapMigrationApiErr(e, t));
    } finally {
      setBusy(false);
    }
  }

  async function onCollectNow() {
    if (!projectId) return;
    setBusy(true);
    try {
      const out = (await bizMigrationCollectNow(projectId)) as {
        hf_status?: string;
        old?: HfCollectSide;
        new?: HfCollectSide;
      };
      const sum = summarizeHfCollect(out);
      if (sum.kind === "inactive") showError(t("bizMigration.hfWindowInactive"));
      else if (sum.kind === "missing") showError(t("bizMigration.hfNotBound"));
      else if (sum.kind === "partial") {
        showError(t("bizMigration.collectPartial", { ok: String(sum.okN), fail: String(sum.failN) }));
      } else if (sum.kind === "fail") showError(sum.err || "collect_failed");
      else showOk(t("bizMigration.collectTriggered"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onMarkProjectDone() {
    if (!projectId) return;
    if (!window.confirm(t("bizMigration.confirmProjectDone"))) return;
    setBusy(true);
    try {
      await bizMigrationPatchProject(projectId, { status: "done" });
      await reloadProjects();
      showOk(t("bizMigration.projectMarkedDone"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteProject() {
    if (!projectId) return;
    if (!window.confirm(t("bizMigration.confirmDeleteProject"))) return;
    setBusy(true);
    try {
      await bizMigrationDeleteProject(projectId);
      closeProject();
      await reloadProjects();
      showOk(t("bizMigration.projectDeleted"));
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

  const collectMetricOptions = useMemo(() => {
    const set = new Set<string>();
    for (const m of monitorTplMetrics(selectedMonitorTpl as MonitorTplOpt | null)) {
      if (m) set.add(m);
    }
    for (const m of project?.collect_metric_ids_effective || []) {
      if (m) set.add(m);
    }
    for (const m of collectMetricIds) {
      if (m) set.add(m);
    }
    if (!set.size) set.add("interface_brief");
    return [...set];
  }, [selectedMonitorTpl, project?.collect_metric_ids_effective, collectMetricIds]);

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

  const createNePages = pageCount(createNeTotal, createNePageSize);

  const createTplMetrics = useMemo(() => {
    const tpl = monitorTpls.find((m) => m.id === createMonitorTplId);
    const base = monitorTplMetrics(tpl);
    const set = new Set([...base, ...createCollectMetricIds]);
    return [...set];
  }, [monitorTpls, createMonitorTplId, createCollectMetricIds]);

  const batchOptions = (items: { id: string; started_at?: string | null }[]) => (
    <>
      <option value="">—</option>
      {items.map((x) => {
        const when = fmtTime(x.started_at);
        const label = when !== "—" ? `${when} · ${x.id}` : x.id;
        return (
          <option key={x.id} value={x.id} title={x.id}>
            {label}
          </option>
        );
      })}
    </>
  );

  const copyBatchId = (id: string) => {
    void (async () => {
      const ok = await writeClipboardText(id);
      if (ok) showOk(t("common.copied"));
      else showError(t("common.opFailed"));
    })();
  };

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
                <th>{t("bizMigration.colHf")}</th>
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
                      {row.old_task?.ne_name ||
                        row.old_hf_task?.ne_name ||
                        row.old_task_id?.slice(0, 8) ||
                        row.old_hf_task_id?.slice(0, 8) ||
                        "—"}
                    </div>
                    <div className="muted">
                      →{" "}
                      {row.new_task?.ne_name ||
                        row.new_hf_task?.ne_name ||
                        row.new_task_id?.slice(0, 8) ||
                        row.new_hf_task_id?.slice(0, 8) ||
                        "—"}
                      {row.old_task?.ne_ip ||
                      row.new_task?.ne_ip ||
                      row.old_hf_task?.ne_ip ||
                      row.new_hf_task?.ne_ip
                        ? ` · ${row.old_task?.ne_ip || row.old_hf_task?.ne_ip || "—"} / ${
                            row.new_task?.ne_ip || row.new_hf_task?.ne_ip || "—"
                          }`
                        : ""}
                    </div>
                  </td>
                  <td>
                    <NmStatusChip color={jobChipColor(row.status)}>{statusLabel(row.status)}</NmStatusChip>
                  </td>
                  <td>
                    {row.old_hf_task_id && row.new_hf_task_id ? (
                      <div className="bm-list-hf">
                        <NmStatusChip color="success">{t("bizMigration.hfReady")}</NmStatusChip>
                        <span className="muted bm-list-hf__iv">
                          {Math.max(60, Number(row.hf_interval_sec || 60))}s
                        </span>
                      </div>
                    ) : (
                      <NmStatusChip color="warning">{t("bizMigration.hfMissing")}</NmStatusChip>
                    )}
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
                  <td colSpan={7}>
                    <div className="pt-list-empty">{t("bizMigration.empty")}</div>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create cutover project — stepped: NE → template → schedule → baseline/mapping */}
      <AppModalShell open={createOpen} onClose={closeCreate} size="lg" className="bm-create-modal">
        <Modal.Header>
          <Modal.Heading>{t("bizMigration.create")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3 bm-create">
          <nav className="bm-steps" aria-label={t("bizMigration.createSteps")}>
            {(
              [
                t("bizMigration.stepDevices"),
                t("bizMigration.stepTemplate"),
                t("bizMigration.stepSchedule"),
                t("bizMigration.stepBaseline"),
              ] as const
            ).map((label, i) => {
              const done = i < createStep;
              const active = i === createStep;
              return (
                <button
                  key={label}
                  type="button"
                  className={`bm-steps__item${active ? " is-active" : ""}${done ? " is-done" : ""}`}
                  onClick={() => {
                    if (i <= createStep || (i > 0 && canAdvanceCreateStep((i - 1) as CreateStep))) {
                      // allow going back freely; forward only if prior steps ok
                      if (i <= createStep) setCreateStep(i as CreateStep);
                      else {
                        let ok = true;
                        for (let s = 0; s < i; s++) {
                          if (!canAdvanceCreateStep(s as CreateStep)) {
                            ok = false;
                            break;
                          }
                        }
                        if (ok) setCreateStep(i as CreateStep);
                      }
                    }
                  }}
                >
                  <span className="bm-steps__num">{done ? "✓" : i + 1}</span>
                  <span className="bm-steps__label">{label}</span>
                </button>
              );
            })}
          </nav>

          {createStep === 0 ? (
            <div className="bm-create__pane">
              <div className="bm-sched-field">
                <div className="bm-pair-card__label">{t("bizMigration.projectName")}</div>
                <Input
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder={t("bizMigration.projectNamePh")}
                  aria-label={t("bizMigration.projectName")}
                />
              </div>
              <div className="bm-pair-grid bm-ne-pick">
                <button
                  type="button"
                  className={`bm-ne-slot${createPickSide === "old" && createNePickerOpen ? " is-picking" : ""}${
                    createOldNe ? " is-filled" : ""
                  }`}
                  onClick={() => pickCreateNeSide("old")}
                >
                  <div className="bm-pair-card__label">{t("bizMigration.pickOldNe")}</div>
                  {createOldNe ? (
                    <>
                      <div className="bm-ne-slot__name">{createOldNe.name}</div>
                      <div className="muted bm-ne-slot__meta">
                        {createOldNe.ip_address || "—"} · {neSourceOf(createOldNe)}
                        {createOldNe.vendor ? ` · ${createOldNe.vendor}` : ""}
                      </div>
                    </>
                  ) : (
                    <div className="bm-ne-slot__empty">{t("bizMigration.pickNeHint")}</div>
                  )}
                </button>
                <button
                  type="button"
                  className={`bm-ne-slot${createPickSide === "new" && createNePickerOpen ? " is-picking" : ""}${
                    createNewNe ? " is-filled" : ""
                  }`}
                  onClick={() => pickCreateNeSide("new")}
                >
                  <div className="bm-pair-card__label">{t("bizMigration.pickNewNe")}</div>
                  {createNewNe ? (
                    <>
                      <div className="bm-ne-slot__name">{createNewNe.name}</div>
                      <div className="muted bm-ne-slot__meta">
                        {createNewNe.ip_address || "—"} · {neSourceOf(createNewNe)}
                        {createNewNe.vendor ? ` · ${createNewNe.vendor}` : ""}
                      </div>
                    </>
                  ) : (
                    <div className="bm-ne-slot__empty">{t("bizMigration.pickNeHint")}</div>
                  )}
                </button>
              </div>

              {createNePickerOpen ? (
                <div className="bm-ne-picker">
                  <div className="bm-ne-picker__head">
                    <span className="bm-mapping__label">
                      {createPickSide === "old" ? t("bizMigration.pickOldNe") : t("bizMigration.pickNewNe")}
                    </span>
                    {createOldNe && createNewNe ? (
                      <Button size="sm" variant="ghost" onPress={() => setCreateNePickerOpen(false)}>
                        {t("bizMigration.hideNePicker")}
                      </Button>
                    ) : null}
                  </div>
                  <div className="filter-inline">
                    <Input
                      value={createNeKeyword}
                      placeholder={t("bizState.neKeywordPh")}
                      onChange={(e) => {
                        setCreateNeKeyword(e.target.value);
                        setCreateNePage(1);
                      }}
                    />
                    <FieldSelect
                      value={createNeSource}
                      onChange={(e) => {
                        setCreateNeSource(e.target.value as NeSourceFilter);
                        setCreateNePage(1);
                      }}
                      aria-label={t("bizState.colSource")}
                    >
                      <option value="all">{t("bizState.allSource")}</option>
                      <option value="managed">managed</option>
                      <option value="ume">ume</option>
                    </FieldSelect>
                    {createNeLoading ? <span className="muted">…</span> : null}
                  </div>
                  <div className="pt-list-table-wrap bm-ne-picker__table">
                    <table className="data-table pt-list-table">
                      <thead>
                        <tr>
                          <th />
                          <th>{t("bizState.colSource")}</th>
                          <th>{t("bizState.colNe")}</th>
                          <th>IP</th>
                          <th>{t("bizState.colVendor")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {createNeItems.map((row) => {
                          const active =
                            createPickSide === "old"
                              ? createOldNe?.id === row.id &&
                                createOldNe &&
                                neSourceOf(createOldNe) === neSourceOf(row)
                              : createNewNe?.id === row.id &&
                                createNewNe &&
                                neSourceOf(createNewNe) === neSourceOf(row);
                          return (
                            <tr
                              key={`${row.source}:${row.id}`}
                              className={active ? "is-selected" : undefined}
                              onClick={() => selectCreateNe(row)}
                              style={{ cursor: "pointer" }}
                            >
                              <td>
                                <input
                                  type="radio"
                                  name={`bm-create-ne-${createPickSide}`}
                                  checked={Boolean(active)}
                                  onChange={() => selectCreateNe(row)}
                                  onClick={(e) => e.stopPropagation()}
                                />
                              </td>
                              <td>
                                <NmStatusChip color={sourceChipColor(row.source)}>{row.source}</NmStatusChip>
                              </td>
                              <td>{row.name || "—"}</td>
                              <td>{row.ip_address || "—"}</td>
                              <td>{row.vendor || "—"}</td>
                            </tr>
                          );
                        })}
                        {!createNeItems.length ? (
                          <tr>
                            <td colSpan={5}>
                              <div className="pt-list-empty">{t("bizState.neEmpty")}</div>
                            </td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                  <ListPager
                    page={createNePage}
                    pages={createNePages}
                    total={createNeTotal}
                    pageSize={createNePageSize}
                    pageSizeOptions={CREATE_NE_PAGE_SIZE_OPTIONS}
                    onPageChange={setCreateNePage}
                    onPageSizeChange={(size) => {
                      setCreateNePageSize(size);
                      setCreateNePage(1);
                    }}
                    disabled={createNeLoading}
                  />
                </div>
              ) : (
                <p className="muted bm-hint">{t("bizMigration.pickNeHint")}</p>
              )}
            </div>
          ) : null}

          {createStep === 1 ? (
            <div className="bm-create__pane">
              <FieldSelect
                label={t("bizMigration.monitorTemplate")}
                value={createMonitorTplId}
                onChange={(e) => {
                  const id = e.target.value;
                  setCreateMonitorTplId(id);
                  const tpl = monitorTpls.find((m) => m.id === id);
                  setCreateCollectMetricIds(monitorTplMetrics(tpl));
                  setCreateMetricIntervalSec({});
                }}
                fullWidth
              >
                <option value="">{t("bizMigration.optionalNone")}</option>
                {monitorTpls.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </FieldSelect>
              <div className="bm-mapping__label">
                {t("bizMigration.sectionMetrics")}
                <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>
                  {createCollectMetricIds.length}/{createTplMetrics.length}
                </span>
              </div>
              <div className="bm-metric-chips">
                {createTplMetrics.map((mid) => {
                  const on = createCollectMetricIds.includes(mid);
                  return (
                    <button
                      key={mid}
                      type="button"
                      className={`bm-metric-chip${on ? " is-on" : ""}`}
                      onClick={() => {
                        setCreateCollectMetricIds((prev) =>
                          on ? prev.filter((x) => x !== mid) : [...prev, mid],
                        );
                      }}
                    >
                      {mid}
                    </button>
                  );
                })}
              </div>
              {!createCollectMetricIds.length ? (
                <p className="form-error bm-hint">{t("bizMigration.needMetrics")}</p>
              ) : null}
            </div>
          ) : null}

          {createStep === 2 ? (
            <div className="bm-create__pane">
              <div className="bm-sched-grid">
                <div className="bm-sched-field">
                  <div className="bm-pair-card__label">{t("bizMigration.hfInterval")}</div>
                  <Input
                    type="number"
                    min={60}
                    value={String(createHfIntervalSec)}
                    onChange={(e) =>
                      setCreateHfIntervalSec(Math.max(60, Number(e.target.value) || 60))
                    }
                    aria-label={t("bizMigration.hfInterval")}
                  />
                </div>
                <div className="bm-sched-field">
                  <div className="bm-pair-card__label">{t("bizMigration.hfStart")}</div>
                  <Input
                    type="datetime-local"
                    value={createHfStartAt}
                    onChange={(e) => setCreateHfStartAt(e.target.value)}
                    aria-label={t("bizMigration.hfStart")}
                  />
                </div>
                <div className="bm-sched-field">
                  <div className="bm-pair-card__label">{t("bizMigration.hfEnd")}</div>
                  <Input
                    type="datetime-local"
                    value={createHfEndAt}
                    onChange={(e) => setCreateHfEndAt(e.target.value)}
                    aria-label={t("bizMigration.hfEnd")}
                  />
                </div>
              </div>
              {createCollectMetricIds.length ? (
                <>
                  <div className="bm-mapping__label">{t("bizMigration.metricIntervalOptional")}</div>
                  <div className="pt-list-table-wrap bm-interval-table">
                    <table className="data-table pt-list-table">
                      <thead>
                        <tr>
                          <th>{t("bizMigration.sectionMetrics")}</th>
                          <th>{t("bizMigration.metricInterval")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {createCollectMetricIds.map((mid) => (
                          <tr key={mid}>
                            <td>{mid}</td>
                            <td>
                              <Input
                                type="number"
                                min={60}
                                placeholder={String(createHfIntervalSec)}
                                value={createMetricIntervalSec[mid] || ""}
                                onChange={(e) =>
                                  setCreateMetricIntervalSec((prev) => ({
                                    ...prev,
                                    [mid]: e.target.value,
                                  }))
                                }
                                aria-label={`${mid} interval`}
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}

          {createStep === 3 ? (
            <div className="bm-create__pane">
              <div className="bm-mapping__label">{t("bizMigration.sectionBaseline")}</div>
              {!createOldHasPortrait || !createNewHasPortrait ? (
                <p className="form-error bm-hint">
                  <Link to="/network/cutover/biz-state" onClick={closeCreate}>
                    {t("bizMigration.openBizState")}
                  </Link>
                </p>
              ) : null}
              <div className="bm-pair-grid">
                <FieldSelect
                  label={t("bizMigration.oldBaseline")}
                  value={createOldBaselineId}
                  onChange={(e) => setCreateOldBaselineId(e.target.value)}
                  fullWidth
                  disabled={!createOldHasPortrait}
                >
                  {batchOptions(createOldBatches)}
                </FieldSelect>
                <FieldSelect
                  label={t("bizMigration.newBaseline")}
                  value={createNewBaselineId}
                  onChange={(e) => setCreateNewBaselineId(e.target.value)}
                  fullWidth
                  disabled={!createNewHasPortrait}
                >
                  {batchOptions(createNewBatches)}
                </FieldSelect>
              </div>

              <div className="bm-mapping">
                <div className="bm-mapping__label">{t("bizMigration.portMapping")}</div>
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
                </div>
                <textarea
                  className="bm-mapping__text"
                  value={mapText}
                  onChange={(e) => setMapText(e.target.value)}
                  placeholder={t("bizCompare.mapHint")}
                  rows={4}
                />
              </div>
            </div>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="secondary" onPress={closeCreate}>
            {t("bizMigration.cancel")}
          </Button>
          <div className="bm-create__footer-spacer" />
          {createStep > 0 ? (
            <Button size="sm" variant="secondary" onPress={onCreateBack}>
              {t("bizMigration.back")}
            </Button>
          ) : null}
          {createStep < CREATE_STEPS - 1 ? (
            <Button size="sm" variant="primary" onPress={onCreateNext}>
              {t("bizMigration.next")}
            </Button>
          ) : (
            <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onCreateProject()}>
              {t("bizMigration.create")}
            </Button>
          )}
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
                    {project.old_task?.ne_name ||
                      project.old_hf_task?.ne_name ||
                      project.old_task_id.slice(0, 8) ||
                      "—"}
                    {project.old_task?.ne_ip || project.old_hf_task?.ne_ip
                      ? ` (${project.old_task?.ne_ip || project.old_hf_task?.ne_ip})`
                      : ""}
                  </span>
                  <span className="bm-detail__arrow">→</span>
                  <span>
                    {project.new_task?.ne_name ||
                      project.new_hf_task?.ne_name ||
                      project.new_task_id.slice(0, 8) ||
                      "—"}
                    {project.new_task?.ne_ip || project.new_hf_task?.ne_ip
                      ? ` (${project.new_task?.ne_ip || project.new_hf_task?.ne_ip})`
                      : ""}
                  </span>
                  {openRedCount > 0 ? (
                    <NmStatusChip color="danger">
                      {t("bizMigration.openRedChip", { n: String(openRedCount) })}
                    </NmStatusChip>
                  ) : null}
                </div>
                <div className="btn-row bm-detail__actions">
                  <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void onCollectNow()}>
                    {t("bizMigration.collectNow")}
                  </Button>
                  {(detailTab === "batches" || detailTab === "board") && batchId ? (
                    <Button
                      size="sm"
                      variant="primary"
                      isDisabled={
                        busy ||
                        !project.old_baseline_batch_id ||
                        !project.new_baseline_batch_id
                      }
                      onPress={() => void onEvaluate()}
                    >
                      {t("bizMigration.evaluate")}
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={busy}
                    onPress={() => void onEnsureHighfreq()}
                  >
                    {t("bizMigration.enableHighfreq")}
                  </Button>
                  {String(project.status || "").toLowerCase() !== "done" ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      isDisabled={busy}
                      onPress={() => void onMarkProjectDone()}
                    >
                      {t("bizMigration.markProjectDone")}
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="danger"
                    isDisabled={busy}
                    onPress={() => void onDeleteProject()}
                  >
                    {t("bizMigration.deleteProject")}
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
                  <div className="bm-setup__meta muted">
                    <span>
                      {t("bizMigration.metaPortrait")}{" "}
                      {project.old_task?.ne_name || project.old_task_id.slice(0, 8) || "—"}
                      {" → "}
                      {project.new_task?.ne_name || project.new_task_id.slice(0, 8) || "—"}
                    </span>
                    <span className="bm-setup__sep">·</span>
                    <span>
                      {t("bizMigration.metaHf")}{" "}
                      {project.old_hf_task?.interval_sec || project.hf_interval_sec || 60}s
                      {project.old_hf_task?.status ? ` ${project.old_hf_task.status}` : ""}
                      {!project.old_hf_task_id ? ` ${t("bizMigration.hfNotBound")}` : ""}
                    </span>
                    <span className="bm-setup__sep">·</span>
                    <span>
                      {t("bizMigration.metaBaseline")}{" "}
                      {project.old_baseline_batch_id && project.new_baseline_batch_id
                        ? t("bizMigration.baselineOk")
                        : t("bizMigration.baselineMissing")}
                    </span>
                    {formatHfBindLine(project.old_hf_bindings) ? (
                      <>
                        <span className="bm-setup__sep">·</span>
                        <span className="bm-setup__binds">{formatHfBindLine(project.old_hf_bindings)}</span>
                      </>
                    ) : null}
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
                    <div className="bm-setup__cmp muted">
                      {selectedMonitorTpl?.compare_template_name ||
                      selectedMonitorTpl?.compare_template_id ? (
                        <Link to="/network/cutover/compare-templates">
                          {selectedMonitorTpl.compare_template_name ||
                            selectedMonitorTpl.compare_template_id}
                        </Link>
                      ) : null}
                    </div>
                  </div>
                  <div className="bm-hf-collect">
                    <div className="bm-mapping__label">{t("bizMigration.projectCollectMetrics")}</div>
                    <div className="bm-metric-chips">
                      {collectMetricOptions.map((mid) => {
                        const on = collectMetricIds.includes(mid);
                        return (
                          <button
                            key={mid}
                            type="button"
                            className={`bm-metric-chip${on ? " is-on" : ""}`}
                            onClick={() => {
                              setCollectMetricIds((prev) =>
                                on ? prev.filter((x) => x !== mid) : [...prev, mid],
                              );
                            }}
                          >
                            {mid}
                          </button>
                        );
                      })}
                    </div>
                    {!collectMetricIds.length ? (
                      <p className="form-error bm-hint">{t("bizMigration.needMetrics")}</p>
                    ) : null}
                    {collectMetricIds.length ? (
                      <div className="pt-list-table-wrap bm-interval-table">
                        <table className="data-table pt-list-table">
                          <thead>
                            <tr>
                              <th>{t("bizMigration.sectionMetrics")}</th>
                              <th>{t("bizMigration.metricInterval")}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {collectMetricIds.map((mid) => (
                              <tr key={mid}>
                                <td>{mid}</td>
                                <td>
                                  <Input
                                    type="number"
                                    min={60}
                                    placeholder={String(hfIntervalSec)}
                                    value={metricIntervalSec[mid] || ""}
                                    onChange={(e) =>
                                      setMetricIntervalSec((prev) => ({
                                        ...prev,
                                        [mid]: e.target.value,
                                      }))
                                    }
                                    aria-label={`${mid} interval`}
                                  />
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                    <div className="bm-mapping__label" style={{ marginTop: 4 }}>
                      {t("bizMigration.sectionSchedule")}
                    </div>
                    <div className="bm-sched-grid">
                      <div className="bm-sched-field">
                        <div className="bm-pair-card__label">{t("bizMigration.hfInterval")}</div>
                        <Input
                          type="number"
                          min={60}
                          value={String(hfIntervalSec)}
                          onChange={(e) => setHfIntervalSec(Math.max(60, Number(e.target.value) || 60))}
                          aria-label={t("bizMigration.hfInterval")}
                        />
                      </div>
                      <div className="bm-sched-field">
                        <div className="bm-pair-card__label">{t("bizMigration.hfStart")}</div>
                        <Input
                          type="datetime-local"
                          value={hfStartAt}
                          onChange={(e) => setHfStartAt(e.target.value)}
                          aria-label={t("bizMigration.hfStart")}
                        />
                      </div>
                      <div className="bm-sched-field">
                        <div className="bm-pair-card__label">{t("bizMigration.hfEnd")}</div>
                        <Input
                          type="datetime-local"
                          value={hfEndAt}
                          onChange={(e) => setHfEndAt(e.target.value)}
                          aria-label={t("bizMigration.hfEnd")}
                        />
                      </div>
                    </div>
                  </div>
                  <div className="bm-mapping">
                    <div className="bm-mapping__label">{t("bizMigration.portMapping")}</div>
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
                      rows={4}
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
                  <div className="bm-batches__toolbar">
                    <div className="btn-row bm-batches__toolbar-main">
                      <FieldSelect
                        value={batchId}
                        onChange={(e) => setBatchId(e.target.value)}
                        aria-label={t("bizMigration.batches")}
                      >
                        {batches.map((b) => (
                          <option key={b.id} value={b.id} title={b.id}>
                            {b.batch_label} ({statusLabel(b.status)}) · {b.id}
                          </option>
                        ))}
                      </FieldSelect>
                      {batch ? (
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
                      ) : null}
                      {!project.old_baseline_batch_id ? (
                        <NmStatusChip color="warning">{t("bizMigration.needBaselineFirst")}</NmStatusChip>
                      ) : null}
                    </div>
                    <div className="btn-row bm-batches__toolbar-side">
                      <Input
                        value={batchLabel}
                        onChange={(e) => setBatchLabel(e.target.value)}
                        size="sm"
                        placeholder={t("bizMigration.defaultBatchLabel")}
                        aria-label={t("bizMigration.batchLabel")}
                        style={{ minWidth: 100 }}
                      />
                      <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void onCreateBatch()}>
                        {t("bizMigration.createBatch")}
                      </Button>
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
                        </>
                      ) : null}
                    </div>
                  </div>
                  {batch ? (
                    <div className="bs-id-row" title={batch.id}>
                      <span className="bs-id-row__label">{t("bizMigration.batchId")}</span>
                      <code className="bs-id-row__value">{batch.id}</code>
                      <Button size="sm" variant="ghost" onPress={() => copyBatchId(batch.id)}>
                        {t("bizMigration.copyBatchId")}
                      </Button>
                    </div>
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
                          : [
                              {
                                metric_id: "—",
                                sheet_id: "—",
                                title: "—",
                                items: [] as ExpectSheetItem[],
                                key_fields: [] as string[],
                                iface_fields: [] as string[],
                              },
                            ]
                        ).map((s) => {
                          const sid = sheetIdentity(s);
                          const active =
                            (expectMetricId || sheetIdentity(expectSheets[0])) === sid;
                          return (
                            <button
                              key={sid}
                              type="button"
                              role="tab"
                              aria-selected={active}
                              className={`ct-editor__nav-item${active ? " is-active" : ""}`}
                              onClick={() => setExpectMetricId(sid)}
                            >
                              <span className="ct-editor__nav-name">{sheetLabel(s)}</span>
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
                  {acceptInfo ? (
                    <div className="bm-board__status">
                      <span
                        className={`bm-board__verdict${acceptInfo.passed ? " is-ok" : " is-bad"}`}
                      >
                        {acceptInfo.passed
                          ? t("bizMigration.acceptPassedShort")
                          : t("bizMigration.acceptFailedShort")}
                      </span>
                      <span className="bm-board__num">
                        {t("bizMigration.progress")} {acceptInfo.progress_ok ?? 0}/
                        {acceptInfo.progress_total ?? 0}
                      </span>
                      <span className="bm-board__num is-bad">
                        {t("bizMigration.anomaly")} {acceptInfo.anomaly ?? 0}
                      </span>
                    </div>
                  ) : null}
                  <div className="bs-cmp-board__toolbar filter-inline">
                    <FieldSelect
                      value={batchId}
                      onChange={(e) => setBatchId(e.target.value)}
                      aria-label={t("bizMigration.batches")}
                    >
                      {batches.map((b) => (
                        <option key={b.id} value={b.id} title={b.id}>
                          {b.batch_label} ({statusLabel(b.status)}) · {b.id}
                        </option>
                      ))}
                    </FieldSelect>
                    {batchId ? (
                      <div className="bs-id-row bs-id-row--inline" title={batchId}>
                        <span className="bs-id-row__label">{t("bizMigration.batchId")}</span>
                        <code className="bs-id-row__value">{batchId}</code>
                        <Button size="sm" variant="ghost" onPress={() => copyBatchId(batchId)}>
                          {t("bizMigration.copyBatchId")}
                        </Button>
                      </div>
                    ) : null}
                    <FieldSelect
                      value={pinOldBatchId}
                      onChange={(e) => setPinOldBatchId(e.target.value)}
                      aria-label={t("bizMigration.pinOldBatch")}
                    >
                      <option value="">{t("bizMigration.pinLatest")}</option>
                      {batchOptions(hfOldBatches)}
                    </FieldSelect>
                    <FieldSelect
                      value={pinNewBatchId}
                      onChange={(e) => setPinNewBatchId(e.target.value)}
                      aria-label={t("bizMigration.pinNewBatch")}
                    >
                      <option value="">{t("bizMigration.pinLatest")}</option>
                      {batchOptions(hfNewBatches)}
                    </FieldSelect>
                    <FieldSelect
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

                  {sheetCards.some((c) => c.new_baseline_missing) ? (
                    <div className="form-error bm-hint">
                      {t("bizMigration.newBaselineMissingBanner", {
                        metrics: sheetCards
                          .filter((c) => c.new_baseline_missing)
                          .map((c) => sheetLabel(c))
                          .join(", "),
                      })}
                    </div>
                  ) : null}
                  {sheetCards.some((c) => c.collect_skipped) ? (
                    <div className="muted bm-hint">
                      {t("bizMigration.collectSkippedBanner", {
                        metrics: sheetCards
                          .filter((c) => c.collect_skipped)
                          .map((c) => sheetLabel(c))
                          .join(", "),
                      })}
                    </div>
                  ) : null}
                  {sheetCards.some((c) => c.current_missing) ? (
                    <div className="form-error bm-hint">
                      {t("bizMigration.currentMissingBanner", {
                        metrics: sheetCards
                          .filter((c) => c.current_missing)
                          .map((c) => sheetLabel(c))
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
                                <code>{r.old_key || r.key_str}</code>
                              </td>
                              <td>
                                <code>{r.new_key || r.new_key_str || "—"}</code>
                              </td>
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
                            const sid = sheetIdentity(c);
                            const active = boardMetricId === sid;
                            const skipped = Boolean(c.collect_skipped);
                            const noCurrent = Boolean(c.current_missing);
                            const mutedCard = skipped || noCurrent;
                            const hot =
                              !mutedCard && ((c.anomaly || 0) > 0 || Boolean(c.new_baseline_missing));
                            return (
                              <button
                                key={sid}
                                type="button"
                                role="tab"
                                aria-selected={active}
                                className={`bs-cmp-nav__item${active ? " is-active" : ""}${
                                  mutedCard ? " is-clean" : hot ? " has-diff" : " is-clean"
                                }`}
                                style={mutedCard ? { opacity: 0.55 } : undefined}
                                onClick={() => setBoardMetricId(sid)}
                              >
                                <span className="bs-cmp-nav__dot" />
                                <span className="bs-cmp-nav__name">
                                  {sheetLabel(c)}
                                  {skipped
                                    ? ` · ${t("bizMigration.collectSkipped")}`
                                    : noCurrent
                                      ? ` · ${t("bizMigration.currentMissing")}`
                                      : c.new_baseline_missing
                                        ? " · baseline"
                                        : ""}
                                </span>
                                <span className="bs-cmp-nav__stats">
                                  {mutedCard ? (
                                    <span className="bs-cmp-nav__num muted">—</span>
                                  ) : (
                                    <>
                                      <span className="bs-cmp-nav__num bs-cmp-nav__num--ok">
                                        {c.progress_ok}/{c.progress_total}
                                      </span>
                                      <span
                                        className={`bs-cmp-nav__num bs-cmp-nav__num--fail${hot ? " is-hot" : ""}`}
                                      >
                                        {c.anomaly}
                                      </span>
                                    </>
                                  )}
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
                                  <th>{t("bizMigration.colOldPort")}</th>
                                  <th>{t("bizMigration.colNewPort")}</th>
                                  <th>{t("bizMigration.colOld")}</th>
                                  <th>{t("bizMigration.colNew")}</th>
                                  <th>{t("bizMigration.colCommand")}</th>
                                  <th>{t("bizMigration.colVerdict")}</th>
                                  <th>{t("bizMigration.colRuleHit")}</th>
                                </tr>
                              </thead>
                              <tbody>
                                {visibleDiffs.map((d) => {
                                  const oldKey = d.old_key || d.key_str || "—";
                                  const newKey = d.new_key || d.new_key_str || "—";
                                  const matchHint =
                                    d.match_old_key &&
                                    d.match_new_key &&
                                    (d.match_old_key !== oldKey || d.match_new_key !== newKey)
                                      ? `match ${d.match_old_key} → ${d.match_new_key}`
                                      : "";
                                  const oldCmd = d.evidence?.old?.command?.raw_command || "";
                                  const newCmd = d.evidence?.new?.command?.raw_command || "";
                                  const cmdLabel = oldCmd || newCmd || "—";
                                  const cmdTitle = [
                                    d.evidence?.old?.device?.ne_name &&
                                      `old: ${d.evidence.old.device.ne_name}`,
                                    oldCmd && `old cmd: ${oldCmd}`,
                                    d.evidence?.new?.device?.ne_name &&
                                      `new: ${d.evidence.new.device.ne_name}`,
                                    newCmd && `new cmd: ${newCmd}`,
                                    matchHint,
                                  ]
                                    .filter(Boolean)
                                    .join("\n");
                                  return (
                                  <tr key={d.id} title={cmdTitle || undefined}>
                                    <td>{sheetLabel(d)}</td>
                                    <td>
                                      <code>{oldKey}</code>
                                    </td>
                                    <td>
                                      <code>{newKey || "—"}</code>
                                    </td>
                                    <td>{d.old_status || d.old_kind || "—"}</td>
                                    <td>{d.new_status || d.new_kind || "—"}</td>
                                    <td className="muted">
                                      <code style={{ fontSize: 11 }}>{cmdLabel}</code>
                                    </td>
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
                                  );
                                })}
                                {!visibleDiffs.length ? (
                                  <tr>
                                    <td colSpan={8}>
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
