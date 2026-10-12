// Pure template editing/import helpers; preserve all comparison settings.
export type MetricSheet = {
  sheet_id?: string;
  title?: string;
  metric_id: string;
  key_fields: string[];
  iface_fields: string[];
  compare_fields: string[];
  display_fields?: string[];
  row_filters?: RowFilter[];
  field_rules?: FieldRule[];
  ignore_port_changes?: boolean | null;
  /** Partial port map: unmapped ports match by name (default) or by content only. */
  unmapped_port_policy?: "" | "by_name" | "ignore_port";
  /** Same-key rows pair by value (default for new runs) or by collection order. */
  duplicate_pairing?: "" | "sorted" | "ordered";
  /** false = skip the built-in per-vendor interface names (GigabitEthernet → GE …). */
  builtin_vendor_canonical?: boolean;
};

export type Side = "" | "both" | "before" | "after";

export type FieldRule = {
  field: string;
  compare?: string;
  normalize?: string;
  ignore?: boolean;
  tolerance?: number;
  /** Cross-vendor value aliases, e.g. {"estab": "established"}; matched case-insensitively. */
  value_map?: Record<string, string>;
  side?: Side;
  apply_to_key?: boolean;
  /** Order for compare=not_worse: earlier entries are better. */
  rank?: string[];
};

export type RowFilter = {
  field?: string;
  op?: string;
  value?: string | number | boolean | (string | number | boolean)[];
  any?: RowFilter[];
  all?: RowFilter[];
  /** Only evaluate this filter on one side of the comparison. */
  side?: Side;
};

export type IfaceRule = { from: string; to: string; side?: Side; vendor?: string };

export const DIRECTIONAL_COMPARE = ["not_worse", "numeric_min", "numeric_max"] as const;
export const MAX_VALUE_MAP = 500;

const SHEET_OPTION_KEYS = ["unmapped_port_policy", "duplicate_pairing", "builtin_vendor_canonical"] as const;

/** The sheet exactly as the API stores it: every identity/compare setting survives a round trip. */
export function sheetPayload(s: MetricSheet, identity?: string): Record<string, unknown> {
  const id = identity ?? (s.sheet_id || s.metric_id);
  const out: Record<string, unknown> = {
    sheet_id: id,
    title: s.title || id,
    metric_id: s.metric_id,
    key_fields: s.key_fields,
    iface_fields: s.iface_fields,
    compare_fields: s.compare_fields,
    display_fields: s.display_fields || [...s.key_fields, ...s.compare_fields],
    row_filters: s.row_filters || [],
    field_rules: s.field_rules || [],
    ignore_port_changes: s.ignore_port_changes,
  };
  for (const key of SHEET_OPTION_KEYS) {
    const value = s[key];
    if (value !== undefined && value !== "") out[key] = value;
  }
  return out;
}

export function sheetOptions(raw: Record<string, unknown>): Partial<MetricSheet> {
  const out: Record<string, unknown> = {};
  for (const key of SHEET_OPTION_KEYS) if (raw[key] !== undefined && raw[key] !== null) out[key] = raw[key];
  return out as Partial<MetricSheet>;
}

