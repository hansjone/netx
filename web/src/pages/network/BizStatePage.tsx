import { useCallback, useEffect, useMemo, useState } from "react";
import {
  bizStateCollectNow,
  bizStateCreateTask,
  bizStateDeleteTask,
  bizStateDiscover,
  bizStateDownloadExport,
  bizStateGetBatch,
  bizStateGetTask,
  bizStateListBatches,
  bizStateListProfiles,
  bizStateListTasks,
  bizStatePatchTask,
  bizStateSetBindings,
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

type Placeholder = {
  name: string;
  bind_mode?: string;
  discover_profile_id?: string;
  discover_value_field?: string;
};

type Profile = {
  profile_id: string;
  title: string;
  command_template: string;
  description: string;
  metric_id: string;
  kind?: string;
  placeholders?: Placeholder[];
};

type BatchRow = {
  id: string;
  status: string;
  row_count: number;
  command_count: number;
  started_at?: string | null;
};

type Candidate = { value: string; label: string; rd?: string };

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
  const [bindItemId, setBindItemId] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedVrfs, setSelectedVrfs] = useState<string[]>([]);
  const [discoverCmd, setDiscoverCmd] = useState("");

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

  const collectProfiles = useMemo(
    () => profiles.filter((p) => (p.kind || "collect") === "collect"),
    [profiles],
  );

  const openTask = async (id: string) => {
    setSelectedId(id);
    setBatchDetail(null);
    setBindItemId("");
    setCandidates([]);
    setSelectedVrfs([]);
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

  const toggleProfileItem = async (profile: Profile, enable: boolean) => {
    if (!selectedId || !detail) return;
    const items = [...(detail.items || [])];
    const idx = items.findIndex((it: any) => it.source_profile_id === profile.profile_id);
    if (enable) {
      if (idx >= 0) {
        items[idx] = { ...items[idx], enabled: true };
      } else {
        items.push({
          source_profile_id: profile.profile_id,
          kind: "catalog",
          enabled: true,
          title: profile.title,
          bindings: [],
        });
      }
    } else if (idx >= 0) {
      items[idx] = { ...items[idx], enabled: false };
    }
    setBusy(true);
    try {
      await bizStatePatchTask(selectedId, { items });
      await openTask(selectedId);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const startDiscover = async (item: any) => {
    if (!selectedId || !detail) return;
    const prof = profiles.find((p) => p.profile_id === item.source_profile_id);
    const ph = (prof?.placeholders || [])[0];
    if (!ph) {
      setErr("该监控项无需参数关联");
      return;
    }
    setBusy(true);
    setErr("");
    setBindItemId(item.id);
    try {
      const res = await bizStateDiscover({
        task_id: selectedId,
        collect_profile_id: item.source_profile_id,
        placeholder: ph.name,
      });
      if (!res.ok) {
        setErr(res.error || "discover failed");
        setCandidates([]);
        return;
      }
      setDiscoverCmd(res.command || "");
      const cand = (res.candidates || []) as Candidate[];
      setCandidates(cand);
      const existing = (item.bindings || [])
        .filter((b: any) => b.placeholder === ph.name)
        .map((b: any) => String(b.value));
      setSelectedVrfs(existing.length ? existing : cand.map((c) => c.value));
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const saveBindings = async () => {
    if (!selectedId || !bindItemId) return;
    const item = (detail?.items || []).find((it: any) => it.id === bindItemId);
    const prof = profiles.find((p) => p.profile_id === item?.source_profile_id);
    const phName = (prof?.placeholders || [])[0]?.name || "vrf";
    setBusy(true);
    try {
      await bizStateSetBindings(
        selectedId,
        bindItemId,
        selectedVrfs.map((v) => ({ placeholder: phName, value: v })),
      );
      await openTask(selectedId);
      setBindItemId("");
      setCandidates([]);
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
        <p className="wb-muted">
          LLDP 快照 + VRF 发现关联（选 VRF 后采路由摘要）+ 割接比对见「业务状态比对」
        </p>
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
                  style={{
                    cursor: "pointer",
                    background: selectedId === t.id ? "var(--wb-row-active, #eef)" : undefined,
                  }}
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

              <h4>可用监控项</h4>
              <table className="wb-table">
                <thead>
                  <tr>
                    <th>启用</th>
                    <th>项</th>
                    <th>模板</th>
                    <th>参数</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {collectProfiles.map((prof) => {
                    const it = (detail.items || []).find(
                      (x: any) => x.source_profile_id === prof.profile_id,
                    );
                    const enabled = Boolean(it?.enabled);
                    const binds = it?.bindings || [];
                    const needsBind = (prof.placeholders || []).length > 0;
                    return (
                      <tr key={prof.profile_id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={enabled}
                            disabled={busy}
                            onChange={(e) => void toggleProfileItem(prof, e.target.checked)}
                          />
                        </td>
                        <td>
                          {prof.title}
                          <div className="wb-muted" style={{ fontSize: 12 }}>
                            {prof.description}
                          </div>
                        </td>
                        <td>
                          <code>{prof.command_template}</code>
                        </td>
                        <td>
                          {needsBind
                            ? binds.length
                              ? binds.map((b: any) => b.value).join(", ")
                              : "未关联"
                            : "—"}
                        </td>
                        <td>
                          {needsBind && enabled && it ? (
                            <button type="button" disabled={busy} onClick={() => void startDiscover(it)}>
                              发现 VRF
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {bindItemId && candidates.length ? (
                <div style={{ marginTop: 12, padding: 12, border: "1px solid var(--wb-border, #ddd)" }}>
                  <h4>选择 VRF 绑定</h4>
                  <p className="wb-muted">
                    命令 <code>{discoverCmd}</code> · 已选 {selectedVrfs.length}
                  </p>
                  <div style={{ maxHeight: 220, overflow: "auto", marginBottom: 8 }}>
                    {candidates.map((c) => (
                      <label key={c.value} style={{ display: "block", marginBottom: 4 }}>
                        <input
                          type="checkbox"
                          checked={selectedVrfs.includes(c.value)}
                          onChange={(e) => {
                            setSelectedVrfs((prev) =>
                              e.target.checked
                                ? [...prev, c.value]
                                : prev.filter((x) => x !== c.value),
                            );
                          }}
                        />{" "}
                        {c.label}
                        {c.rd ? <span className="wb-muted"> · RD {c.rd}</span> : null}
                      </label>
                    ))}
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button type="button" disabled={busy || !selectedVrfs.length} onClick={() => void saveBindings()}>
                      保存关联
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setBindItemId("");
                        setCandidates([]);
                      }}
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : null}

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
                  {(batchDetail.lldp_neighbors || []).length ? (
                    <>
                      <h5>LLDP</h5>
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
                    </>
                  ) : null}
                  {(batchDetail.vrf_route_summary || []).length ? (
                    <>
                      <h5>VRF 路由摘要</h5>
                      <table className="wb-table">
                        <thead>
                          <tr>
                            <th>VRF</th>
                            <th>来源</th>
                            <th>条数</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(batchDetail.vrf_route_summary || []).map((r: any, i: number) => (
                            <tr key={i}>
                              <td>{r.vrf}</td>
                              <td>{r.source}</td>
                              <td>{r.networks}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
