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
type KindFilter = "all" | "added" | "removed" | "changed" | "unchanged";

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
type Template = {
  id: string;
  name: string;
  metric_id: string;
  key_fields: string[];
  iface_fields: string[];
  compare_fields: string[];
  ignore_fields: string[];
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
  const [tplMetric, setTplMetric] = useState("lldp_neighbor");
  const [tplKeys, setTplKeys] = useState<string[]>([]);
  const [tplIfaces, setTplIfaces] = useState<string[]>([]);
  const [tplCompare, setTplCompare] = useState<string[]>([]);
  const [tplIgnore, setTplIgnore] = useState<string[]>([]);
  const [tplNote, setTplNote] = useState("");

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

  // result filters
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
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

  const metricFields = useMemo(() => {
    return metrics.find((m) => m.metric_id === tplMetric)?.fields || [];
  }, [metrics, tplMetric]);

  const filteredJobs = useMemo(() => {
    const kw = debouncedListKw.trim().toLowerCase();
    if (!kw) return jobs;
    return jobs.filter((j) => {
      const tpl = templates.find((x) => x.id === j.template_id);
      return `${j.name} ${j.mode} ${j.status} ${tpl?.name || ""} ${tpl?.metric_id || ""}`
        .toLowerCase()
        .includes(kw);
    });
  }, [jobs, templates, debouncedListKw]);

  const filteredTemplates = useMemo(() => {
    const kw = debouncedListKw.trim().toLowerCase();
    if (!kw) return templates;
    return templates.filter((x) =>
      `${x.name} ${x.metric_id} ${x.note}`.toLowerCase().includes(kw),
    );
  }, [templates, debouncedListKw]);

  const activeTemplate = useMemo(
    () => templates.find((x) => x.id === (runDetail?.template_id || templateId)),
    [templates, runDetail, templateId],
  );

  const resultColumns = useMemo(() => {
    const tpl = (runDetail?.template as Template | null) || activeTemplate;
    const keys = tpl?.key_fields?.length ? tpl.key_fields : Object.keys((runDetail?.diffs?.[0]?.key as any) || {});
    const compare = (tpl?.compare_fields || []).filter((f) => !keys.includes(f));
    return { keys, compare };
  }, [runDetail, activeTemplate]);

  const filteredDiffs = useMemo(() => {
    const diffs = (runDetail?.diffs || []) as DiffRow[];
    const kw = debouncedResultKw.trim().toLowerCase();
    return diffs.filter((d) => {
      if (kindFilter !== "all" && d.kind !== kindFilter) return false;
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
  }, [runDetail, kindFilter, debouncedResultKw]);

  const openNewTemplate = () => {
    const first = metrics[0]?.metric_id || "lldp_neighbor";
    const fields = metrics.find((m) => m.metric_id === first)?.fields || [];
    setTplEditId("");
    setTplName("");
    setTplMetric(first);
    setTplKeys(fields.filter((f) => f.is_key).map((f) => f.name));
    setTplIfaces(fields.filter((f) => f.is_interface).map((f) => f.name));
    setTplCompare(fields.map((f) => f.name));
    setTplIgnore([]);
    setTplNote("");
    setTplOpen(true);
  };

  const openEditTemplate = (tpl: Template) => {
    setTplEditId(tpl.id);
    setTplName(tpl.name);
    setTplMetric(tpl.metric_id);
    setTplKeys([...(tpl.key_fields || [])]);
    setTplIfaces([...(tpl.iface_fields || [])]);
    setTplCompare([...(tpl.compare_fields || [])]);
    setTplIgnore([...(tpl.ignore_fields || [])]);
    setTplNote(tpl.note || "");
    setTplOpen(true);
  };

  const onMetricChange = (metricId: string) => {
    setTplMetric(metricId);
    const fields = metrics.find((m) => m.metric_id === metricId)?.fields || [];
    setTplKeys(fields.filter((f) => f.is_key).map((f) => f.name));
    setTplIfaces(fields.filter((f) => f.is_interface).map((f) => f.name));
    setTplCompare(fields.map((f) => f.name));
    setTplIgnore([]);
  };

  const saveTemplate = async () => {
    if (!tplKeys.length) {
      showError(t("bizCompare.keyRequired"));
      return;
    }
    setBusy(true);
    try {
      const body = {
        name: tplName || tplMetric,
        metric_id: tplMetric,
        key_fields: tplKeys,
        iface_fields: tplIfaces,
        compare_fields: tplCompare,
        ignore_fields: tplIgnore,
        note: tplNote,
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
    setKindFilter("all");
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
      setJobDetailTab("result");
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const summary = runDetail?.summary || {};

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
        {templates.map((tpl) => (
          <option key={tpl.id} value={tpl.id}>
            {tpl.name} ({tpl.metric_id})
          </option>
        ))}
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
        <div className="btn-row nm-config-modal__tabs">
          <Button
            size="sm"
            variant={pageTab === "jobs" ? "primary" : "secondary"}
            onPress={() => setPageTab("jobs")}
          >
            {t("bizCompare.jobList")}
          </Button>
          <Button
            size="sm"
            variant={pageTab === "templates" ? "primary" : "secondary"}
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
                  <th>metric</th>
                  <th>{t("bizCompare.keyFields")}</th>
                  <th>{t("bizCompare.ifaceFields")}</th>
                  <th>{t("bizCompare.compareFields")}</th>
                  <th>{t("bizCompare.colActions")}</th>
                </tr>
              </thead>
              <tbody>
                {filteredTemplates.map((tpl) => (
                  <tr key={tpl.id}>
                    <td>
                      <div className="pt-list-task-name">{tpl.name}</div>
                      {tpl.note ? <div className="muted">{tpl.note}</div> : null}
                    </td>
                    <td>
                      <code>{tpl.metric_id}</code>
                    </td>
                    <td className="muted">{(tpl.key_fields || []).join(", ") || "—"}</td>
                    <td className="muted">{(tpl.iface_fields || []).join(", ") || "—"}</td>
                    <td className="muted">{(tpl.compare_fields || []).join(", ") || "—"}</td>
                    <td>
                      <div className="pt-list-actions">
                        <Button size="sm" variant="secondary" onPress={() => openEditTemplate(tpl)}>
                          {t("bizCompare.edit")}
                        </Button>
                        <Button size="sm" variant="danger" isDisabled={busy} onPress={() => void removeTemplate(tpl.id)}>
                          {t("bizCompare.delete")}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!filteredTemplates.length ? (
                  <tr>
                    <td colSpan={6}>
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
                  return (
                    <tr key={j.id}>
                      <td>
                        <div className="pt-list-task-name">{j.name}</div>
                      </td>
                      <td className="muted">
                        {tpl?.name || j.template_id.slice(0, 8)}
                        {tpl ? ` · ${tpl.metric_id}` : ""}
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
          <FieldSelect
            label="metric"
            value={tplMetric}
            onChange={(e) => onMetricChange(e.target.value)}
            fullWidth
            disabled={Boolean(tplEditId)}
          >
            {metrics.map((m) => (
              <option key={m.metric_id} value={m.metric_id}>
                {m.metric_id}
              </option>
            ))}
          </FieldSelect>
          <p className="muted">{t("bizCompare.templateHint")}</p>
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("bizCompare.field")}</th>
                  <th>{t("bizCompare.keyFields")}</th>
                  <th>{t("bizCompare.ifaceFields")}</th>
                  <th>{t("bizCompare.compareFields")}</th>
                  <th>{t("bizCompare.ignoreFields")}</th>
                </tr>
              </thead>
              <tbody>
                {metricFields.map((f) => (
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
                        checked={tplKeys.includes(f.name)}
                        onChange={(e) => setTplKeys((prev) => toggleInList(prev, f.name, e.target.checked))}
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={tplIfaces.includes(f.name)}
                        onChange={(e) => setTplIfaces((prev) => toggleInList(prev, f.name, e.target.checked))}
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={tplCompare.includes(f.name)}
                        onChange={(e) => setTplCompare((prev) => toggleInList(prev, f.name, e.target.checked))}
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={tplIgnore.includes(f.name)}
                        onChange={(e) => setTplIgnore((prev) => toggleInList(prev, f.name, e.target.checked))}
                      />
                    </td>
                  </tr>
                ))}
                {!metricFields.length ? (
                  <tr>
                    <td colSpan={5}>
                      <div className="pt-list-empty">{t("bizCompare.noMetricFields")}</div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizCompare.note")}</span>
            <Input value={tplNote} onChange={(e) => setTplNote(e.target.value)} />
          </label>
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
          <div className="btn-row nm-config-modal__tabs">
            <Button
              size="sm"
              variant={jobDetailTab === "config" ? "primary" : "secondary"}
              onPress={() => setJobDetailTab("config")}
            >
              {t("bizCompare.tabConfig")}
            </Button>
            <Button
              size="sm"
              variant={jobDetailTab === "result" ? "primary" : "secondary"}
              onPress={() => setJobDetailTab("result")}
            >
              {t("bizCompare.tabResult")}
            </Button>
          </div>

          {jobDetailTab === "config" ? (
            renderJobForm(false)
          ) : (
            <>
              <div className="pt-list-kpis">
                <div className="pt-list-kpi">
                  <div className="pt-list-kpi__label">+</div>
                  <div className="pt-list-kpi__value">{summary.added ?? 0}</div>
                </div>
                <div className="pt-list-kpi">
                  <div className="pt-list-kpi__label">−</div>
                  <div className="pt-list-kpi__value">{summary.removed ?? 0}</div>
                </div>
                <div className="pt-list-kpi pt-list-kpi--live">
                  <div className="pt-list-kpi__label">~</div>
                  <div className="pt-list-kpi__value">{summary.changed ?? 0}</div>
                </div>
                <div className="pt-list-kpi">
                  <div className="pt-list-kpi__label">=</div>
                  <div className="pt-list-kpi__value">{summary.unchanged ?? 0}</div>
                </div>
              </div>

              <div className="filter-inline">
                <FieldSelect
                  value={kindFilter}
                  onChange={(e) => setKindFilter(e.target.value as KindFilter)}
                >
                  <option value="all">{t("bizCompare.kindAll")}</option>
                  <option value="added">{t("bizCompare.kindAdded")}</option>
                  <option value="removed">{t("bizCompare.kindRemoved")}</option>
                  <option value="changed">{t("bizCompare.kindChanged")}</option>
                  <option value="unchanged">{t("bizCompare.kindUnchanged")}</option>
                </FieldSelect>
                <Input
                  value={resultKw}
                  placeholder={t("bizCompare.resultFilterPh")}
                  onChange={(e) => setResultKw(e.target.value)}
                />
                <span className="muted bs-sheet-count">
                  {filteredDiffs.length}/{(runDetail?.diffs || []).length}
                </span>
              </div>

              {runDetail ? (
                <p className="muted">
                  before={String(runDetail.before_batch_id || "").slice(0, 8)}… · after=
                  {String(runDetail.after_batch_id || "").slice(0, 8)}… · metric=
                  {runDetail.metric_id || "—"}
                </p>
              ) : (
                <div className="pt-list-empty">{t("bizCompare.pickRun")}</div>
              )}

              <div className="pt-list-table-wrap bs-cmp-result-table">
                <table className="data-table pt-list-table bs-cmp-diff-table">
                  <thead>
                    <tr>
                      <th rowSpan={2}>{t("bizCompare.colKind")}</th>
                      {resultColumns.keys.map((k) => (
                        <th key={k} rowSpan={2}>
                          {k}
                        </th>
                      ))}
                      {resultColumns.compare.map((f) => (
                        <th key={f} colSpan={2} className="bs-cmp-pair-head">
                          {f}
                        </th>
                      ))}
                    </tr>
                    <tr>
                      {resultColumns.compare.flatMap((f) => [
                        <th key={`${f}-pre`} className="bs-cmp-pre-head">
                          pre
                        </th>,
                        <th key={`${f}-post`} className="bs-cmp-post-head">
                          post
                        </th>,
                      ])}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDiffs.slice(0, 2000).map((d, i) => {
                      const pre = (d.mapped_before || d.before || {}) as Record<string, unknown>;
                      const post = (d.after || {}) as Record<string, unknown>;
                      return (
                        <tr key={i} className={`bs-cmp-row bs-cmp-row--${d.kind}`}>
                          <td>
                            <NmStatusChip
                              color={
                                d.kind === "added"
                                  ? "success"
                                  : d.kind === "removed"
                                    ? "danger"
                                    : d.kind === "changed"
                                      ? "warning"
                                      : "default"
                              }
                            >
                              {d.kind}
                            </NmStatusChip>
                          </td>
                          {resultColumns.keys.map((k) => (
                            <td key={k}>
                              <code>{cellText(d.key?.[k] ?? pre[k] ?? post[k]) || "—"}</code>
                            </td>
                          ))}
                          {resultColumns.compare.flatMap((f) => {
                            const changed = Boolean(d.changes?.[f]) || (d.kind === "changed" && cellText(pre[f]) !== cellText(post[f]));
                            const pv = cellText(pre[f]);
                            const av = cellText(post[f]);
                            const mismatch = changed && pv !== av;
                            return [
                              <td
                                key={`${f}-pre-${i}`}
                                className={
                                  d.kind === "removed" || mismatch
                                    ? "bs-cmp-cell bs-cmp-cell--pre"
                                    : "bs-cmp-cell"
                                }
                              >
                                {d.kind === "added" ? "—" : pv || "—"}
                              </td>,
                              <td
                                key={`${f}-post-${i}`}
                                className={
                                  d.kind === "added" || mismatch
                                    ? "bs-cmp-cell bs-cmp-cell--post"
                                    : "bs-cmp-cell"
                                }
                              >
                                {d.kind === "removed" ? "—" : av || "—"}
                              </td>,
                            ];
                          })}
                        </tr>
                      );
                    })}
                    {runDetail && !filteredDiffs.length ? (
                      <tr>
                        <td colSpan={1 + resultColumns.keys.length + resultColumns.compare.length * 2}>
                          <div className="pt-list-empty">{t("bizCompare.resultEmpty")}</div>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

              <div className="bs-sheet-tabs" role="tablist">
                {runs.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    className={`bs-sheet-tab${runDetail?.id === r.id ? " is-active" : ""}`}
                    onClick={() => void loadRun(r.id)}
                  >
                    {fmtTime(r.created_at)}
                    <span className="bs-sheet-tab__count">
                      +{r.summary?.added ?? 0}/−{r.summary?.removed ?? 0}/~{r.summary?.changed ?? 0}
                    </span>
                  </button>
                ))}
                {!runs.length ? <span className="muted">{t("bizCompare.noRuns")}</span> : null}
              </div>
            </>
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
