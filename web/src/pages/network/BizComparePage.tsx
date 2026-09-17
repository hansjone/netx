import { Button, Input } from "@heroui/react";
import { useCallback, useEffect, useState } from "react";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizCompareCreateJob,
  bizCompareCreateMapping,
  bizCompareGetRun,
  bizCompareListJobs,
  bizCompareListMappings,
  bizCompareListRuns,
  bizCompareListTemplates,
  bizCompareRunJob,
  bizCompareUpdateJob,
  bizCompareUpdateMapping,
  bizCompareValidateMapping,
  bizStateListBatches,
  bizStateListTasks,
  formatErr,
} from "../../services/api";
import { formatSystemTime } from "../../utils/time";
import { jobChipColor, NmStatusChip } from "./nmChips";

type TaskOpt = { id: string; ne_name: string; ne_ip: string; vendor: string };
type BatchOpt = { id: string; status: string; row_count: number; started_at?: string | null };
type Template = { id: string; name: string; metric_id: string };
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
};

function fmtTime(v?: string | null) {
  if (!v) return "—";
  return formatSystemTime(v) || v;
}

export function BizComparePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [tasks, setTasks] = useState<TaskOpt[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [runs, setRuns] = useState<any[]>([]);
  const [runDetail, setRunDetail] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("割接比对");
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

  const refresh = useCallback(async () => {
    const [taskRes, tpl, maps, j] = await Promise.all([
      bizStateListTasks(),
      bizCompareListTemplates(),
      bizCompareListMappings(),
      bizCompareListJobs(),
    ]);
    setTasks((taskRes.items || []) as TaskOpt[]);
    setTemplates((tpl.items || []) as Template[]);
    setMappings((maps.items || []) as Mapping[]);
    setJobs((j.items || []) as Job[]);
    const items = (tpl.items || []) as Template[];
    if (!templateId && items[0]) setTemplateId(items[0].id);
  }, [templateId]);

  useEffect(() => {
    void (async () => {
      try {
        await refresh();
      } catch (e) {
        showError(formatErr(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once
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

  const updateExistingMapping = async () => {
    setBusy(true);
    try {
      const rows = parseMapRows();
      if (mappingId) {
        await bizCompareUpdateMapping(mappingId, { name: mapName, rows });
      } else {
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
      showError(`${t("bizCompare.validateMapping")}: mapping / batches`);
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
      setSelectedJobId(String(j.id));
      showOk(t("bizCompare.created"));
      await refresh();
      const r = await bizCompareListRuns(String(j.id));
      setRuns(r.items || []);
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const openJob = async (id: string) => {
    setSelectedJobId(id);
    setRunDetail(null);
    const r = await bizCompareListRuns(id);
    setRuns(r.items || []);
    const job = jobs.find((x) => x.id === id);
    if (job) {
      setName(job.name);
      setTemplateId(job.template_id);
      setMappingId(job.mapping_id);
      setBeforeTaskId(job.before_task_id);
      setAfterTaskId(job.after_task_id);
      setBeforeBatchId(job.before_batch_id);
      setAfterBatchId(job.after_batch_id);
      setMode(job.mode === "auto" ? "auto" : "manual");
      if (job.mapping_id) loadMappingText(job.mapping_id);
    }
  };

  const runNow = async () => {
    if (!selectedJobId) return;
    setBusy(true);
    try {
      await bizCompareUpdateJob(selectedJobId, {
        before_batch_id: beforeBatchId,
        after_batch_id: mode === "manual" ? afterBatchId : "",
        mode,
        mapping_id: mappingId,
        template_id: templateId,
      });
      const run = await bizCompareRunJob(selectedJobId);
      setRunDetail(run);
      showOk(t("bizCompare.ran"));
      const r = await bizCompareListRuns(selectedJobId);
      setRuns(r.items || []);
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
    } catch (e) {
      showError(formatErr(e));
    }
  };

  const loadMappingText = (id: string) => {
    const m = mappings.find((x) => x.id === id);
    if (!m) return;
    setMappingId(id);
    setMapName(m.name);
    setMapText(["before_if,after_if", ...m.rows.map((r) => `${r.before_if},${r.after_if}`)].join("\n"));
  };

  const taskLabel = (row: TaskOpt) => `${row.ne_name || row.ne_ip || row.id} (${row.vendor || "-"})`;

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("bizCompare.title")}</h2>
        <div className="btn-row">
          <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void createJob()}>
            {t("bizCompare.createCompare")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            isDisabled={busy || !selectedJobId}
            onPress={() => void runNow()}
          >
            {t("bizCompare.runNow")}
          </Button>
        </div>
      </div>

      <div className="pt-list">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <div>
            <h3 style={{ marginTop: 0 }}>{t("bizCompare.mapping")}</h3>
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
              <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => void updateExistingMapping()}>
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
              rows={8}
              style={{ width: "100%", fontFamily: "ui-monospace, monospace" }}
            />
            {validateOut ? (
              <pre className="muted" style={{ fontSize: 12, maxHeight: 160, overflow: "auto" }}>
                {JSON.stringify(validateOut, null, 2)}
              </pre>
            ) : null}
          </div>

          <div>
            <h3 style={{ marginTop: 0 }}>{t("bizCompare.createJob")}</h3>
            <div style={{ display: "grid", gap: 8 }}>
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
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1.6fr", gap: 16, marginTop: 16 }}>
          <div>
            <h3>{t("bizCompare.jobList")}</h3>
            <div className="pt-list-table-wrap">
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th>{t("bizCompare.colName")}</th>
                    <th>{t("bizCompare.colMode")}</th>
                    <th>{t("bizCompare.colStatus")}</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((j) => (
                    <tr
                      key={j.id}
                      className={selectedJobId === j.id ? "is-selected" : undefined}
                      style={{ cursor: "pointer" }}
                      onClick={() => void openJob(j.id)}
                    >
                      <td>
                        <div className="pt-list-task-name">{j.name}</div>
                      </td>
                      <td>{j.mode}</td>
                      <td>
                        <NmStatusChip color={jobChipColor(j.status)}>{j.status}</NmStatusChip>
                      </td>
                    </tr>
                  ))}
                  {!jobs.length ? (
                    <tr>
                      <td colSpan={3}>
                        <div className="pt-list-empty">{t("bizCompare.emptyJobs")}</div>
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <h4 style={{ marginTop: 16 }}>{t("bizCompare.runs")}</h4>
            <div className="pt-list-actions" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
              {runs.map((r) => (
                <Button key={r.id} size="sm" variant="secondary" onPress={() => void loadRun(r.id)}>
                  {fmtTime(r.created_at)} · +{r.summary?.added ?? 0} / -{r.summary?.removed ?? 0} / ~
                  {r.summary?.changed ?? 0}
                </Button>
              ))}
            </div>
          </div>

          <div>
            <h3>{t("bizCompare.result")}</h3>
            {!runDetail ? (
              <div className="pt-list-empty">{t("bizCompare.pickRun")}</div>
            ) : (
              <>
                <p>
                  before={String(runDetail.before_batch_id || "").slice(0, 8)}… after=
                  {String(runDetail.after_batch_id || "").slice(0, 8)}… ·{" "}
                  <strong>
                    +{runDetail.summary?.added || 0} / -{runDetail.summary?.removed || 0} / ~
                    {runDetail.summary?.changed || 0} / ={runDetail.summary?.unchanged || 0}
                  </strong>
                </p>
                {runDetail.mapping_stats ? (
                  <p className="muted" style={{ fontSize: 12 }}>
                    mapping ok={String(runDetail.mapping_stats.ok)} miss_before=
                    {(runDetail.mapping_stats.miss_before || []).length} miss_after=
                    {(runDetail.mapping_stats.miss_after || []).length}
                  </p>
                ) : null}
                <div className="pt-list-table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{t("bizCompare.colKind")}</th>
                        <th>{t("bizCompare.colKey")}</th>
                        <th>{t("bizCompare.colChange")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(runDetail.diffs || [])
                        .filter((d: any) => d.kind !== "unchanged")
                        .slice(0, 300)
                        .map((d: any, i: number) => (
                          <tr key={i}>
                            <td>
                              <NmStatusChip
                                color={
                                  d.kind === "added"
                                    ? "success"
                                    : d.kind === "removed"
                                      ? "danger"
                                      : "warning"
                                }
                              >
                                {d.kind}
                              </NmStatusChip>
                            </td>
                            <td>
                              <code style={{ fontSize: 11 }}>{JSON.stringify(d.key)}</code>
                            </td>
                            <td>
                              <code style={{ fontSize: 11 }}>
                                {d.kind === "changed" ? JSON.stringify(d.changes) : "—"}
                              </code>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
