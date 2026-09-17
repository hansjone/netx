import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListPager } from "../../components/ListPager";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizCompareCreateJob,
  bizCompareCreateMapping,
  bizCompareCreateTemplate,
  bizCompareDeleteJob,
  bizCompareDeleteTemplate,
  bizCompareDownloadRun,
  bizCompareGetRun,
  bizCompareListJobs,
  bizCompareListMappings,
  bizCompareListMetrics,
  bizCompareListRunDiffs,
  bizCompareListRuns,
  bizCompareListTemplates,
  bizCompareRunJob,
  bizCompareUpdateJob,
  bizCompareUpdateMapping,
  bizCompareUpdateTemplate,
  bizCompareValidateMapping,
  bizStateListBatches,
  bizStateListTasks,
  formatErr,
} from "../../services/api";
import { formatSystemTime } from "../../utils/time";
import { jobChipColor, NmStatusChip } from "./nmChips";

type PageTab = "templates" | "jobs";
type JobDetailTab = "config" | "result";
type KindFilter = "diff" | "all" | "added" | "removed" | "changed" | "unchanged";

type TaskOpt = { id: string; ne_name: string; ne_ip: string; vendor: string };
type BatchOpt = { id: string; status: string; row_count: number; started_at?: string | null };
type MetricField = {
  name: string;
  display_name: string;
  dtype: string;
  is_key: boolean;
  is_interface: boolean;
  role: string;
};
type MetricSchema = { metric_id: string; fields: MetricField[] };

type MetricSheet = {
  metric_id: string;
  key_fields: string[];
  iface_fields: string[];
  compare_fields: string[];
  display_fields?: string[];
  row_filters?: RowFilter[];
  field_rules?: FieldRule[];
};

type FieldRule = {
  field: string;
  compare?: string;
  normalize?: string;
  ignore?: boolean;
  tolerance?: number;
};

type RowFilter = {
  field?: string;
  op?: string;
  value?: string | string[];
  any?: RowFilter[];
  all?: RowFilter[];
};

type Template = {
  id: string;
  name: string;
  metrics?: MetricSheet[];
  metric_ids?: string[];
  metric_id: string;
  key_fields: string[];
  iface_fields: string[];
  compare_fields: string[];
  note: string;
};

type Mapping = { id: string; name: string; rows: { before_if: string; after_if: string }[] };
type Job = {
  id: string;
  name: string;
  template_id: string;
  mapping_id: string;
  before_task_id: string;
  after_task_id: string;
  before_batch_id: string;
  after_batch_id: string;
  mode: string;
  status: string;
  note?: string;
};

type DiffRow = {
  kind: string;
  key: Record<string, unknown>;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  mapped_before?: Record<string, unknown> | null;
  changes: Record<string, { before?: unknown; after?: unknown; reason?: string }>;
};

type RunSheet = {
  metric_id: string;
  key_fields: string[];
  iface_fields: string[];
  compare_fields: string[];
  display_fields?: string[];
  field_rules?: FieldRule[];
  mode?: string;
  summary?: Record<string, number>;
  diffs?: DiffRow[];
};

function fmtTime(v?: string | null) {
  if (!v) return "—";
  return formatSystemTime(v) || v;
}

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function failFieldNames(d: DiffRow): string[] {
  if (d.kind === "added" || d.kind === "removed") return [];
  return Object.keys(d.changes || {});
}

function toggleInList(list: string[], name: string, on: boolean): string[] {
  if (on) return list.includes(name) ? list : [...list, name];
  return list.filter((x) => x !== name);
}

function taskLabel(row: TaskOpt) {
  return `${row.ne_name || row.ne_ip || row.id} (${row.vendor || "-"})`;
}

function templateSheets(tpl?: Template | null): MetricSheet[] {
  if (!tpl) return [];
  if (tpl.metrics?.length) return tpl.metrics;
  if (tpl.metric_id) {
    return [
      {
        metric_id: tpl.metric_id,
        key_fields: [...(tpl.key_fields || [])],
        iface_fields: [...(tpl.iface_fields || [])],
        compare_fields: [...(tpl.compare_fields || [])],
        display_fields: [...(tpl.key_fields || []), ...(tpl.compare_fields || [])],
        row_filters: [],
        field_rules: [],
      },
    ];
  }
  return [];
}

function defaultSheetForMetric(schema: MetricSchema | undefined, metricId: string): MetricSheet {
  const fields = schema?.fields || [];
  const key_fields = fields.filter((f) => f.is_key).map((f) => f.name);
  const compare_fields = fields
    .filter((f) => !f.is_key && (f.role === "state" || f.role === "meta"))
    .map((f) => f.name);
  const sheet: MetricSheet = {
    metric_id: metricId,
    key_fields,
    iface_fields: fields.filter((f) => f.is_interface).map((f) => f.name),
    compare_fields,
    display_fields: [...key_fields, ...compare_fields],
    row_filters: [],
    field_rules: [],
  };
  // Match backend ARP defaults when adding from UI
  if (metricId === "arp") {
    sheet.row_filters = [
      {
        any: [
          { field: "entry_type", op: "eq", value: "dynamic" },
          {
            all: [
              { field: "entry_type", op: "empty" },
              { field: "age", op: "age_timer" },
            ],
          },
        ],
      },
    ];
    if (fields.some((f) => f.name === "mac")) {
      sheet.field_rules = [{ field: "mac", normalize: "mac" }];
    }
    const ctx = ["vrf", "entry_type", "age"].filter(
      (n) => !key_fields.includes(n) && !compare_fields.includes(n) && fields.some((f) => f.name === n),
    );
    sheet.display_fields = [...key_fields, ...compare_fields, ...ctx];
  }
  return sheet;
}

function cloneSheet(s: MetricSheet): MetricSheet {
  const keys = [...(s.key_fields || [])];
  const compare = [...(s.compare_fields || [])];
  const display =
    s.display_fields && s.display_fields.length
      ? [...s.display_fields]
      : [...keys, ...compare];
  return {
    metric_id: s.metric_id,
    key_fields: keys,
    iface_fields: [...(s.iface_fields || [])],
    compare_fields: compare,
    display_fields: display,
    row_filters: JSON.parse(JSON.stringify(s.row_filters || [])),
    field_rules: JSON.parse(JSON.stringify(s.field_rules || [])),
  };
}

const TEMPLATE_EXPORT_FORMAT = "netx.biz_compare_template";
const TEMPLATE_EXPORT_VERSION = 1;

function templateExportPayload(tpl: Template) {
  const metrics = templateSheets(tpl).map((s) => ({
    metric_id: s.metric_id,
    key_fields: [...(s.key_fields || [])],
    iface_fields: [...(s.iface_fields || [])],
    compare_fields: [...(s.compare_fields || [])],
    display_fields:
      s.display_fields && s.display_fields.length
        ? [...s.display_fields]
        : [...(s.key_fields || []), ...(s.compare_fields || [])],
    row_filters: JSON.parse(JSON.stringify(s.row_filters || [])),
    field_rules: JSON.parse(JSON.stringify(s.field_rules || [])),
  }));
  return {
    format: TEMPLATE_EXPORT_FORMAT,
    version: TEMPLATE_EXPORT_VERSION,
    exported_at: new Date().toISOString(),
    name: tpl.name,
    note: tpl.note || "",
    metrics,
  };
}

function parseTemplateImport(raw: unknown): {
  name: string;
  note: string;
  metrics: MetricSheet[];
} | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  // Accept our export shape or a bare {name, metrics} / TemplateIn body
  const name = String(o.name || "").trim();
  const note = String(o.note || "");
  let metricsRaw = o.metrics;
  if (!Array.isArray(metricsRaw) && o.metric_id) {
    metricsRaw = [
      {
        metric_id: o.metric_id,
        key_fields: o.key_fields || [],
        iface_fields: o.iface_fields || [],
        compare_fields: o.compare_fields || [],
        display_fields: o.display_fields || [],
        row_filters: o.row_filters || [],
        field_rules: o.field_rules || [],
      },
    ];
  }
  if (!name || !Array.isArray(metricsRaw) || !metricsRaw.length) return null;
  const metrics: MetricSheet[] = [];
  for (const item of metricsRaw) {
    if (!item || typeof item !== "object") continue;
    const m = item as Record<string, unknown>;
    const mid = String(m.metric_id || "").trim();
    const keys = Array.isArray(m.key_fields)
      ? (m.key_fields as unknown[]).map((x) => String(x).trim()).filter(Boolean)
      : [];
    if (!mid || !keys.length) continue;
    metrics.push({
      metric_id: mid,
      key_fields: keys,
      iface_fields: Array.isArray(m.iface_fields)
        ? (m.iface_fields as unknown[]).map((x) => String(x).trim()).filter(Boolean)
        : [],
      compare_fields: Array.isArray(m.compare_fields)
        ? (m.compare_fields as unknown[]).map((x) => String(x).trim()).filter(Boolean)
        : [],
      display_fields: Array.isArray(m.display_fields)
        ? (m.display_fields as unknown[]).map((x) => String(x).trim()).filter(Boolean)
        : undefined,
      row_filters: Array.isArray(m.row_filters) ? (m.row_filters as RowFilter[]) : [],
      field_rules: Array.isArray(m.field_rules) ? (m.field_rules as FieldRule[]) : [],
    });
  }
  if (!metrics.length) return null;
  return { name, note, metrics };
}

