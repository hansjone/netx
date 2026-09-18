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
type SuccessPat = { old: string[]; new: string[] };
type SheetOverride = {
  metric_id: string;
  status_fields?: string[];
  down_values?: string[];
  up_values?: string[];
  success?: SuccessPat[];
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

const KIND_TOKENS = ["removed", "added", "unchanged", "changed"] as const;
const STATUS_TOKENS = ["up", "down", "other"] as const;

function emptyOverride(metricId: string): SheetOverride {
  return {
    metric_id: metricId,
    status_fields: [],
    down_values: ["down"],
    up_values: ["up"],
    success: [{ old: ["removed"], new: ["added", "unchanged"] }],
    skip_dual: false,
  };
}

/** Built-in presets aligned with backend seeds. */
export function presetForMetric(metricId: string): SheetOverride {
  const mid = metricId;
  if (mid === "interface_brief") {
    return {
      metric_id: mid,
      status_fields: ["admin", "phy", "prot"],
      down_values: ["down"],
      up_values: ["up"],
      success: [{ old: ["removed", "down"], new: ["added", "up", "unchanged"] }],
      skip_dual: false,
    };
  }
  if (mid === "bgp_peer") {
    return {
      metric_id: mid,
      status_fields: ["state"],
      down_values: ["idle", "active", "connect", "down"],
      up_values: ["established"],
      success: [{ old: ["removed", "down"], new: ["added", "up", "unchanged"] }],
      skip_dual: false,
    };
  }
  if (mid === "arp" || mid === "nd6_cache" || mid === "lldp_neighbor") {
    return {
      metric_id: mid,
      status_fields: [],
      down_values: [],
      up_values: [],
      success: [{ old: ["removed"], new: ["added", "unchanged"] }],
      skip_dual: false,
    };
  }
  if (mid.includes("isis") || mid.includes("ospf") || mid.includes("adjacency")) {
    return {
      metric_id: mid,
      status_fields: ["state", "status"].filter(Boolean),
      down_values: ["down", "init", "idle"],
      up_values: ["up", "full", "2way"],
      success: [{ old: ["removed", "down"], new: ["added", "up", "unchanged"] }],
      skip_dual: false,
    };
  }
  if (mid.includes("route") || mid.includes("vrf")) {
    return {
      metric_id: mid,
      status_fields: [],
      down_values: [],
      up_values: [],
      success: [{ old: ["removed"], new: ["added", "unchanged"] }],
      skip_dual: false,
    };
  }
  return emptyOverride(mid);
}

function skipPreset(metricId: string): SheetOverride {
  return { ...emptyOverride(metricId), skip_dual: true, success: [] };
}

function overrideFor(
  overrides: SheetOverride[],
  metricId: string,
): SheetOverride {
  const found = overrides.find((o) => o.metric_id === metricId);
  return found ? { ...emptyOverride(metricId), ...found, metric_id: metricId } : emptyOverride(metricId);
}

function csvValues(raw: string): string[] {
  return raw
    .split(/[,;\s]+/)
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
}

function toggleInList(list: string[], value: string): string[] {
  if (list.includes(value)) return list.filter((x) => x !== value);
  return [...list, value];
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
    const first = compareTpls[0]?.id || "";
    setCompareId(first);
    setNote("");
    setOutOfExpect("strict");
    setCollectIds([]);
    setOverrides([]);
    setActiveSheetIdx(0);
    setShowAdvancedJson(false);
    setOverridesText("[]");
    setEditOpen(true);
  };

  const openEdit = (row: MonitorTpl) => {
    setEditId(row.id);
    setName(row.name || "");
    setCompareId(row.compare_template_id || "");
    setNote(row.note || "");
    const d = row.defaults || {};
    setOutOfExpect(String(d.out_of_expect || "strict"));
    setCollectIds(Array.isArray(row.collect_metric_ids) ? [...row.collect_metric_ids] : []);
    setOverrides(Array.isArray(row.sheet_overrides) ? (row.sheet_overrides as SheetOverride[]) : []);
    setActiveSheetIdx(0);
    setShowAdvancedJson(false);
    setOverridesText(JSON.stringify(row.sheet_overrides || [], null, 2));
    setEditOpen(true);
  };

  const closeEdit = () => setEditOpen(false);

  const applyPreset = (kind: "auto" | "skip") => {
    if (!activeMetricId) return;
    const next = kind === "skip" ? skipPreset(activeMetricId) : presetForMetric(activeMetricId);
    syncOverride(activeMetricId, next);
  };

  const applyAllPresets = () => {
    setOverrides(sheets.map((s) => presetForMetric(s.metric_id)));
  };

  const save = async () => {
    if (!name.trim()) {
      showError(t("bizMonitorTpl.needName"));
      return;
    }
    let sheet_overrides = overrides.filter((o) => o.metric_id);
    if (showAdvancedJson) {
      try {
        const parsed = JSON.parse(overridesText || "[]") as unknown[];
        if (!Array.isArray(parsed)) throw new Error("overrides");
        sheet_overrides = parsed as SheetOverride[];
      } catch {
        showError(t("bizMonitorTpl.overridesInvalid"));
        return;
      }
    }
    const defaults = {
      dual_mode: "migrate_pair",
      out_of_expect: outOfExpect || "strict",
    };
    const body = {
      name: name.trim(),
      compare_template_id: compareId,
      collect_metric_ids: collectIds,
      defaults,
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

  const tokenOptions = useMemo(() => {
    const fieldToks: string[] = [];
    for (const f of activeOverride.status_fields || []) {
      for (const v of activeOverride.down_values || []) {
        fieldToks.push(`field:${f}:${v}`);
      }
      for (const v of activeOverride.up_values || []) {
        fieldToks.push(`field:${f}:${v}`);
      }
    }
    return [...KIND_TOKENS, ...STATUS_TOKENS, ...fieldToks];
  }, [activeOverride]);

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
        <Modal.Body className="flex flex-col gap-4">
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            {t("bizMonitorTpl.formHint")}
          </p>

          <div className="flex flex-col gap-2">
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
              hint={t("bizMonitorTpl.compareHint")}
            >
              <option value="">{t("bizMonitorTpl.pickCompare")}</option>
              {compareTpls.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {(c.metrics || []).length ? ` (${(c.metrics || []).length})` : ""}
                </option>
              ))}
            </FieldSelect>
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("bizMonitorTpl.colNote")}
              aria-label={t("bizMonitorTpl.colNote")}
            />
          </div>

          <div className="flex flex-col gap-2">
            <strong style={{ fontSize: 13 }}>{t("bizMonitorTpl.globalPolicy")}</strong>
            <FieldSelect
              label={t("bizMonitorTpl.outOfExpect")}
              value={outOfExpect}
              onChange={(e) => setOutOfExpect(e.target.value)}
              fullWidth
              hint={t("bizMonitorTpl.outOfExpectHint")}
            >
              <option value="strict">{t("bizMonitorTpl.ooeStrict")}</option>
              <option value="warn">{t("bizMonitorTpl.ooeWarn")}</option>
              <option value="ignore">{t("bizMonitorTpl.ooeIgnore")}</option>
            </FieldSelect>
          </div>

          {sheets.length ? (
            <div className="flex flex-col gap-2">
              <strong style={{ fontSize: 13 }}>{t("bizMonitorTpl.collectTitle")}</strong>
              <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                {t("bizMonitorTpl.collectHint")}
              </p>
              <div className="btn-row" style={{ flexWrap: "wrap", gap: 8 }}>
                {sheets.map((s) => {
                  const checked = collectIds.length === 0 || collectIds.includes(s.metric_id);
                  const explicit = collectIds.length > 0;
                  return (
                    <label key={s.metric_id} className="config-sync-policy-check" style={{ margin: 0 }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          if (!explicit) {
                            setCollectIds(
                              sheets.map((x) => x.metric_id).filter((id) => id !== s.metric_id),
                            );
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
                      <span>{s.metric_id}</span>
                    </label>
                  );
                })}
                {collectIds.length ? (
                  <Button size="sm" variant="ghost" onPress={() => setCollectIds([])}>
                    {t("bizMonitorTpl.collectAll")}
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}

          {sheets.length ? (
            <div className="flex flex-col gap-3">
              <div className="btn-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
                <strong style={{ fontSize: 13 }}>{t("bizMonitorTpl.sheetRules")}</strong>
                <Button size="sm" variant="secondary" onPress={applyAllPresets}>
                  {t("bizMonitorTpl.applyAllPresets")}
                </Button>
              </div>
              <div className="bs-sheet-tabs btn-row" style={{ flexWrap: "wrap", gap: 6 }}>
                {sheets.map((s, i) => (
                  <Button
                    key={s.metric_id}
                    size="sm"
                    variant={activeSheetIdx === i ? "primary" : "secondary"}
                    onPress={() => setActiveSheetIdx(i)}
                  >
                    {s.metric_id}
                    {overrideFor(overrides, s.metric_id).skip_dual ? " · skip" : ""}
                  </Button>
                ))}
              </div>

              {activeSheet ? (
                <div className="flex flex-col gap-3" style={{ borderTop: "1px solid var(--border, #e5e7eb)", paddingTop: 12 }}>
                  <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                    {t("bizMonitorTpl.sheetHowReadonly")}: Key=
                    {(activeSheet.key_fields || []).join(",") || "—"} ·{" "}
                    {t("bizMonitorTpl.iface")}=
                    {(activeSheet.iface_fields || []).join(",") || "—"} ·{" "}
                    {t("bizMonitorTpl.compare")}=
                    {(activeSheet.compare_fields || []).length
                      ? (activeSheet.compare_fields || []).join(",")
                      : t("bizMonitorTpl.presenceOnly")}
                    {" · "}
                    <Link to="/network/cutover/compare-templates">{t("bizMonitorTpl.editInCompare")}</Link>
                  </p>

                  <div className="btn-row" style={{ flexWrap: "wrap", gap: 8 }}>
                    <Button size="sm" variant="secondary" onPress={() => applyPreset("auto")}>
                      {t("bizMonitorTpl.presetAuto")}
                    </Button>
                    <Button size="sm" variant="ghost" onPress={() => applyPreset("skip")}>
                      {t("bizMonitorTpl.presetSkip")}
                    </Button>
                    <label className="config-sync-policy-check" style={{ marginLeft: 8 }}>
                      <input
                        type="checkbox"
                        checked={Boolean(activeOverride.skip_dual)}
                        onChange={(e) =>
                          syncOverride(activeMetricId, { skip_dual: e.target.checked })
                        }
                      />
                      <span>{t("bizMonitorTpl.skipDual")}</span>
                    </label>
                  </div>

                  {!activeOverride.skip_dual ? (
                    <>
                      <div className="flex flex-col gap-2">
                        <strong style={{ fontSize: 13 }}>{t("bizMonitorTpl.statusFields")}</strong>
                        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                          {t("bizMonitorTpl.statusFieldsHint")}
                        </p>
                        <div className="btn-row" style={{ flexWrap: "wrap", gap: 8 }}>
                          {(activeFields.length
                            ? activeFields
                            : [
                                ...(activeSheet.compare_fields || []),
                                ...(activeSheet.key_fields || []),
                              ].map((n) => ({ name: n }))
                          ).map((f) => (
                            <label key={f.name} className="config-sync-policy-check" style={{ margin: 0 }}>
                              <input
                                type="checkbox"
                                checked={(activeOverride.status_fields || []).includes(f.name)}
                                onChange={() =>
                                  syncOverride(activeMetricId, {
                                    status_fields: toggleInList(
                                      activeOverride.status_fields || [],
                                      f.name,
                                    ),
                                  })
                                }
                              />
                              <span>{f.display_name || f.name}</span>
                            </label>
                          ))}
                          {!activeFields.length && !(activeSheet.compare_fields || []).length ? (
                            <span className="muted" style={{ fontSize: 12 }}>
                              {t("bizMonitorTpl.noFields")}
                            </span>
                          ) : null}
                        </div>
                        <div
                          style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 12,
                          }}
                        >
                          <div className="flex flex-col gap-1">
                            <span className="muted" style={{ fontSize: 12 }}>
                              {t("bizMonitorTpl.downValues")}
                            </span>
                            <Input
                              value={(activeOverride.down_values || []).join(", ")}
                              onChange={(e) =>
                                syncOverride(activeMetricId, {
                                  down_values: csvValues(e.target.value),
                                })
                              }
                              placeholder="down, idle"
                              aria-label={t("bizMonitorTpl.downValues")}
                            />
                          </div>
                          <div className="flex flex-col gap-1">
                            <span className="muted" style={{ fontSize: 12 }}>
                              {t("bizMonitorTpl.upValues")}
                            </span>
                            <Input
                              value={(activeOverride.up_values || []).join(", ")}
                              onChange={(e) =>
                                syncOverride(activeMetricId, {
                                  up_values: csvValues(e.target.value),
                                })
                              }
                              placeholder="up, established"
                              aria-label={t("bizMonitorTpl.upValues")}
                            />
                          </div>
                        </div>
                      </div>

                      <div className="flex flex-col gap-2">
                        <strong style={{ fontSize: 13 }}>{t("bizMonitorTpl.successPatterns")}</strong>
                        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                          {t("bizMonitorTpl.successPatternsHint")}
                        </p>
                        {(activeOverride.success || []).map((pat, pi) => (
                          <div
                            key={pi}
                            style={{
                              display: "grid",
                              gridTemplateColumns: "1fr 1fr auto",
                              gap: 12,
                              alignItems: "start",
                              padding: "8px 0",
                              borderBottom: "1px solid var(--border, #eee)",
                            }}
                          >
                            <div>
                              <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                                {t("bizMonitorTpl.oldSideAny")}
                              </div>
                              <div className="btn-row" style={{ flexWrap: "wrap", gap: 4 }}>
                                {tokenOptions.map((tok) => (
                                  <label
                                    key={`o-${pi}-${tok}`}
                                    className="config-sync-policy-check"
                                    style={{ margin: 0, fontSize: 12 }}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={(pat.old || []).includes(tok)}
                                      onChange={() => {
                                        const success = [...(activeOverride.success || [])];
                                        success[pi] = {
                                          ...pat,
                                          old: toggleInList(pat.old || [], tok),
                                        };
                                        syncOverride(activeMetricId, { success });
                                      }}
                                    />
                                    <span>{tok}</span>
                                  </label>
                                ))}
                              </div>
                            </div>
                            <div>
                              <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                                {t("bizMonitorTpl.newSideAny")}
                              </div>
                              <div className="btn-row" style={{ flexWrap: "wrap", gap: 4 }}>
                                {tokenOptions.map((tok) => (
                                  <label
                                    key={`n-${pi}-${tok}`}
                                    className="config-sync-policy-check"
                                    style={{ margin: 0, fontSize: 12 }}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={(pat.new || []).includes(tok)}
                                      onChange={() => {
                                        const success = [...(activeOverride.success || [])];
                                        success[pi] = {
                                          ...pat,
                                          new: toggleInList(pat.new || [], tok),
                                        };
                                        syncOverride(activeMetricId, { success });
                                      }}
                                    />
                                    <span>{tok}</span>
                                  </label>
                                ))}
                              </div>
                            </div>
                            <Button
                              size="sm"
                              variant="ghost"
                              onPress={() => {
                                const success = (activeOverride.success || []).filter((_, i) => i !== pi);
                                syncOverride(activeMetricId, {
                                  success: success.length
                                    ? success
                                    : [{ old: ["removed"], new: ["added"] }],
                                });
                              }}
                            >
                              ×
                            </Button>
                          </div>
                        ))}
                        <Button
                          size="sm"
                          variant="ghost"
                          onPress={() =>
                            syncOverride(activeMetricId, {
                              success: [
                                ...(activeOverride.success || []),
                                { old: ["removed"], new: ["added"] },
                              ],
                            })
                          }
                        >
                          {t("bizMonitorTpl.addSuccessRow")}
                        </Button>
                      </div>
                    </>
                  ) : (
                    <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                      {t("bizMonitorTpl.skipDualHint")}
                    </p>
                  )}
                </div>
              ) : null}
            </div>
          ) : (
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              {t("bizMonitorTpl.needCompareFirst")}
            </p>
          )}

          <div className="flex flex-col gap-2">
            <Button
              size="sm"
              variant="ghost"
              onPress={() => {
                if (!showAdvancedJson) {
                  setOverridesText(JSON.stringify(overrides, null, 2));
                } else {
                  try {
                    const parsed = JSON.parse(overridesText || "[]") as SheetOverride[];
                    if (Array.isArray(parsed)) setOverrides(parsed);
                  } catch {
                    /* keep visual */
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
                style={{ minHeight: 160, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
                value={overridesText}
                onChange={(e) => setOverridesText(e.target.value)}
                aria-label={t("bizMonitorTpl.overrides")}
              />
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
