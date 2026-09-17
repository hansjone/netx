import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizCompareCreateJob,
  bizCompareCreateMapping,
  bizCompareCreateTemplate,
  bizCompareDeleteTemplate,
  bizCompareGetRun,
  bizCompareListJobs,
  bizCompareListMappings,
  bizCompareListMetrics,
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
  changes: Record<string, { before?: unknown; after?: unknown }>;
};

type RunSheet = {
  metric_id: string;
  key_fields: string[];
  iface_fields: string[];
  compare_fields: string[];
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
      },
    ];
  }
  return [];
}

function defaultSheetForMetric(schema: MetricSchema | undefined, metricId: string): MetricSheet {
  const fields = schema?.fields || [];
  return {
    metric_id: metricId,
    key_fields: fields.filter((f) => f.is_key).map((f) => f.name),
    iface_fields: fields.filter((f) => f.is_interface).map((f) => f.name),
    // Default: compare non-key state/meta values; empty = presence-only
    compare_fields: fields
      .filter((f) => !f.is_key && (f.role === "state" || f.role === "meta"))
      .map((f) => f.name),
  };
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
  const [tplAddMetric, setTplAddMetric] = useState("");

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

  // result filters
  const [kindFilter, setKindFilter] = useState<KindFilter>("diff");
  const [resultKw, setResultKw] = useState("");
  const debouncedResultKw = useDebouncedValue(resultKw, 200);

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

  const availableToAdd = useMemo(() => {
    const used = new Set(tplSheets.map((s) => s.metric_id));
    return metrics.filter((m) => !used.has(m.metric_id));
  }, [metrics, tplSheets]);

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

  const resultColumns = useMemo(() => {
    const keys =
      activeRunSheet?.key_fields?.length
        ? activeRunSheet.key_fields
        : Object.keys((activeRunSheet?.diffs?.[0]?.key as any) || {});
    const compare = (activeRunSheet?.compare_fields || []).filter((f) => !keys.includes(f));
    return { keys, compare, presence: !(activeRunSheet?.compare_fields || []).length };
  }, [activeRunSheet]);

  const filteredDiffs = useMemo(() => {
    const diffs = (activeRunSheet?.diffs || []) as DiffRow[];
    const kw = debouncedResultKw.trim().toLowerCase();
    return diffs.filter((d) => {
      if (kindFilter === "diff") {
        if (d.kind === "unchanged") return false;
      } else if (kindFilter !== "all" && d.kind !== kindFilter) {
        return false;
      }
      if (!kw) return true;
      const blob = [
        d.kind,
        ...Object.values(d.key || {}),
        ...Object.values(d.before || {}),
        ...Object.values(d.after || {}),
        JSON.stringify(d.changes || {}),
      ]
        .map(cellText)
        .join(" ")
        .toLowerCase();
      return blob.includes(kw);
    });
  }, [activeRunSheet, kindFilter, debouncedResultKw]);

  const kindLabel = (kind: string) => {
    if (kind === "added") return t("bizCompare.kindAddedShort");
    if (kind === "removed") return t("bizCompare.kindRemovedShort");
    if (kind === "changed") return t("bizCompare.kindChangedShort");
    if (kind === "unchanged") return t("bizCompare.kindUnchangedShort");
    return kind;
  };

  const summary = runDetail?.summary || {};

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
        return next;
      }),
    );
  };

  const openNewTemplate = () => {
    const first = metrics[0];
    const sheet = first
      ? defaultSheetForMetric(first, first.metric_id)
      : { metric_id: "lldp_neighbor", key_fields: [], iface_fields: [], compare_fields: [] };
    setTplEditId("");
    setTplName("");
    setTplNote("");
    setTplSheets([sheet]);
    setTplSheetIdx(0);
    setTplAddMetric("");
    setTplOpen(true);
  };

  const openEditTemplate = (tpl: Template) => {
    const sheets = templateSheets(tpl);
    setTplEditId(tpl.id);
    setTplName(tpl.name);
    setTplNote(tpl.note || "");
    setTplSheets(
      sheets.length
        ? sheets.map((s) => ({
            metric_id: s.metric_id,
            key_fields: [...(s.key_fields || [])],
            iface_fields: [...(s.iface_fields || [])],
            compare_fields: [...(s.compare_fields || [])],
          }))
        : [],
    );
    setTplSheetIdx(0);
    setTplAddMetric("");
    setTplOpen(true);
  };

  const addTplMetric = () => {
    const mid = tplAddMetric || availableToAdd[0]?.metric_id;
    if (!mid) return;
    const schema = metrics.find((m) => m.metric_id === mid);
    setTplSheets((prev) => [...prev, defaultSheetForMetric(schema, mid)]);
    setTplSheetIdx(tplSheets.length);
    setTplAddMetric("");
  };

  const removeTplMetric = (idx: number) => {
    setTplSheets((prev) => {
      if (prev.length <= 1) return prev;
      const next = prev.filter((_, i) => i !== idx);
      return next;
    });
    setTplSheetIdx((i) => Math.max(0, Math.min(i, tplSheets.length - 2)));
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
            <Button size="sm" variant="primary" onPress={openNewTemplate}>
              {t("bizCompare.newTemplate")}
            </Button>
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

          <p className="muted">{t("bizCompare.templateHint")}</p>

          <div className="filter-inline">
            <FieldSelect
              label={t("bizCompare.addMetric")}
              value={tplAddMetric}
              onChange={(e) => setTplAddMetric(e.target.value)}
            >
              <option value="">{t("bizCompare.pickMetric")}</option>
              {availableToAdd.map((m) => (
                <option key={m.metric_id} value={m.metric_id}>
                  {m.metric_id}
                </option>
              ))}
            </FieldSelect>
            <Button
              size="sm"
              variant="secondary"
              isDisabled={!availableToAdd.length || (!tplAddMetric && !availableToAdd[0])}
              onPress={addTplMetric}
            >
              {t("bizCompare.addSheet")}
            </Button>
          </div>

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
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={tplSheets.length <= 1}
                  onPress={() => removeTplMetric(tplSheetIdx)}
                >
                  {t("bizCompare.removeSheet")}
                </Button>
              </div>

              <div className="pt-list-table-wrap">
                <table className="data-table pt-list-table">
                  <thead>
                    <tr>
                      <th>{t("bizCompare.field")}</th>
                      <th>{t("bizCompare.keyFields")}</th>
                      <th>{t("bizCompare.ifaceFields")}</th>
                      <th>{t("bizCompare.compareFields")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeTplFields.map((f) => {
                      const isKey = activeTplSheet.key_fields.includes(f.name);
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
                              checked={!isKey && activeTplSheet.compare_fields.includes(f.name)}
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
                        </tr>
                      );
                    })}
                    {!activeTplFields.length ? (
                      <tr>
                        <td colSpan={4}>
                          <div className="pt-list-empty">{t("bizCompare.noMetricFields")}</div>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="pt-list-empty">{t("bizCompare.metricsRequired")}</div>
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
      <AppModalShell open={Boolean(jobId)} onClose={closeJob} size="lg" className="app-heroui-modal--xl">
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
            <div className="bs-workbook-body bs-cmp-result flex flex-col gap-3" style={{ flex: 1, minHeight: 0 }}>
              <div className="bs-cmp-result__toolbar">
                <FieldSelect
                  label={t("bizCompare.pickBatchRun")}
                  value={runDetail?.id || ""}
                  onChange={(e) => {
                    const id = e.target.value;
                    if (id) void loadRun(id);
                  }}
                  fullWidth
                >
                  <option value="">{t("bizCompare.pickRun")}</option>
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {fmtTime(r.created_at)} · +{r.summary?.added ?? 0} / −{r.summary?.removed ?? 0} / ~
                      {r.summary?.changed ?? 0}
                    </option>
                  ))}
                </FieldSelect>
              </div>

              {runDetail ? (
                <>
                  <div className="bs-cmp-kpis">
                    <button
                      type="button"
                      className={`bs-cmp-kpi bs-cmp-kpi--added${kindFilter === "added" ? " is-active" : ""}`}
                      onClick={() => setKindFilter("added")}
                    >
                      <span className="bs-cmp-kpi__label">{t("bizCompare.kindAddedShort")}</span>
                      <span className="bs-cmp-kpi__value">{summary.added ?? 0}</span>
                    </button>
                    <button
                      type="button"
                      className={`bs-cmp-kpi bs-cmp-kpi--removed${kindFilter === "removed" ? " is-active" : ""}`}
                      onClick={() => setKindFilter("removed")}
                    >
                      <span className="bs-cmp-kpi__label">{t("bizCompare.kindRemovedShort")}</span>
                      <span className="bs-cmp-kpi__value">{summary.removed ?? 0}</span>
                    </button>
                    <button
                      type="button"
                      className={`bs-cmp-kpi bs-cmp-kpi--changed${kindFilter === "changed" ? " is-active" : ""}`}
                      onClick={() => setKindFilter("changed")}
                    >
                      <span className="bs-cmp-kpi__label">{t("bizCompare.kindChangedShort")}</span>
                      <span className="bs-cmp-kpi__value">{summary.changed ?? 0}</span>
                    </button>
                    <button
                      type="button"
                      className={`bs-cmp-kpi bs-cmp-kpi--unchanged${kindFilter === "unchanged" ? " is-active" : ""}`}
                      onClick={() => setKindFilter("unchanged")}
                    >
                      <span className="bs-cmp-kpi__label">{t("bizCompare.kindUnchangedShort")}</span>
                      <span className="bs-cmp-kpi__value">{summary.unchanged ?? 0}</span>
                    </button>
                  </div>

                  <div className="bs-cmp-filter-bar">
                    <div className="bs-cmp-kind-pills" role="tablist">
                      {(
                        [
                          ["diff", t("bizCompare.kindDiff")],
                          ["all", t("bizCompare.kindAll")],
                          ["added", t("bizCompare.kindAddedShort")],
                          ["removed", t("bizCompare.kindRemovedShort")],
                          ["changed", t("bizCompare.kindChangedShort")],
                          ["unchanged", t("bizCompare.kindUnchangedShort")],
                        ] as const
                      ).map(([id, label]) => (
                        <button
                          key={id}
                          type="button"
                          className={`bs-cmp-kind-pill${kindFilter === id ? " is-active" : ""}`}
                          onClick={() => setKindFilter(id)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <Input
                      value={resultKw}
                      placeholder={t("bizCompare.resultFilterPh")}
                      onChange={(e) => setResultKw(e.target.value)}
                    />
                    <span className="muted bs-sheet-count">
                      {filteredDiffs.length}/{(activeRunSheet?.diffs || []).length}
                      {resultColumns.presence ? ` · ${t("bizCompare.presenceShort")}` : ""}
                    </span>
                  </div>

                  <div className="pt-list-table-wrap bs-sheet-table bs-cmp-result-table">
                    <table className="data-table pt-list-table bs-cmp-diff-table">
                      <thead>
                        <tr>
                          <th className="bs-cmp-col-kind">{t("bizCompare.colKind")}</th>
                          {resultColumns.keys.map((k) => (
                            <th key={k}>{k}</th>
                          ))}
                          {resultColumns.compare.map((f) => (
                            <th key={f}>{f}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {filteredDiffs.slice(0, 2000).map((d, i) => {
                          const pre = (d.mapped_before || d.before || {}) as Record<string, unknown>;
                          const post = (d.after || {}) as Record<string, unknown>;
                          return (
                            <tr key={i} className={`bs-cmp-row bs-cmp-row--${d.kind}`}>
                              <td className="bs-cmp-col-kind">
                                <span className={`bs-cmp-badge bs-cmp-badge--${d.kind}`}>
                                  {kindLabel(d.kind)}
                                </span>
                              </td>
                              {resultColumns.keys.map((k) => (
                                <td key={k} className="bs-cmp-key-cell">
                                  {cellText(d.key?.[k] ?? pre[k] ?? post[k]) || "—"}
                                </td>
                              ))}
                              {resultColumns.compare.map((f) => {
                                const pv = cellText(pre[f]);
                                const av = cellText(post[f]);
                                if (d.kind === "added") {
                                  return (
                                    <td key={f} className="bs-cmp-val-cell">
                                      <span className="bs-cmp-val bs-cmp-val--post">{av || "—"}</span>
                                    </td>
                                  );
                                }
                                if (d.kind === "removed") {
                                  return (
                                    <td key={f} className="bs-cmp-val-cell">
                                      <span className="bs-cmp-val bs-cmp-val--pre">{pv || "—"}</span>
                                    </td>
                                  );
                                }
                                const mismatch =
                                  Boolean(d.changes?.[f]) ||
                                  (d.kind === "changed" && pv !== av);
                                if (!mismatch) {
                                  return (
                                    <td key={f} className="bs-cmp-val-cell">
                                      <span className="bs-cmp-val">{pv || av || "—"}</span>
                                    </td>
                                  );
                                }
                                return (
                                  <td key={f} className="bs-cmp-val-cell bs-cmp-val-cell--diff">
                                    <span className="bs-cmp-val bs-cmp-val--pre">{pv || "—"}</span>
                                    <span className="bs-cmp-val-arrow" aria-hidden>
                                      →
                                    </span>
                                    <span className="bs-cmp-val bs-cmp-val--post">{av || "—"}</span>
                                  </td>
                                );
                              })}
                            </tr>
                          );
                        })}
                        {runDetail && !filteredDiffs.length ? (
                          <tr>
                            <td
                              colSpan={
                                1 + resultColumns.keys.length + Math.max(resultColumns.compare.length, 0)
                              }
                            >
                              <div className="pt-list-empty">{t("bizCompare.resultEmpty")}</div>
                            </td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>

                  <div className="bs-sheet-tabs" role="tablist">
                    {runSheets.map((s) => {
                      const sc = s.summary || {};
                      const dirty =
                        Number(sc.added || 0) + Number(sc.removed || 0) + Number(sc.changed || 0);
                      return (
                        <button
                          key={s.metric_id}
                          type="button"
                          className={`bs-sheet-tab${resultSheetId === s.metric_id ? " is-active" : ""}`}
                          onClick={() => setResultSheetId(s.metric_id)}
                        >
                          {s.metric_id}
                          <span className="bs-sheet-tab__count">
                            {dirty > 0
                              ? `${t("bizCompare.diffCount", { n: String(dirty) })}`
                              : t("bizCompare.kindUnchangedShort")}
                            {s.mode === "presence" ? ` · ${t("bizCompare.presenceShort")}` : ""}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </>
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
            <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void runNow()}>
              {t("bizCompare.runNow")}
            </Button>
          )}
          <Button size="sm" variant="ghost" onPress={closeJob}>
            {t("bizState.cancel")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