export function parseTemplateImport(raw: unknown): {
  name: string;
  note: string;
  metrics: MetricSheet[];
  iface_normalize_rules?: IfaceRule[];
} | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  if ((o.format !== undefined && o.format !== "netx.biz_compare_template") || (o.version !== undefined && o.version !== 1)) {
    throw new Error("template: unsupported_format_version");
  }
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
        ignore_fields: o.ignore_fields || [],
        ignore_port_changes: o.ignore_port_changes,
      },
    ];
  }
  if (!name || !Array.isArray(metricsRaw) || !metricsRaw.length) return null;
  const metrics: MetricSheet[] = [];
  for (const item of metricsRaw) {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error(`metrics[${metrics.length}]: sheet_required`);
    const m = item as Record<string, unknown>;
    const mid = String(m.metric_id || "").trim();
    const path = `metrics[${metrics.length}]`;
    const keys = stringList(m.key_fields, `${path}.key_fields`);
    if (!mid || !keys.length) throw new Error(`${path}: metric_id_and_keys_required`);
    const rules = m.field_rules === undefined ? [] : m.field_rules;
    if (!Array.isArray(rules)) throw new Error(`${path}.field_rules: list_required`);
    const ignore = stringList(m.ignore_fields ?? [], `${path}.ignore_fields`);
    const fieldRules = structuredClone(rules) as FieldRule[];
    for (const field of ignore) {
      const rule = fieldRules.find((r) => r?.field === field);
      if (rule) Object.assign(rule, { compare: "ignore", ignore: true });
      else fieldRules.push({ field, compare: "ignore", ignore: true });
    }
    if (m.row_filters !== undefined && !Array.isArray(m.row_filters)) throw new Error(`${path}.row_filters: list_required`);
    if (m.ignore_port_changes != null && typeof m.ignore_port_changes !== "boolean") throw new Error(`${path}: boolean_port_policy_required`);
    metrics.push({
      sheet_id: String(m.sheet_id || mid).trim() || mid,
      title: String(m.title || m.sheet_id || mid).trim() || mid,
      metric_id: mid,
      key_fields: keys,
      iface_fields: stringList(m.iface_fields ?? [], `${path}.iface_fields`),
      compare_fields: stringList(m.compare_fields ?? [], `${path}.compare_fields`).filter((f) => !ignore.includes(f)),
      display_fields: m.display_fields === undefined ? undefined : stringList(m.display_fields, `${path}.display_fields`),
      row_filters: (m.row_filters ?? []) as RowFilter[],
      field_rules: fieldRules,
      ignore_port_changes: m.ignore_port_changes as boolean | null | undefined,
      ...sheetOptions(m),
    });
  }
  if (!metrics.length) return null;
  const normRaw = o.iface_normalize_rules;
  const iface_normalize_rules: IfaceRule[] = [];
  if (Array.isArray(normRaw)) {
    for (const item of normRaw) {
      if (!item || typeof item !== "object") throw new Error("iface_normalize_rules: alias_pair_required");
      const r = item as Record<string, unknown>;
      const fr = String(r.from || "").trim();
      const to = String(r.to || "").trim();
      if (!fr || !to) throw new Error("iface_normalize_rules: alias_pair_required");
      const rule: IfaceRule = { from: fr, to };
      const side = String(r.side || "").trim().toLowerCase();
      if (side === "before" || side === "after") rule.side = side;
      const vendor = String(r.vendor || "").trim().toLowerCase();
      if (vendor) rule.vendor = vendor;
      if (iface_normalize_rules.some((x) => sameAlias(x, rule))) throw new Error("iface_normalize_rules: duplicate_alias");
      iface_normalize_rules.push(rule);
    }
  }
  if (normRaw != null && !Array.isArray(normRaw)) throw new Error("iface_normalize_rules: list_required");
  validateTemplateDraft(metrics);
  return { name, note, metrics, iface_normalize_rules };
}

function sameAlias(a: IfaceRule, b: IfaceRule): boolean {
  return a.from.toLowerCase() === b.from.toLowerCase() && (a.side || "") === (b.side || "") && (a.vendor || "") === (b.vendor || "");
}

