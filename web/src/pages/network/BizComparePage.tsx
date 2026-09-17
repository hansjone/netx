import { useCallback, useEffect, useState } from "react";
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
} from "../../services/api";

type TaskOpt = { id: string; ne_name: string; ne_ip: string; vendor: string };
type BatchOpt = { id: string; status: string; row_count: number; started_at?: string | null };
type Template = { id: string; name: string; metric_id: string; key_fields: string[]; iface_fields: string[] };
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

export function BizComparePage() {
  const [tasks, setTasks] = useState<TaskOpt[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [runs, setRuns] = useState<any[]>([]);
  const [runDetail, setRunDetail] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  // create job form
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

  // mapping editor
  const [mapName, setMapName] = useState("端口映射");
  const [mapText, setMapText] = useState("old-if,new-if\n");
  const [validateOut, setValidateOut] = useState<any>(null);

  const refresh = useCallback(async () => {
    const [t, tpl, maps, j] = await Promise.all([
      bizStateListTasks(),
      bizCompareListTemplates(),
      bizCompareListMappings(),
      bizCompareListJobs(),
    ]);
    setTasks((t.items || []) as TaskOpt[]);
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
      } catch (e: any) {
        setErr(String(e?.message || e));
      }
    })();
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
      if (!s || s.startsWith("#") || s.toLowerCase().startsWith("old")) continue;
      const parts = s.split(/[,|\t]+/).map((x) => x.trim());
      if (parts.length >= 2 && parts[0] && parts[1]) {
        rows.push({ before_if: parts[0], after_if: parts[1] });
      }
    }
    return rows;
  };

  const saveMapping = async () => {
    setBusy(true);
    setErr("");
    try {
      const rows = parseMapRows();
      const m = await bizCompareCreateMapping({ name: mapName, rows });
      setMappingId(String(m.id));
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const doValidate = async () => {
    if (!mappingId || !beforeBatchId || !afterBatchId) {
      setErr("请先选择映射与前后批次");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const v = await bizCompareValidateMapping({
        mapping_id: mappingId,
        before_batch_id: beforeBatchId,
        after_batch_id: afterBatchId,
        template_id: templateId,
      });
      setValidateOut(v);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const createJob = async () => {
    setBusy(true);
    setErr("");
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
      await refresh();
      const r = await bizCompareListRuns(String(j.id));
      setRuns(r.items || []);
    } catch (e: any) {
      setErr(String(e?.message || e));
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
    }
  };

  const runNow = async () => {
    if (!selectedJobId) return;
    setBusy(true);
    setErr("");
    try {
      // sync latest batch selection onto job before run
      await bizCompareUpdateJob(selectedJobId, {
        before_batch_id: beforeBatchId,
        after_batch_id: mode === "manual" ? afterBatchId : "",
        mode,
        mapping_id: mappingId,
        template_id: templateId,
      });
      const run = await bizCompareRunJob(selectedJobId);
      setRunDetail(run);
      const r = await bizCompareListRuns(selectedJobId);
      setRuns(r.items || []);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const loadRun = async (runId: string) => {
    const d = await bizCompareGetRun(runId);
    setRunDetail(d);
  };

  const loadMappingText = (id: string) => {
    const m = mappings.find((x) => x.id === id);
    if (!m) return;
    setMappingId(id);
    setMapName(m.name);
    setMapText(["before_if,after_if", ...m.rows.map((r) => `${r.before_if},${r.after_if}`)].join("\n"));
  };

  const updateExistingMapping = async () => {
    if (!mappingId) {
      await saveMapping();
      return;
    }
    setBusy(true);
    try {
      await bizCompareUpdateMapping(mappingId, { name: mapName, rows: parseMapRows() });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const taskLabel = (t: TaskOpt) => `${t.ne_name || t.ne_ip || t.id} (${t.vendor || "-"})`;

  return (
    <div className="wb-page">
      <div className="wb-page__header">
        <h1>业务状态比对</h1>
        <p className="wb-muted">Phase2：模板 + 端口映射 + 前后批次 diff（支持 auto 追比）</p>
      </div>
      {err ? <div className="wb-alert wb-alert--error">{err}</div> : null}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div className="wb-card">
          <h3>端口映射</h3>
          <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            <select
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
              <option value="">新建映射…</option>
              {mappings.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            <input value={mapName} onChange={(e) => setMapName(e.target.value)} placeholder="映射名称" />
            <button type="button" disabled={busy} onClick={() => void updateExistingMapping()}>
              保存映射
            </button>
            <button type="button" disabled={busy} onClick={() => void doValidate()}>
              校验映射
            </button>
          </div>
          <textarea
            value={mapText}
            onChange={(e) => setMapText(e.target.value)}
            rows={8}
            style={{ width: "100%", fontFamily: "monospace" }}
            placeholder="before_if,after_if 每行一对"
          />
          {validateOut ? (
            <pre className="wb-muted" style={{ fontSize: 12, maxHeight: 160, overflow: "auto" }}>
              {JSON.stringify(validateOut, null, 2)}
            </pre>
          ) : null}
        </div>

        <div className="wb-card">
          <h3>建立比对任务</h3>
          <div style={{ display: "grid", gap: 8 }}>
            <label>
              名称{" "}
              <input value={name} onChange={(e) => setName(e.target.value)} style={{ width: "70%" }} />
            </label>
            <label>
              模板{" "}
              <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.metric_id})
                  </option>
                ))}
              </select>
            </label>
            <label>
              模式{" "}
              <select value={mode} onChange={(e) => setMode(e.target.value as "manual" | "auto")}>
                <option value="manual">手工选批次</option>
                <option value="auto">自动（钉死操作前，操作后取最新批次）</option>
              </select>
            </label>
            <label>
              操作前任务{" "}
              <select value={beforeTaskId} onChange={(e) => setBeforeTaskId(e.target.value)}>
                <option value="">选择…</option>
                {tasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {taskLabel(t)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              操作前批次{" "}
              <select value={beforeBatchId} onChange={(e) => setBeforeBatchId(e.target.value)}>
                <option value="">选择…</option>
                {beforeBatches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.started_at} · {b.status} · rows={b.row_count}
                  </option>
                ))}
              </select>
            </label>
            <label>
              操作后任务{" "}
              <select value={afterTaskId} onChange={(e) => setAfterTaskId(e.target.value)}>
                <option value="">同操作前 / 选择…</option>
                {tasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {taskLabel(t)}
                  </option>
                ))}
              </select>
            </label>
            {mode === "manual" ? (
              <label>
                操作后批次{" "}
                <select value={afterBatchId} onChange={(e) => setAfterBatchId(e.target.value)}>
                  <option value="">选择…</option>
                  {afterBatches.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.started_at} · {b.status} · rows={b.row_count}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <p className="wb-muted">auto：每次操作后任务新批次到达会自动跑比对</p>
            )}
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" disabled={busy} onClick={() => void createJob()}>
                创建比对任务
              </button>
              <button type="button" disabled={busy || !selectedJobId} onClick={() => void runNow()}>
                立即比对
              </button>
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.6fr", gap: 16, marginTop: 16 }}>
        <div className="wb-card">
          <h3>比对任务列表</h3>
          <table className="wb-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>模式</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr
                  key={j.id}
                  style={{ cursor: "pointer", background: selectedJobId === j.id ? "var(--wb-row-active, #eef)" : undefined }}
                  onClick={() => void openJob(j.id)}
                >
                  <td>{j.name}</td>
                  <td>{j.mode}</td>
                  <td>{j.status}</td>
                </tr>
              ))}
              {!jobs.length ? (
                <tr>
                  <td colSpan={3} className="wb-muted">
                    暂无
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          <h4>历史 Run</h4>
          <ul>
            {runs.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => void loadRun(r.id)}>
                  {r.created_at} · added={r.summary?.added} removed={r.summary?.removed} changed={r.summary?.changed}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="wb-card">
          <h3>比对结果</h3>
          {!runDetail ? (
            <p className="wb-muted">选择 Run 或点「立即比对」</p>
          ) : (
            <>
              <p>
                before={runDetail.before_batch_id?.slice(0, 8)}… after={runDetail.after_batch_id?.slice(0, 8)}… ·{" "}
                <strong>
                  +{runDetail.summary?.added || 0} / -{runDetail.summary?.removed || 0} / ~{runDetail.summary?.changed || 0} / ={runDetail.summary?.unchanged || 0}
                </strong>
              </p>
              {runDetail.mapping_stats ? (
                <p className="wb-muted" style={{ fontSize: 12 }}>
                  映射校验 ok={String(runDetail.mapping_stats.ok)} miss_before={(runDetail.mapping_stats.miss_before || []).length}{" "}
                  miss_after={(runDetail.mapping_stats.miss_after || []).length}
                </p>
              ) : null}
              <table className="wb-table">
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>Key</th>
                    <th>变更</th>
                  </tr>
                </thead>
                <tbody>
                  {(runDetail.diffs || [])
                    .filter((d: any) => d.kind !== "unchanged")
                    .slice(0, 300)
                    .map((d: any, i: number) => (
                      <tr key={i}>
                        <td>{d.kind}</td>
                        <td>
                          <code style={{ fontSize: 11 }}>{JSON.stringify(d.key)}</code>
                        </td>
                        <td>
                          <code style={{ fontSize: 11 }}>{d.kind === "changed" ? JSON.stringify(d.changes) : "-"}</code>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
