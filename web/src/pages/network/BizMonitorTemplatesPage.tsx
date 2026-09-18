import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizCompareListMetrics,
  bizCompareListTemplates,
  bizMonitorCreateTemplate,
  bizMonitorDeleteTemplate,
  bizMonitorListTemplates,
  bizMonitorUpdateTemplate,
  formatErr,
} from "../../services/api";

type MetricField = { name: string; display_name?: string };
type MetricSchema = { metric_id: string; fields: MetricField[] };
type CompareSheet = {
  metric_id: string;
  key_fields?: string[];
  iface_fields?: string[];
  compare_fields?: string[];
  display_fields?: string[];
};
type CompareTpl = { id: string; name: string; metrics?: CompareSheet[] };
/** Leaf: presence (row existence) or value (field + strategy). */
type RuleCond = {
  type: "presence" | "value";
  field?: string;
  op?: string;
  value?: string | string[];
};
/** OR-of-AND: each inner list is AND; outer list is OR. */
type CondGroup = RuleCond[];
type DualPat = {
  old_groups?: CondGroup[];
  new_groups?: CondGroup[];
  /** Legacy — migrated into groups on normalize. */
  old?: string[];
  new?: string[];
  old_mode?: "any" | "all";
  new_mode?: "any" | "all";
  old_conds?: RuleCond[];
  new_conds?: RuleCond[];
};
type SheetOverride = {
  metric_id: string;
  status_fields?: string[];
  down_values?: string[];
  up_values?: string[];
  success?: DualPat[];
  anomaly?: DualPat[];
  skip_dual?: boolean;
  field_tokens?: Array<{ side: string; field: string; in: string[] }>;
};
type MonitorTpl = {
  id: string;
  name: string;
  compare_template_id: string;
  compare_template_name?: string;
  collect_metric_ids?: string[];
  defaults?: Record<string, unknown>;
  sheet_overrides?: SheetOverride[];
  note?: string;
};

const PRESENCE_TOKENS = [
  { id: "removed", labelKey: "tokRemoved" },
  { id: "added", labelKey: "tokAdded" },
  { id: "unchanged", labelKey: "tokUnchanged" },
  { id: "changed", labelKey: "tokChanged" },
] as const;

const VALUE_OPS = [
  "eq",
  "ne",
  "in",
  "not_in",
  "empty",
  "not_empty",
  "changed",
  "unchanged",
] as const;

const OPS_NEED_VALUE = new Set(["eq", "ne", "in", "not_in"]);

function emptyCond(type: RuleCond["type"] = "presence", field = ""): RuleCond {
  if (type === "value") return { type: "value", field, op: "eq", value: "" };
  return { type: "presence", value: "removed" };
}

function emptyGroup(type: RuleCond["type"] = "presence", field = ""): CondGroup {
  return [emptyCond(type, field)];
}

function migrateLegacyCond(raw: Record<string, unknown>): RuleCond {
  const typ = String(raw.type || "").toLowerCase();
  if (typ === "status") {
    // Legacy status class: bind a field later; empty field still matches the old class.
    return { type: "value", field: "", op: "eq", value: String(raw.value ?? raw.status ?? "") };
  }
  if (typ === "field" || typ === "value") {
    return {
      type: "value",
      field: String(raw.field || ""),
      op: String(raw.op || "eq"),
      value: (raw.value as string | string[]) ?? "",
    };
  }
  if (typ === "kind" || typ === "presence" || raw.kind != null) {
    return {
      type: "presence",
      value: String(raw.value ?? raw.kind ?? "removed"),
    };
  }
  if (raw.field) {
    return {
      type: "value",
      field: String(raw.field),
      op: String(raw.op || "eq"),
      value: (raw.value as string | string[]) ?? "",
    };
  }
  return { type: "presence", value: String(raw.value || "removed") };
}

function tokenToCond(tok: string): RuleCond {
  if (PRESENCE_TOKENS.some((x) => x.id === tok)) return { type: "presence", value: tok };
  if (tok === "up" || tok === "down" || tok === "other") {
    return { type: "value", field: "", op: "eq", value: tok };
  }
  if (tok.startsWith("field:")) {
    const rest = tok.slice("field:".length);
    const i = rest.indexOf(":");
    if (i > 0) {
      return { type: "value", field: rest.slice(0, i), op: "eq", value: rest.slice(i + 1) };
    }
  }
  return { type: "presence", value: tok };
}

function groupsFromFlat(conds: RuleCond[], mode: "any" | "all"): CondGroup[] {
  if (!conds.length) return [];
  if (mode === "all") return [conds];
  return conds.map((c) => [c]);
}

function groupsFromLegacyTokens(tokens: string[] | undefined): CondGroup[] {
  return (tokens || []).map((tok) => [tokenToCond(tok)]);
}

function normalizeGroups(
  groups: CondGroup[] | undefined,
  flatConds: RuleCond[] | undefined,
  mode: "any" | "all" | undefined,
  legacyTokens: string[] | undefined,
): CondGroup[] {
  if (Array.isArray(groups)) {
    if (!groups.length) return [];
    return groups
      .map((g) =>
        (g || [])
          .filter(Boolean)
          .map((c) => migrateLegacyCond(c as unknown as Record<string, unknown>)),
      )
      .filter((g) => g.length > 0);
  }
  if (flatConds?.length) {
    return groupsFromFlat(
      flatConds.map((c) => migrateLegacyCond(c as unknown as Record<string, unknown>)),
      mode || "any",
    );
  }
  return groupsFromLegacyTokens(legacyTokens);
}