function downloadJsonFile(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function ruleForField(sheet: MetricSheet, field: string): FieldRule {
  return (sheet.field_rules || []).find((r) => r.field === field) || { field };
}

function upsertFieldRule(sheet: MetricSheet, field: string, patch: Partial<FieldRule>): FieldRule[] {
  const rules = [...(sheet.field_rules || [])];
  const idx = rules.findIndex((r) => r.field === field);
  const next: FieldRule = { ...(idx >= 0 ? rules[idx] : { field }), ...patch, field };
  const norm = (next.normalize || "").toLowerCase();
  const cmp = (next.compare || "").toLowerCase();
  const empty =
    !next.ignore &&
    (!cmp || cmp === "eq") &&
    (!norm || norm === "none" || norm === "strip") &&
    (next.tolerance === undefined || next.tolerance === null);
  if (empty) {
    return rules.filter((r) => r.field !== field);
  }
  if (idx >= 0) rules[idx] = next;
  else rules.push(next);
  return rules;
}

function isLeafFilter(f: RowFilter): boolean {
  return !f.any && !f.all && Boolean(f.field || f.op);
}

/** Expand sheet row_filters into OR-groups of AND leaf conditions for editing. */
function toOrGroups(filters: RowFilter[]): RowFilter[][] {
  const list = filters || [];
  if (!list.length) return [];
  if (list.length === 1 && list[0]?.any && Array.isArray(list[0].any)) {
    return list[0].any
      .map((g) => {
        if (g?.all && Array.isArray(g.all)) return g.all.filter(isLeafFilter);
        if (isLeafFilter(g)) return [g];
        return [] as RowFilter[];
      })
      .filter((g) => g.length);
  }
  if (list.every(isLeafFilter)) return [list.map((f) => ({ ...f }))];
  // Mixed / opaque: keep editable leaves only
  const leaves = list.filter(isLeafFilter);
  return leaves.length ? [leaves] : [];
}

/** Serialize OR-groups back to engine row_filters (AND of leaves, or single any-of). */
function fromOrGroups(groups: RowFilter[][]): RowFilter[] {
  const clean = groups
    .map((g) =>
      g
        .map((f) => ({
          field: f.field || "",
          op: f.op || "eq",
          value: f.value ?? "",
        }))
        .filter((f) => f.field),
    )
    .filter((g) => g.length);
  if (!clean.length) return [];
  if (clean.length === 1) return clean[0];
  return [
    {
      any: clean.map((g) => (g.length === 1 ? g[0] : { all: g })),
    },
  ];
}

function emptyLeaf(field = ""): RowFilter {
  return { field, op: "eq", value: "" };
}

function metricLabel(id: string) {
  return id;
}

export function BizComparePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();

  const [pageTab, setPageTab] = useState<PageTab>("jobs");
  const [busy, setBusy] = useState(false);

  const [tasks, setTasks] = useState<TaskOpt[]>([]);
  const [metrics, setMetrics] = useState<MetricSchema[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [listKw, setListKw] = useState("");
  const debouncedListKw = useDebouncedValue(listKw, 250);

  // template editor
  const [tplOpen, setTplOpen] = useState(false);
  const [tplEditId, setTplEditId] = useState("");
  const [tplName, setTplName] = useState("");
  const [tplNote, setTplNote] = useState("");
  const [tplSheets, setTplSheets] = useState<MetricSheet[]>([]);
  const [tplSheetIdx, setTplSheetIdx] = useState(0);

  // job create / detail
  const [jobCreateOpen, setJobCreateOpen] = useState(false);
  const [jobId, setJobId] = useState("");
  const [jobDetailTab, setJobDetailTab] = useState<JobDetailTab>("config");
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [mappingId, setMappingId] = useState("");
  const [beforeTaskId, setBeforeTaskId] = useState("");
  const [afterTaskId, setAfterTaskId] = useState("");
  const [beforeBatches, setBeforeBatches] = useState<BatchOpt[]>([]);
  const [afterBatches, setAfterBatches] = useState<BatchOpt[]>([]);
  const [beforeBatchId, setBeforeBatchId] = useState("");
  const [afterBatchId, setAfterBatchId] = useState("");
  const [mode, setMode] = useState<"manual" | "auto">("manual");
  const [mapName, setMapName] = useState("端口映射");
  const [mapText, setMapText] = useState("before_if,after_if\n");
  const [validateOut, setValidateOut] = useState<any>(null);
  const [runs, setRuns] = useState<any[]>([]);
  const [runDetail, setRunDetail] = useState<any>(null);
  const [resultSheetId, setResultSheetId] = useState("");

  // result filters (server-paged)
  const [kindFilter, setKindFilter] = useState<KindFilter>("diff");
  const [resultKw, setResultKw] = useState("");
  const debouncedResultKw = useDebouncedValue(resultKw, 300);
  const [resultPage, setResultPage] = useState(1);
  const [resultPageSize, setResultPageSize] = useState(100);
  const [resultTotal, setResultTotal] = useState(0);
  const [pagedDiffs, setPagedDiffs] = useState<DiffRow[]>([]);
  const [diffsLoading, setDiffsLoading] = useState(false);
  const boardRef = useRef<HTMLDivElement | null>(null);
  const [boardFs, setBoardFs] = useState(false);
  const tplImportRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    const [taskRes, tpl, maps, j, met] = await Promise.all([
      bizStateListTasks(),
      bizCompareListTemplates(),
      bizCompareListMappings(),
      bizCompareListJobs(),
      bizCompareListMetrics(),
    ]);
    setTasks((taskRes.items || []) as TaskOpt[]);
    setTemplates((tpl.items || []) as Template[]);
    setMappings((maps.items || []) as Mapping[]);
    setJobs((j.items || []) as Job[]);
    setMetrics((met.items || []) as MetricSchema[]);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await refresh();
      } catch (e) {
        showError(formatErr(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  useEffect(() => {
    void (async () => {
      if (!beforeTaskId) {
        setBeforeBatches([]);
        return;
      }
      const b = await bizStateListBatches(beforeTaskId);
      setBeforeBatches((b.items || []) as BatchOpt[]);
    })();
  }, [beforeTaskId]);

  useEffect(() => {
    void (async () => {
      if (!afterTaskId) {
        setAfterBatches([]);
        return;
      }
      const b = await bizStateListBatches(afterTaskId);
      setAfterBatches((b.items || []) as BatchOpt[]);
    })();
  }, [afterTaskId]);

  const filteredJobs = useMemo(() => {
    const kw = debouncedListKw.trim().toLowerCase();
    if (!kw) return jobs;
    return jobs.filter((j) => {
      const tpl = templates.find((x) => x.id === j.template_id);
      const mids = (tpl?.metric_ids || templateSheets(tpl).map((s) => s.metric_id)).join(" ");
      return `${j.name} ${j.mode} ${j.status} ${tpl?.name || ""} ${mids}`.toLowerCase().includes(kw);
    });
  }, [jobs, templates, debouncedListKw]);

  const filteredTemplates = useMemo(() => {
    const kw = debouncedListKw.trim().toLowerCase();
    if (!kw) return templates;
    return templates.filter((x) => {
      const mids = (x.metric_ids || templateSheets(x).map((s) => s.metric_id)).join(" ");
      return `${x.name} ${mids} ${x.note}`.toLowerCase().includes(kw);
    });
  }, [templates, debouncedListKw]);

  const activeTplSheet = tplSheets[tplSheetIdx] || null;
  const activeTplFields = useMemo(() => {
    if (!activeTplSheet) return [];
    return metrics.find((m) => m.metric_id === activeTplSheet.metric_id)?.fields || [];
  }, [metrics, activeTplSheet]);

  const runSheets: RunSheet[] = useMemo(() => {
    const sheets = (runDetail?.sheets || []) as RunSheet[];
    if (sheets.length) return sheets;
    if (runDetail?.diffs) {
      return [
        {
          metric_id: String(runDetail.metric_id || "result"),
          key_fields: [],
          iface_fields: [],
          compare_fields: [],
          mode: "fields",
          summary: runDetail.summary,
          diffs: runDetail.diffs,
        },
      ];
    }
    return [];
  }, [runDetail]);

  useEffect(() => {
    if (!runSheets.length) {
      setResultSheetId("");
      return;
    }
    if (!resultSheetId || !runSheets.some((s) => s.metric_id === resultSheetId)) {
      setResultSheetId(runSheets[0].metric_id);
    }
  }, [runSheets, resultSheetId]);

  const activeRunSheet = useMemo(
    () => runSheets.find((s) => s.metric_id === resultSheetId) || runSheets[0] || null,
    [runSheets, resultSheetId],
  );

    // Reset page when sheet / filter / page size changes
  useEffect(() => {
    setResultPage(1);
  }, [resultSheetId, kindFilter, debouncedResultKw, resultPageSize, runDetail?.id]);

  useEffect(() => {
    const runId = String(runDetail?.id || "");
    const mid = resultSheetId || activeRunSheet?.metric_id || "";
    if (!runId || !mid || jobDetailTab !== "result") {
      setPagedDiffs([]);
      setResultTotal(0);
      return;
    }
    let cancelled = false;
    void (async () => {
      setDiffsLoading(true);
      try {
        const res = await bizCompareListRunDiffs({
          runId,
          metricId: mid,
          kind: kindFilter,
          kw: debouncedResultKw.trim(),
          page: resultPage,
          pageSize: resultPageSize,
        });
        if (cancelled) return;
        setPagedDiffs((res.items || []) as DiffRow[]);
        setResultTotal(Number(res.total || 0));
        const pages = Math.max(
          1,
          Math.ceil(Number(res.total || 0) / Number(res.page_size || resultPageSize)),
        );
        if (resultPage > pages) setResultPage(pages);
      } catch (e) {
        if (!cancelled) {
          // Keep previous rows to avoid strip/table jump; only clear on hard empty run
          showError(formatErr(e));
        }
      } finally {
        if (!cancelled) setDiffsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    runDetail?.id,
    resultSheetId,
    activeRunSheet?.metric_id,
    kindFilter,
    debouncedResultKw,
    resultPage,
    resultPageSize,
    jobDetailTab,
    showError,
  ]);

  const resultColumns = useMemo(() => {
    const keys = activeRunSheet?.key_fields?.length
      ? activeRunSheet.key_fields
      : Object.keys((pagedDiffs[0]?.key as Record<string, unknown>) || {});
    const compare = (activeRunSheet?.compare_fields || []).filter((f) => !keys.includes(f));
    const compareSet = new Set(compare);
    const keySet = new Set(keys);
    let display = (activeRunSheet?.display_fields || []).filter(Boolean);
    if (!display.length) {
      display = [...keys, ...compare];
    } else {
      // keys first, then rest of display order
      const rest = display.filter((f) => !keySet.has(f));
      display = [...keys, ...rest];
    }
    const extras = display.filter((f) => !keySet.has(f));
    return {
      keys,
      compare,
      extras,
      compareSet,
      presence: !(activeRunSheet?.compare_fields || []).length,
    };
  }, [activeRunSheet, pagedDiffs]);

  const kindLabel = (kind: string) => {
    if (kind === "added") return t("bizCompare.kindAddedShort");
    if (kind === "removed") return t("bizCompare.kindRemovedShort");
    if (kind === "changed") return t("bizCompare.kindChangedShort");
    if (kind === "unchanged") return t("bizCompare.kindUnchangedShort");
    return kind;
  };

  const verdictLabel = (kind: string) => {
    if (kind === "unchanged") return t("bizCompare.kindPass");
    return t("bizCompare.kindFail");
  };

  const failFieldsLabel = (d: DiffRow) => {
    if (d.kind === "added" || d.kind === "removed") return t("bizCompare.failWholeRow");
    const names = failFieldNames(d);
    return names.length ? names.join(" · ") : t("bizCompare.failFieldsEmpty");
  };

  const summary = runDetail?.summary || {};
  const resultPages = Math.max(1, Math.ceil(resultTotal / Math.max(1, resultPageSize)));
  const sheetCards = useMemo(() => {
    const raw = (summary.sheet_cards || []) as Array<{
      metric_id: string;
      mode?: string;
      added?: number;
      removed?: number;
      changed?: number;
      unchanged?: number;
      before_count?: number;
      after_count?: number;
      diff_count?: number;
      pass_rate?: number;
    }>;
    // Diffs first so ops can scan quickly when many sheets
    return [...raw].sort((a, b) => {
      const da = Number(a.diff_count || 0);
      const db = Number(b.diff_count || 0);
      if (da !== db) return db - da;
      return String(a.metric_id).localeCompare(String(b.metric_id));
    });
  }, [summary.sheet_cards]);
  const sheetFailCount = sheetCards.filter((c) => Number(c.diff_count || 0) > 0).length;
  const activeSheetCard = sheetCards.find((c) => c.metric_id === resultSheetId) || sheetCards[0];

  useEffect(() => {
    const syncFs = () => {
      const el = boardRef.current;
      setBoardFs(Boolean(el && document.fullscreenElement === el));
    };
    document.addEventListener("fullscreenchange", syncFs);
    return () => document.removeEventListener("fullscreenchange", syncFs);
  }, []);

  const toggleBoardFullscreen = async () => {
    const el = boardRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement === el) await document.exitFullscreen();
      else await el.requestFullscreen();
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const downloadRunTables = async () => {
    if (!runDetail?.id) return;
    setBusy(true);
    try {
      await bizCompareDownloadRun(String(runDetail.id));
      showOk(t("bizCompare.exportOk"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const updateActiveSheet = (patch: Partial<MetricSheet>) => {
    setTplSheets((prev) =>
      prev.map((s, i) => {
        if (i !== tplSheetIdx) return s;
        const next = { ...s, ...patch };
        // Keys are identity — drop from compare
        if (patch.key_fields || patch.compare_fields) {
          const keySet = new Set(next.key_fields);
          next.compare_fields = next.compare_fields.filter((f) => !keySet.has(f));
        }
        if (patch.display_fields && !patch.key_fields && !patch.compare_fields) {
          // Explicit display toggle: keys always forced on
          const disp = new Set(patch.display_fields);
          for (const k of next.key_fields) disp.add(k);
          next.display_fields = [
            ...next.key_fields,
            ...[...disp].filter((f) => !next.key_fields.includes(f)),
          ];
        } else if (patch.key_fields || patch.compare_fields) {
          const disp = new Set(next.display_fields || []);
          for (const k of next.key_fields) disp.add(k);
          if (patch.compare_fields) {
            const prevCompare = new Set(s.compare_fields || []);
            for (const f of next.compare_fields) {
              if (!prevCompare.has(f)) disp.add(f);
            }
          }
          next.display_fields = [
            ...next.key_fields,
            ...[...disp].filter((f) => !next.key_fields.includes(f)),
          ];
        }
        return next;
      }),
    );
  };

  const openNewTemplate = () => {
    setTplEditId("");
    setTplName("");
    setTplNote("");
    setTplSheets([]);
    setTplSheetIdx(0);
    setTplOpen(true);
  };

  const openEditTemplate = (tpl: Template) => {
    const sheets = templateSheets(tpl);
    setTplEditId(tpl.id);
    setTplName(tpl.name);
    setTplNote(tpl.note || "");
    setTplSheets(sheets.length ? sheets.map(cloneSheet) : []);
    setTplSheetIdx(0);
    setTplOpen(true);
  };

  const toggleTplMetric = (metricId: string, on: boolean) => {
    if (on) {
      const schema = metrics.find((m) => m.metric_id === metricId);
      setTplSheets((prev) => {
        if (prev.some((s) => s.metric_id === metricId)) return prev;
        const next = [...prev, defaultSheetForMetric(schema, metricId)];
        setTplSheetIdx(next.length - 1);
        return next;
      });
      return;
    }
    setTplSheets((prev) => {
      const idx = prev.findIndex((s) => s.metric_id === metricId);
      if (idx < 0) return prev;
      const next = prev.filter((_, i) => i !== idx);
      setTplSheetIdx((cur) => {
        if (!next.length) return 0;
        if (cur > idx) return cur - 1;
        if (cur >= next.length) return next.length - 1;
        return cur;
      });
      return next;
    });
  };

  const removeTplMetric = (idx: number) => {
    setTplSheets((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      setTplSheetIdx((cur) => {
        if (!next.length) return 0;
        if (cur > idx) return cur - 1;
        if (cur >= next.length) return next.length - 1;
        return cur;
      });
      return next;
    });
  };

  const saveTemplate = async () => {
    if (!tplSheets.length) {
      showError(t("bizCompare.metricsRequired"));
      return;
    }
    for (const s of tplSheets) {
      if (!s.key_fields.length) {
        showError(`${metricLabel(s.metric_id)}: ${t("bizCompare.keyRequired")}`);
        return;
      }
    }
    setBusy(true);
    try {
      const body = {
        name: tplName || tplSheets.map((s) => s.metric_id).join("+"),
        note: tplNote,
        metrics: tplSheets.map((s) => ({
          metric_id: s.metric_id,
          key_fields: s.key_fields,
          iface_fields: s.iface_fields,
          compare_fields: s.compare_fields,
          display_fields: s.display_fields || [...s.key_fields, ...s.compare_fields],
          row_filters: s.row_filters || [],
          field_rules: s.field_rules || [],
        })),
      };
      if (tplEditId) await bizCompareUpdateTemplate(tplEditId, body);
      else await bizCompareCreateTemplate(body);
      showOk(t("bizCompare.templateSaved"));
      setTplOpen(false);
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const removeTemplate = async (id: string) => {
    if (!window.confirm(t("bizCompare.confirmDeleteTemplate"))) return;
    setBusy(true);
    try {
      await bizCompareDeleteTemplate(id);
      showOk(t("bizCompare.templateDeleted"));
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const exportTemplate = (tpl: Template) => {
    const payload = templateExportPayload(tpl);
    const safe = (tpl.name || "template").replace(/[^\w\u4e00-\u9fff.-]+/g, "_").slice(0, 64);
    downloadJsonFile(`netx-compare-template-${safe}.json`, payload);
    showOk(t("bizCompare.templateExported"));
  };

  const importTemplateFile = async (file: File) => {
    setBusy(true);
    try {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        showError(t("bizCompare.templateImportInvalid"));
        return;
      }
      const body = parseTemplateImport(parsed);
      if (!body) {
        showError(t("bizCompare.templateImportInvalid"));
        return;
      }
      await bizCompareCreateTemplate({
        name: body.name,
        note: body.note,
        metrics: body.metrics.map((s) => ({
          metric_id: s.metric_id,
          key_fields: s.key_fields,
          iface_fields: s.iface_fields,
          compare_fields: s.compare_fields,
          display_fields: s.display_fields || [...s.key_fields, ...s.compare_fields],
          row_filters: s.row_filters || [],
          field_rules: s.field_rules || [],
        })),
      });
      showOk(t("bizCompare.templateImported"));
      await refresh();
      setPageTab("templates");
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
      if (tplImportRef.current) tplImportRef.current.value = "";
    }
  };

  const removeJob = async (id: string) => {
    if (!window.confirm(t("bizCompare.confirmDeleteJob"))) return;
    setBusy(true);
    try {
      await bizCompareDeleteJob(id);
      if (jobId === id) closeJob();
      showOk(t("bizCompare.jobDeleted"));
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

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

  const loadMappingText = (id: string) => {
    const m = mappings.find((x) => x.id === id);
    if (!m) return;
    setMappingId(id);
    setMapName(m.name);
    setMapText(["before_if,after_if", ...m.rows.map((r) => `${r.before_if},${r.after_if}`)].join("\n"));
  };

  const saveMapping = async () => {
    setBusy(true);
    try {
      const rows = parseMapRows();
      if (mappingId) await bizCompareUpdateMapping(mappingId, { name: mapName, rows });
      else {
        const m = await bizCompareCreateMapping({ name: mapName, rows });
        setMappingId(String(m.id));
      }
      showOk(t("bizCompare.mappingSaved"));
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const doValidate = async () => {
    if (!mappingId || !beforeBatchId || !afterBatchId) {
      showError(t("bizCompare.validateNeed"));
      return;
    }
    setBusy(true);
    try {
      const v = await bizCompareValidateMapping({
        mapping_id: mappingId,
        before_batch_id: beforeBatchId,
        after_batch_id: afterBatchId,
        template_id: templateId,
      });
      setValidateOut(v);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const resetJobForm = (preset?: Partial<Job>) => {
    setName(preset?.name || t("bizCompare.defaultJobName"));
    setTemplateId(preset?.template_id || templates[0]?.id || "");
    setMappingId(preset?.mapping_id || "");
    setBeforeTaskId(preset?.before_task_id || "");
    setAfterTaskId(preset?.after_task_id || "");
    setBeforeBatchId(preset?.before_batch_id || "");
    setAfterBatchId(preset?.after_batch_id || "");
    setMode(preset?.mode === "auto" ? "auto" : "manual");
    setValidateOut(null);
    if (preset?.mapping_id) loadMappingText(preset.mapping_id);
    else {
      setMapName(t("bizCompare.mapping"));
      setMapText("before_if,after_if\n");
    }
  };

  const openCreateJob = () => {
    resetJobForm();
    setJobCreateOpen(true);
  };

  const createJob = async () => {
    setBusy(true);
    try {
      const j = await bizCompareCreateJob({
        name,
        template_id: templateId,
        mapping_id: mappingId,
        before_task_id: beforeTaskId,
        after_task_id: afterTaskId || beforeTaskId,
        before_batch_id: beforeBatchId,
        after_batch_id: mode === "manual" ? afterBatchId : "",
        mode,
      });
      showOk(t("bizCompare.created"));
      setJobCreateOpen(false);
      await refresh();
      await openJob(String(j.id));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const openJob = async (id: string) => {
    setJobId(id);
    setJobDetailTab("config");
    setRunDetail(null);
    setResultSheetId("");
    setKindFilter("diff");
    setResultKw("");
    const job = jobs.find((x) => x.id === id);
    if (job) resetJobForm(job);
    try {
      const r = await bizCompareListRuns(id);
      setRuns(r.items || []);
      if ((r.items || []).length) {
        const latest = await bizCompareGetRun(String((r.items as any[])[0].id));
        setRunDetail(latest);
        setJobDetailTab("result");
      }
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const closeJob = () => {
    setJobId("");
    setRuns([]);
    setRunDetail(null);
    setResultSheetId("");
  };

  const saveJobConfig = async () => {
    if (!jobId) return;
    setBusy(true);
    try {
      await bizCompareUpdateJob(jobId, {
        name,
        template_id: templateId,
        mapping_id: mappingId,
        before_task_id: beforeTaskId,
        after_task_id: afterTaskId || beforeTaskId,
        before_batch_id: beforeBatchId,
        after_batch_id: mode === "manual" ? afterBatchId : "",
        mode,
      });
      showOk(t("bizCompare.jobSaved"));
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    if (!jobId) return;
    setBusy(true);
    try {
      await bizCompareUpdateJob(jobId, {
        before_batch_id: beforeBatchId,
        after_batch_id: mode === "manual" ? afterBatchId : "",
        mode,
        mapping_id: mappingId,
        template_id: templateId,
      });
      const run = await bizCompareRunJob(jobId);
      setRunDetail(run);
      setJobDetailTab("result");
      showOk(t("bizCompare.ran"));
      const r = await bizCompareListRuns(jobId);
      setRuns(r.items || []);
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const loadRun = async (runId: string) => {
    try {
      const d = await bizCompareGetRun(runId);
      setRunDetail(d);
      setKindFilter("diff");
      setResultKw("");
      setJobDetailTab("result");
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const renderJobForm = (compact = false) => (
    <div className="bs-cmp-form" style={{ display: "grid", gap: 8 }}>
      <label className="ui-field ui-field--full">
        <span className="ui-field__label">{t("bizCompare.jobName")}</span>
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <FieldSelect
        label={t("bizCompare.template")}
        value={templateId}
        onChange={(e) => setTemplateId(e.target.value)}
        fullWidth
      >
        {templates.map((tpl) => {
          const mids = tpl.metric_ids || templateSheets(tpl).map((s) => s.metric_id);
          return (
            <option key={tpl.id} value={tpl.id}>
              {tpl.name} ({mids.length} {t("bizCompare.sheetsUnit")})
            </option>
          );
        })}
      </FieldSelect>
      <FieldSelect
        label={t("bizCompare.mode")}
        value={mode}
        onChange={(e) => setMode(e.target.value as "manual" | "auto")}
        fullWidth
      >
        <option value="manual">{t("bizCompare.modeManual")}</option>
        <option value="auto">{t("bizCompare.modeAuto")}</option>
      </FieldSelect>
      <FieldSelect
        label={t("bizCompare.beforeTask")}
        value={beforeTaskId}
        onChange={(e) => setBeforeTaskId(e.target.value)}
        fullWidth
      >
        <option value="">{t("bizCompare.pick")}</option>
        {tasks.map((row) => (
          <option key={row.id} value={row.id}>
            {taskLabel(row)}
          </option>
        ))}
      </FieldSelect>
      <FieldSelect
        label={t("bizCompare.beforeBatch")}
        value={beforeBatchId}
        onChange={(e) => setBeforeBatchId(e.target.value)}
        fullWidth
      >
        <option value="">{t("bizCompare.pick")}</option>
        {beforeBatches.map((b) => (
          <option key={b.id} value={b.id}>
            {fmtTime(b.started_at)} · {b.status} · rows={b.row_count}
          </option>
        ))}
      </FieldSelect>
      <FieldSelect
        label={t("bizCompare.afterTask")}
        value={afterTaskId}
        onChange={(e) => setAfterTaskId(e.target.value)}
        fullWidth
      >
        <option value="">{t("bizCompare.sameAsBefore")}</option>
        {tasks.map((row) => (
          <option key={row.id} value={row.id}>
            {taskLabel(row)}
          </option>
        ))}
      </FieldSelect>
      {mode === "manual" ? (
        <FieldSelect
          label={t("bizCompare.afterBatch")}
          value={afterBatchId}
          onChange={(e) => setAfterBatchId(e.target.value)}
          fullWidth
        >
          <option value="">{t("bizCompare.pick")}</option>
          {afterBatches.map((b) => (
            <option key={b.id} value={b.id}>
              {fmtTime(b.started_at)} · {b.status} · rows={b.row_count}
            </option>
          ))}
        </FieldSelect>
      ) : (
        <p className="muted">{t("bizCompare.autoHint")}</p>
      )}

      {!compact ? (
        <div className="bs-cmp-mapping">
          <h4 style={{ margin: "8px 0" }}>{t("bizCompare.mapping")}</h4>
          <p className="muted">{t("bizCompare.mappingOptionalHint")}</p>
          <div className="filter-inline" style={{ marginBottom: 8 }}>
            <FieldSelect
              value={mappingId}
              onChange={(e) => {
                const id = e.target.value;
                if (id) loadMappingText(id);
                else {
                  setMappingId("");
                  setMapText("before_if,after_if\n");
                }
              }}
            >
              <option value="">{t("bizCompare.newMapping")}</option>
              {mappings.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </FieldSelect>
            <Input value={mapName} placeholder={t("bizCompare.mapName")} onChange={(e) => setMapName(e.target.value)} />
            <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void saveMapping()}>
              {t("bizCompare.saveMapping")}
            </Button>
            <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void doValidate()}>
              {t("bizCompare.validateMapping")}
            </Button>
          </div>
          <textarea
            value={mapText}
            onChange={(e) => setMapText(e.target.value)}
            placeholder={t("bizCompare.mapHint")}
            rows={5}
            style={{ width: "100%", fontFamily: "ui-monospace, monospace" }}
          />
          {validateOut ? (
            <pre className="muted" style={{ fontSize: 12, maxHeight: 120, overflow: "auto" }}>
              {JSON.stringify(validateOut, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("bizCompare.title")}</h2>
        <div className="btn-row">
          {pageTab === "templates" ? (
            <>
              <input
                ref={tplImportRef}
                type="file"
                accept="application/json,.json"
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void importTemplateFile(f);
                }}
              />
              <Button
                size="sm"
                variant="secondary"
                isDisabled={busy}
                onPress={() => tplImportRef.current?.click()}
              >
                {t("bizCompare.importTemplate")}
              </Button>
              <Button size="sm" variant="primary" onPress={openNewTemplate}>
                {t("bizCompare.newTemplate")}
              </Button>
            </>
          ) : (
            <Button size="sm" variant="primary" onPress={openCreateJob}>
              {t("bizCompare.createCompare")}
            </Button>
          )}
        </div>
      </div>

      <div className="pt-list">
        <div className="btn-row nm-config-modal__tabs" role="tablist">
          <Button
            size="sm"
            variant={pageTab === "jobs" ? "primary" : "secondary"}
            className={pageTab === "jobs" ? "is-active" : undefined}
            onPress={() => setPageTab("jobs")}
          >
            {t("bizCompare.jobList")}
          </Button>
          <Button
            size="sm"
            variant={pageTab === "templates" ? "primary" : "secondary"}
            className={pageTab === "templates" ? "is-active" : undefined}
            onPress={() => setPageTab("templates")}
          >
            {t("bizCompare.templates")}
          </Button>
        </div>

        <div className="filter-inline">
          <Input
            value={listKw}
            placeholder={
              pageTab === "jobs" ? t("bizCompare.jobFilterPh") : t("bizCompare.templateFilterPh")
            }
            onChange={(e) => setListKw(e.target.value)}
          />
        </div>

        {pageTab === "templates" ? (
          <div className="pt-list-table-wrap">
            <p className="muted" style={{ margin: "0 0 8px" }}>
              {t("bizCompare.templateImportHint")}
            </p>
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("bizCompare.colName")}</th>
                  <th>{t("bizCompare.colSheets")}</th>
                  <th>{t("bizCompare.note")}</th>
                  <th>{t("bizCompare.colActions")}</th>
                </tr>
              </thead>
              <tbody>
                {filteredTemplates.map((tpl) => {
                  const sheets = templateSheets(tpl);
                  return (
                    <tr key={tpl.id}>
                      <td>
                        <div className="pt-list-task-name">{tpl.name}</div>
                      </td>
                      <td>
                        <div className="bs-cmp-metric-chips">
                          {sheets.map((s) => (
                            <code key={s.metric_id} className="bs-cmp-metric-chip">
                              {s.metric_id}
                              {!s.compare_fields?.length ? (
                                <span className="muted"> · {t("bizCompare.presenceShort")}</span>
                              ) : null}
                            </code>
                          ))}
                          {!sheets.length ? "—" : null}
                        </div>
                      </td>
                      <td className="muted">{tpl.note || "—"}</td>
                      <td>
                        <div className="pt-list-actions">
                          <Button size="sm" variant="secondary" onPress={() => openEditTemplate(tpl)}>
                            {t("bizCompare.edit")}
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            isDisabled={busy}
                            onPress={() => exportTemplate(tpl)}
                          >
                            {t("bizCompare.exportTemplate")}
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            isDisabled={busy}
                            onPress={() => void removeTemplate(tpl.id)}
                          >
                            {t("bizCompare.delete")}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {!filteredTemplates.length ? (
                  <tr>
                    <td colSpan={4}>
                      <div className="pt-list-empty">{t("bizCompare.emptyTemplates")}</div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("bizCompare.colName")}</th>
                  <th>{t("bizCompare.template")}</th>
                  <th>{t("bizCompare.colMode")}</th>
                  <th>{t("bizCompare.colStatus")}</th>
                  <th>{t("bizCompare.colActions")}</th>
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map((j) => {
                  const tpl = templates.find((x) => x.id === j.template_id);
                  const n = templateSheets(tpl).length;
                  return (
                    <tr key={j.id}>
                      <td>
                        <div className="pt-list-task-name">{j.name}</div>
                      </td>
                      <td className="muted">
                        {tpl?.name || j.template_id.slice(0, 8)}
                        {n ? ` · ${n} ${t("bizCompare.sheetsUnit")}` : ""}
                      </td>
                      <td>{j.mode}</td>
                      <td>
                        <NmStatusChip color={jobChipColor(j.status)}>{j.status}</NmStatusChip>
                      </td>
                      <td>
                        <div className="pt-list-actions">
                          <Button size="sm" variant="primary" onPress={() => void openJob(j.id)}>
                            {t("bizCompare.detail")}
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            isDisabled={busy}
                            onPress={() => void removeJob(j.id)}
                          >
                            {t("bizCompare.delete")}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {!filteredJobs.length ? (
                  <tr>
                    <td colSpan={5}>
                      <div className="pt-list-empty">{t("bizCompare.emptyJobs")}</div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Template editor */}
      <AppModalShell open={tplOpen} onClose={() => setTplOpen(false)} size="lg" className="app-heroui-modal--xl">
        <Modal.Header>
          <Modal.Heading>
            {tplEditId ? t("bizCompare.editTemplate") : t("bizCompare.newTemplate")}
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizCompare.colName")}</span>
            <Input value={tplName} onChange={(e) => setTplName(e.target.value)} />
          </label>
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizCompare.note")}</span>
            <Input value={tplNote} onChange={(e) => setTplNote(e.target.value)} />
          </label>

          <div className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizCompare.stepPickMetrics")}</span>
            <p className="muted" style={{ marginTop: 0 }}>
              {t("bizCompare.pickMetricsHint")}
            </p>
            <div
              className="pt-list-table-wrap"
              style={{ maxHeight: 220, overflow: "auto", border: "1px solid var(--border, #ddd)", borderRadius: 6 }}
            >
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th style={{ width: 48 }} />
                    <th>{t("bizCompare.addMetric")}</th>
                    <th>{t("bizCompare.colFields")}</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.map((m) => {
                    const checked = tplSheets.some((s) => s.metric_id === m.metric_id);
                    return (
                      <tr key={m.metric_id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => toggleTplMetric(m.metric_id, e.target.checked)}
                          />
                        </td>
                        <td>
                          <code>{m.metric_id}</code>
                        </td>
                        <td className="muted">{m.fields?.length || 0}</td>
                      </tr>
                    );
                  })}
                  {!metrics.length ? (
                    <tr>
                      <td colSpan={3}>
                        <div className="pt-list-empty">{t("bizCompare.noMetricFields")}</div>
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <p className="muted">
              {t("bizCompare.selectedCount", { n: String(tplSheets.length) })}
            </p>
          </div>

          <div className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizCompare.stepConfigRules")}</span>
            <p className="muted" style={{ marginTop: 0 }}>
              {t("bizCompare.templateHint")}
            </p>
          </div>

          {!tplSheets.length ? (
            <div className="pt-list-empty">{t("bizCompare.noMetricsYet")}</div>
          ) : (
            <>
              <div className="bs-sheet-tabs" role="tablist">
                {tplSheets.map((s, i) => (
                  <button
                    key={s.metric_id}
                    type="button"
                    className={`bs-sheet-tab${i === tplSheetIdx ? " is-active" : ""}`}
                    onClick={() => setTplSheetIdx(i)}
                  >
                    {s.metric_id}
                    {!s.compare_fields.length ? (
                      <span className="bs-sheet-tab__count">{t("bizCompare.presenceShort")}</span>
                    ) : (
                      <span className="bs-sheet-tab__count">{s.compare_fields.length}</span>
                    )}
                    {(s.row_filters || []).length ? (
                      <span className="bs-sheet-tab__count">F{(s.row_filters || []).length}</span>
                    ) : null}
                  </button>
                ))}
              </div>

              {activeTplSheet ? (
                <>
                  <div className="filter-inline" style={{ justifyContent: "space-between" }}>
                    <span className="muted">
                      {activeTplSheet.compare_fields.length
                        ? t("bizCompare.modeFields")
                        : t("bizCompare.modePresence")}
                      {(activeTplSheet.row_filters || []).length
                        ? ` · ${(activeTplSheet.row_filters || []).length} filter(s)`
                        : ""}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onPress={() => removeTplMetric(tplSheetIdx)}
                    >
                      {t("bizCompare.removeSheet")}
                    </Button>
                  </div>

                  <div className="ui-field ui-field--full">
                    <span className="ui-field__label">{t("bizCompare.rowFilters")}</span>
                    <p className="muted" style={{ marginTop: 0 }}>
                      {t("bizCompare.rowFiltersHint")}
                    </p>
                    {(() => {
                      const groups = toOrGroups(activeTplSheet.row_filters || []);
                      const setGroups = (next: RowFilter[][]) =>
                        updateActiveSheet({ row_filters: fromOrGroups(next) });
                      const fieldOpts = activeTplFields.map((f) => f.name);
                      const opOpts = [
                        "eq",
                        "ne",
                        "in",
                        "not_in",
                        "empty",
                        "not_empty",
                        "regex",
                        "age_timer",
                      ];
                      return (
                        <>
                          <div className="filter-inline" style={{ flexWrap: "wrap", gap: 8 }}>
                            <Button
                              size="sm"
                              variant="secondary"
                              onPress={() => {
                                const g = groups.length ? groups : [];
                                setGroups([
                                  ...g,
                                  [emptyLeaf(fieldOpts[0] || "")],
                                ]);
                              }}
                            >
                              {t("bizCompare.filterAddOr")}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onPress={() => setGroups([])}
                            >
                              {t("bizCompare.filterClear")}
                            </Button>
                          </div>
                          {!groups.length ? (
                            <p className="muted">{t("bizCompare.rowFiltersEmpty")}</p>
                          ) : null}
                          {groups.map((group, gi) => (
                            <div
                              key={gi}
                              style={{
                                marginTop: 10,
                                padding: "8px 10px",
                                border: "1px solid var(--border, #ddd)",
                                borderRadius: 6,
                              }}
                            >
                              {gi > 0 ? (
                                <div className="muted" style={{ marginBottom: 6 }}>
                                  {t("bizCompare.filterOr")}
                                </div>
                              ) : null}
                              {group.map((filt, fi) => (
                                <div key={fi} className="filter-inline" style={{ marginTop: fi ? 6 : 0 }}>
                                  <select
                                    value={filt.field || ""}
                                    onChange={(e) => {
                                      const next = groups.map((g) => g.map((x) => ({ ...x })));
                                      next[gi][fi] = { ...next[gi][fi], field: e.target.value };
                                      setGroups(next);
                                    }}
                                  >
                                    <option value="">{t("bizCompare.filterField")}</option>
                                    {fieldOpts.map((name) => (
                                      <option key={name} value={name}>
                                        {name}
                                      </option>
                                    ))}
                                  </select>
                                  <select
                                    value={filt.op || "eq"}
                                    onChange={(e) => {
                                      const next = groups.map((g) => g.map((x) => ({ ...x })));
                                      next[gi][fi] = { ...next[gi][fi], op: e.target.value };
                                      setGroups(next);
                                    }}
                                  >
                                    {opOpts.map((op) => (
                                      <option key={op} value={op}>
                                        {op}
                                      </option>
                                    ))}
                                  </select>
                                  <Input
                                    value={
                                      Array.isArray(filt.value)
                                        ? filt.value.join(",")
                                        : String(filt.value ?? "")
                                    }
                                    placeholder={t("bizCompare.filterValue")}
                                    isDisabled={["empty", "not_empty", "age_timer"].includes(
                                      filt.op || "",
                                    )}
                                    onChange={(e) => {
                                      const next = groups.map((g) => g.map((x) => ({ ...x })));
                                      const op = next[gi][fi]?.op || "eq";
                                      let value: string | string[] = e.target.value;
                                      if (op === "in" || op === "not_in") {
                                        value = e.target.value
                                          .split(",")
                                          .map((x) => x.trim())
                                          .filter(Boolean);
                                      }
                                      next[gi][fi] = { ...next[gi][fi], value };
                                      setGroups(next);
                                    }}
                                  />
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onPress={() => {
                                      const next = groups.map((g) => g.map((x) => ({ ...x })));
                                      next[gi].splice(fi, 1);
                                      setGroups(next.filter((g) => g.length));
                                    }}
                                  >
                                    ×
                                  </Button>
                                </div>
                              ))}
                              <div className="filter-inline" style={{ marginTop: 8, gap: 8 }}>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onPress={() => {
                                    const next = groups.map((g) => g.map((x) => ({ ...x })));
                                    next[gi].push(emptyLeaf(fieldOpts[0] || ""));
                                    setGroups(next);
                                  }}
                                >
                                  {t("bizCompare.filterAddAnd")}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onPress={() => {
                                    const next = groups.filter((_, i) => i !== gi);
                                    setGroups(next);
                                  }}
                                >
                                  {t("bizCompare.filterRemoveGroup")}
                                </Button>
                              </div>
                            </div>
                          ))}
                        </>
                      );
                    })()}
                  </div>

                  <div className="pt-list-table-wrap">
                    <table className="data-table pt-list-table">
                      <thead>
                        <tr>
                          <th>{t("bizCompare.field")}</th>
                          <th>{t("bizCompare.keyFields")}</th>
                          <th>{t("bizCompare.ifaceFields")}</th>
                          <th>{t("bizCompare.compareFields")}</th>
                          <th>{t("bizCompare.compareMode")}</th>
                          <th>{t("bizCompare.tolerance")}</th>
                          <th>{t("bizCompare.displayField")}</th>
                          <th>{t("bizCompare.normalizeField")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {activeTplFields.map((f) => {
                          const isKey = activeTplSheet.key_fields.includes(f.name);
                          const isCompare =
                            !isKey && activeTplSheet.compare_fields.includes(f.name);
                          const isDisplay = (activeTplSheet.display_fields || []).includes(f.name);
                          const rule = ruleForField(activeTplSheet, f.name);
                          const cmpMode = (rule.compare || "eq").toLowerCase() || "eq";
                          const norm = rule.normalize || "none";
                          const needsTol = cmpMode === "numeric" || cmpMode === "percent";
                          return (
                            <tr key={f.name}>
                              <td>
                                <div className="pt-list-task-name">{f.display_name || f.name}</div>
                                <div className="muted">
                                  <code>{f.name}</code> · {f.role}
                                </div>
                              </td>
                              <td>
                                <input
                                  type="checkbox"
                                  checked={isKey}
                                  onChange={(e) =>
                                    updateActiveSheet({
                                      key_fields: toggleInList(
                                        activeTplSheet.key_fields,
                                        f.name,
                                        e.target.checked,
                                      ),
                                    })
                                  }
                                />
                              </td>
                              <td>
                                <input
                                  type="checkbox"
                                  checked={activeTplSheet.iface_fields.includes(f.name)}
                                  onChange={(e) =>
                                    updateActiveSheet({
                                      iface_fields: toggleInList(
                                        activeTplSheet.iface_fields,
                                        f.name,
                                        e.target.checked,
                                      ),
                                    })
                                  }
                                />
                              </td>
                              <td>
                                <input
                                  type="checkbox"
                                  disabled={isKey}
                                  title={isKey ? t("bizCompare.keyIsIdentity") : undefined}
                                  checked={isCompare}
                                  onChange={(e) =>
                                    updateActiveSheet({
                                      compare_fields: toggleInList(
                                        activeTplSheet.compare_fields,
                                        f.name,
                                        e.target.checked,
                                      ),
                                    })
                                  }
                                />
                              </td>
                              <td>
                                <select
                                  value={cmpMode === "ignore" ? "eq" : cmpMode}
                                  disabled={!isCompare}
                                  onChange={(e) =>
                                    updateActiveSheet({
                                      field_rules: upsertFieldRule(activeTplSheet, f.name, {
                                        compare: e.target.value,
                                        tolerance:
                                          e.target.value === "eq"
                                            ? undefined
                                            : rule.tolerance ?? 0,
                                      }),
                                    })
                                  }
                                >
                                  <option value="eq">{t("bizCompare.modeEq")}</option>
                                  <option value="numeric">{t("bizCompare.modeNumeric")}</option>
                                  <option value="percent">{t("bizCompare.modePercent")}</option>
                                </select>
                              </td>
                              <td>
                                <Input
                                  type="number"
                                  value={
                                    needsTol && rule.tolerance !== undefined && rule.tolerance !== null
                                      ? String(rule.tolerance)
                                      : ""
                                  }
                                  placeholder={cmpMode === "percent" ? "%" : ""}
                                  isDisabled={!isCompare || !needsTol}
                                  onChange={(e) => {
                                    const raw = e.target.value.trim();
                                    const tol = raw === "" ? undefined : Number(raw);
                                    updateActiveSheet({
                                      field_rules: upsertFieldRule(activeTplSheet, f.name, {
                                        compare: cmpMode,
                                        tolerance: Number.isFinite(tol as number)
                                          ? (tol as number)
                                          : undefined,
                                      }),
                                    });
                                  }}
                                />
                              </td>
                              <td>
                                <input
                                  type="checkbox"
                                  disabled={isKey}
                                  title={isKey ? t("bizCompare.keyAlwaysDisplay") : undefined}
                                  checked={isKey || isDisplay}
                                  onChange={(e) =>
                                    updateActiveSheet({
                                      display_fields: toggleInList(
                                        activeTplSheet.display_fields || [],
                                        f.name,
                                        e.target.checked,
                                      ),
                                    })
                                  }
                                />
                              </td>
                              <td>
                                <select
                                  value={norm}
                                  disabled={!isCompare}
                                  onChange={(e) =>
                                    updateActiveSheet({
                                      field_rules: upsertFieldRule(activeTplSheet, f.name, {
                                        normalize: e.target.value,
                                      }),
                                    })
                                  }
                                >
                                  <option value="none">{t("bizCompare.normalizeNone")}</option>
                                  <option value="lower">{t("bizCompare.normalizeLower")}</option>
                                  <option value="upper">{t("bizCompare.normalizeUpper")}</option>
                                  <option value="mac">{t("bizCompare.normalizeMac")}</option>
                                  <option value="empty_as_blank">
                                    {t("bizCompare.normalizeEmpty")}
                                  </option>
                                </select>
                              </td>
                            </tr>
                          );
                        })}
                        {!activeTplFields.length ? (
                          <tr>
                            <td colSpan={8}>
                              <div className="pt-list-empty">{t("bizCompare.noMetricFields")}</div>
                            </td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : null}
            </>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void saveTemplate()}>
            {t("bizCompare.saveTemplate")}
          </Button>
          <Button size="sm" variant="ghost" onPress={() => setTplOpen(false)}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Create job */}
      <AppModalShell open={jobCreateOpen} onClose={() => setJobCreateOpen(false)} size="lg">
        <Modal.Header>
          <Modal.Heading>{t("bizCompare.createCompare")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">{renderJobForm(true)}</Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void createJob()}>
            {t("bizCompare.createCompare")}
          </Button>
          <Button size="sm" variant="ghost" onPress={() => setJobCreateOpen(false)}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Job detail */}
      <AppModalShell
        open={Boolean(jobId)}
        onClose={closeJob}
        size={jobDetailTab === "result" ? "cover" : "lg"}
        className={`app-heroui-modal--xl${jobDetailTab === "result" ? " bs-cmp-board-modal" : ""}`}
      >
        <Modal.Header>
          <Modal.Heading>
            {name || t("bizCompare.detail")} · {jobId.slice(0, 8)}…
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3 bs-cmp-job-body">
          <div className="btn-row nm-config-modal__tabs" role="tablist">
            <Button
              size="sm"
              variant={jobDetailTab === "config" ? "primary" : "secondary"}
              className={jobDetailTab === "config" ? "is-active" : undefined}
              onPress={() => setJobDetailTab("config")}
            >
              {t("bizCompare.tabConfig")}
            </Button>
            <Button
              size="sm"
              variant={jobDetailTab === "result" ? "primary" : "secondary"}
              className={jobDetailTab === "result" ? "is-active" : undefined}
              onPress={() => setJobDetailTab("result")}
            >
              {t("bizCompare.tabResult")}
            </Button>
          </div>

          {jobDetailTab === "config" ? (
            renderJobForm(false)
          ) : (
            <div
              ref={boardRef}
              className={`bs-cmp-board${boardFs ? " is-fullscreen" : ""}`}
            >
              <div className="bs-cmp-board__toolbar">
                <FieldSelect
                  label={t("bizCompare.pickBatchRun")}
                  value={runDetail?.id || ""}
                  onChange={(e) => {
                    const id = e.target.value;
                    if (id) void loadRun(id);
                  }}
                >
                  <option value="">{t("bizCompare.pickRun")}</option>
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {fmtTime(r.created_at)} · {t("bizCompare.failCount")}{" "}
                      {(r.summary?.added ?? 0) +
                        (r.summary?.removed ?? 0) +
                        (r.summary?.changed ?? 0)}
                    </option>
                  ))}
                </FieldSelect>
                <div className="btn-row">
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={busy || !runDetail?.id}
                    onPress={() => void downloadRunTables()}
                  >
                    {t("bizCompare.exportTables")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={!runDetail}
                    onPress={() => void toggleBoardFullscreen()}
                  >
                    {boardFs ? t("bizCompare.exitFullscreen") : t("bizCompare.fullscreen")}
                  </Button>
                </div>
              </div>

              {runDetail ? (
                <div className="bs-cmp-board__body">
                  <aside className="bs-cmp-nav" aria-label={t("bizCompare.sheetNavTitle")}>
                    <div className="bs-cmp-nav__head">
                      <div className="bs-cmp-nav__title">{t("bizCompare.sheetNavTitle")}</div>
                      <div className="bs-cmp-nav__hint">
                        {t("bizCompare.sheetNavHint", {
                          n: String(sheetCards.length || runSheets.length),
                        })}
                        {sheetFailCount > 0
                          ? ` · ${t("bizCompare.sheetFailCount", { n: String(sheetFailCount) })}`
                          : ""}
                      </div>
                    </div>
                    <div className="bs-cmp-nav__list" role="tablist">
                      {(sheetCards.length
                        ? sheetCards
                        : runSheets.map((s) => ({
                            metric_id: s.metric_id,
                            mode: s.mode,
                            added: s.summary?.added,
                            removed: s.summary?.removed,
                            changed: s.summary?.changed,
                            unchanged: s.summary?.unchanged,
                            before_count: s.summary?.before_count,
                            after_count: s.summary?.after_count,
                            diff_count:
                              Number(s.summary?.added || 0) +
                              Number(s.summary?.removed || 0) +
                              Number(s.summary?.changed || 0),
                            pass_rate: s.summary?.pass_rate,
                          }))
                      ).map((c) => {
                        const dirty = Number(c.diff_count || 0);
                        const active = resultSheetId === c.metric_id;
                        return (
                          <button
                            key={c.metric_id}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            className={`bs-cmp-nav__item${active ? " is-active" : ""}${
                              dirty > 0 ? " has-diff" : " is-clean"
                            }`}
                            onClick={() => setResultSheetId(c.metric_id)}
                            title={c.metric_id}
                          >
                            <span className="bs-cmp-nav__dot" aria-hidden />
                            <span className="bs-cmp-nav__name">{c.metric_id}</span>
                            <span className="bs-cmp-nav__meta">
                              {dirty > 0 ? (
                                <>
                                  <em className="bs-cmp-nav__fail">{t("bizCompare.kindFail")}</em>
                                  <span className="bs-cmp-nav__counts">
                                    <span>
                                      {t("bizCompare.missCount")} {Number(c.removed || 0)}
                                    </span>
                                    <span>
                                      {t("bizCompare.extraCount")} {Number(c.added || 0)}
                                    </span>
                                    <span>
                                      {t("bizCompare.mismatchCount")} {Number(c.changed || 0)}
                                    </span>
                                  </span>
                                </>
                              ) : (
                                <em className="bs-cmp-nav__pass">{t("bizCompare.sheetAllPass")}</em>
                              )}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </aside>

                  <div className="bs-cmp-main">
                    <div
                      className={`bs-cmp-overview${summary.ok ? " is-ok" : " is-warn"}`}
                    >
                      <span className="bs-cmp-overview__tag">{t("bizCompare.runOverview")}</span>
                      <span className="bs-cmp-overview__verdict">
                        {summary.ok ? t("bizCompare.verdictPass") : t("bizCompare.verdictFail")}
                      </span>
                      <span className="bs-cmp-overview__stat">
                        {t("bizCompare.failCount")}{" "}
                        <b>{summary.diff_count ?? 0}</b>
                      </span>
                      <span className="bs-cmp-overview__stat">
                        {t("bizCompare.missCount")} <b>{summary.removed ?? 0}</b>
                      </span>
                      <span className="bs-cmp-overview__stat">
                        {t("bizCompare.extraCount")} <b>{summary.added ?? 0}</b>
                      </span>
                      <span className="bs-cmp-overview__stat">
                        {t("bizCompare.mismatchCount")} <b>{summary.changed ?? 0}</b>
                      </span>
                      <span className="bs-cmp-overview__stat">
                        {t("bizCompare.matchCount")} <b>{summary.unchanged ?? 0}</b>
                      </span>
                      <span className="bs-cmp-overview__time muted">
                        {fmtTime(runDetail.created_at)}
                      </span>
                    </div>

                    <div
                      className={`bs-cmp-strip${
                        Number(activeSheetCard?.diff_count || 0) > 0 ? " is-warn" : " is-ok"
                      }`}
                    >
                      <div className="bs-cmp-strip__sheet">
                        <span className="bs-cmp-strip__sheet-tag">
                          {t("bizCompare.sheetCurrent")}
                        </span>
                        <strong
                          className="bs-cmp-strip__sheet-name"
                          title={activeSheetCard?.metric_id || ""}
                        >
                          {activeSheetCard?.metric_id || "—"}
                        </strong>
                        <span className="muted bs-cmp-strip__sheet-mode">
                          {Number(activeSheetCard?.diff_count || 0) > 0
                            ? t("bizCompare.kindFail")
                            : t("bizCompare.kindPass")}
                          {activeSheetCard?.mode === "presence"
                            ? ` · ${t("bizCompare.presenceShort")}`
                            : ""}
                        </span>
                      </div>
                      <div className="bs-cmp-strip__kinds" role="group">
                        {(
                          [
                            ["diff", Number(activeSheetCard?.diff_count || 0), "diff"],
                            ["removed", activeSheetCard?.removed ?? 0, "removed"],
                            ["added", activeSheetCard?.added ?? 0, "added"],
                            ["changed", activeSheetCard?.changed ?? 0, "changed"],
                            ["unchanged", activeSheetCard?.unchanged ?? 0, "unchanged"],
                            ["all", null, "all"],
                          ] as const
                        ).map(([id, n, cls]) => (
                          <button
                            key={id}
                            type="button"
                            className={`bs-cmp-strip__kind bs-cmp-strip__kind--${cls}${
                              kindFilter === id ? " is-active" : ""
                            }`}
                            onClick={() => setKindFilter(id as KindFilter)}
                          >
                            {id === "diff"
                              ? t("bizCompare.kindDiff")
                              : id === "all"
                                ? t("bizCompare.kindAll")
                                : kindLabel(id)}
                            {n !== null ? (
                              <>
                                {" "}
                                <b>{n}</b>
                              </>
                            ) : null}
                          </button>
                        ))}
                      </div>
                      <span className="bs-cmp-strip__range muted">
                        {activeSheetCard?.before_count ?? 0}→{activeSheetCard?.after_count ?? 0}
                      </span>
                    </div>

                    <div className="bs-cmp-filter-bar">
                      <Input
                        value={resultKw}
                        placeholder={t("bizCompare.resultFilterPh")}
                        onChange={(e) => setResultKw(e.target.value)}
                      />
                      <span className="muted bs-sheet-count">
                        {diffsLoading ? "…" : `${pagedDiffs.length}/${resultTotal}`}
                      </span>
                    </div>

                    <div
                      className={`pt-list-table-wrap bs-sheet-table bs-cmp-result-table${
                        diffsLoading ? " is-loading" : ""
                      }`}
                    >
                      <table className="data-table pt-list-table bs-cmp-diff-table">
                        <thead>
                          <tr>
                            <th className="bs-cmp-col-kind">{t("bizCompare.colKind")}</th>
                            <th className="bs-cmp-col-fail">{t("bizCompare.colFailFields")}</th>
                            {resultColumns.keys.map((k) => (
                              <th key={k}>{k}</th>
                            ))}
                            {resultColumns.extras.map((f) => (
                              <th
                                key={f}
                                className={
                                  resultColumns.compareSet.has(f)
                                    ? "bs-cmp-col-compare"
                                    : "bs-cmp-col-display"
                                }
                                title={
                                  resultColumns.compareSet.has(f)
                                    ? t("bizCompare.compareFields")
                                    : t("bizCompare.displayField")
                                }
                              >
                                {f}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {pagedDiffs.map((d, i) => {
                            const pre = (d.mapped_before || d.before || {}) as Record<
                              string,
                              unknown
                            >;
                            const post = (d.after || {}) as Record<string, unknown>;
                            const isFail = d.kind !== "unchanged";
                            return (
                              <tr key={i} className={`bs-cmp-row bs-cmp-row--${d.kind}`}>
                                <td className="bs-cmp-col-kind">
                                  <span
                                    className={`bs-cmp-badge bs-cmp-badge--${
                                      isFail ? "fail" : "pass"
                                    }`}
                                  >
                                    {verdictLabel(d.kind)}
                                  </span>
                                  <span className={`bs-cmp-badge bs-cmp-badge--${d.kind}`}>
                                    {kindLabel(d.kind)}
                                  </span>
                                </td>
                                <td className="bs-cmp-fail-cell">
                                  {isFail ? failFieldsLabel(d) : t("bizCompare.failFieldsEmpty")}
                                </td>
                                {resultColumns.keys.map((k) => (
                                  <td key={k} className="bs-cmp-key-cell">
                                    {cellText(d.key?.[k] ?? pre[k] ?? post[k]) || "—"}
                                  </td>
                                ))}
                                {resultColumns.extras.map((f) => {
                                  const pv = cellText(pre[f]);
                                  const av = cellText(post[f]);
                                  const isCmp = resultColumns.compareSet.has(f);
                                  const ch = d.changes?.[f];
                                  if (!isCmp) {
                                    const show =
                                      d.kind === "removed" ? pv || "—" : av || pv || "—";
                                    return (
                                      <td key={f} className="bs-cmp-val-cell bs-cmp-val-cell--ctx">
                                        <span className="bs-cmp-val">{show}</span>
                                      </td>
                                    );
                                  }
                                  if (d.kind === "added") {
                                    return (
                                      <td key={f} className="bs-cmp-val-cell">
                                        <span className="bs-cmp-val bs-cmp-val--post">
                                          {av || "—"}
                                        </span>
                                      </td>
                                    );
                                  }
                                  if (d.kind === "removed") {
                                    return (
                                      <td key={f} className="bs-cmp-val-cell">
                                        <span className="bs-cmp-val bs-cmp-val--pre">
                                          {pv || "—"}
                                        </span>
                                      </td>
                                    );
                                  }
                                  const mismatch = Boolean(ch);
                                  if (!mismatch) {
                                    return (
                                      <td key={f} className="bs-cmp-val-cell">
                                        <span className="bs-cmp-val">{pv || av || "—"}</span>
                                      </td>
                                    );
                                  }
                                  return (
                                    <td key={f} className="bs-cmp-val-cell bs-cmp-val-cell--diff">
                                      <span className="bs-cmp-val bs-cmp-val--pre">
                                        {pv || "—"}
                                      </span>
                                      <span className="bs-cmp-val-arrow" aria-hidden>
                                        →
                                      </span>
                                      <span className="bs-cmp-val bs-cmp-val--post">
                                        {av || "—"}
                                      </span>
                                      {ch?.reason ? (
                                        <div className="bs-cmp-val-reason muted">{ch.reason}</div>
                                      ) : null}
                                    </td>
                                  );
                                })}
                              </tr>
                            );
                          })}
                          {runDetail && !diffsLoading && !pagedDiffs.length ? (
                            <tr>
                              <td
                                colSpan={
                                  2 +
                                  resultColumns.keys.length +
                                  Math.max(resultColumns.extras.length, 0)
                                }
                              >
                                <div className="pt-list-empty">{t("bizCompare.resultEmpty")}</div>
                              </td>
                            </tr>
                          ) : null}
                        </tbody>
                      </table>
                    </div>

                    <ListPager
                      page={resultPage}
                      pages={resultPages}
                      total={resultTotal}
                      pageSize={resultPageSize}
                      pageSizeOptions={[50, 100, 200, 500]}
                      onPageChange={setResultPage}
                      onPageSizeChange={(n) => {
                        setResultPageSize(n);
                        setResultPage(1);
                      }}
                      disabled={diffsLoading || !runDetail}
                    />
                  </div>
                </div>
              ) : (
                <div className="pt-list-empty">{t("bizCompare.noRuns")}</div>
              )}
            </div>
          )}
        </Modal.Body>
        <Modal.Footer>
          {jobDetailTab === "config" ? (
            <>
              <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void saveJobConfig()}>
                {t("bizCompare.saveJob")}
              </Button>
              <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void runNow()}>
                {t("bizCompare.runNow")}
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={busy || !runDetail?.id}
                onPress={() => void downloadRunTables()}
              >
                {t("bizCompare.exportTables")}
              </Button>
              <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void runNow()}>
                {t("bizCompare.runNow")}
              </Button>
            </>
          )}
          <Button size="sm" variant="ghost" onPress={closeJob}>
            {t("bizState.cancel")}
          </Button>
          {jobId ? (
            <Button size="sm" variant="danger" isDisabled={busy} onPress={() => void removeJob(jobId)}>
              {t("bizCompare.delete")}
            </Button>
          ) : null}
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