/** `from,to[,side[,vendor]]` per line; side is before/after, vendor a vendor key such as huawei. */
export function parseIfaceNormText(text: string): IfaceRule[] {
  const out: IfaceRule[] = [];
  for (const [index, line] of text.split(/\r?\n/).entries()) {
    const s = line.trim();
    if (!s || s.startsWith("#")) continue;
    const parts = (s.includes(",") ? s.split(",") : s.includes("\t") ? s.split("\t") : s.split(/\s+/)).map((x) => x.trim());
    const [fr = "", to = "", side = "", vendor = ""] = parts;
    if (!fr || !to) throw new Error(`iface_normalize_rules:${index + 1}: alias_pair_required`);
    const sideN = side.toLowerCase();
    if (sideN && !["before", "after", "both"].includes(sideN)) throw new Error(`iface_normalize_rules:${index + 1}: unknown_alias_side`);
    const rule: IfaceRule = { from: fr, to };
    if (sideN === "before" || sideN === "after") rule.side = sideN;
    if (vendor) rule.vendor = vendor.toLowerCase();
    if (out.some((x) => sameAlias(x, rule))) throw new Error(`iface_normalize_rules:${index + 1}: duplicate_alias`);
    out.push(rule);
  }
  return out;
}

export function formatIfaceNormText(rules?: IfaceRule[]): string {
  return (rules || [])
    .map((r) => [r.from, r.to, r.side || (r.vendor ? "both" : ""), r.vendor || ""].filter((x, i, arr) => x || i < 2 || arr.slice(i).some(Boolean)).join(","))
    .join("\n");
}

/** `source,canonical` per line (blank lines and # comments ignored) → value_map. */
export function parseValueMapText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [index, line] of text.split(/\r?\n/).entries()) {
    const s = line.trim();
    if (!s || s.startsWith("#")) continue;
    const parts = (s.includes(",") ? s.split(",") : s.includes("\t") ? s.split("\t") : s.split(/\s+/)).map((x) => x.trim());
    if (parts.length < 2 || !parts[0] || !parts[1]) throw new Error(`value_map:${index + 1}: pair_required`);
    out[parts[0]] = parts.slice(1).join(" ");
  }
  if (Object.keys(out).length > MAX_VALUE_MAP) throw new Error("value_map: too_large");
  return out;
}

export function formatValueMapText(map?: Record<string, string>): string {
  return Object.entries(map || {}).map(([k, v]) => `${k},${v}`).join("\n");
}

function isLeafFilter(f: RowFilter): boolean {
  return Boolean(f && !f.any && !f.all && f.field);
}

function stringList(raw: unknown, path: string): string[] {
  if (!Array.isArray(raw) || raw.some((x) => typeof x !== "string" || !x.trim())) throw new Error(`${path}: nonempty_strings_required`);
  const values = raw.map((x: string) => x.trim());
  if (new Set(values).size !== values.length) throw new Error(`${path}: duplicate_field`);
  return values;
}

