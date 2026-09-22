import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  bizCompareDeleteRun,
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
import { cutoverCachedGet, cutoverCachedGetSWR, invalidateCutoverCache } from "./cutoverDataCache";
import { jobChipColor, NmStatusChip } from "./nmChips";

type PageTab = "templates" | "jobs";
type JobDetailTab = "config" | "result";
type KindFilter = "diff" | "all" | "added" | "removed" | "changed" | "unchanged";
type CreateJobStep = 0 | 1 | 2 | 3;
const CREATE_JOB_STEPS = 4;

type TaskOpt = { id: string; ne_name: string; ne_ip: string; vendor: string };
type BatchOpt = {
  id: string;
  status: string;
  row_count: number;
  started_at?: string | null;
  alias?: string;
};
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
  sheet_id?: string;
  title?: string;
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
  iface_normalize_rules?: { from: string; to: string }[];
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
  enabled_sheet_ids?: string[];
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
  sheet_id?: string;
  title?: string;
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

/** Prefer first non-empty row dict (API may return {} for null sides). */
function pickSideRow(
  ...candidates: Array<Record<string, unknown> | null | undefined>
): Record<string, unknown> {
  for (const c of candidates) {
    if (c && typeof c === "object" && Object.keys(c).length > 0) return c;
  }
  return {};
}