function normalizePat(pat: DualPat, opts?: { allowEmptySide?: boolean }): DualPat {
  const allowEmpty = Boolean(opts?.allowEmptySide);
  const old_groups = normalizeGroups(pat.old_groups, pat.old_conds, pat.old_mode, pat.old);
  const new_groups = normalizeGroups(pat.new_groups, pat.new_conds, pat.new_mode, pat.new);
  if (allowEmpty) {
    return { old_groups, new_groups };
  }
  return {
    old_groups: old_groups.length ? old_groups : [emptyGroup("presence")],
    new_groups: new_groups.length ? new_groups : [emptyGroup("presence")],
  };
}

function successFromPresence(oldVals: string[], newVals: string[]): DualPat {
  return normalizePat({
    old_groups: oldVals.map((v) => [{ type: "presence", value: v }]),
    new_groups: newVals.map((v) => [{ type: "presence", value: v }]),
  });
}

function anomalyBothGone(): DualPat {
  return normalizePat(
    {
      old_groups: [[{ type: "presence", value: "removed" }]],
      new_groups: [[{ type: "presence", value: "removed" }]],
    },
    { allowEmptySide: true },
  );
}

function anomalySideOnly(
  side: "old" | "new",
  groups: CondGroup[],
): DualPat {
  return normalizePat(
    {
      old_groups: side === "old" ? groups : [],
      new_groups: side === "new" ? groups : [],
    },
    { allowEmptySide: true },
  );
}

function defaultAnomalyForState(field: string, downValues: string[]): DualPat[] {
  const downGroup: CondGroup = [{ type: "value", field, op: "in", value: downValues }];
  return [
    anomalyBothGone(),
    anomalySideOnly("old", [[{ type: "presence", value: "removed" }], downGroup]),
    anomalySideOnly("new", [[{ type: "presence", value: "removed" }], downGroup]),
  ];
}

function defaultAnomalyPresenceOnly(): DualPat[] {
  return [
    anomalyBothGone(),
    anomalySideOnly("old", [[{ type: "presence", value: "removed" }]]),
    anomalySideOnly("new", [[{ type: "presence", value: "removed" }]]),
  ];
}

function emptyOverride(metricId: string): SheetOverride {
  return {
    metric_id: metricId,
    status_fields: [],
    down_values: [],
    up_values: [],
    success: [successFromPresence(["removed"], ["added"])],
    anomaly: defaultAnomalyPresenceOnly(),
    skip_dual: false,
  };
}

export function presetForMetric(metricId: string): SheetOverride {
  const mid = metricId;
  if (mid === "interface_brief") {
    const upConds: RuleCond[] = [
      { type: "value", field: "admin", op: "in", value: ["up"] },
      { type: "value", field: "phy", op: "in", value: ["up"] },
    ];
    return {
      metric_id: mid,
      status_fields: ["admin", "phy", "prot"],
      down_values: ["down"],
      up_values: ["up"],
      success: [
        normalizePat({
          old_groups: [
            [{ type: "presence", value: "removed" }],
            [
              { type: "value", field: "admin", op: "in", value: ["down"] },
              { type: "value", field: "phy", op: "in", value: ["down"] },
            ],
          ],
          new_groups: [
            [{ type: "presence", value: "added" }, ...upConds],
            [{ type: "presence", value: "unchanged" }, ...upConds],
            upConds,
          ],
        }),
      ],
      anomaly: defaultAnomalyForState("admin", ["down"]),
      skip_dual: false,
    };
  }
  if (mid === "bgp_peer") {
    const upConds: RuleCond[] = [{ type: "value", field: "state", op: "eq", value: "established" }];
    return {
      metric_id: mid,
      status_fields: ["state"],
      down_values: ["idle", "active", "connect", "down"],
      up_values: ["established"],
      success: [
        normalizePat({
          old_groups: [
            [{ type: "presence", value: "removed" }],
            [
              {
                type: "value",
                field: "state",
                op: "in",
                value: ["idle", "active", "connect", "down"],
              },
            ],
          ],
          new_groups: [
            [{ type: "presence", value: "added" }, ...upConds],
            [{ type: "presence", value: "unchanged" }, ...upConds],
            upConds,
          ],
        }),
      ],
      anomaly: defaultAnomalyForState("state", ["idle", "active", "connect", "down"]),
      skip_dual: false,
    };
  }
  if (mid === "arp" || mid === "nd6_cache" || mid === "lldp_neighbor") {
    return {
      metric_id: mid,
      status_fields: [],
      down_values: [],
      up_values: [],
      success: [successFromPresence(["removed"], ["added"])],
      anomaly: defaultAnomalyPresenceOnly(),
      skip_dual: false,
    };
  }
  if (mid.includes("isis") || mid.includes("ospf") || mid.includes("adjacency")) {
    const upConds: RuleCond[] = [
      { type: "value", field: "state", op: "in", value: ["up", "full", "2way"] },
    ];
    return {
      metric_id: mid,
      status_fields: ["state"],
      down_values: ["down", "init", "idle"],
      up_values: ["up", "full", "2way"],
      success: [
        normalizePat({
          old_groups: [
            [{ type: "presence", value: "removed" }],
            [
              {
                type: "value",
                field: "state",
                op: "in",
                value: ["down", "init", "idle"],
              },
            ],
          ],
          new_groups: [
            [{ type: "presence", value: "added" }, ...upConds],
            [{ type: "presence", value: "unchanged" }, ...upConds],
            upConds,
          ],
        }),
      ],
      anomaly: defaultAnomalyForState("state", ["down", "init", "idle"]),
      skip_dual: false,
    };
  }
  if (mid.includes("route") || mid.includes("vrf")) {
    return {
      metric_id: mid,
      status_fields: [],
      down_values: [],
      up_values: [],
      success: [successFromPresence(["removed"], ["added"])],
      anomaly: defaultAnomalyPresenceOnly(),
      skip_dual: false,
    };
  }
  return emptyOverride(mid);
}