export function validateTemplateDraft(metrics: MetricSheet[]): void {
  if (!metrics.length) throw new Error("metrics: metrics_required");
  const ids = new Set<string>();
  const ops = new Set(["eq", "==", "ci_eq", "ne", "!=", "in", "not_in", "nin", "contains", "empty", "not_empty", "nonempty", "regex", "age_timer"]);
  function filters(nodes: RowFilter[], path: string, depth = 0) {
    if (!Array.isArray(nodes) || depth > 12) throw new Error(`${path}: invalid_filters`);
    nodes.forEach((f, i) => {
      const here = `${path}[${i}]`;
      if (!f || typeof f !== "object") throw new Error(`${here}: filter_required`);
      if (f.any !== undefined || f.all !== undefined) {
        if ((f.any !== undefined && f.all !== undefined) || f.field || f.op || f.value != null) throw new Error(`${here}: group_or_leaf_required`);
        const kids = f.any ?? f.all;
        if (!Array.isArray(kids) || !kids.length) throw new Error(`${here}: nonempty_group_required`);
        filters(kids, here, depth + 1);
      } else {
        const op = (f.op || "eq").trim().toLowerCase();
        if (!f.field?.trim() || !ops.has(op)) throw new Error(`${here}: invalid_filter_field_or_operator`);
        if (["in", "not_in", "nin"].includes(op) && (!Array.isArray(f.value) || !f.value.length)) throw new Error(`${here}: nonempty_value_list_required`);
        if (!["in", "not_in", "nin", "empty", "not_empty", "nonempty", "age_timer"].includes(op) && !["string", "number", "boolean"].includes(typeof f.value)) throw new Error(`${here}: scalar_value_required`);
        if (["contains", "regex"].includes(op) && !String(f.value ?? "").trim()) throw new Error(`${here}: value_required`);
      }
    });
  }
  metrics.forEach((s, i) => {
    const path = `metrics[${i}]`;
    const id = (s.sheet_id || s.metric_id).trim();
    if (!s.metric_id?.trim() || !id || ids.has(id)) throw new Error(`${path}: invalid_or_duplicate_sheet_id`);
    ids.add(id);
    if (!stringList(s.key_fields, `${path}.key_fields`).length) throw new Error(`${path}: key_fields_required`);
    for (const key of ["iface_fields", "compare_fields", "display_fields"] as const) if (s[key] !== undefined) stringList(s[key], `${path}.${key}`);
    const fields = new Set<string>();
    for (const r of s.field_rules || []) {
      // one rule per (field, side): a field may carry a different value_map on each side
      const marker = `${r?.field?.trim()}\u0000${r?.side === "before" || r?.side === "after" ? r.side : ""}`;
      if (!r?.field?.trim() || fields.has(marker)) throw new Error(`${path}.field_rules: invalid_or_duplicate_field`);
      fields.add(marker);
      if (!["", "eq", "ignore", "skip", "off", "numeric", "number", "int", "float", "percent", "pct", "rel", ...DIRECTIONAL_COMPARE].includes((r.compare || "").toLowerCase())) throw new Error(`${path}.field_rules: unknown_compare_mode`);
      if ((r.compare || "").toLowerCase() === "not_worse" && (!Array.isArray(r.rank) || r.rank.length < 2)) throw new Error(`${path}.${r.field}: rank_list_required`);
      if (r.value_map != null) {
        const entries = Object.entries(r.value_map);
        if (typeof r.value_map !== "object" || entries.some(([k, v]) => typeof k !== "string" || typeof v !== "string")) throw new Error(`${path}.${r.field}: value_map_string_pairs_required`);
        if (entries.length > MAX_VALUE_MAP) throw new Error(`${path}.${r.field}: value_map_too_large`);
      }
      if (!["", "none", "strip", "lower", "upper", "mac", "empty_as_blank"].includes((r.normalize || "").toLowerCase())) throw new Error(`${path}.field_rules: unknown_normalize_mode`);
      if (r.tolerance != null && (typeof r.tolerance !== "number" || !Number.isFinite(r.tolerance) || r.tolerance < 0)) throw new Error(`${path}.${r.field}: finite_nonnegative_tolerance_required`);
    }
    filters(s.row_filters || [], `${path}.row_filters`);
  });
}

/** Expand sheet row_filters into OR-groups of AND leaf conditions for editing. */
export function toOrGroups(filters: RowFilter[]): RowFilter[][] | null {
  const list = filters || [];
  if (!list.length) return [];
  if (list.length === 1 && list[0]?.any && Array.isArray(list[0].any)) {
    const groups: RowFilter[][] = [];
    for (const g of list[0].any) {
      if (g?.all?.length && !g.any && !g.field && g.all.every(isLeafFilter)) groups.push(g.all);
      else if (isLeafFilter(g)) groups.push([g]);
      else return null;
    }
    return groups;
  }
  if (list.every(isLeafFilter)) return [list.map((f) => ({ ...f }))];
  // Opaque structures stay intact. Never show an editable, incomplete subset.
  return null;
}

/** Serialize OR-groups back to engine row_filters (AND of leaves, or single any-of). */
export function fromOrGroups(groups: RowFilter[][]): RowFilter[] {
  const clean = groups
    .map((g) =>
      g
        .map((f) => ({
          field: f.field || "",
          op: f.op || "eq",
          value: f.value ?? "",
          ...(f.side === "before" || f.side === "after" ? { side: f.side } : {}),
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
