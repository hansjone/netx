import { useCallback, useEffect, useState } from "react";
import {
  bizStateCollectNow,
  bizStateCreateTask,
  bizStateDeleteTask,
  bizStateDownloadExport,
  bizStateGetBatch,
  bizStateGetTask,
  bizStateListBatches,
  bizStateListProfiles,
  bizStateListTasks,
  bizStatePatchTask,
  fetchManagedNe,
} from "../../services/api";
import type { ManagedNeItem } from "../../types";

type TaskRow = {
  id: string;
  ne_name: string;
  ne_ip: string;
  vendor: string;
  status: string;
  collect_running: boolean;
  last_error: string;
  last_collect_ended_at?: string | null;
};

type Profile = {
  profile_id: string;
  title: string;
  command_template: string;
  description: string;
  metric_id: string;
};

type BatchRow = {
  id: string;
  status: string;
  row_count: number;
  command_count: number;
  started_at?: string | null;
};

export function BizStatePage() {
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [batches, setBatches] = useState<BatchRow[]>([]);
  const [batchDetail, setBatchDetail] = useState<any>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [nes, setNes] = useState<ManagedNeItem[]>([]);
  const [neId, setNeId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const refreshTasks = useCallback(async () => {
    const res = await bizStateListTasks();
    setTasks((res.items || []) as TaskRow[]);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await refreshTasks();
        const neRes = await fetchManagedNe({
          keyword: "",
          vendor: "",
          connectStatus: "",
          page: 1,
          pageSize: 200,
        });
        setNes(neRes.items || []);
      } catch (e: any) {
        setErr(String(e?.message || e));
      }
    })();
  }, [refreshTasks]);
  const openTask = async (id: string) => {
    setSelectedId(id);
    setBatchDetail(null);
    setErr("");
    try {
      const t = await bizStateGetTask(id);
      setDetail(t);
      const b = await bizStateListBatches(id);
      setBatches((b.items || []) as BatchRow[]);
      const p = await bizStateListProfiles({
        vendor: t.vendor || "",
        device_type: t.device_type || "",
      });
      setProfiles((p.items || []) as Profile[]);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };

  const createTask = async () => {
    if (!neId) return;
    const ne = nes.find((n) => n.id === neId);
    if (!ne) return;
    setBusy(true);
    setErr("");
    try {
      const t = await bizStateCreateTask({
        source: "managed",
        ne_id: ne.id,
        ne_name: ne.name,
        ne_ip: ne.ip_address,
        vendor: ne.vendor,
        device_type: ne.device_type,
      });
      await refreshTasks();
      await openTask(t.id);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const setStatus = async (status: string) => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await bizStatePatchTask(selectedId, { status });
      await openTask(selectedId);
      await refreshTasks();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const collectNow = async () => {
    if (!selectedId) return;
    setBusy(true);
    setErr("");
    try {
      await bizStateCollectNow(selectedId);
      await openTask(selectedId);
      await refreshTasks();
      // Poll a few times while collect_running
      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const t = await bizStateGetTask(selectedId);
        setDetail(t);
        if (!t.collect_running) {
          const b = await bizStateListBatches(selectedId);
          setBatches((b.items || []) as BatchRow[]);
          break;
        }
      }
      await refreshTasks();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const openBatch = async (batchId: string) => {
    try {
      const d = await bizStateGetBatch(batchId);
      setBatchDetail(d);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };

  const removeTask = async () => {
    if (!selectedId) return;
    if (!window.confirm("删除该业务监控任务及所有批次？")) return;
    setBusy(true);
    try {
      await bizStateDeleteTask(selectedId);
      setSelectedId("");
      setDetail(null);
      setBatches([]);
      setBatchDetail(null);
      await refreshTasks();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wb-page">
      <div className="wb-page__header">
        <h1>业务状态监控</h1>
        <p className="wb-muted">Phase1：LLDP 邻居快照采集（共享解析，独立批次；比对 Phase2）</p>
      </div>
      {err ? <div className="wb-alert wb-alert--error">{err}</div> : null}

      <div className="wb-card" style={{ marginBottom: 16 }}>
        <h3>新建任务</h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select value={neId} onChange={(e) => setNeId(e.target.value)}>
            <option value="">选择托管网元…</option>
            {nes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name || n.ip_address} ({n.vendor})
              </option>
            ))}
          </select>
          <button type="button" disabled={busy || !neId} onClick={() => void createTask()}>
            创建（默认启用 LLDP）
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 16 }}>
        <div className="wb-card">
          <h3>任务列表</h3>
          <table className="wb-table">
            <thead>
              <tr>
                <th>网元</th>
                <th>状态</th>
                <th>最近采集</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr
                  key={t.id}
                  onClick={() => void openTask(t.id)}
                  style={{ cursor: "pointer", background: selectedId === t.id ? "var(--wb-row-active, #eef)" : undefined }}
                >
                  <td>
                    {t.ne_name || t.ne_ip}
                    <div className="wb-muted">{t.vendor}</div>
                  </td>
                  <td>
                    {t.status}
                    {t.collect_running ? " · 采集中" : ""}
                  </td>
                  <td className="wb-muted">{t.last_collect_ended_at || "-"}</td>
                </tr>
              ))}
              {!tasks.length ? (
                <tr>
                  <td colSpan={3} className="wb-muted">
                    暂无任务
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="wb-card">
          {!detail ? (
            <p className="wb-muted">选择左侧任务查看详情</p>
          ) : (
            <>
              <h3>
                {detail.ne_name} · {detail.status}
              </h3>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                <button type="button" disabled={busy} onClick={() => void setStatus("running")}>
                  启动周期
                </button>
                <button type="button" disabled={busy} onClick={() => void setStatus("paused")}>
                  暂停
                </button>
                <button type="button" disabled={busy} onClick={() => void collectNow()}>
                  立即采集
                </button>
                <button type="button" disabled={busy} onClick={() => void removeTask()}>
                  删除
                </button>
              </div>
              {detail.last_error ? <div className="wb-alert wb-alert--error">{detail.last_error}</div> : null}

              <h4>启用项（勾选表）</h4>
              <table className="wb-table">
                <thead>
                  <tr>
                    <th>启用</th>
                    <th>项</th>
                    <th>命令</th>
                    <th>类型</th>
                  </tr>
                </thead>
                <tbody>
                  {(detail.items || []).map((it: any) => {
                    const prof = profiles.find((p) => p.profile_id === it.source_profile_id);
                    return (
                      <tr key={it.id}>
                        <td>{it.enabled ? "✓" : ""}</td>
                        <td>
                          {it.title || prof?.title || it.source_profile_id}
                          {prof?.description ? (
                            <div className="wb-muted" style={{ fontSize: 12 }}>
                              {prof.description}
                            </div>
                          ) : null}
                        </td>
                        <td>
                          <code>{it.command_override || prof?.command_template || "-"}</code>
                        </td>
                        <td>{it.kind}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              <h4 style={{ marginTop: 16 }}>采集批次</h4>
              <table className="wb-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>状态</th>
                    <th>行数</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {batches.map((b) => (
                    <tr key={b.id}>
                      <td>{b.started_at || "-"}</td>
                      <td>{b.status}</td>
                      <td>{b.row_count}</td>
                      <td style={{ display: "flex", gap: 8 }}>
                        <button type="button" onClick={() => void openBatch(b.id)}>
                          查看
                        </button>
                        <button type="button" onClick={() => void bizStateDownloadExport(b.id)}>
                          导出
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!batches.length ? (
                    <tr>
                      <td colSpan={4} className="wb-muted">
                        尚无批次
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>

              {batchDetail ? (
                <div style={{ marginTop: 16 }}>
                  <h4>批次详情 {batchDetail.id}</h4>
                  <p className="wb-muted">
                    命令 {batchDetail.command_count} · 行 {batchDetail.row_count} · {batchDetail.status}
                  </p>
                  <table className="wb-table">
                    <thead>
                      <tr>
                        <th>本端口</th>
                        <th>对端系统</th>
                        <th>对端口</th>
                        <th>管理IP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(batchDetail.lldp_neighbors || []).slice(0, 200).map((n: any, i: number) => (
                        <tr key={i}>
                          <td>{n.local_if}</td>
                          <td>{n.remote_sys}</td>
                          <td>{n.remote_if}</td>
                          <td>{n.remote_ip}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
