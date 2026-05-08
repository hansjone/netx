import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiPost,
  disconnectUmeToken,
  fetchUmeCurrentAlarms,
  fetchUmeHistoryAlarms,
  fetchUmeNe,
  fetchUmeSyncStatus,
  fetchUmeTokenStatus,
  refreshUmeToken,
} from "../services/api";
import { formatSystemTime } from "../utils/time";

function toIsoNoMs(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function UmePage() {
  const queryClient = useQueryClient();
  const [tokenOpError, setTokenOpError] = useState("");
  const [syncPage, setSyncPage] = useState(1);
  const [syncPageSize, setSyncPageSize] = useState(20);
  const [neKeyword, setNeKeyword] = useState("");
  const [nePage, setNePage] = useState(1);
  const [nePageSize, setNePageSize] = useState(50);
  const [expandedNeId, setExpandedNeId] = useState("");

  const [curSeverity, setCurSeverity] = useState("");
  const [curCleared, setCurCleared] = useState("");
  const [curNeId, setCurNeId] = useState("");
  const [curKeyword, setCurKeyword] = useState("");
  const [curPage, setCurPage] = useState(1);
  const [curPageSize, setCurPageSize] = useState(50);

  const [hisSeverity, setHisSeverity] = useState("");
  const [hisNeId, setHisNeId] = useState("");
  const [hisKeyword, setHisKeyword] = useState("");
  const [hisTimeFrom, setHisTimeFrom] = useState("");
  const [hisTimeTo, setHisTimeTo] = useState("");
  const [hisPage, setHisPage] = useState(1);
  const [hisPageSize, setHisPageSize] = useState(50);

  const applyQuickRange = (hours: number) => {
    const now = new Date();
    const from = new Date(now.getTime() - hours * 3600 * 1000);
    setHisTimeFrom(toIsoNoMs(from));
    setHisTimeTo(toIsoNoMs(now));
    setHisPage(1);
  };

  const syncMutation = useMutation({
    mutationFn: async (domains: string[]) => apiPost<{ ok: boolean; jobs: unknown[] }>("/v1/ume/sync", { domains }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
      await queryClient.invalidateQueries({ queryKey: ["umeNE"] });
      await queryClient.invalidateQueries({ queryKey: ["umeCurrentAlarms"] });
      await queryClient.invalidateQueries({ queryKey: ["umeHistoryAlarms"] });
    },
  });

  const syncStatusQuery = useQuery({
    queryKey: ["umeSyncStatus", syncPage, syncPageSize],
    queryFn: () => fetchUmeSyncStatus({ page: syncPage, pageSize: syncPageSize }),
    staleTime: 5000,
  });
  const tokenStatusQuery = useQuery({
    queryKey: ["umeTokenStatus"],
    queryFn: fetchUmeTokenStatus,
    staleTime: 3000,
    refetchInterval: 5000,
  });
  const tokenRefreshMutation = useMutation({
    mutationFn: refreshUmeToken,
    onSuccess: async (res) => {
      if (!res?.ok) {
        setTokenOpError(String(res.error || res.error_kind || "token_refresh_failed"));
      } else {
        setTokenOpError("");
      }
      await queryClient.invalidateQueries({ queryKey: ["umeTokenStatus"] });
    },
    onError: (err) => {
      setTokenOpError(String(err));
    },
  });
  const tokenDisconnectMutation = useMutation({
    mutationFn: disconnectUmeToken,
    onSuccess: async (res) => {
      if (!res?.ok) {
        setTokenOpError(String(res.error || res.error_kind || "token_disconnect_failed"));
      } else {
        setTokenOpError("");
      }
      await queryClient.invalidateQueries({ queryKey: ["umeTokenStatus"] });
    },
    onError: (err) => {
      setTokenOpError(String(err));
    },
  });

  const tokenExpiresIn = Number(tokenStatusQuery.data?.expires_in_s || 0);
  const tokenLevel = !tokenStatusQuery.data?.has_token
    ? "down"
    : tokenExpiresIn < 15
      ? "down"
      : tokenExpiresIn < 60
        ? "unknown"
        : "up";
  const neQuery = useQuery({
    queryKey: ["umeNE", neKeyword, nePage, nePageSize],
    queryFn: () => fetchUmeNe({ keyword: neKeyword, page: nePage, pageSize: nePageSize }),
    staleTime: 5000,
  });
  const currentQuery = useQuery({
    queryKey: ["umeCurrentAlarms", curSeverity, curCleared, curNeId, curKeyword, curPage, curPageSize],
    queryFn: () =>
      fetchUmeCurrentAlarms({
        severity: curSeverity,
        isCleared: curCleared,
        neId: curNeId,
        keyword: curKeyword,
        page: curPage,
        pageSize: curPageSize,
      }),
    staleTime: 5000,
  });
  const historyQuery = useQuery({
    queryKey: ["umeHistoryAlarms", hisSeverity, hisNeId, hisKeyword, hisTimeFrom, hisTimeTo, hisPage, hisPageSize],
    queryFn: () =>
      fetchUmeHistoryAlarms({
        severity: hisSeverity,
        neId: hisNeId,
        keyword: hisKeyword,
        timeFrom: hisTimeFrom,
        timeTo: hisTimeTo,
        page: hisPage,
        pageSize: hisPageSize,
      }),
    staleTime: 5000,
  });

  return (
    <>
      <section className="cards">
        <article className="card card--full">
          <h3>UME Token 状态</h3>
          <div className="actions-row actions-row--inline">
            <span className={`conn-pill conn-pill--${tokenStatusQuery.data?.has_token ? "up" : "down"}`}>
              token: {tokenStatusQuery.data?.has_token ? "connected" : "disconnected"}
            </span>
            <span className={`conn-pill conn-pill--${tokenLevel}`}>
              expires_in: {typeof tokenStatusQuery.data?.expires_in_s === "number" ? `${tokenStatusQuery.data.expires_in_s}s` : "-"}
            </span>
            {tokenStatusQuery.data?.token_preview ? <span className="conn-pill">preview: {tokenStatusQuery.data.token_preview}</span> : null}
          </div>
          <div className="actions-row actions-row--inline">
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey: ["umeTokenStatus"] })}
              disabled={tokenStatusQuery.isFetching}
            >
              刷新状态
            </button>
            <button onClick={() => tokenRefreshMutation.mutate()} disabled={tokenRefreshMutation.isPending}>
              手动续期/登录
            </button>
            <button onClick={() => tokenDisconnectMutation.mutate()} disabled={tokenDisconnectMutation.isPending}>
              断开 token
            </button>
          </div>
          {(tokenOpError || tokenRefreshMutation.error || tokenDisconnectMutation.error) && (
            <div className="pill pill--high">
              操作失败: {tokenOpError || String(tokenRefreshMutation.error || tokenDisconnectMutation.error)}
            </div>
          )}
        </article>
        <article className="card card--full">
          <h3>UME 同步</h3>
          <div className="actions-row actions-row--inline">
            <button onClick={() => syncMutation.mutate(["inventory"])} disabled={syncMutation.isPending}>
              同步 Inventory
            </button>
            <button onClick={() => syncMutation.mutate(["alarms_current"])} disabled={syncMutation.isPending}>
              同步当前告警
            </button>
            <button onClick={() => syncMutation.mutate(["alarms_history"])} disabled={syncMutation.isPending}>
              同步历史告警
            </button>
            <button onClick={() => syncMutation.mutate(["inventory", "alarms_current", "alarms_history"])} disabled={syncMutation.isPending}>
              全量同步
            </button>
          </div>
          {syncMutation.error && <div className="pill pill--high">同步失败: {String(syncMutation.error)}</div>}
        </article>
      </section>

      <section className="panel">
        <h2>同步状态</h2>
        <div className="actions-row actions-row--inline">
          <button onClick={() => queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] })} disabled={syncStatusQuery.isFetching}>
            刷新
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>domain</th>
              <th>status</th>
              <th>pulled</th>
              <th>inserted</th>
              <th>updated</th>
              <th>started_at</th>
              <th>ended_at</th>
              <th>error</th>
            </tr>
          </thead>
          <tbody>
            {(syncStatusQuery.data?.items || []).map((x) => (
              <tr key={x.id}>
                <td>{x.id}</td>
                <td>{x.domain}</td>
                <td>{x.status}</td>
                <td>{x.pulled_count}</td>
                <td>{x.inserted_count}</td>
                <td>{x.updated_count}</td>
                <td>{formatSystemTime(x.started_at)}</td>
                <td>{x.ended_at ? formatSystemTime(x.ended_at) : "-"}</td>
                <td>{x.error_message || "-"}</td>
              </tr>
            ))}
            {!syncStatusQuery.isLoading && (syncStatusQuery.data?.items || []).length === 0 && (
              <tr>
                <td colSpan={9}>暂无同步记录</td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">
            共 {syncStatusQuery.data?.total || 0} 条 · 第 {syncPage}/
            {Math.max(1, Math.ceil(Math.max(0, Number(syncStatusQuery.data?.total || 0)) / Math.max(1, syncPageSize)))} 页
          </div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setSyncPage(Math.max(1, syncPage - 1))} disabled={syncPage <= 1}>
              上一页
            </button>
            <button
              className="pager__btn"
              onClick={() => setSyncPage(syncPage + 1)}
              disabled={syncPage >= Math.max(1, Math.ceil(Math.max(0, Number(syncStatusQuery.data?.total || 0)) / Math.max(1, syncPageSize)))}
            >
              下一页
            </button>
            <select
              className="pager__size"
              value={String(syncPageSize)}
              onChange={(e) => {
                setSyncPageSize(Number(e.target.value) || 20);
                setSyncPage(1);
              }}
            >
              <option value="20">20/页</option>
              <option value="50">50/页</option>
              <option value="100">100/页</option>
              <option value="200">200/页</option>
            </select>
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>网元清单</h2>
        <div className="filter-inline">
          <input value={neKeyword} placeholder="keyword(ne_id/ne_name/user_label/ip)" onChange={(e) => setNeKeyword(e.target.value)} />
          <button onClick={() => queryClient.invalidateQueries({ queryKey: ["umeNE"] })}>查询</button>
        </div>
        <table>
          <thead>
            <tr>
              <th>ne_id</th>
              <th>user_label</th>
              <th>ne_name</th>
              <th>ip</th>
              <th>type</th>
              <th>device_level</th>
              <th>host_name</th>
              <th>hw_ver</th>
              <th>last_seen</th>
            </tr>
          </thead>
          <tbody>
            {(neQuery.data?.items || []).map((x) => (
              <Fragment key={x.ne_id}>
                <tr>
                  <td>
                    <button
                      className="link-btn"
                      onClick={() => setExpandedNeId(expandedNeId === x.ne_id ? "" : x.ne_id)}
                      title={expandedNeId === x.ne_id ? "收起详情" : "展开详情"}
                    >
                      {x.ne_id}
                    </button>
                  </td>
                  <td>{x.user_label}</td>
                  <td>{x.ne_name}</td>
                  <td>{x.ip_address}</td>
                  <td>{x.ne_type}</td>
                  <td>{x.device_level || "-"}</td>
                  <td>{x.host_name || "-"}</td>
                  <td>{x.hardware_version || "-"}</td>
                  <td>{x.last_seen_at ? formatSystemTime(x.last_seen_at) : "-"}</td>
                </tr>
                {expandedNeId === x.ne_id ? (
                  <tr>
                    <td colSpan={9}>
                      <div style={{ fontSize: 12, display: "grid", gridTemplateColumns: "repeat(3, minmax(180px, 1fr))", gap: 8 }}>
                        <div>consistent_state: {x.consistent_state || "-"}</div>
                        <div>admin_status: {x.admin_status || "-"}</div>
                        <div>connection_status: {x.connection_status || "-"}</div>
                        <div>maintain_status: {x.maintain_status || "-"}</div>
                        <div>address_type: {x.address_type || "-"}</div>
                        <div>location: {x.location || "-"}</div>
                        <div>loopback: {x.loopback || "-"}</div>
                        <div>net_mask: {x.net_mask || "-"}</div>
                        <div>mac: {x.mac || "-"}</div>
                        <div>interface_version: {x.interface_version || "-"}</div>
                        <div>create_time: {x.create_time || "-"}</div>
                        <div>creator: {x.creator || "-"}</div>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            ))}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">
            共 {neQuery.data?.total || 0} 条 · 第 {nePage}/
            {Math.max(1, Math.ceil(Math.max(0, Number(neQuery.data?.total || 0)) / Math.max(1, nePageSize)))} 页
          </div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setNePage(Math.max(1, nePage - 1))} disabled={nePage <= 1}>
              上一页
            </button>
            <button
              className="pager__btn"
              onClick={() => setNePage(nePage + 1)}
              disabled={nePage >= Math.max(1, Math.ceil(Math.max(0, Number(neQuery.data?.total || 0)) / Math.max(1, nePageSize)))}
            >
              下一页
            </button>
            <select
              className="pager__size"
              value={String(nePageSize)}
              onChange={(e) => {
                setNePageSize(Number(e.target.value) || 50);
                setNePage(1);
              }}
            >
              <option value="20">20/页</option>
              <option value="50">50/页</option>
              <option value="100">100/页</option>
              <option value="200">200/页</option>
              <option value="500">500/页</option>
            </select>
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>当前告警</h2>
        <div className="filter-inline">
          <input value={curKeyword} placeholder="keyword" onChange={(e) => setCurKeyword(e.target.value)} />
          <input value={curNeId} placeholder="ne_id" onChange={(e) => setCurNeId(e.target.value)} />
          <select value={curSeverity} onChange={(e) => setCurSeverity(e.target.value)}>
            <option value="">全部级别</option>
            <option value="critical">critical</option>
            <option value="major">major</option>
            <option value="minor">minor</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
          </select>
          <select value={curCleared} onChange={(e) => setCurCleared(e.target.value)}>
            <option value="">is_cleared: all</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </div>
        <table>
          <thead>
            <tr>
              <th>time_created</th>
              <th>severity</th>
              <th>user_label</th>
              <th>object_name</th>
              <th>cause</th>
            </tr>
          </thead>
          <tbody>
            {(currentQuery.data?.items || []).map((x) => (
              <tr key={x.alarm_key}>
                <td>{x.time_created}</td>
                <td>{x.perceived_severity}</td>
                <td>
                  <button
                    className="link-btn"
                    onClick={() => {
                      setCurNeId(x.ne_id || "");
                      setCurKeyword(x.user_label || x.ne_name || x.ne_id || "");
                      setCurPage(1);
                    }}
                  >
                    {x.user_label || x.ne_name || x.ne_id}
                  </button>
                </td>
                <td>{x.object_name}</td>
                <td>{x.native_probable_cause}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">
            共 {currentQuery.data?.total || 0} 条 · 第 {curPage}/
            {Math.max(1, Math.ceil(Math.max(0, Number(currentQuery.data?.total || 0)) / Math.max(1, curPageSize)))} 页
          </div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setCurPage(Math.max(1, curPage - 1))} disabled={curPage <= 1}>
              上一页
            </button>
            <button
              className="pager__btn"
              onClick={() => setCurPage(curPage + 1)}
              disabled={curPage >= Math.max(1, Math.ceil(Math.max(0, Number(currentQuery.data?.total || 0)) / Math.max(1, curPageSize)))}
            >
              下一页
            </button>
            <select
              className="pager__size"
              value={String(curPageSize)}
              onChange={(e) => {
                setCurPageSize(Number(e.target.value) || 50);
                setCurPage(1);
              }}
            >
              <option value="50">50/页</option>
              <option value="100">100/页</option>
              <option value="200">200/页</option>
              <option value="500">500/页</option>
            </select>
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>历史告警</h2>
        <div className="filter-inline">
          <input value={hisKeyword} placeholder="keyword" onChange={(e) => setHisKeyword(e.target.value)} />
          <input value={hisNeId} placeholder="ne_id" onChange={(e) => setHisNeId(e.target.value)} />
          <input value={hisTimeFrom} placeholder="time_from(ISO8601)" onChange={(e) => setHisTimeFrom(e.target.value)} />
          <input value={hisTimeTo} placeholder="time_to(ISO8601)" onChange={(e) => setHisTimeTo(e.target.value)} />
          <select value={hisSeverity} onChange={(e) => setHisSeverity(e.target.value)}>
            <option value="">全部级别</option>
            <option value="critical">critical</option>
            <option value="major">major</option>
            <option value="minor">minor</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
          </select>
        </div>
        <div className="actions-row actions-row--inline">
          <button onClick={() => applyQuickRange(1)}>最近1小时</button>
          <button onClick={() => applyQuickRange(24)}>最近24小时</button>
          <button onClick={() => applyQuickRange(24 * 7)}>最近7天</button>
          <button
            onClick={() => {
              setHisTimeFrom("");
              setHisTimeTo("");
              setHisPage(1);
            }}
          >
            清空时间
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>time_created</th>
              <th>severity</th>
              <th>user_label</th>
              <th>object_name</th>
              <th>cause</th>
            </tr>
          </thead>
          <tbody>
            {(historyQuery.data?.items || []).map((x) => (
              <tr key={x.alarm_key}>
                <td>{x.time_created}</td>
                <td>{x.perceived_severity}</td>
                <td>
                  <button
                    className="link-btn"
                    onClick={() => {
                      setCurNeId(x.ne_id || "");
                      setCurKeyword(x.user_label || x.ne_name || x.ne_id || "");
                      setCurPage(1);
                    }}
                  >
                    {x.user_label || x.ne_name || x.ne_id}
                  </button>
                </td>
                <td>{x.object_name}</td>
                <td>{x.native_probable_cause}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">
            共 {historyQuery.data?.total || 0} 条 · 第 {hisPage}/
            {Math.max(1, Math.ceil(Math.max(0, Number(historyQuery.data?.total || 0)) / Math.max(1, hisPageSize)))} 页
          </div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setHisPage(Math.max(1, hisPage - 1))} disabled={hisPage <= 1}>
              上一页
            </button>
            <button
              className="pager__btn"
              onClick={() => setHisPage(hisPage + 1)}
              disabled={hisPage >= Math.max(1, Math.ceil(Math.max(0, Number(historyQuery.data?.total || 0)) / Math.max(1, hisPageSize)))}
            >
              下一页
            </button>
            <select
              className="pager__size"
              value={String(hisPageSize)}
              onChange={(e) => {
                setHisPageSize(Number(e.target.value) || 50);
                setHisPage(1);
              }}
            >
              <option value="50">50/页</option>
              <option value="100">100/页</option>
              <option value="200">200/页</option>
              <option value="500">500/页</option>
            </select>
          </div>
        </div>
      </section>
    </>
  );
}