function skipPreset(metricId: string): SheetOverride {
  return { ...emptyOverride(metricId), skip_dual: true, success: [], anomaly: [] };
}

function overrideFor(overrides: SheetOverride[], metricId: string): SheetOverride {
  const found = overrides.find((o) => o.metric_id === metricId);
  const merged = found ? { ...emptyOverride(metricId), ...found, metric_id: metricId } : emptyOverride(metricId);
  return {
    ...merged,
    success: (merged.success || []).map((p) => normalizePat(p)),
    anomaly: (merged.anomaly || []).map((p) => normalizePat(p, { allowEmptySide: true })),
  };
}

function csvValues(raw: string): string[] {
  return raw
    .split(/[,;\s]+/)
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
}

function Chip({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`mt-chip${active ? " is-on" : ""}`}
      aria-pressed={active}
    >
      {label}
    </button>
  );
}

function patchGroupCond(
  groups: CondGroup[],
  gi: number,
  ci: number,
  patch: Partial<RuleCond> | RuleCond,
): CondGroup[] {
  return groups.map((g, i) =>
    i === gi ? g.map((x, j) => (j === ci ? { ...x, ...patch } : x)) : g,
  );
}

function SideGroupsEditor({
  allowEmpty,
  groups,
  fieldChoices,
  t,
  onChange,
}: {
  allowEmpty: boolean;
  groups: CondGroup[];
  fieldChoices: MetricField[];
  t: (k: string, vars?: Record<string, string | number>) => string;
  onChange: (next: CondGroup[]) => void;
}) {
  const dontCare = allowEmpty && groups.length === 0;
  const setGroups = (next: CondGroup[]) => {
    if (!next.length && !allowEmpty) onChange([emptyGroup()]);
    else onChange(next);
  };

  return (
    <div className="mt-side">
      {allowEmpty ? (
        <label className="mt-dontcare">
          <input
            type="checkbox"
            checked={dontCare}
            onChange={(e) => {
              if (e.target.checked) setGroups([]);
              else setGroups([emptyGroup("presence")]);
            }}
          />
          <span>{t("bizMonitorTpl.sideDontCare")}</span>
        </label>
      ) : null}
      {dontCare ? (
        <p className="muted mt-side-empty">{t("bizMonitorTpl.sideDontCareHint")}</p>
      ) : (
        <>
          {groups.map((group, gi) => (
            <div key={gi} className="mt-or-wrap">
              {gi > 0 ? <div className="mt-logic-badge mt-logic-badge--or">{t("bizMonitorTpl.or")}</div> : null}
              <div className="mt-and-box">
                <div className="mt-and-box__head">
                  <span className="muted">{t("bizMonitorTpl.groupN", { n: String(gi + 1) })}</span>
                  <button
                    type="button"
                    className="mt-icon-btn"
                    aria-label={t("bizMonitorTpl.removeRow")}
                    onClick={() => {
                      const next = groups.filter((_, i) => i !== gi);
                      setGroups(next.length ? next : allowEmpty ? [] : [emptyGroup()]);
                    }}
                  >
                    ×
                  </button>
                </div>
                {group.map((cond, ci) => (
                  <div key={ci}>
                    {ci > 0 ? (
                      <div className="mt-logic-badge mt-logic-badge--and">{t("bizMonitorTpl.and")}</div>
                    ) : null}
                    <div className={`mt-cond-row${cond.type === "value" ? " is-value" : " is-presence"}`}>
                      <select
                        className="mt-select"
                        value={cond.type}
                        onChange={(e) => {
                          const typ = e.target.value as RuleCond["type"];
                          setGroups(
                            patchGroupCond(
                              groups,
                              gi,
                              ci,
                              emptyCond(typ, fieldChoices[0]?.name || ""),
                            ),
                          );
                        }}
                      >
                        <option value="presence">{t("bizMonitorTpl.groupPresence")}</option>
                        <option value="value">{t("bizMonitorTpl.groupValue")}</option>
                      </select>
                      {cond.type === "presence" ? (
                        <select
                          className="mt-select mt-select--grow"
                          value={String(cond.value || "removed")}
                          onChange={(e) =>
                            setGroups(patchGroupCond(groups, gi, ci, { value: e.target.value }))
                          }
                        >
                          {PRESENCE_TOKENS.map((tok) => (
                            <option key={tok.id} value={tok.id}>
                              {t(`bizMonitorTpl.${tok.labelKey}`)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <>
                          <select
                            className="mt-select"
                            value={cond.field || ""}
                            onChange={(e) =>
                              setGroups(patchGroupCond(groups, gi, ci, { field: e.target.value }))
                            }
                          >
                            <option value="">{t("bizMonitorTpl.pickField")}</option>
                            {fieldChoices.map((f) => (
                              <option key={f.name} value={f.name}>
                                {f.name}
                              </option>
                            ))}
                          </select>
                          <select
                            className="mt-select"
                            value={cond.op || "eq"}
                            onChange={(e) =>
                              setGroups(patchGroupCond(groups, gi, ci, { op: e.target.value }))
                            }
                          >
                            {VALUE_OPS.map((op) => (
                              <option key={op} value={op}>
                                {t(`bizMonitorTpl.op_${op}`)}
                              </option>
                            ))}
                          </select>
                          <Input
                            className="mt-cond-value"
                            value={
                              Array.isArray(cond.value)
                                ? cond.value.join(",")
                                : String(cond.value ?? "")
                            }
                            disabled={!OPS_NEED_VALUE.has(cond.op || "eq")}
                            placeholder={
                              cond.op === "in" || cond.op === "not_in"
                                ? "a,b,c"
                                : t("bizMonitorTpl.condValue")
                            }
                            onChange={(e) => {
                              const raw = e.target.value;
                              const val =
                                cond.op === "in" || cond.op === "not_in" ? csvValues(raw) : raw;
                              setGroups(patchGroupCond(groups, gi, ci, { value: val }));
                            }}
                          />
                        </>
                      )}
                      <button
                        type="button"
                        className="mt-icon-btn"
                        aria-label={t("bizMonitorTpl.removeRow")}
                        onClick={() => {
                          const next = groups
                            .map((g, i) => (i === gi ? g.filter((_, j) => j !== ci) : g))
                            .filter((g) => g.length > 0);
                          setGroups(next.length ? next : allowEmpty ? [] : [emptyGroup()]);
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
                  onClick={() =>
                    setGroups(
                      groups.map((g, i) =>
                        i === gi ? [...g, emptyCond("value", fieldChoices[0]?.name || "")] : g,
                      ),
                    )
                  }
                >
                  {t("bizMonitorTpl.addAndShort")}
                </button>
              </div>
            </div>
          ))}
          <button
            type="button"
            className="mt-link-btn"
            onClick={() => setGroups([...groups, emptyGroup("presence")])}
          >
            {t("bizMonitorTpl.addOrShort")}
          </button>
        </>
      )}
    </div>
  );
}

function DualPatternList({
  kind,
  patterns,
  fieldChoices,
  t,
  onChange,
}: {
  kind: "success" | "anomaly";
  patterns: DualPat[];
  fieldChoices: MetricField[];
  t: (k: string, vars?: Record<string, string | number>) => string;
  onChange: (next: DualPat[]) => void;
}) {
  const allowEmpty = kind === "anomaly";
  const cardClass = kind === "anomaly" ? "mt-rule-card mt-rule-card--anomaly" : "mt-rule-card";
  const rowKey = kind === "anomaly" ? "anomalyRow" : "successRow";
  const addKey = kind === "anomaly" ? "addAnomalyRow" : "addSuccessRow";
  const hintKey = kind === "anomaly" ? "anomalyPatternsHintShort" : "successPatternsHintShort";

  const setPat = (pi: number, nextPat: DualPat) => {
    const list = [...patterns];
    list[pi] = normalizePat(nextPat, { allowEmptySide: allowEmpty });
    onChange(list);
  };

  const updateSideGroups = (pi: number, side: "old" | "new", groups: CondGroup[]) => {
    const pat = normalizePat(patterns[pi] || {}, { allowEmptySide: allowEmpty });
    const nextGroups = groups.length > 0 ? groups : allowEmpty ? [] : [emptyGroup()];
    if (side === "old") setPat(pi, { ...pat, old_groups: nextGroups });
    else setPat(pi, { ...pat, new_groups: nextGroups });
  };

  const defaultNewPat = (): DualPat =>
    kind === "anomaly" ? anomalyBothGone() : successFromPresence(["removed"], ["added"]);

  return (
    <div className={`mt-block mt-block--${kind}`}>
      <p className="muted mt-block__hint">{t(`bizMonitorTpl.${hintKey}`)}</p>
      {patterns.map((rawPat, pi) => {
        const pat = normalizePat(rawPat, { allowEmptySide: allowEmpty });
        return (
          <div key={pi} className={cardClass}>
            <div className="mt-rule-card__head">
              <span>{t(`bizMonitorTpl.${rowKey}`, { n: String(pi + 1) })}</span>
              <Button
                size="sm"
                variant="ghost"
                onPress={() => {
                  const next = patterns.filter((_, i) => i !== pi);
                  onChange(next.length ? next : kind === "success" ? [defaultNewPat()] : []);
                }}
              >
                {t("bizMonitorTpl.removeRow")}
              </Button>
            </div>
            <div className="mt-rule-card__grid">
              <div className="mt-side-col">
                <div className="mt-side-col__label">{t("bizMonitorTpl.oldSideAny")}</div>
                <SideGroupsEditor
                  allowEmpty={allowEmpty}
                  groups={pat.old_groups || []}
                  fieldChoices={fieldChoices}
                  t={t}
                  onChange={(g) => updateSideGroups(pi, "old", g)}
                />
              </div>
              <div className="mt-rule-arrow" aria-hidden>
                →
              </div>
              <div className="mt-side-col">
                <div className="mt-side-col__label">{t("bizMonitorTpl.newSideAny")}</div>
                <SideGroupsEditor
                  allowEmpty={allowEmpty}
                  groups={pat.new_groups || []}
                  fieldChoices={fieldChoices}
                  t={t}
                  onChange={(g) => updateSideGroups(pi, "new", g)}
                />
              </div>
            </div>
          </div>
        );
      })}
      <Button
        size="sm"
        variant="secondary"
        onPress={() =>
          onChange([
            ...patterns.map((p) => normalizePat(p, { allowEmptySide: allowEmpty })),
            defaultNewPat(),
          ])
        }
      >
        {t(`bizMonitorTpl.${addKey}`)}
      </Button>
    </div>
  );
}

export function BizMonitorTemplatesPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();

  const [items, setItems] = useState<MonitorTpl[]>([]);
  const [compareTpls, setCompareTpls] = useState<CompareTpl[]>([]);
  const [metricSchemas, setMetricSchemas] = useState<MetricSchema[]>([]);
  const [busy, setBusy] = useState(false);
  const [listKw, setListKw] = useState("");
  const debouncedKw = useDebouncedValue(listKw, 250);

  const [editOpen, setEditOpen] = useState(false);
  const [editId, setEditId] = useState("");
  const [name, setName] = useState("");
  const [compareId, setCompareId] = useState("");
  const [note, setNote] = useState("");
  const [outOfExpect, setOutOfExpect] = useState("strict");
  const [collectIds, setCollectIds] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<SheetOverride[]>([]);
  const [activeSheetIdx, setActiveSheetIdx] = useState(0);
  const [ruleTab, setRuleTab] = useState<"success" | "anomaly">("success");
  const [showMore, setShowMore] = useState(false);
  const [showAdvancedJson, setShowAdvancedJson] = useState(false);
  const [overridesText, setOverridesText] = useState("[]");

  const refresh = useCallback(async () => {
    const [mon, cmp, metrics] = await Promise.all([
      bizMonitorListTemplates(),
      bizCompareListTemplates(),
      bizCompareListMetrics(),
    ]);
    setItems((mon.items || []) as MonitorTpl[]);
    setCompareTpls(
      ((cmp.items || []) as Record<string, unknown>[]).map((x) => ({
        id: String(x.id || ""),
        name: String(x.name || x.id || ""),
        metrics: Array.isArray(x.metrics) ? (x.metrics as CompareSheet[]) : [],
      })),
    );
    setMetricSchemas(
      ((metrics.items || []) as MetricSchema[]).map((m) => ({
        metric_id: m.metric_id,
        fields: (m.fields || []).map((f) => ({
          name: f.name,
          display_name: f.display_name,
        })),
      })),
    );
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await refresh();
      } catch (e) {
        showError(formatErr(e));
      }
    })();
  }, [refresh, showError]);

  const filtered = useMemo(() => {
    const kw = debouncedKw.trim().toLowerCase();
    if (!kw) return items;
    return items.filter((row) => {
      const blob = `${row.name} ${row.compare_template_name || ""} ${row.note || ""} ${row.compare_template_id}`.toLowerCase();
      return blob.includes(kw);
    });
  }, [items, debouncedKw]);

  const selectedCompare = useMemo(
    () => compareTpls.find((c) => c.id === compareId) || null,
    [compareTpls, compareId],
  );
  const sheets = useMemo(() => selectedCompare?.metrics || [], [selectedCompare]);
  const activeSheet = sheets[activeSheetIdx] || sheets[0] || null;
  const activeMetricId = activeSheet?.metric_id || "";
  const activeOverride = useMemo(
    () => (activeMetricId ? overrideFor(overrides, activeMetricId) : emptyOverride("")),
    [overrides, activeMetricId],
  );
  const activeFields = useMemo(() => {
    const schema = metricSchemas.find((m) => m.metric_id === activeMetricId);
    return schema?.fields || [];
  }, [metricSchemas, activeMetricId]);

  const sheetKey = useMemo(() => sheets.map((s) => s.metric_id).join("|"), [sheets]);

  useEffect(() => {
    if (!sheets.length) {
      setActiveSheetIdx(0);
      return;
    }
    if (activeSheetIdx >= sheets.length) setActiveSheetIdx(0);
  }, [sheets, activeSheetIdx]);

  useEffect(() => {
    if (!sheetKey) return;
    setOverrides((prev) => {
      const byId = new Map(prev.map((o) => [o.metric_id, o]));
      return sheets.map((s) => byId.get(s.metric_id) || emptyOverride(s.metric_id));
    });
  }, [compareId, sheetKey]);

  const syncOverride = (metricId: string, patch: Partial<SheetOverride>) => {
    setOverrides((prev) => {
      const others = prev.filter((o) => o.metric_id !== metricId);
      const base = overrideFor(prev, metricId);
      return [...others, { ...base, ...patch, metric_id: metricId }];
    });
  };

  const openCreate = () => {
    setEditId("");
    setName("");
    setCompareId(compareTpls[0]?.id || "");
    setNote("");
    setOutOfExpect("strict");
    setCollectIds([]);
    setOverrides([]);
    setActiveSheetIdx(0);
    setRuleTab("success");
    setShowMore(false);
    setShowAdvancedJson(false);
    setOverridesText("[]");
    setEditOpen(true);
  };

  const openEdit = (row: MonitorTpl) => {
    setEditId(row.id);
    setName(row.name || "");
    setCompareId(row.compare_template_id || "");
    setNote(row.note || "");
    setOutOfExpect(String((row.defaults || {}).out_of_expect || "strict"));
    setCollectIds(Array.isArray(row.collect_metric_ids) ? [...row.collect_metric_ids] : []);
    setOverrides(Array.isArray(row.sheet_overrides) ? (row.sheet_overrides as SheetOverride[]) : []);
    setActiveSheetIdx(0);
    setRuleTab("success");
    setShowMore(false);
    setShowAdvancedJson(false);
    setOverridesText(JSON.stringify(row.sheet_overrides || [], null, 2));
    setEditOpen(true);
  };

  const closeEdit = () => setEditOpen(false);

  const applyPreset = (kind: "auto" | "skip") => {
    if (!activeMetricId) return;
    syncOverride(
      activeMetricId,
      kind === "skip" ? skipPreset(activeMetricId) : presetForMetric(activeMetricId),
    );
  };

  const applyAllPresets = () => {
    setOverrides(sheets.map((s) => presetForMetric(s.metric_id)));
  };

  const save = async () => {
    if (!name.trim()) {
      showError(t("bizMonitorTpl.needName"));
      return;
    }
    let sheet_overrides = overrides
      .filter((o) => o.metric_id)
      .map((o) => ({
        ...o,
        success: (o.success || []).map((p) => normalizePat(p)),
        anomaly: (o.anomaly || []).map((p) => normalizePat(p, { allowEmptySide: true })),
      }));
    if (showAdvancedJson) {
      try {
        const parsed = JSON.parse(overridesText || "[]") as unknown[];
        if (!Array.isArray(parsed)) throw new Error("overrides");
        sheet_overrides = (parsed as SheetOverride[]).map((o) => ({
          ...o,
          success: (o.success || []).map((p) => normalizePat(p)),
          anomaly: (o.anomaly || []).map((p) => normalizePat(p, { allowEmptySide: true })),
        }));
      } catch {
        showError(t("bizMonitorTpl.overridesInvalid"));
        return;
      }
    }
    const body = {
      name: name.trim(),
      compare_template_id: compareId,
      collect_metric_ids: collectIds,
      defaults: { dual_mode: "migrate_pair", out_of_expect: outOfExpect || "strict" },
      sheet_overrides,
      note: note.trim(),
    };
    setBusy(true);
    try {
      if (editId) {
        await bizMonitorUpdateTemplate(editId, body);
        showOk(t("bizMonitorTpl.saved"));
      } else {
        await bizMonitorCreateTemplate(body);
        showOk(t("bizMonitorTpl.created"));
      }
      closeEdit();
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm(t("bizMonitorTpl.confirmDelete"))) return;
    setBusy(true);
    try {
      await bizMonitorDeleteTemplate(id);
      showOk(t("bizMonitorTpl.deleted"));
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const fieldChoices: MetricField[] =
    activeFields.length > 0
      ? activeFields
      : [...(activeSheet?.compare_fields || []), ...(activeSheet?.key_fields || [])].map((n) => ({
          name: n,
        }));

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("bizMonitorTpl.title")}</h2>
        <div className="btn-row">
          <Button size="sm" variant="primary" onPress={openCreate}>
            {t("bizMonitorTpl.create")}
          </Button>
        </div>
      </div>
      <p className="panel__hint muted">{t("bizMonitorTpl.hint")}</p>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        <Link to="/network/cutover/compare-templates">{t("bizMonitorTpl.openCompare")}</Link>
      </p>

      <div className="pt-list">
        <div className="pt-list-kpis">
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("bizMonitorTpl.kpiTotal")}</div>
            <div className="pt-list-kpi__value">{items.length}</div>
          </div>
        </div>
        <div className="filter-inline">
          <Input
            value={listKw}
            placeholder={t("bizMonitorTpl.listFilterPh")}
            onChange={(e) => setListKw(e.target.value)}
          />
        </div>
        <div className="pt-list-table-wrap">
          <table className="data-table pt-list-table">
            <thead>
              <tr>
                <th>{t("bizMonitorTpl.colName")}</th>
                <th>{t("bizMonitorTpl.colCompare")}</th>
                <th>{t("bizMonitorTpl.colCollect")}</th>
                <th>{t("bizMonitorTpl.colNote")}</th>
                <th>{t("bizMonitorTpl.colActions")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={row.id}>
                  <td>
                    <div className="pt-list-task-name">{row.name}</div>
                  </td>
                  <td>{row.compare_template_name || row.compare_template_id || "—"}</td>
                  <td>
                    {(row.collect_metric_ids || []).length
                      ? (row.collect_metric_ids || []).join(", ")
                      : t("bizMonitorTpl.collectAll")}
                  </td>
                  <td className="muted">{row.note || "—"}</td>
                  <td>
                    <div className="pt-list-actions">
                      <Button size="sm" variant="primary" onPress={() => openEdit(row)}>
                        {t("bizMonitorTpl.edit")}
                      </Button>
                      <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => void remove(row.id)}>
                        {t("bizMonitorTpl.delete")}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!filtered.length ? (
                <tr>
                  <td colSpan={5}>
                    <div className="pt-list-empty">{t("bizMonitorTpl.empty")}</div>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      <AppModalShell open={editOpen} onClose={closeEdit} size="cover">
        <Modal.Header>
          <Modal.Heading>
            {editId ? t("bizMonitorTpl.edit") : t("bizMonitorTpl.create")}
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3 mt-editor">
          {/* Basics */}
          <div className="mt-editor__basics">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("bizMonitorTpl.namePh")}
              aria-label={t("bizMonitorTpl.colName")}
            />
            <FieldSelect
              label={t("bizMonitorTpl.colCompare")}
              value={compareId}
              onChange={(e) => {
                setCompareId(e.target.value);
                setActiveSheetIdx(0);
              }}
              fullWidth
            >
              <option value="">{t("bizMonitorTpl.pickCompare")}</option>
              {compareTpls.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {(c.metrics || []).length ? ` · ${(c.metrics || []).length}${t("bizMonitorTpl.sheetsUnit")}` : ""}
                </option>
              ))}
            </FieldSelect>
            <FieldSelect
              label={t("bizMonitorTpl.outOfExpect")}
              value={outOfExpect}
              onChange={(e) => setOutOfExpect(e.target.value)}
              fullWidth
            >
              <option value="strict">{t("bizMonitorTpl.ooeStrict")}</option>
              <option value="warn">{t("bizMonitorTpl.ooeWarn")}</option>
              <option value="ignore">{t("bizMonitorTpl.ooeIgnore")}</option>
            </FieldSelect>
          </div>

          {!sheets.length ? (
            <p className="muted" style={{ margin: 0 }}>
              {t("bizMonitorTpl.needCompareFirst")}
            </p>
          ) : (
            <div className="mt-editor__main">
              {/* Left sheet nav */}
              <aside className="mt-editor__nav" aria-label={t("bizMonitorTpl.sheetRules")}>
                <div className="mt-editor__nav-head">
                  <span>{t("bizMonitorTpl.sheetRules")}</span>
                  <Button size="sm" variant="ghost" onPress={applyAllPresets}>
                    {t("bizMonitorTpl.applyAllPresetsShort")}
                  </Button>
                </div>
                <div className="mt-editor__nav-list" role="tablist">
                  {sheets.map((s, i) => {
                    const ov = overrideFor(overrides, s.metric_id);
                    return (
                      <button
                        key={s.metric_id}
                        type="button"
                        role="tab"
                        aria-selected={activeSheetIdx === i}
                        className={`mt-editor__nav-item${activeSheetIdx === i ? " is-active" : ""}`}
                        onClick={() => {
                          setActiveSheetIdx(i);
                          setRuleTab("success");
                        }}
                      >
                        <span className="mt-editor__nav-name">{s.metric_id}</span>
                        <span className="mt-editor__nav-tag">
                          {ov.skip_dual
                            ? t("bizMonitorTpl.tagSkip")
                            : (ov.success || []).length || (ov.anomaly || []).length
                              ? t("bizMonitorTpl.tagRules", {
                                  n: String(
                                    (ov.success || []).length + (ov.anomaly || []).length,
                                  ),
                                })
                              : t("bizMonitorTpl.tagEmpty")}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </aside>

              {/* Right rule editor */}
              {activeSheet ? (
                <div className="mt-editor__pane">
                  <div className="mt-editor__pane-head">
                    <div>
                      <div className="mt-editor__metric">{activeMetricId}</div>
                      <div className="mt-editor__meta muted">
                        <span>Key {(activeSheet.key_fields || []).join(" · ") || "—"}</span>
                        <span>
                          {t("bizMonitorTpl.compare")}{" "}
                          {(activeSheet.compare_fields || []).length
                            ? (activeSheet.compare_fields || []).join(" · ")
                            : t("bizMonitorTpl.presenceOnly")}
                        </span>
                        <Link to="/network/cutover/compare-templates">{t("bizMonitorTpl.editInCompare")}</Link>
                      </div>
                    </div>
                    <div className="btn-row" style={{ gap: 6 }}>
                      <Button size="sm" variant="secondary" onPress={() => applyPreset("auto")}>
                        {t("bizMonitorTpl.presetAuto")}
                      </Button>
                      <Button size="sm" variant="ghost" onPress={() => applyPreset("skip")}>
                        {t("bizMonitorTpl.presetSkip")}
                      </Button>
                    </div>
                  </div>

                  <label className="mt-skip-check">
                    <input
                      type="checkbox"
                      checked={Boolean(activeOverride.skip_dual)}
                      onChange={(e) => syncOverride(activeMetricId, { skip_dual: e.target.checked })}
                    />
                    <span>{t("bizMonitorTpl.skipDual")}</span>
                  </label>

                  {!activeOverride.skip_dual ? (
                    <>
                      <div className="mt-rule-tabs" role="tablist">
                        <button
                          type="button"
                          role="tab"
                          aria-selected={ruleTab === "success"}
                          className={`mt-rule-tab${ruleTab === "success" ? " is-active" : ""}`}
                          onClick={() => setRuleTab("success")}
                        >
                          {t("bizMonitorTpl.successPatternsShort")}
                          <span className="mt-rule-tab__n">{(activeOverride.success || []).length}</span>
                        </button>
                        <button
                          type="button"
                          role="tab"
                          aria-selected={ruleTab === "anomaly"}
                          className={`mt-rule-tab mt-rule-tab--anomaly${ruleTab === "anomaly" ? " is-active" : ""}`}
                          onClick={() => setRuleTab("anomaly")}
                        >
                          {t("bizMonitorTpl.anomalyPatternsShort")}
                          <span className="mt-rule-tab__n">{(activeOverride.anomaly || []).length}</span>
                        </button>
                      </div>
                      {ruleTab === "success" ? (
                        <DualPatternList
                          kind="success"
                          patterns={activeOverride.success || []}
                          fieldChoices={fieldChoices}
                          t={t}
                          onChange={(success) => syncOverride(activeMetricId, { success })}
                        />
                      ) : (
                        <DualPatternList
                          kind="anomaly"
                          patterns={activeOverride.anomaly || []}
                          fieldChoices={fieldChoices}
                          t={t}
                          onChange={(anomaly) => syncOverride(activeMetricId, { anomaly })}
                        />
                      )}
                    </>
                  ) : (
                    <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                      {t("bizMonitorTpl.skipDualHint")}
                    </p>
                  )}
                </div>
              ) : null}
            </div>
          )}

          <div className="mt-editor__more">
            <Button size="sm" variant="ghost" onPress={() => setShowMore((v) => !v)}>
              {showMore ? t("bizMonitorTpl.hideMore") : t("bizMonitorTpl.showMore")}
            </Button>
            {showMore ? (
              <div className="flex flex-col gap-3" style={{ marginTop: 8 }}>
                <Input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={t("bizMonitorTpl.colNote")}
                  aria-label={t("bizMonitorTpl.colNote")}
                />
                <div>
                  <div className="mt-block__title">{t("bizMonitorTpl.collectTitle")}</div>
                  <p className="muted" style={{ margin: "0 0 6px", fontSize: 12 }}>
                    {t("bizMonitorTpl.collectHint")}
                  </p>
                  <div className="mt-chip-row">
                    {sheets.map((s) => {
                      const checked = collectIds.length === 0 || collectIds.includes(s.metric_id);
                      const explicit = collectIds.length > 0;
                      return (
                        <Chip
                          key={s.metric_id}
                          active={checked}
                          label={s.metric_id}
                          onClick={() => {
                            if (!explicit) {
                              setCollectIds(sheets.map((x) => x.metric_id).filter((id) => id !== s.metric_id));
                              return;
                            }
                            setCollectIds((prev) => {
                              if (prev.includes(s.metric_id)) {
                                const next = prev.filter((x) => x !== s.metric_id);
                                return next.length === sheets.length ? [] : next;
                              }
                              const next = [...prev, s.metric_id];
                              return next.length === sheets.length ? [] : next;
                            });
                          }}
                        />
                      );
                    })}
                    {collectIds.length ? (
                      <Button size="sm" variant="ghost" onPress={() => setCollectIds([])}>
                        {t("bizMonitorTpl.collectAll")}
                      </Button>
                    ) : null}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onPress={() => {
                    if (!showAdvancedJson) setOverridesText(JSON.stringify(overrides, null, 2));
                    else {
                      try {
                        const parsed = JSON.parse(overridesText || "[]") as SheetOverride[];
                        if (Array.isArray(parsed)) setOverrides(parsed);
                      } catch {
                        /* keep */
                      }
                    }
                    setShowAdvancedJson((v) => !v);
                  }}
                >
                  {showAdvancedJson ? t("bizMonitorTpl.hideJson") : t("bizMonitorTpl.showJson")}
                </Button>
                {showAdvancedJson ? (
                  <textarea
                    className="form-textarea"
                    style={{ minHeight: 140, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
                    value={overridesText}
                    onChange={(e) => setOverridesText(e.target.value)}
                    aria-label={t("bizMonitorTpl.overrides")}
                  />
                ) : null}
              </div>
            ) : null}
          </div>
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="secondary" onPress={closeEdit}>
            {t("bizMonitorTpl.cancel")}
          </Button>
          <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void save()}>
            {t("bizMonitorTpl.save")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