/** Non-key fields: always show before/after pair for cutover review. */
function PairCell(props: {
  beforeText: string;
  afterText: string;
  kind: string;
  mismatch: boolean;
  reason?: string;
  beforeLabel: string;
  afterLabel: string;
  zoneStart?: boolean;
}) {
  const {
    beforeText,
    afterText,
    kind,
    mismatch,
    reason,
    beforeLabel,
    afterLabel,
    zoneStart = false,
  } = props;
  const pre = beforeText || "—";
  const post = afterText || "—";
  const isAdded = kind === "added";
  const isRemoved = kind === "removed";
  // Whole-row missing/extra: emphasize the present side; do not strike it out.
  const preClass = [
    "bs-cmp-val",
    !beforeText || isAdded ? "is-empty" : "",
    isRemoved && beforeText ? "bs-cmp-val--present-pre" : "",
    !isAdded && !isRemoved && mismatch && beforeText ? "bs-cmp-val--pre" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const postClass = [
    "bs-cmp-val",
    !afterText || isRemoved ? "is-empty" : "",
    isAdded && afterText ? "bs-cmp-val--present-post" : "",
    !isAdded && !isRemoved && mismatch && afterText ? "bs-cmp-val--post" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <td
      className={`bs-cmp-val-cell bs-cmp-val-cell--pair${
        mismatch || isAdded || isRemoved ? " bs-cmp-val-cell--diff" : ""
      }${isAdded ? " is-added" : ""}${isRemoved ? " is-removed" : ""}${
        zoneStart ? " bs-cmp-zone-start" : ""
      }`}
    >
      <div className="bs-cmp-pair">
        <div className="bs-cmp-pair__row">
          <span className="bs-cmp-pair__tag">{beforeLabel}</span>
          <span className={preClass}>{isAdded ? "—" : pre}</span>
        </div>
        <div className="bs-cmp-pair__row">
          <span className="bs-cmp-pair__tag">{afterLabel}</span>
          <span className={postClass}>{isRemoved ? "—" : post}</span>
        </div>
      </div>
      {reason ? <div className="bs-cmp-val-reason muted">{reason}</div> : null}
    </td>
  );
}

function toggleInList(list: string[], name: string, on: boolean): string[] {
  if (on) return list.includes(name) ? list : [...list, name];
  return list.filter((x) => x !== name);
}

function sideDeviceName(side?: {
  ne_name?: string;
  ne_ip?: string;
  label?: string;
  batch_id?: string;
} | null): string {
  if (!side) return "—";
  const name = String(side.ne_name || "").trim();
  const ip = String(side.ne_ip || "").trim();
  const label = String(side.label || "").trim();
  // Prefer real device identity; ignore hex-ish fallbacks that look like batch ids
  if (name) return name;
  if (ip) return ip;
  if (label && !/^[a-f0-9]{8,32}$/i.test(label)) return label;
  return "—";
}

function sideCollectTime(side?: { started_at?: string | null } | null): string {
  const raw = side?.started_at;
  if (!raw) return "—";
  const full = fmtTime(raw);
  if (!full || full === raw) {
    // Prefer compact local time when possible
    try {
      const d = new Date(/[zZ]$|[+-]\d{2}:\d{2}$/.test(String(raw)) ? raw : `${String(raw).replace(" ", "T")}Z`);
      if (!Number.isNaN(d.getTime())) {
        const m = d.getMonth() + 1;
        const day = d.getDate();
        const hh = String(d.getHours()).padStart(2, "0");
        const mm = String(d.getMinutes()).padStart(2, "0");
        return `${m}/${day} ${hh}:${mm}`;
      }
    } catch {
      /* fall through */
    }
    return full || "—";
  }
  // Compact: drop seconds / year noise from locale string when long
  // e.g. "2026/9/18 09:35:53" -> "9/18 09:35"
  const m = String(full).match(/(?:\d{4}[/-])?(\d{1,2})[/-](\d{1,2})\s+(\d{2}):(\d{2})/);
  if (m) return `${m[1]}/${m[2]} ${m[3]}:${m[4]}`;
  return full;
}

type SideInfo = {
  ne_name?: string;
  ne_ip?: string;
  label?: string;
  batch_id?: string;
  started_at?: string | null;
};

function enrichSide(
  side: Record<string, unknown> | null | undefined,
  taskId: string,
  tasks: TaskOpt[],
  batches: BatchOpt[],
): SideInfo {
  const base: SideInfo = { ...(side || {}) } as SideInfo;
  const bid = String(base.batch_id || "").trim();
  if (!base.started_at && bid) {
    const batch = batches.find((b) => b.id === bid);
    if (batch?.started_at) base.started_at = batch.started_at;
  }
  if (sideDeviceName(base) === "—") {
    const task = tasks.find((t) => t.id === taskId);
    if (task) {
      base.ne_name = task.ne_name || base.ne_name;
      base.ne_ip = task.ne_ip || base.ne_ip;
      base.label = task.ne_name || task.ne_ip || base.label;
    }
  }
  return base;
}

function taskLabel(row: TaskOpt) {
  return `${row.ne_name || row.ne_ip || row.id} (${row.vendor || "-"})`;
}

function batchOptLabel(b: BatchOpt) {
  const alias = String(b.alias || "").trim();
  const when = fmtTime(b.started_at);
  const tail = `${b.status} · rows=${b.row_count}`;
  if (alias) return `${alias} · ${when} · ${tail}`;
  return `${when} · ${tail}`;
}

function sheetIdentity(s: { sheet_id?: string; metric_id?: string } | null | undefined): string {
  return String(s?.sheet_id || s?.metric_id || "").trim();
}

function sheetLabel(s: { title?: string; sheet_id?: string; metric_id?: string } | null | undefined): string {
  return String(s?.title || s?.sheet_id || s?.metric_id || "").trim() || "—";
}

function templateSheets(tpl?: Template | null): MetricSheet[] {
  if (!tpl) return [];
  if (tpl.metrics?.length) return tpl.metrics;
  if (tpl.metric_id) {
    return [
      {
        sheet_id: tpl.metric_id,
        title: tpl.metric_id,
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
    sheet_id: metricId,
    title: metricId,
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
    sheet_id: s.sheet_id || s.metric_id,
    title: s.title || s.sheet_id || s.metric_id,
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
    sheet_id: s.sheet_id || s.metric_id,
    title: s.title || s.sheet_id || s.metric_id,
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
    iface_normalize_rules: [...(tpl.iface_normalize_rules || [])],
    metrics,
  };
}

function parseTemplateImport(raw: unknown): {
  name: string;
  note: string;
  metrics: MetricSheet[];
  iface_normalize_rules?: { from: string; to: string }[];
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
      sheet_id: String(m.sheet_id || mid).trim() || mid,
      title: String(m.title || m.sheet_id || mid).trim() || mid,
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
  const normRaw = o.iface_normalize_rules;
  const iface_normalize_rules: { from: string; to: string }[] = [];
  if (Array.isArray(normRaw)) {
    for (const item of normRaw) {
      if (!item || typeof item !== "object") continue;
      const r = item as Record<string, unknown>;
      const fr = String(r.from || "").trim();
      const to = String(r.to || "").trim();
      if (fr && to) iface_normalize_rules.push({ from: fr, to });
    }
  }
  return { name, note, metrics, iface_normalize_rules };
}

function ifaceNormRulesToText(rules?: { from: string; to: string }[]): string {
  return (rules || []).map((r) => `${r.from},${r.to}`).join("\n");
}

function parseIfaceNormText(text: string): { from: string; to: string }[] {
  const out: { from: string; to: string }[] = [];
  for (const line of text.split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith("#")) continue;
    let fr = "";
    let to = "";
    if (s.includes(",")) {
      const i = s.indexOf(",");
      fr = s.slice(0, i).trim();
      to = s.slice(i + 1).trim();
    } else if (s.includes("\t")) {
      const i = s.indexOf("\t");
      fr = s.slice(0, i).trim();
      to = s.slice(i + 1).trim();
    } else {
      const parts = s.split(/\s+/);
      if (parts.length >= 2) {
        fr = parts[0];
        to = parts.slice(1).join(" ");
      }
    }
    if (fr && to) out.push({ from: fr, to });
  }
  return out;
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

const FILTER_OPS = [
  "eq",
  "ne",
  "in",
  "not_in",
  "contains",
  "empty",
  "not_empty",
  "regex",
  "age_timer",
] as const;

function TplRowFiltersEditor({
  groups,
  fieldOpts,
  t,
  onChange,
}: {
  groups: RowFilter[][];
  fieldOpts: string[];
  t: (k: string, vars?: Record<string, string | number>) => string;
  onChange: (next: RowFilter[][]) => void;
}) {
  const setGroups = onChange;
  return (
    <div className="ct-filters">
      <p className="muted ct-filters__hint">{t("bizCompare.rowFiltersHintShort")}</p>
      <div className="ct-filters__actions">
        <button
          type="button"
          className="mt-link-btn"
          onClick={() => setGroups([...(groups.length ? groups : []), [emptyLeaf(fieldOpts[0] || "")]])}
        >
          {t("bizCompare.filterAddOrShort")}
        </button>
        {groups.length ? (
          <button type="button" className="mt-link-btn" onClick={() => setGroups([])}>
            {t("bizCompare.filterClear")}
          </button>
        ) : null}
      </div>
      {!groups.length ? <p className="muted ct-filters__empty">{t("bizCompare.rowFiltersEmpty")}</p> : null}
      {groups.map((group, gi) => (
        <div key={gi} className="mt-or-wrap">
          {gi > 0 ? <div className="mt-logic-badge mt-logic-badge--or">{t("bizCompare.filterOr")}</div> : null}
          <div className="mt-and-box">
            <div className="mt-and-box__head">
              <span className="muted">{t("bizCompare.filterGroupN", { n: String(gi + 1) })}</span>
              <button
                type="button"
                className="mt-icon-btn"
                aria-label={t("bizCompare.filterRemoveGroup")}
                onClick={() => setGroups(groups.filter((_, i) => i !== gi))}
              >
                ×
              </button>
            </div>
            {group.map((filt, fi) => (
              <div key={fi}>
                {fi > 0 ? (
                  <div className="mt-logic-badge mt-logic-badge--and">{t("bizCompare.filterAnd")}</div>
                ) : null}
                <div className="ct-filter-row">
                  <select
                    className="mt-select"
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
                    className="mt-select"
                    value={filt.op || "eq"}
                    onChange={(e) => {
                      const next = groups.map((g) => g.map((x) => ({ ...x })));
                      next[gi][fi] = { ...next[gi][fi], op: e.target.value };
                      setGroups(next);
                    }}
                  >
                    {FILTER_OPS.map((op) => (
                      <option key={op} value={op}>
                        {op}
                      </option>
                    ))}
                  </select>
                  <Input
                    value={
                      Array.isArray(filt.value) ? filt.value.join(",") : String(filt.value ?? "")
                    }
                    placeholder={t("bizCompare.filterValue")}
                    isDisabled={["empty", "not_empty", "age_timer"].includes(filt.op || "")}
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
                  <button
                    type="button"
                    className="mt-icon-btn"
                    aria-label={t("bizCompare.filterRemoveGroup")}
                    onClick={() => {
                      const next = groups.map((g) => g.map((x) => ({ ...x })));
                      next[gi].splice(fi, 1);
                      setGroups(next.filter((g) => g.length));
                    }}
                  >
                    ×
                  </button>
                </div>
              </div>
            ))}
            <button
              type="button"
              className="mt-link-btn"
              onClick={() => {
                const next = groups.map((g) => g.map((x) => ({ ...x })));
                next[gi].push(emptyLeaf(fieldOpts[0] || ""));
                setGroups(next);
              }}
            >
              {t("bizCompare.filterAddAndShort")}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function metricLabel(id: string) {
  return id;
}

export type BizComparePageMode = "jobs" | "templates" | "all";

export function BizComparePage({ pageMode = "all" }: { pageMode?: BizComparePageMode }) {
  const { t } = useI18n();
  const { showOk, showError } = useToast();

  const [pageTab, setPageTab] = useState<PageTab>(pageMode === "templates" ? "templates" : "jobs");
  // Route-bound modes must follow pageMode; internal tabs only when pageMode === "all".
  // Without this, React reuses the same component instance across
  // /biz-compare ↔ /compare-templates and pageTab stays stuck.
  const activeTab: PageTab =
    pageMode === "templates" ? "templates" : pageMode === "jobs" ? "jobs" : pageTab;
  const showTabSwitch = pageMode === "all";
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
  const [tplNormText, setTplNormText] = useState("");
  const [tplSheets, setTplSheets] = useState<MetricSheet[]>([]);
  const [tplSheetIdx, setTplSheetIdx] = useState(0);
  const [tplPaneTab, setTplPaneTab] = useState<"fields" | "filters">("fields");
  const [showMetricPicker, setShowMetricPicker] = useState(true);

  // job create / detail
  const [jobCreateOpen, setJobCreateOpen] = useState(false);
  const [createStep, setCreateStep] = useState<CreateJobStep>(0);
  const [jobId, setJobId] = useState("");
  const [jobDetailTab, setJobDetailTab] = useState<JobDetailTab>("config");
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [enabledSheetIds, setEnabledSheetIds] = useState<string[]>([]);
  const [mappingId, setMappingId] = useState("");
  const [beforeTaskId, setBeforeTaskId] = useState("");
  const [afterTaskId, setAfterTaskId] = useState("");
  const [beforeBatches, setBeforeBatches] = useState<BatchOpt[]>([]);
  const [afterBatches, setAfterBatches] = useState<BatchOpt[]>([]);
  const [beforeBatchId, setBeforeBatchId] = useState("");
  const [afterBatchId, setAfterBatchId] = useState("");
  const [mode, setMode] = useState<"manual" | "auto">("manual");
  const [mapName, setMapName] = useState("端口映射");
  const [mapText, setMapText] = useState("");
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
  const tableScrollRef = useRef<HTMLDivElement | null>(null);
  const tableScrollPosRef = useRef({ top: 0, left: 0 });
  const [boardFs, setBoardFs] = useState(false);
  const [navCollapsed, setNavCollapsed] = useState(false);
  const tplImportRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async (opts?: { force?: boolean }) => {
    type Bundle = {
      taskRes: Awaited<ReturnType<typeof bizStateListTasks>>;
      tpl: Awaited<ReturnType<typeof bizCompareListTemplates>>;
      maps: Awaited<ReturnType<typeof bizCompareListMappings>>;
      j: Awaited<ReturnType<typeof bizCompareListJobs>>;
      met: Awaited<ReturnType<typeof bizCompareListMetrics>>;
    };
    const fetchBundle = async (): Promise<Bundle> => {
      const [taskRes, tpl, maps, j, met] = await Promise.all([
        bizStateListTasks(),
        bizCompareListTemplates(),
        bizCompareListMappings(),
        bizCompareListJobs(),
        bizCompareListMetrics(),
      ]);
      return { taskRes, tpl, maps, j, met };
    };
    const apply = (b: Bundle) => {
      setTasks((b.taskRes.items || []) as TaskOpt[]);
      setTemplates((b.tpl.items || []) as Template[]);
      setMappings((b.maps.items || []) as Mapping[]);
      setJobs((b.j.items || []) as Job[]);
      setMetrics((b.met.items || []) as MetricSchema[]);
    };
    if (opts?.force) {
      apply(await cutoverCachedGet("bizCompare:lists", fetchBundle, { force: true }));
      return;
    }
    apply(await cutoverCachedGetSWR("bizCompare:lists", fetchBundle, apply));
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

  // Keep local tab + dismiss overlays when route mode flips (component may be reused).
  useEffect(() => {
    if (pageMode === "templates") setPageTab("templates");
    else if (pageMode === "jobs") setPageTab("jobs");
    setTplOpen(false);
    setJobCreateOpen(false);
    setCreateStep(0);
    setJobId("");
    setRuns([]);
    setRunDetail(null);
    setResultSheetId("");
    if (document.fullscreenElement === boardRef.current) {
      void document.exitFullscreen().catch(() => undefined);
    }
    setBoardFs(false);
  }, [pageMode]);

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

  const selectedJobTemplate = useMemo(
    () => templates.find((x) => x.id === templateId) || null,
    [templates, templateId],
  );

  const jobTemplateSheets = useMemo(
    () => (selectedJobTemplate ? templateSheets(selectedJobTemplate) : []),
    [selectedJobTemplate],
  );

  const jobSheetAllIds = useMemo(
    () => jobTemplateSheets.map((s) => sheetIdentity(s)).filter(Boolean),
    [jobTemplateSheets],
  );

  const isJobSheetOn = useCallback(
    (sid: string) => {
      if (!enabledSheetIds.length) return true;
      return enabledSheetIds.includes(sid);
    },
    [enabledSheetIds],
  );

  const enabledJobSheetCount = useMemo(() => {
    if (!jobSheetAllIds.length) return 0;
    if (!enabledSheetIds.length) return jobSheetAllIds.length;
    return jobSheetAllIds.filter((id) => enabledSheetIds.includes(id)).length;
  }, [jobSheetAllIds, enabledSheetIds]);

  const toggleJobSheet = useCallback(
    (sid: string) => {
      const all = jobSheetAllIds;
      if (!all.length) return;
      const currentlyOn = !enabledSheetIds.length
        ? [...all]
        : enabledSheetIds.filter((id) => all.includes(id));
      const next = currentlyOn.includes(sid)
        ? currentlyOn.filter((id) => id !== sid)
        : [...currentlyOn, sid];
      // Empty list means "all on" (new template sheets auto-included)
      setEnabledSheetIds(next.length === all.length ? [] : next);
    },
    [jobSheetAllIds, enabledSheetIds],
  );

  const setJobTemplateAndSheets = useCallback(
    (nextTplId: string, presetIds?: string[] | null) => {
      setTemplateId(nextTplId);
      const tpl = templates.find((x) => x.id === nextTplId);
      const all = tpl ? templateSheets(tpl).map((s) => sheetIdentity(s)).filter(Boolean) : [];
      if (presetIds && presetIds.length) {
        const kept = presetIds.filter((id) => all.includes(id));
        setEnabledSheetIds(kept.length === all.length ? [] : kept);
      } else {
        setEnabledSheetIds([]);
      }
    },
    [templates],
  );

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
    if (!resultSheetId || !runSheets.some((s) => sheetIdentity(s) === resultSheetId)) {
      setResultSheetId(sheetIdentity(runSheets[0]));
    }
  }, [runSheets, resultSheetId]);

  const activeRunSheet = useMemo(
    () => runSheets.find((s) => sheetIdentity(s) === resultSheetId) || runSheets[0] || null,
    [runSheets, resultSheetId],
  );

    // Reset page when sheet / filter / page size changes
  useEffect(() => {
    setResultPage(1);
  }, [resultSheetId, kindFilter, debouncedResultKw, resultPageSize, runDetail?.id]);

  useEffect(() => {
    const runId = String(runDetail?.id || "");
    const mid = resultSheetId || sheetIdentity(activeRunSheet) || "";
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
    }
    // Column order: Key → Compare → Display-only (context)
    // Compare fields always appear (even if display unticked), matching backend.
    const compareCols = [...compare];
    const displayOnly = display.filter((f) => !keySet.has(f) && !compareSet.has(f));
    let extras = [...compareCols, ...displayOnly];
    // Presence / sparse display: if no value columns, pull fields from sample
    // added/removed rows so 缺失/多余 still show side data.
    if (!extras.length && pagedDiffs.length) {
      const sample =
        pagedDiffs.find((d) => d.kind === "added" || d.kind === "removed") || pagedDiffs[0];
      const side = pickSideRow(
        sample?.mapped_before as Record<string, unknown> | undefined,
        sample?.before as Record<string, unknown> | undefined,
        sample?.after as Record<string, unknown> | undefined,
      );
      extras = Object.keys(side).filter((f) => !keySet.has(f) && !f.startsWith("_"));
    }
    return {
      keys,
      compare: compareCols,
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

  /** Table verdict: fail = missing+mismatch; success = match; added is special. */
  const rowVerdict = (kind: string): { label: string; tone: "fail" | "pass" | "added" } => {
    if (kind === "added") return { label: t("bizCompare.kindAddedShort"), tone: "added" };
    if (kind === "unchanged") return { label: t("bizCompare.kindSuccess"), tone: "pass" };
    return { label: t("bizCompare.kindFail"), tone: "fail" };
  };

  const failFieldsLabel = (d: DiffRow) => {
    if (d.kind === "added") return t("bizCompare.kindAddedShort");
    if (d.kind === "removed") return t("bizCompare.failWholeRow");
    const names = failFieldNames(d);
    return names.length ? names.join(" · ") : t("bizCompare.failFieldsEmpty");
  };

  const sheetFailOf = (c: {
    fail_count?: number;
    diff_count?: number;
    removed?: number;
    changed?: number;
    added?: number;
  }) => {
    // Always derive from removed+changed so "新增" never counts as fail
    // (legacy diff_count used to include added).
    if (c.removed !== undefined || c.changed !== undefined) {
      return Number(c.removed || 0) + Number(c.changed || 0);
    }
    if (c.fail_count !== undefined) return Number(c.fail_count || 0);
    const legacy = Number(c.diff_count || 0);
    const added = Number(c.added || 0);
    return Math.max(0, legacy - added);
  };

  const sheetSuccessOf = (c: { success_count?: number; unchanged?: number }) =>
    Number(c.success_count ?? c.unchanged ?? 0);

  const sheetPassRateOf = (c: {
    pass_rate?: number;
    fail_count?: number;
    diff_count?: number;
    removed?: number;
    changed?: number;
    success_count?: number;
    unchanged?: number;
    added?: number;
  }) => {
    // Always recompute from fail/success so added never skews pass rate
    const fail = sheetFailOf(c);
    const ok = sheetSuccessOf(c);
    const judged = fail + ok;
    return judged ? Math.round((ok / judged) * 1000) / 10 : 100;
  };

  const summary = runDetail?.summary || {};
  const resultPages = Math.max(1, Math.ceil(resultTotal / Math.max(1, resultPageSize)));
  const sheetCards = useMemo(() => {
    const raw = (summary.sheet_cards || []) as Array<{
      sheet_id?: string;
      title?: string;
      metric_id: string;
      mode?: string;
      added?: number;
      removed?: number;
      changed?: number;
      unchanged?: number;
      before_count?: number;
      after_count?: number;
      fail_count?: number;
      success_count?: number;
      diff_count?: number;
      pass_rate?: number;
    }>;
    // Failures first so ops can scan quickly when many sheets
    return [...raw].sort((a, b) => {
      const da = sheetFailOf(a);
      const db = sheetFailOf(b);
      if (da !== db) return db - da;
      return String(a.metric_id).localeCompare(String(b.metric_id));
    });
  }, [summary.sheet_cards]);
  const activeSheetCard = sheetCards.find((c) => sheetIdentity(c) === resultSheetId) || sheetCards[0];
  const activeFail = sheetFailOf(activeSheetCard || {});
  const activeSuccess = sheetSuccessOf(activeSheetCard || {});
  const activePassRate = sheetPassRateOf(activeSheetCard || {});
  const activeAdded = Number(activeSheetCard?.added || 0);
  const showFailCol =
    kindFilter === "diff" || kindFilter === "all" || kindFilter === "added";
  const resultEmptyColSpan =
    1 +
    (showFailCol ? 1 : 0) +
    resultColumns.keys.length +
    Math.max(resultColumns.extras.length, 0);

  const rememberTableScroll = useCallback(() => {
    const wrap = tableScrollRef.current;
    if (!wrap) return;
    tableScrollPosRef.current = { top: wrap.scrollTop, left: wrap.scrollLeft };
  }, []);

  // Native Fullscreen escapes modal transform containing-block (CSS fixed fails inside dialog).
  useEffect(() => {
    const syncFs = () => {
      const el = boardRef.current;
      setBoardFs(Boolean(el && document.fullscreenElement === el));
    };
    document.addEventListener("fullscreenchange", syncFs);
    return () => document.removeEventListener("fullscreenchange", syncFs);
  }, []);

  // CSS-fallback immersive: Esc exits without closing the job modal.
  useEffect(() => {
    if (!boardFs || document.fullscreenElement) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      rememberTableScroll();
      setBoardFs(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [boardFs, rememberTableScroll]);

  // Keep table scroll across fullscreen enter/exit (layout swap otherwise jumps to top).
  useLayoutEffect(() => {
    const wrap = tableScrollRef.current;
    if (!wrap) return;
    const { top, left } = tableScrollPosRef.current;
    wrap.scrollTop = top;
    wrap.scrollLeft = left;
  }, [boardFs]);

  const toggleBoardFullscreen = async () => {
    const el = boardRef.current;
    if (!el) return;
    rememberTableScroll();
    try {
      if (document.fullscreenElement === el) {
        await document.exitFullscreen();
        return;
      }
      if (!document.fullscreenElement) {
        await el.requestFullscreen();
        return;
      }
    } catch {
      // Native FS blocked — CSS immersive fallback
    }
    setBoardFs((v) => !v);
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
    setTplNormText("XXVGE,xxvgei\nXGE,xgei\nCGE,cgei\nGE,gei\nSG,smartgroup");
    setTplSheets([]);
    setTplSheetIdx(0);
    setTplPaneTab("fields");
    setShowMetricPicker(true);
    setTplOpen(true);
  };

  const openEditTemplate = (tpl: Template) => {
    const sheets = templateSheets(tpl);
    setTplEditId(tpl.id);
    setTplName(tpl.name);
    setTplNote(tpl.note || "");
    setTplNormText(ifaceNormRulesToText(tpl.iface_normalize_rules));
    setTplSheets(sheets.length ? sheets.map(cloneSheet) : []);
    setTplSheetIdx(0);
    setTplPaneTab("fields");
    setShowMetricPicker(!sheets.length);
    setTplOpen(true);
  };

  const toggleTplMetric = (metricId: string, on: boolean) => {
    if (on) {
      const schema = metrics.find((m) => m.metric_id === metricId);
      setTplSheets((prev) => {
        const next = [...prev, defaultSheetForMetric(schema, metricId)];
        setTplSheetIdx(next.length - 1);
        if (!prev.length) setShowMetricPicker(false);
        return next;
      });
      return;
    }
    // Uncheck removes ALL sheets of this source metric
    setTplSheets((prev) => {
      const next = prev.filter((s) => s.metric_id !== metricId);
      setTplSheetIdx((cur) => {
        if (!next.length) return 0;
        if (cur >= next.length) return next.length - 1;
        return cur;
      });
      return next;
    });
  };

  const duplicateTplSheet = (idx: number) => {
    setTplSheets((prev) => {
      const src = prev[idx];
      if (!src) return prev;
      const baseId = sheetIdentity(src);
      let n = 2;
      let candidate = `${baseId}.${n}`;
      const used = new Set(prev.map((s) => sheetIdentity(s)));
      while (used.has(candidate)) {
        n += 1;
        candidate = `${baseId}.${n}`;
      }
      const copy = cloneSheet(src);
      copy.sheet_id = candidate;
      copy.title = `${sheetLabel(src)} (${n})`;
      const next = [...prev];
      next.splice(idx + 1, 0, copy);
      setTplSheetIdx(idx + 1);
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
        showError(`${sheetLabel(s)}: ${t("bizCompare.keyRequired")}`);
        return;
      }
      if (!sheetIdentity(s)) {
        showError(`${s.metric_id}: ${t("bizCompare.sheetIdRequired")}`);
        return;
      }
    }
    const ids = tplSheets.map((s) => sheetIdentity(s));
    if (new Set(ids).size !== ids.length) {
      showError(t("bizCompare.duplicateSheetId"));
      return;
    }
    setBusy(true);
    try {
      const body = {
        name: tplName || tplSheets.map((s) => sheetLabel(s)).join("+"),
        note: tplNote,
        iface_normalize_rules: parseIfaceNormText(tplNormText),
        metrics: tplSheets.map((s) => ({
          sheet_id: sheetIdentity(s),
          title: s.title || sheetIdentity(s),
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
      invalidateCutoverCache("bizMonitor:");
      invalidateCutoverCache("bizMigration:");
      await refresh({ force: true });
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
      invalidateCutoverCache("bizMonitor:");
      invalidateCutoverCache("bizMigration:");
      await refresh({ force: true });
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
        iface_normalize_rules: body.iface_normalize_rules || [],
        metrics: body.metrics.map((s) => ({
          sheet_id: sheetIdentity(s),
          title: s.title || sheetIdentity(s),
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
      invalidateCutoverCache("bizMonitor:");
      invalidateCutoverCache("bizMigration:");
      await refresh({ force: true });
      if (pageMode !== "jobs") setPageTab("templates");
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
      await refresh({ force: true });
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
    setMapText(m.rows.map((r) => `${r.before_if},${r.after_if}`).join("\n"));
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
      await refresh({ force: true });
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
    const tplId = preset?.template_id || templates[0]?.id || "";
    setName(preset?.name || t("bizCompare.defaultJobName"));
    setTemplateId(tplId);
    const tpl = templates.find((x) => x.id === tplId);
    const all = tpl ? templateSheets(tpl).map((s) => sheetIdentity(s)).filter(Boolean) : [];
    const presetIds = (preset?.enabled_sheet_ids || []).map(String).filter(Boolean);
    if (presetIds.length) {
      const kept = presetIds.filter((id) => all.includes(id));
      setEnabledSheetIds(kept.length === all.length ? [] : kept);
    } else {
      setEnabledSheetIds([]);
    }
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
      setMapText("");
    }
  };

  const jobConfigBody = () => ({
    name,
    template_id: templateId,
    mapping_id: mappingId,
    before_task_id: beforeTaskId,
    after_task_id: afterTaskId || beforeTaskId,
    before_batch_id: beforeBatchId,
    after_batch_id: mode === "manual" ? afterBatchId : "",
    mode,
    enabled_sheet_ids: enabledSheetIds,
  });

  const closeCreateJob = () => {
    setJobCreateOpen(false);
    setCreateStep(0);
  };

  const openCreateJob = () => {
    resetJobForm();
    setCreateStep(0);
    setJobCreateOpen(true);
  };

  const canAdvanceCreateStep = (step: CreateJobStep): boolean => {
    if (step === 0) return Boolean(name.trim() && templateId);
    if (step === 1) return enabledJobSheetCount > 0;
    if (step === 2) {
      if (!beforeTaskId || !beforeBatchId) return false;
      if (mode === "manual" && !afterBatchId) return false;
      return true;
    }
    return true;
  };

  const createStepBlockReason = (step: CreateJobStep): string | null => {
    if (step === 0) {
      if (!name.trim()) return t("bizCompare.needJobName");
      if (!templateId) return t("bizCompare.needTemplate");
      return null;
    }
    if (step === 1) {
      if (!enabledJobSheetCount) return t("bizCompare.needSheets");
      return null;
    }
    if (step === 2) {
      if (!beforeTaskId || !beforeBatchId) return t("bizCompare.needBeforeBatch");
      if (mode === "manual" && !afterBatchId) return t("bizCompare.needAfterBatch");
      return null;
    }
    return null;
  };

  const onCreateNext = () => {
    const reason = createStepBlockReason(createStep);
    if (reason) {
      showError(reason);
      return;
    }
    setCreateStep((s) => Math.min(CREATE_JOB_STEPS - 1, (s + 1) as CreateJobStep) as CreateJobStep);
  };

  const onCreateBack = () => {
    setCreateStep((s) => Math.max(0, s - 1) as CreateJobStep);
  };

  const createJob = async () => {
    const reason =
      createStepBlockReason(0) ||
      createStepBlockReason(1) ||
      createStepBlockReason(2);
    if (reason) {
      showError(reason);
      return;
    }
    setBusy(true);
    try {
      const j = await bizCompareCreateJob(jobConfigBody());
      showOk(t("bizCompare.created"));
      closeCreateJob();
      await refresh({ force: true });
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
    if (document.fullscreenElement === boardRef.current) {
      void document.exitFullscreen().catch(() => undefined);
    }
    setBoardFs(false);
    setJobId("");
    setRuns([]);
    setRunDetail(null);
    setResultSheetId("");
  };

  const saveJobConfig = async () => {
    if (!jobId) return;
    if (!enabledJobSheetCount) {
      showError(t("bizCompare.needSheets"));
      return;
    }
    setBusy(true);
    try {
      await bizCompareUpdateJob(jobId, jobConfigBody());
      showOk(t("bizCompare.jobSaved"));
      await refresh({ force: true });
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    if (!jobId) return;
    if (!enabledJobSheetCount) {
      showError(t("bizCompare.needSheets"));
      return;
    }
    setBusy(true);
    try {
      await bizCompareUpdateJob(jobId, jobConfigBody());
      const run = await bizCompareRunJob(jobId);
      setRunDetail(run);
      setJobDetailTab("result");
      showOk(t("bizCompare.ran"));
      const r = await bizCompareListRuns(jobId);
      setRuns(r.items || []);
      await refresh({ force: true });
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

  const removeRun = async (runId: string) => {
    if (!runId) return;
    if (!window.confirm(t("bizCompare.confirmDeleteRun"))) return;
    setBusy(true);
    try {
      const wasCurrent = String(runDetail?.id || "") === runId;
      await bizCompareDeleteRun(runId);
      let nextRuns: typeof runs = [];
      if (jobId) {
        const r = await bizCompareListRuns(jobId);
        nextRuns = r.items || [];
      } else {
        nextRuns = (runs || []).filter((r) => String(r.id) !== runId);
      }
      setRuns(nextRuns);
      if (wasCurrent) {
        if (nextRuns.length) {
          await loadRun(String(nextRuns[0].id));
        } else {
          setRunDetail(null);
          setResultSheetId("");
        }
      }
      showOk(t("bizCompare.runDeleted"));
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const renderSheetChips = () => (
    <div className="bm-create__pane" style={{ padding: 0, border: "none", background: "transparent" }}>
      <div className="bm-mapping__label">
        {t("bizCompare.enabledSheets")}
        <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>
          {enabledJobSheetCount}/{jobSheetAllIds.length || 0}
        </span>
      </div>
      <p className="muted bm-hint">{t("bizCompare.enabledSheetsHint")}</p>
      <div className="bm-metric-chips">
        {jobTemplateSheets.map((s) => {
          const sid = sheetIdentity(s);
          const on = isJobSheetOn(sid);
          return (
            <button
              key={sid}
              type="button"
              className={`bm-metric-chip${on ? " is-on" : ""}`}
              onClick={() => toggleJobSheet(sid)}
              title={s.metric_id !== sid ? s.metric_id : undefined}
            >
              {sheetLabel(s)}
            </button>
          );
        })}
      </div>
      {!jobTemplateSheets.length ? (
        <p className="muted bm-hint">{t("bizCompare.noTemplateSheets")}</p>
      ) : null}
      {jobTemplateSheets.length && !enabledJobSheetCount ? (
        <p className="form-error bm-hint">{t("bizCompare.needSheets")}</p>
      ) : null}
    </div>
  );

  const renderMappingBlock = () => (
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
              setMapText("");
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
  );

  const renderBatchFields = () => (
    <>
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
            {batchOptLabel(b)}
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
              {batchOptLabel(b)}
            </option>
          ))}
        </FieldSelect>
      ) : (
        <p className="muted">{t("bizCompare.autoHint")}</p>
      )}
    </>
  );

  const renderJobForm = () => (
    <div className="bs-cmp-form" style={{ display: "grid", gap: 8 }}>
      <label className="ui-field ui-field--full">
        <span className="ui-field__label">{t("bizCompare.jobName")}</span>
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <FieldSelect
        label={t("bizCompare.template")}
        value={templateId}
        onChange={(e) => setJobTemplateAndSheets(e.target.value)}
        fullWidth
      >
        {templates.map((tpl) => {
          const n = templateSheets(tpl).length;
          return (
            <option key={tpl.id} value={tpl.id}>
              {tpl.name} ({n} {t("bizCompare.sheetsUnit")})
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
      {renderSheetChips()}
      {renderBatchFields()}
      {renderMappingBlock()}
    </div>
  );

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>
          {pageMode === "templates" ? t("bizCompare.templates") : t("bizCompare.title")}
        </h2>
        <div className="btn-row">
          {activeTab === "templates" ? (
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
        {showTabSwitch ? (
          <div className="btn-row nm-config-modal__tabs" role="tablist">
            <Button
              size="sm"
              variant={activeTab === "jobs" ? "primary" : "secondary"}
              className={activeTab === "jobs" ? "is-active" : undefined}
              onPress={() => setPageTab("jobs")}
            >
              {t("bizCompare.jobList")}
            </Button>
            <Button
              size="sm"
              variant={activeTab === "templates" ? "primary" : "secondary"}
              className={activeTab === "templates" ? "is-active" : undefined}
              onPress={() => setPageTab("templates")}
            >
              {t("bizCompare.templates")}
            </Button>
          </div>
        ) : null}

        <div className="filter-inline">
          <Input
            value={listKw}
            placeholder={
              activeTab === "jobs" ? t("bizCompare.jobFilterPh") : t("bizCompare.templateFilterPh")
            }
            onChange={(e) => setListKw(e.target.value)}
          />
        </div>

        {activeTab === "templates" ? (
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
                          {sheets.map((s, i) => (
                            <code key={`${sheetIdentity(s)}-${i}`} className="bs-cmp-metric-chip">
                              {sheetLabel(s)}
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
                            {t("bizCompare.deleteJob")}
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
      <AppModalShell open={tplOpen} onClose={() => setTplOpen(false)} size="cover">
        <Modal.Header>
          <Modal.Heading>
            {tplEditId ? t("bizCompare.editTemplate") : t("bizCompare.newTemplate")}
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3 ct-editor">
          <div className="ct-editor__basics">
            <Input
              value={tplName}
              onChange={(e) => setTplName(e.target.value)}
              placeholder={t("bizCompare.colName")}
              aria-label={t("bizCompare.colName")}
            />
            <Input
              value={tplNote}
              onChange={(e) => setTplNote(e.target.value)}
              placeholder={t("bizCompare.note")}
              aria-label={t("bizCompare.note")}
            />
          </div>

          <div className="ct-editor__norm">
            <div className="ct-editor__pick-title">{t("bizCompare.ifaceNormalize")}</div>
            <p className="muted ct-editor__pick-hint">{t("bizCompare.ifaceNormalizeHint")}</p>
            <textarea
              className="ct-editor__norm-area"
              rows={5}
              value={tplNormText}
              onChange={(e) => setTplNormText(e.target.value)}
              placeholder={"GE,gei\nSG,smartgroup"}
              aria-label={t("bizCompare.ifaceNormalize")}
            />
          </div>

          <div className="ct-editor__pick">
            <div className="ct-editor__pick-head">
              <span className="ct-editor__pick-title">
                {t("bizCompare.stepPickMetrics")}
                <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>
                  {t("bizCompare.selectedCount", { n: String(tplSheets.length) })}
                </span>
              </span>
              <Button size="sm" variant="ghost" onPress={() => setShowMetricPicker((v) => !v)}>
                {showMetricPicker ? t("bizCompare.hideMetrics") : t("bizCompare.showMetrics")}
              </Button>
            </div>
            {showMetricPicker ? (
              <>
                <p className="muted ct-editor__pick-hint">{t("bizCompare.pickMetricsHintShort")}</p>
                <div className="ct-metric-chips">
                  {metrics.map((m) => {
                    const checked = tplSheets.some((s) => s.metric_id === m.metric_id);
                    return (
                      <button
                        key={m.metric_id}
                        type="button"
                        className={`mt-chip${checked ? " is-on" : ""}`}
                        aria-pressed={checked}
                        onClick={() => toggleTplMetric(m.metric_id, !checked)}
                      >
                        {m.metric_id}
                        <span className="ct-metric-chip__n">{m.fields?.length || 0}</span>
                      </button>
                    );
                  })}
                  {!metrics.length ? (
                    <span className="muted">{t("bizCompare.noMetricFields")}</span>
                  ) : null}
                </div>
              </>
            ) : null}
          </div>

          {!tplSheets.length ? (
            <div className="pt-list-empty">{t("bizCompare.noMetricsYet")}</div>
          ) : (
            <div className="ct-editor__main">
              <aside className="ct-editor__nav" aria-label={t("bizCompare.colSheets")}>
                <div className="ct-editor__nav-head">
                  <span>{t("bizCompare.colSheets")}</span>
                </div>
                <div className="ct-editor__nav-list" role="tablist">
                  {tplSheets.map((s, i) => {
                    const filterN = toOrGroups(s.row_filters || []).length;
                    return (
                      <button
                        key={`${sheetIdentity(s)}-${i}`}
                        type="button"
                        role="tab"
                        aria-selected={tplSheetIdx === i}
                        className={`ct-editor__nav-item${tplSheetIdx === i ? " is-active" : ""}`}
                        onClick={() => {
                          setTplSheetIdx(i);
                          setTplPaneTab("fields");
                        }}
                      >
                        <span className="ct-editor__nav-name">{sheetLabel(s)}</span>
                        <span className="ct-editor__nav-tag">
                          {!s.compare_fields.length
                            ? t("bizCompare.presenceShort")
                            : t("bizCompare.tagCompareN", { n: String(s.compare_fields.length) })}
                          {filterN ? ` · F${filterN}` : ""}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </aside>

              {activeTplSheet ? (
                <div className="ct-editor__pane">
                  <div className="ct-editor__pane-head">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="ct-editor__metric-edit" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <Input
                          value={activeTplSheet.title || ""}
                          placeholder={t("bizCompare.sheetTitle")}
                          aria-label={t("bizCompare.sheetTitle")}
                          onChange={(e) => updateActiveSheet({ title: e.target.value })}
                          style={{ maxWidth: 220 }}
                        />
                        <Input
                          value={activeTplSheet.sheet_id || activeTplSheet.metric_id}
                          placeholder={t("bizCompare.sheetId")}
                          aria-label={t("bizCompare.sheetId")}
                          onChange={(e) => updateActiveSheet({ sheet_id: e.target.value.trim() })}
                          style={{ maxWidth: 220 }}
                        />
                      </div>
                      <div className="ct-editor__meta muted">
                        {activeTplSheet.metric_id}
                        {" · "}
                        {activeTplSheet.compare_fields.length
                          ? t("bizCompare.modeFieldsShort")
                          : t("bizCompare.presenceShort")}
                        {" · "}
                        Key {(activeTplSheet.key_fields || []).join(" · ") || "—"}
                      </div>
                    </div>
                    <div className="btn-row" style={{ gap: 6 }}>
                      <Button size="sm" variant="secondary" onPress={() => duplicateTplSheet(tplSheetIdx)}>
                        {t("bizCompare.splitSheet")}
                      </Button>
                      <Button size="sm" variant="ghost" onPress={() => removeTplMetric(tplSheetIdx)}>
                        {t("bizCompare.removeSheet")}
                      </Button>
                    </div>
                  </div>

                  <div className="mt-rule-tabs" role="tablist">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={tplPaneTab === "fields"}
                      className={`mt-rule-tab${tplPaneTab === "fields" ? " is-active" : ""}`}
                      onClick={() => setTplPaneTab("fields")}
                    >
                      {t("bizCompare.tabFields")}
                      <span className="mt-rule-tab__n">{activeTplFields.length}</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={tplPaneTab === "filters"}
                      className={`mt-rule-tab${tplPaneTab === "filters" ? " is-active" : ""}`}
                      onClick={() => setTplPaneTab("filters")}
                    >
                      {t("bizCompare.tabFilters")}
                      <span className="mt-rule-tab__n">
                        {toOrGroups(activeTplSheet.row_filters || []).length}
                      </span>
                    </button>
                  </div>

                  {tplPaneTab === "filters" ? (
                    <TplRowFiltersEditor
                      groups={toOrGroups(activeTplSheet.row_filters || [])}
                      fieldOpts={activeTplFields.map((f) => f.name)}
                      t={t}
                      onChange={(next) => updateActiveSheet({ row_filters: fromOrGroups(next) })}
                    />
                  ) : (
                    <>
                      <p className="muted ct-field-hint">{t("bizCompare.fieldsHintShort")}</p>
                      <div className="pt-list-table-wrap ct-field-table-wrap">
                        <table className="data-table pt-list-table ct-field-table">
                          <thead>
                            <tr>
                              <th>{t("bizCompare.field")}</th>
                              <th title={t("bizCompare.keyFields")}>{t("bizCompare.keyFields")}</th>
                              <th title={t("bizCompare.ifaceFields")}>{t("bizCompare.ifaceFields")}</th>
                              <th title={t("bizCompare.compareFields")}>{t("bizCompare.compareFields")}</th>
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
                                  <code className="ct-field-name">{f.name}</code>
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
                                    className="mt-select"
                                    value={cmpMode === "ignore" ? "eq" : cmpMode}
                                    disabled={!isCompare}
                                    onChange={(e) =>
                                      updateActiveSheet({
                                        field_rules: upsertFieldRule(activeTplSheet, f.name, {
                                          compare: e.target.value,
                                          tolerance:
                                            e.target.value === "eq" ? undefined : rule.tolerance ?? 0,
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
                                      needsTol &&
                                      rule.tolerance !== undefined &&
                                      rule.tolerance !== null
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
                                    className="mt-select"
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
                  )}
                </div>
              ) : null}
            </div>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="secondary" onPress={() => setTplOpen(false)}>
            {t("bizState.cancel")}
          </Button>
          <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void saveTemplate()}>
            {t("bizCompare.saveTemplate")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      {/* Create job — guided steps */}
      <AppModalShell
        open={jobCreateOpen}
        onClose={closeCreateJob}
        size="lg"
        className="bm-create-modal"
      >
        <Modal.Header>
          <Modal.Heading>{t("bizCompare.createCompare")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3 bm-create">
          <nav className="bm-steps" aria-label={t("bizCompare.createSteps")}>
            {(
              [
                t("bizCompare.stepBasics"),
                t("bizCompare.stepSheets"),
                t("bizCompare.stepBatches"),
                t("bizCompare.stepMapping"),
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
                    if (i <= createStep) {
                      setCreateStep(i as CreateJobStep);
                      return;
                    }
                    let ok = true;
                    for (let s = 0; s < i; s++) {
                      if (!canAdvanceCreateStep(s as CreateJobStep)) {
                        ok = false;
                        break;
                      }
                    }
                    if (ok) setCreateStep(i as CreateJobStep);
                  }}
                >
                  <span className="bm-steps__num">{done ? "✓" : i + 1}</span>
                  <span className="bm-steps__label">{label}</span>
                </button>
              );
            })}
          </nav>

          {createStep === 0 ? (
            <div className="bm-create__pane bs-cmp-form" style={{ display: "grid", gap: 8 }}>
              <label className="ui-field ui-field--full">
                <span className="ui-field__label">{t("bizCompare.jobName")}</span>
                <Input value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <FieldSelect
                label={t("bizCompare.template")}
                value={templateId}
                onChange={(e) => setJobTemplateAndSheets(e.target.value)}
                fullWidth
              >
                {templates.map((tpl) => {
                  const n = templateSheets(tpl).length;
                  return (
                    <option key={tpl.id} value={tpl.id}>
                      {tpl.name} ({n} {t("bizCompare.sheetsUnit")})
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
            </div>
          ) : null}

          {createStep === 1 ? <div className="bm-create__pane">{renderSheetChips()}</div> : null}

          {createStep === 2 ? (
            <div className="bm-create__pane bs-cmp-form" style={{ display: "grid", gap: 8 }}>
              {renderBatchFields()}
            </div>
          ) : null}

          {createStep === 3 ? <div className="bm-create__pane">{renderMappingBlock()}</div> : null}
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="secondary" onPress={closeCreateJob}>
            {t("bizState.cancel")}
          </Button>
          <div className="bm-create__footer-spacer" />
          {createStep > 0 ? (
            <Button size="sm" variant="secondary" onPress={onCreateBack}>
              {t("bizCompare.back")}
            </Button>
          ) : null}
          {createStep < CREATE_JOB_STEPS - 1 ? (
            <Button size="sm" variant="primary" onPress={onCreateNext}>
              {t("bizCompare.next")}
            </Button>
          ) : (
            <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void createJob()}>
              {t("bizCompare.createCompare")}
            </Button>
          )}
        </Modal.Footer>
      </AppModalShell>

      {/* Job detail */}
      <AppModalShell
        open={Boolean(jobId)}
        onClose={closeJob}
        dismissible={!boardFs}
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
            renderJobForm()
          ) : (
            <div
              ref={boardRef}
              className={`bs-cmp-board${boardFs ? " is-fullscreen" : ""}`}
            >
              <div className="bs-cmp-board__toolbar">
                <div className="bs-cmp-board__meta">
                  {runDetail ? (
                    <div className="bs-cmp-sides" aria-label={t("bizCompare.sidesTitle")}>
                      {(() => {
                        const before = enrichSide(
                          runDetail.before,
                          beforeTaskId,
                          tasks,
                          beforeBatches,
                        );
                        const after = enrichSide(
                          runDetail.after,
                          afterTaskId || beforeTaskId,
                          tasks,
                          afterBatches.length ? afterBatches : beforeBatches,
                        );
                        const beforeName = sideDeviceName(before);
                        const afterName = sideDeviceName(after);
                        const beforeTime = sideCollectTime(before);
                        const afterTime = sideCollectTime(after);
                        return (
                          <>
                            <div className="bs-cmp-sides__side is-before">
                              <span className="bs-cmp-sides__tag">{t("bizCompare.sideBefore")}</span>
                              <strong className="bs-cmp-sides__name" title={`${beforeName} ${beforeTime}`}>
                                {beforeName}
                              </strong>
                              <span className="bs-cmp-sides__meta" title={beforeTime}>
                                {beforeTime}
                              </span>
                            </div>
                            <span className="bs-cmp-sides__arrow" aria-hidden>
                              →
                            </span>
                            <div className="bs-cmp-sides__side is-after">
                              <span className="bs-cmp-sides__tag">{t("bizCompare.sideAfter")}</span>
                              <strong className="bs-cmp-sides__name" title={`${afterName} ${afterTime}`}>
                                {afterName}
                              </strong>
                              <span className="bs-cmp-sides__meta" title={afterTime}>
                                {afterTime}
                              </span>
                            </div>
                          </>
                        );
                      })()}
                    </div>
                  ) : null}
                  {!boardFs ? (
                    <>
                      <select
                        className="ui-field__select bs-cmp-board__run-select"
                        aria-label={t("bizCompare.pickBatchRun")}
                        value={runDetail?.id || ""}
                        onChange={(e) => {
                          const id = e.target.value;
                          if (id) void loadRun(id);
                        }}
                      >
                        <option value="">{t("bizCompare.pickRun")}</option>
                        {runs.map((r) => {
                          const before = enrichSide(
                            (r as any).before,
                            beforeTaskId,
                            tasks,
                            beforeBatches,
                          );
                          const after = enrichSide(
                            (r as any).after,
                            afterTaskId || beforeTaskId,
                            tasks,
                            afterBatches.length ? afterBatches : beforeBatches,
                          );
                          const bl = sideDeviceName(before);
                          const al = sideDeviceName(after);
                          const when = formatSystemTime((r as any).created_at) || "";
                          return (
                            <option key={r.id} value={r.id}>
                              {when ? `${when} · ` : ""}
                              {bl} {sideCollectTime(before)} → {al} {sideCollectTime(after)}
                            </option>
                          );
                        })}
                      </select>
                      {runs.length ? (
                        <span className="muted bs-cmp-board__run-count">
                          {t("bizCompare.runCount", { n: String(runs.length) })}
                        </span>
                      ) : null}
                    </>
                  ) : null}
                </div>
                <div className="btn-row bs-cmp-board__actions">
                  {!boardFs ? (
                    <>
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
                        variant="danger"
                        isDisabled={busy || !runDetail?.id}
                        onPress={() => void removeRun(String(runDetail?.id || ""))}
                      >
                        {t("bizCompare.deleteRun")}
                      </Button>
                    </>
                  ) : null}
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
                <div
                  className={`bs-cmp-board__body${navCollapsed ? " is-nav-collapsed" : ""}`}
                >
                  <aside
                    className={`bs-cmp-nav${navCollapsed ? " is-collapsed" : ""}`}
                    aria-label={t("bizCompare.sheetNavTitle")}
                  >
                    <div className="bs-cmp-nav__head">
                      <button
                        type="button"
                        className="bs-cmp-nav__toggle"
                        aria-expanded={!navCollapsed}
                        title={
                          navCollapsed
                            ? t("bizCompare.expandSheetNav")
                            : t("bizCompare.collapseSheetNav")
                        }
                        onClick={() => setNavCollapsed((v) => !v)}
                      >
                        {navCollapsed ? "»" : "«"}
                      </button>
                      {!navCollapsed ? (
                        <>
                          <span className="bs-cmp-nav__head-main">
                            <span className="bs-cmp-nav__title">{t("bizCompare.sheetNavTitle")}</span>
                            <span
                              className="bs-cmp-nav__count"
                              title={t("bizCompare.sheetNavHint", {
                                n: String(sheetCards.length || runSheets.length),
                              })}
                            >
                              {sheetCards.length || runSheets.length}
                            </span>
                          </span>
                          <span className="bs-cmp-nav__legend" aria-hidden>
                            <span
                              className="bs-cmp-nav__num bs-cmp-nav__num--fail is-hot"
                              title={t("bizCompare.kindFail")}
                            >
                              {t("bizCompare.kindFail")}
                            </span>
                            <span
                              className="bs-cmp-nav__num bs-cmp-nav__num--ok is-hot"
                              title={t("bizCompare.kindSuccess")}
                            >
                              {t("bizCompare.kindSuccess")}
                            </span>
                            <span
                              className="bs-cmp-nav__num bs-cmp-nav__num--rate"
                              title={t("bizCompare.passRateShort")}
                            >
                              %
                            </span>
                          </span>
                        </>
                      ) : null}
                    </div>
                    <div className="bs-cmp-nav__list" role="tablist">
                      {(sheetCards.length
                        ? sheetCards
                        : runSheets.map((s) => {
                            const removed = Number(s.summary?.removed || 0);
                            const changed = Number(s.summary?.changed || 0);
                            const unchanged = Number(s.summary?.unchanged || 0);
                            const fail = removed + changed;
                            const judged = fail + unchanged;
                            return {
                              sheet_id: sheetIdentity(s),
                              title: sheetLabel(s),
                              metric_id: s.metric_id,
                              mode: s.mode,
                              added: s.summary?.added,
                              removed,
                              changed,
                              unchanged,
                              before_count: s.summary?.before_count,
                              after_count: s.summary?.after_count,
                              fail_count: fail,
                              success_count: unchanged,
                              diff_count: fail,
                              pass_rate: judged
                                ? Math.round((unchanged / judged) * 1000) / 10
                                : 100,
                            };
                          })
                      ).map((c) => {
                        const fail = sheetFailOf(c);
                        const ok = sheetSuccessOf(c);
                        const rate = sheetPassRateOf(c);
                        const id = sheetIdentity(c);
                        const label = sheetLabel(c);
                        const active = resultSheetId === id;
                        return (
                          <button
                            key={id}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            className={`bs-cmp-nav__item${active ? " is-active" : ""}${
                              fail > 0 ? " has-diff" : " is-clean"
                            }`}
                            onClick={() => setResultSheetId(id)}
                            title={`${label} · ${t("bizCompare.kindFail")} ${fail} · ${t("bizCompare.kindSuccess")} ${ok} · ${t("bizCompare.passRateShort")} ${rate}%`}
                          >
                            <span className="bs-cmp-nav__dot" aria-hidden />
                            <span className="bs-cmp-nav__name">{label}</span>
                            <span className="bs-cmp-nav__stats">
                              <span
                                className={`bs-cmp-nav__num bs-cmp-nav__num--fail${
                                  fail > 0 ? " is-hot" : ""
                                }`}
                              >
                                {fail}
                              </span>
                              <span className="bs-cmp-nav__num bs-cmp-nav__num--ok is-hot">
                                {ok}
                              </span>
                              <span className="bs-cmp-nav__num bs-cmp-nav__num--rate">
                                {rate}%
                              </span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </aside>

                  <div className="bs-cmp-main">
                    <div
                      className={`bs-cmp-strip${activeFail > 0 ? " is-warn" : " is-ok"}`}
                    >
                      <div className="bs-cmp-strip__sheet">
                        <span className="bs-cmp-strip__sheet-tag">
                          {t("bizCompare.sheetCurrent")}
                        </span>
                        <strong
                          className="bs-cmp-strip__sheet-name"
                          title={sheetLabel(activeSheetCard)}
                        >
                          {sheetLabel(activeSheetCard)}
                        </strong>
                        <span className="muted bs-cmp-strip__sheet-mode">
                          {activeFail > 0 ? t("bizCompare.kindFail") : t("bizCompare.kindPass")}
                          {activeSheetCard?.mode === "presence"
                            ? ` · ${t("bizCompare.presenceShort")}`
                            : ""}
                          {` · ${activeSheetCard?.before_count ?? 0}→${activeSheetCard?.after_count ?? 0}`}
                        </span>
                      </div>
                      <div className="bs-cmp-strip__pass" title={t("bizCompare.passRate")}>
                        <span className="bs-cmp-strip__pass-label">
                          {t("bizCompare.passRate")}
                        </span>
                        <span className="bs-cmp-strip__pass-value">{activePassRate}%</span>
                      </div>
                      <div className="bs-cmp-strip__kinds" role="group">
                        {(
                          [
                            ["diff", activeFail, "diff"],
                            ["added", activeAdded, "added"],
                            ["unchanged", activeSuccess, "unchanged"],
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
                                : id === "added"
                                  ? t("bizCompare.kindAddedShort")
                                  : t("bizCompare.kindSuccess")}
                            {n !== null ? (
                              <>
                                {" "}
                                <b>{n}</b>
                              </>
                            ) : null}
                          </button>
                        ))}
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
                    </div>

                    <div
                      ref={tableScrollRef}
                      onScroll={rememberTableScroll}
                      className={`pt-list-table-wrap bs-sheet-table bs-cmp-result-table${
                        diffsLoading ? " is-loading" : ""
                      }`}
                    >
                      <table className="data-table pt-list-table bs-cmp-diff-table">
                        <thead>
                          {(() => {
                            const keyCols = resultColumns.keys;
                            const compareCols = resultColumns.extras.filter((f) =>
                              resultColumns.compareSet.has(f),
                            );
                            const displayCols = resultColumns.extras.filter(
                              (f) => !resultColumns.compareSet.has(f),
                            );
                            const verdictColSpan = showFailCol ? 2 : 1;
                            const hasGroups =
                              keyCols.length + compareCols.length + displayCols.length > 0;
                            return (
                              <>
                                <tr className="bs-cmp-group-row">
                                  <th
                                    colSpan={verdictColSpan}
                                    className="bs-cmp-group bs-cmp-group--verdict"
                                  >
                                    {t("bizCompare.colKind")}
                                  </th>
                                  {keyCols.length ? (
                                    <th
                                      colSpan={keyCols.length}
                                      className="bs-cmp-group bs-cmp-group--key"
                                    >
                                      {t("bizCompare.keyFields")}
                                    </th>
                                  ) : null}
                                  {compareCols.length ? (
                                    <th
                                      colSpan={compareCols.length}
                                      className="bs-cmp-group bs-cmp-group--compare"
                                    >
                                      {t("bizCompare.compareFields")}
                                    </th>
                                  ) : null}
                                  {displayCols.length ? (
                                    <th
                                      colSpan={displayCols.length}
                                      className="bs-cmp-group bs-cmp-group--display"
                                    >
                                      {t("bizCompare.displayField")}
                                    </th>
                                  ) : null}
                                </tr>
                                <tr className="bs-cmp-field-row">
                                  <th className="bs-cmp-col-kind bs-cmp-sticky-kind bs-cmp-zone-start">
                                    <span className="bs-cmp-th__name">
                                      {t("bizCompare.colResult")}
                                    </span>
                                  </th>
                                  {showFailCol ? (
                                    <th className="bs-cmp-col-fail">
                                      <span className="bs-cmp-th__name">
                                        {t("bizCompare.colDetail")}
                                      </span>
                                    </th>
                                  ) : null}
                                  {hasGroups
                                    ? keyCols.map((k, ki) => (
                                        <th
                                          key={k}
                                          className={`bs-cmp-col-key${
                                            ki === 0 ? " bs-cmp-zone-start" : ""
                                          }`}
                                        >
                                          <span className="bs-cmp-th__name">{k}</span>
                                        </th>
                                      ))
                                    : null}
                                  {hasGroups
                                    ? compareCols.map((f, fi) => (
                                        <th
                                          key={f}
                                          className={`bs-cmp-col-compare${
                                            fi === 0 ? " bs-cmp-zone-start" : ""
                                          }`}
                                        >
                                          <span className="bs-cmp-th__name">{f}</span>
                                        </th>
                                      ))
                                    : null}
                                  {hasGroups
                                    ? displayCols.map((f, fi) => (
                                        <th
                                          key={f}
                                          className={`bs-cmp-col-display${
                                            fi === 0 ? " bs-cmp-zone-start" : ""
                                          }`}
                                        >
                                          <span className="bs-cmp-th__name">{f}</span>
                                        </th>
                                      ))
                                    : null}
                                </tr>
                              </>
                            );
                          })()}
                        </thead>
                        <tbody>
                          {pagedDiffs.map((d, i) => {
                            const pre = pickSideRow(
                              d.mapped_before as Record<string, unknown> | null | undefined,
                              d.before as Record<string, unknown> | null | undefined,
                            );
                            const post = pickSideRow(
                              d.after as Record<string, unknown> | null | undefined,
                            );
                            const verdict = rowVerdict(d.kind);
                            const isFail = verdict.tone === "fail";
                            return (
                              <tr key={i} className={`bs-cmp-row bs-cmp-row--${d.kind}`}>
                                <td className="bs-cmp-col-kind bs-cmp-sticky-kind">
                                  <span className={`bs-cmp-verdict bs-cmp-verdict--${verdict.tone}`}>
                                    {verdict.label}
                                  </span>
                                  {isFail ? (
                                    <span className="bs-cmp-verdict__sub">
                                      {kindLabel(d.kind)}
                                    </span>
                                  ) : null}
                                </td>
                                {showFailCol ? (
                                  <td className="bs-cmp-fail-cell">
                                    {verdict.tone === "pass"
                                      ? t("bizCompare.failFieldsEmpty")
                                      : failFieldsLabel(d)}
                                  </td>
                                ) : null}
                                {resultColumns.keys.map((k, ki) => (
                                  <td
                                    key={k}
                                    className={`bs-cmp-key-cell${
                                      ki === 0 ? " bs-cmp-zone-start" : ""
                                    }`}
                                  >
                                    {cellText(d.key?.[k] ?? pre[k] ?? post[k]) || "—"}
                                  </td>
                                ))}
                                {resultColumns.extras.map((f, fi) => {
                                  const pv = cellText(pre[f]);
                                  const av = cellText(post[f]);
                                  const ch = d.changes?.[f];
                                  const isCmp = resultColumns.compareSet.has(f);
                                  const prev = resultColumns.extras[fi - 1];
                                  const prevCmp = prev
                                    ? resultColumns.compareSet.has(prev)
                                    : null;
                                  const zoneStart = fi === 0 || prevCmp !== isCmp;
                                  const mismatch =
                                    d.kind === "added" || d.kind === "removed"
                                      ? Boolean(pv || av)
                                      : isCmp
                                        ? Boolean(ch)
                                        : pv !== av;
                                  return (
                                    <PairCell
                                      key={f}
                                      beforeText={pv}
                                      afterText={av}
                                      kind={d.kind}
                                      mismatch={mismatch}
                                      reason={ch?.reason}
                                      beforeLabel={t("bizCompare.pairBefore")}
                                      afterLabel={t("bizCompare.pairAfter")}
                                      zoneStart={zoneStart}
                                    />
                                  );
                                })}
                              </tr>
                            );
                          })}
                          {runDetail && !diffsLoading && !pagedDiffs.length ? (
                            <tr>
                              <td colSpan={resultEmptyColSpan}>
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
              {jobId ? (
                <Button size="sm" variant="danger" isDisabled={busy} onPress={() => void removeJob(jobId)}>
                  {t("bizCompare.deleteJob")}
                </Button>
              ) : null}
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
              <Button
                size="sm"
                variant="danger"
                isDisabled={busy || !runDetail?.id}
                onPress={() => void removeRun(String(runDetail?.id || ""))}
              >
                {t("bizCompare.deleteRun")}
              </Button>
              <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void runNow()}>
                {t("bizCompare.runNow")}
              </Button>
            </>
          )}
          <Button size="sm" variant="ghost" onPress={closeJob}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
