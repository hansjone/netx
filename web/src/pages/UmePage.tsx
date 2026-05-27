import { Fragment, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiPost,
  disconnectUmeToken,
  fetchUmeCurrentAlarms,
  fetchUmeNe,
  cancelUmeAlarmSubscription,
  clearLocalUmeAlarmSubscription,
  establishUmeAlarmSubscription,
  fetchUmeAlarmSubscriptionStatus,
  fetchUmeSyncStatus,
  fetchUmeTokenStatus,
  refreshUmeToken,
} from "../services/api";
import { formatSystemTime } from "../utils/time";

export type UmePageProps = {
  /** Optional toasts from App shell (fixed bottom-right). */
  toastOk?: (message: string) => void;
  toastError?: (message: string) => void;
};

const UME_ALARM_SUBSCRIPTION_HELP =
  "订阅需手动建立/取消；建立后（或重启后若库中仍有有效订阅）后台会自动连接 WSS 接收实时告警。后台任务 alarms_current_ws_consumer 的 pause/resume 会停止或恢复 WSS 连接（不会取消 UME 订阅）。";

const RUNTIME_INTERVAL_LABEL_EN: Record<string, string> = {
  未启用: "disabled",
  实时: "realtime",
};

function runtimeIntervalLabel(label?: string | null): string {
  const raw = String(label ?? "").trim();
  if (!raw) return "—";
  return RUNTIME_INTERVAL_LABEL_EN[raw] ?? raw;
}

function HelpHint({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (ev: MouseEvent) => {
      if (!rootRef.current?.contains(ev.target as Node)) setOpen(false);
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="help-hint" ref={rootRef}>
      <button
        type="button"
        className="help-hint__trigger"
        aria-label="帮助说明"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open ? (
        <div className="help-hint__popover" role="tooltip">
          {text}
        </div>
      ) : null}
    </span>
  );
}

export function UmePage({ toastOk, toastError }: UmePageProps) {
  const queryClient = useQueryClient();
  const [tokenOpError, setTokenOpError] = useState("");
  const [runtimeTaskError, setRuntimeTaskError] = useState("");
  const [subscriptionOpError, setSubscriptionOpError] = useState("");
  const [syncPage, setSyncPage] = useState(1);
  const [syncPageSize, setSyncPageSize] = useState(20);
  const [neKeyword, setNeKeyword] = useState("");
  const [nePage, setNePage] = useState(1);
  const [nePageSize, setNePageSize] = useState(50);
  const [expandedNeId, setExpandedNeId] = useState("");

  const [curSeverity, setCurSeverity] = useState("");
  const [curCleared, setCurCleared] = useState("");
  const [curHostName, setCurHostName] = useState("");
  const [curKeyword, setCurKeyword] = useState("");
  const [curPage, setCurPage] = useState(1);
  const [curPageSize, setCurPageSize] = useState(50);

  const syncMutation = useMutation({
    mutationFn: async (domains: string[]) => apiPost<{ ok: boolean; jobs: unknown[] }>("/v1/ume/sync", { domains }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
      await queryClient.invalidateQueries({ queryKey: ["umeNE"] });
      await queryClient.invalidateQueries({ queryKey: ["umeCurrentAlarms"] });
    },
  });

  const syncStatusQuery = useQuery({
    queryKey: ["umeSyncStatus", syncPage, syncPageSize],
    queryFn: () => fetchUmeSyncStatus({ page: syncPage, pageSize: syncPageSize }),
    staleTime: 5000,
    refetchInterval: 5000,
  });
  const tokenStatusQuery = useQuery({
    queryKey: ["umeTokenStatus"],
    queryFn: fetchUmeTokenStatus,
    staleTime: 3000,
    refetchInterval: 5000,
  });
  const tokenRefreshMutation = useMutation({
    mutationFn: refreshUmeToken,
    onMutate: () => {
      setTokenOpError("");
    },
    onSuccess: async (res) => {
      if (!res?.ok) {
        const msg = String(res.error || res.error_kind || "token_refresh_failed");
        setTokenOpError(msg);
        toastError?.(msg);
      } else {
        setTokenOpError("");
        toastOk?.(res.changed ? "续期成功，token 已更新" : "续期完成（与此前一致）");
      }
      await queryClient.invalidateQueries({ queryKey: ["umeTokenStatus"] });
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
    },
    onError: (err) => {
      const msg = String(err);
      setTokenOpError(msg);
      toastError?.(msg);
    },
  });
  const subscriptionStatusQuery = useQuery({
    queryKey: ["umeAlarmSubscription"],
    queryFn: fetchUmeAlarmSubscriptionStatus,
    staleTime: 3000,
    refetchInterval: 5000,
  });
  const confirmClearLocalSubscription = (hint?: string) =>
    window.confirm(
      `${hint || "UME 侧告警订阅已不存在或已过期（服务器订阅已丢失）。"}\n\n是否清除本地订阅记录？清除后可点击「建立告警订阅」重新订阅。`,
    );

  const subscriptionEstablishMutation = useMutation({
    mutationFn: (opts?: { forceReestablish?: boolean }) => establishUmeAlarmSubscription(opts),
    onMutate: () => setSubscriptionOpError(""),
    onSuccess: async (res) => {
      if (!res?.active) {
        const msg = "建立订阅未返回有效 id/uri";
        setSubscriptionOpError(msg);
        toastError?.(msg);
      } else {
        setSubscriptionOpError("");
        toastOk?.(
          res.already_exists
            ? "订阅已存在，未重复建立（WSS 将保持/恢复连接）"
            : "告警订阅已建立，WSS 将自动连接",
        );
      }
      await queryClient.invalidateQueries({ queryKey: ["umeAlarmSubscription"] });
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
    },
    onError: (err) => {
      const msg = String(err);
      setSubscriptionOpError(msg);
      toastError?.(msg);
    },
  });
  const subscriptionClearLocalMutation = useMutation({
    mutationFn: clearLocalUmeAlarmSubscription,
    onMutate: () => setSubscriptionOpError(""),
    onSuccess: async () => {
      setSubscriptionOpError("");
      toastOk?.("已清除本地订阅记录，可重新建立订阅");
      await queryClient.invalidateQueries({ queryKey: ["umeAlarmSubscription"] });
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
    },
    onError: (err) => {
      const msg = String(err);
      setSubscriptionOpError(msg);
      toastError?.(msg);
    },
  });

  const subscriptionCancelMutation = useMutation({
    mutationFn: (opts?: { forceClearLocal?: boolean }) => cancelUmeAlarmSubscription(opts),
    onMutate: () => setSubscriptionOpError(""),
    onSuccess: async (res) => {
      if (res?.needs_local_cleanup) {
        const msg =
          res.message ||
          "UME 侧告警订阅已丢失。请确认是否清除本地订阅记录后重新订阅。";
        if (confirmClearLocalSubscription(msg)) {
          subscriptionCancelMutation.mutate({ forceClearLocal: true });
          return;
        }
        setSubscriptionOpError(msg);
        toastError?.(msg);
        await queryClient.invalidateQueries({ queryKey: ["umeAlarmSubscription"] });
        await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
        return;
      }
      if (res?.active) {
        const msg = "取消订阅失败：UME 侧订阅可能仍存在，请查看错误详情后重试";
        setSubscriptionOpError(msg);
        toastError?.(msg);
      } else {
        setSubscriptionOpError("");
        toastOk?.(res?.ume_already_missing ? "本地已清除（UME 侧订阅此前已不存在）" : "告警订阅已取消");
      }
      await queryClient.invalidateQueries({ queryKey: ["umeAlarmSubscription"] });
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
    },
    onError: (err) => {
      const msg = String(err);
      setSubscriptionOpError(msg);
      toastError?.(msg);
    },
  });

  const tokenDisconnectMutation = useMutation({
    mutationFn: disconnectUmeToken,
    onMutate: () => {
      setTokenOpError("");
    },
    onSuccess: async (res) => {
      if (!res?.ok) {
        const msg = String(res.error || res.error_kind || "token_disconnect_failed");
        setTokenOpError(msg);
        toastError?.(msg);
      } else {
        setTokenOpError("");
        toastOk?.("已断开 UME token");
      }
      await queryClient.invalidateQueries({ queryKey: ["umeTokenStatus"] });
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
    },
    onError: (err) => {
      const msg = String(err);
      setTokenOpError(msg);
      toastError?.(msg);
    },
  });

  const tokenExpiresIn = Number(tokenStatusQuery.data?.expires_in_s || 0);
  const hasToken = Boolean(tokenStatusQuery.data?.has_token);
  const tokenNeedsRenewal = hasToken && tokenExpiresIn <= 0;
  const tokenLevel = !hasToken
    ? "down"
    : tokenNeedsRenewal
      ? "warn"
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
    queryKey: ["umeCurrentAlarms", curSeverity, curCleared, curHostName, curKeyword, curPage, curPageSize],
    queryFn: () =>
      fetchUmeCurrentAlarms({
        severity: curSeverity,
        isCleared: curCleared,
        hostName: curHostName,
        keyword: curKeyword,
        page: curPage,
        pageSize: curPageSize,
      }),
    staleTime: 5000,
  });
  const runningTasks = (syncStatusQuery.data?.items || []).filter((x) => String(x.status || "").toLowerCase() === "running");
  const runtimeTasks = syncStatusQuery.data?.runtime_tasks || [];
  const alarmSub =
    subscriptionStatusQuery.data ??
    syncStatusQuery.data?.alarm_subscription ??
    ({ active: false } as const);
  const wsConsumer = runtimeTasks.find((t) => t.task === "alarms_current_ws_consumer");
  const wsConn = subscriptionStatusQuery.data?.ws_connection;
  const wsState = String(wsConn?.state || "");
  const wsPaused = Boolean(wsConsumer?.paused);
  const wsLabel =
    wsConn?.label ||
    (wsPaused ? "已暂停" : wsConsumer?.last_error || wsConsumer?.status || "-");
  const subscriptionActive = Boolean(alarmSub.active);
  const serverSubLost = Boolean(
    subscriptionStatusQuery.data?.server_subscription_lost ?? alarmSub.server_subscription_lost,
  );
  const serverSubLostReason = String(
    subscriptionStatusQuery.data?.server_subscription_lost_reason ??
      alarmSub.server_subscription_lost_reason ??
      "",
  );
  const currentAlarmsMode =
    subscriptionStatusQuery.data?.current_alarms_mode ?? alarmSub.current_alarms_mode ?? "rest";
  const scheduledSyncSkipped = Boolean(
    subscriptionStatusQuery.data?.scheduled_sync_skipped ?? alarmSub.scheduled_sync_skipped,
  );
  const wsPillLevel =
    serverSubLost || wsState === "subscription_lost"
      ? "warn"
      : wsPaused || wsState === "paused"
        ? "warn"
        : wsState === "connected"
          ? "up"
          : wsState === "connecting" || wsState === "reconnecting" || wsState === "waiting_token"
            ? "warn"
            : wsState === "no_subscription" || wsState === "disconnected" || wsState === "error" || wsState === "init"
              ? "down"
              : String(wsConsumer?.last_error || "").includes("connected")
                ? "up"
                : "unknown";
  const wsLogs = [...(subscriptionStatusQuery.data?.ws_logs ?? alarmSub.ws_logs ?? [])].reverse();
  const subPending =
    subscriptionEstablishMutation.isPending ||
    subscriptionCancelMutation.isPending ||
    subscriptionClearLocalMutation.isPending;

  const runtimeTaskMutation = useMutation({
    mutationFn: async (vars: { task: string; action: "pause" | "resume" }) =>
      apiPost<{ ok: boolean }>(
        `/v1/ume/runtime/tasks/${encodeURIComponent(vars.task)}/${vars.action}`,
        {},
      ),
    onMutate: () => {
      setRuntimeTaskError("");
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] });
    },
    onError: (err) => {
      setRuntimeTaskError(String(err));
    },
  });

  return (
    <>
      <section className="cards">
        <article className="card card--full">
          <h3>UME Token 状态</h3>
          <div className="actions-row actions-row--inline">
            <span
              className={`conn-pill conn-pill--${!hasToken ? "down" : tokenNeedsRenewal ? "warn" : "up"}`}
              title={
                tokenNeedsRenewal
                  ? "库中 token 缺少有效过期时间或已过期，请点「手动续期/登录」或等待后台自动续期"
                  : undefined
              }
            >
              token: {!hasToken ? "disconnected" : tokenNeedsRenewal ? "connected (需续期)" : "connected"}
            </span>
            <span className={`conn-pill conn-pill--${tokenLevel}`}>
              expires_in:{" "}
              {typeof tokenStatusQuery.data?.expires_in_s === "number"
                ? tokenNeedsRenewal
                  ? "需续期 / 0s"
                  : `${tokenStatusQuery.data.expires_in_s}s`
                : "-"}
            </span>
            {tokenStatusQuery.data?.token_preview ? <span className="conn-pill">preview: {tokenStatusQuery.data.token_preview}</span> : null}
          </div>
          <div className="actions-row actions-row--inline">
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey: ["umeTokenStatus"] })}
              disabled={tokenStatusQuery.isFetching}
            >
              {tokenStatusQuery.isFetching ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  刷新中…
                </>
              ) : (
                "刷新状态"
              )}
            </button>
            <button
              type="button"
              onClick={() => tokenRefreshMutation.mutate()}
              disabled={tokenRefreshMutation.isPending || tokenDisconnectMutation.isPending}
            >
              {tokenRefreshMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  续期中…
                </>
              ) : (
                "手动续期/登录"
              )}
            </button>
            <button
              type="button"
              onClick={() => tokenDisconnectMutation.mutate()}
              disabled={tokenRefreshMutation.isPending || tokenDisconnectMutation.isPending}
            >
              {tokenDisconnectMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  断开中…
                </>
              ) : (
                "断开 token"
              )}
            </button>
          </div>
          {(tokenOpError || tokenRefreshMutation.error || tokenDisconnectMutation.error) && (
            <div className="pill pill--high">
              操作失败: {tokenOpError || String(tokenRefreshMutation.error || tokenDisconnectMutation.error)}
            </div>
          )}
        </article>
        <article className="card card--full">
          <h3 className="card-title-with-hint">
            UME 告警订阅（WebSocket）
            <HelpHint text={UME_ALARM_SUBSCRIPTION_HELP} />
          </h3>
          <div className="actions-row actions-row--inline">
            <span className={`conn-pill conn-pill--${subscriptionActive ? "up" : "down"}`}>
              订阅: {subscriptionActive ? "已建立" : "未建立"}
            </span>
            {subscriptionActive && alarmSub.subscription_id ? (
              <span className="conn-pill" title={alarmSub.wss_uri || ""}>
                id: {String(alarmSub.subscription_id).slice(0, 12)}…
              </span>
            ) : null}
            {subscriptionActive || wsConsumer ? (
              <span
                className={`conn-pill conn-pill--${wsPillLevel}`}
                title={wsConn?.detail || wsConsumer?.last_error || alarmSub.wss_uri || ""}
              >
                WSS: {wsLabel}
              </span>
            ) : null}
          </div>
          {scheduledSyncSkipped && !serverSubLost ? (
            <div className="pill pill--low" style={{ marginTop: 8 }}>
              当前告警由 WSS 实时维护（模式: {currentAlarmsMode}），定时 REST 全量同步已暂停。需要全量对账时请使用下方「同步当前告警」。
            </div>
          ) : null}
          {serverSubLost ? (
            <div className="pill pill--medium" style={{ marginTop: 8 }}>
              UME 服务器侧告警订阅已丢失或已过期，本地记录可能仍显示「已建立」。请确认清除本地记录后重新订阅。
              {serverSubLostReason ? (
                <div className="muted" style={{ marginTop: 6, fontSize: 12, wordBreak: "break-word" }}>
                  {serverSubLostReason.slice(0, 280)}
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="actions-row actions-row--inline">
            <button
              type="button"
              onClick={() => {
                if (serverSubLost && subscriptionActive) {
                  if (
                    window.confirm(
                      "将清除本地订阅记录并在 UME 上重新建立告警订阅，是否继续？",
                    )
                  ) {
                    subscriptionEstablishMutation.mutate({ forceReestablish: true });
                  }
                  return;
                }
                subscriptionEstablishMutation.mutate();
              }}
              disabled={subPending || (!serverSubLost && subscriptionActive) || !hasToken}
              title={
                !hasToken
                  ? "请先登录 UME token"
                  : serverSubLost
                    ? "清除本地失效记录并重新向 UME 建立订阅"
                    : undefined
              }
            >
              {subscriptionEstablishMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {serverSubLost ? "重新订阅…" : "建立订阅…"}
                </>
              ) : serverSubLost ? (
                "清除本地并重新订阅"
              ) : (
                "建立告警订阅"
              )}
            </button>
            {serverSubLost && subscriptionActive ? (
              <button
                type="button"
                onClick={() => {
                  if (confirmClearLocalSubscription()) {
                    subscriptionClearLocalMutation.mutate();
                  }
                }}
                disabled={subPending}
              >
                {subscriptionClearLocalMutation.isPending ? (
                  <>
                    <span className="inline-spinner" aria-hidden />
                    清除中…
                  </>
                ) : (
                  "仅清除本地记录"
                )}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => subscriptionCancelMutation.mutate()}
              disabled={subPending || !subscriptionActive}
            >
              {subscriptionCancelMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  取消订阅…
                </>
              ) : (
                "取消告警订阅"
              )}
            </button>
            <button
              type="button"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["umeAlarmSubscription"] })}
              disabled={subscriptionStatusQuery.isFetching}
            >
              刷新订阅状态
            </button>
          </div>
          {subscriptionOpError ? (
            <div className="pill pill--high">订阅操作失败: {subscriptionOpError}</div>
          ) : null}
          <div style={{ marginTop: 12 }}>
            <div className="actions-row actions-row--inline" style={{ marginTop: 0 }}>
              <strong style={{ fontSize: 13 }}>WSS 运行日志</strong>
              <span className="muted" style={{ fontSize: 12 }}>
                {wsConsumer?.last_run_at
                  ? `最近活动 ${formatSystemTime(String(wsConsumer.last_run_at))}`
                  : "自动刷新"}
              </span>
            </div>
            <div className="ws-log-panel" role="log" aria-live="polite">
              {wsLogs.length === 0 ? (
                <div className="ws-log-line ws-log-line--info">暂无日志（建立订阅并连接 WSS 后会出现连接、收包、重连等记录）</div>
              ) : (
                wsLogs.map((line, idx) => {
                  const lvl = String(line.level || "info").toLowerCase();
                  const cls =
                    lvl === "error" ? "ws-log-line--error" : lvl === "warning" ? "ws-log-line--warning" : "ws-log-line--info";
                  return (
                    <div key={`${line.ts}-${idx}`} className={`ws-log-line ${cls}`}>
                      <span className="ws-log-ts">{formatSystemTime(line.ts)}</span>
                      <span>[{lvl}] </span>
                      <span>{line.message}</span>
                      {line.subscription_id ? (
                        <span className="muted"> · {String(line.subscription_id).slice(0, 8)}…</span>
                      ) : null}
                    </div>
                  );
                })
              )}
            </div>
          </div>
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
            <button onClick={() => syncMutation.mutate(["inventory", "alarms_current"])} disabled={syncMutation.isPending}>
              全量同步
            </button>
          </div>
          {syncMutation.error && <div className="pill pill--high">同步失败: {String(syncMutation.error)}</div>}
        </article>
      </section>

      <section className="panel">
        <h2>当前任务</h2>
        <div className="actions-row actions-row--inline">
          <span className={`conn-pill conn-pill--${runningTasks.length > 0 ? "unknown" : "up"}`}>
            running: {runningTasks.length}
          </span>
          <button onClick={() => queryClient.invalidateQueries({ queryKey: ["umeSyncStatus"] })} disabled={syncStatusQuery.isFetching}>
            刷新
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>domain</th>
              <th>trigger_mode</th>
              <th>status</th>
              <th>started_at</th>
              <th>error</th>
            </tr>
          </thead>
          <tbody>
            {runningTasks.map((x) => (
              <tr key={`running-${x.id}`}>
                <td>{x.id}</td>
                <td>{x.domain}</td>
                <td>{x.trigger_mode}</td>
                <td>{x.status}</td>
                <td>{formatSystemTime(x.started_at)}</td>
                <td>{x.error_message || "-"}</td>
              </tr>
            ))}
            {!syncStatusQuery.isLoading && runningTasks.length === 0 && (
              <tr>
                <td colSpan={6}>当前无运行中的任务</td>
              </tr>
            )}
          </tbody>
        </table>
        <h3 style={{ marginTop: 12 }}>后台任务</h3>
        {runtimeTaskError ? (
          <div className="pill pill--high" style={{ marginBottom: 8 }}>
            后台任务操作失败: {runtimeTaskError}
          </div>
        ) : null}
        <table>
          <thead>
            <tr>
              <th>task</th>
              <th title="Configured sleep between loop iterations (clamped at process start)">interval</th>
              <th>status</th>
              <th title="Last finished sync for scheduled tasks; refreshed at each loop tick">
                last_run_at
              </th>
              <th>last_error</th>
              <th>actions</th>
            </tr>
          </thead>
          <tbody>
            {runtimeTasks.map((x) => (
              <tr key={`runtime-${x.task}`}>
                <td>{x.task}</td>
                <td title={typeof x.interval_s === "number" ? `${x.interval_s}s` : undefined}>
                  {runtimeIntervalLabel(x.interval_label)}
                </td>
                <td>{x.status}</td>
                <td>{x.last_run_at ? formatSystemTime(x.last_run_at) : "-"}</td>
                <td>{x.last_error || "-"}</td>
                <td>
                  {Boolean(x.paused) ? (
                    <button
                      type="button"
                      className="link-btn"
                      disabled={runtimeTaskMutation.isPending}
                      onClick={() => runtimeTaskMutation.mutate({ task: x.task, action: "resume" })}
                    >
                      resume
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="link-btn"
                      disabled={runtimeTaskMutation.isPending}
                      onClick={() => runtimeTaskMutation.mutate({ task: x.task, action: "pause" })}
                    >
                      pause
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!syncStatusQuery.isLoading && runtimeTasks.length === 0 && (
              <tr>
                <td colSpan={6}>暂无后台任务状态</td>
              </tr>
            )}
          </tbody>
        </table>
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
              <th title="全量对账删除条数：inventory 为网元行，alarms_current 为当前告警行，其余域多为 0">deleted</th>
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
                <td>{Number(x.deleted ?? 0)}</td>
                <td>{formatSystemTime(x.started_at)}</td>
                <td>{x.ended_at ? formatSystemTime(x.ended_at) : "-"}</td>
                <td>{x.error_message || "-"}</td>
              </tr>
            ))}
            {!syncStatusQuery.isLoading && (syncStatusQuery.data?.items || []).length === 0 && (
              <tr>
                <td colSpan={10}>暂无同步记录</td>
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
          <input value={neKeyword} placeholder="keyword(ne_id/ne_name/user_label/ip/host_name)" onChange={(e) => setNeKeyword(e.target.value)} />
          <button type="button" onClick={() => queryClient.invalidateQueries({ queryKey: ["umeNE"] })}>查询</button>
          <button
            type="button"
            title="清空 keyword，回到第 1 页"
            onClick={() => {
              setNeKeyword("");
              setNePage(1);
            }}
            disabled={!neKeyword.trim()}
          >
            清除筛选
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>ne_id</th>
              <th>user_label</th>
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
                  <td>{x.ip_address}</td>
                  <td>{x.ne_type}</td>
                  <td>{x.device_level || "-"}</td>
                  <td>{x.host_name || "-"}</td>
                  <td>{x.hardware_version || "-"}</td>
                  <td>{x.last_seen_at ? formatSystemTime(x.last_seen_at) : "-"}</td>
                </tr>
                {expandedNeId === x.ne_id ? (
                  <tr>
                    <td colSpan={8}>
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
          <input
            value={curKeyword}
            placeholder="keyword(告警键/原因/ne_name/host_name/ip 等)"
            onChange={(e) => setCurKeyword(e.target.value)}
          />
          <input value={curHostName} placeholder="host_name（含匹配）" onChange={(e) => setCurHostName(e.target.value)} />
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
          <button
            type="button"
            title="清空 keyword、host_name、级别、is_cleared，回到第 1 页"
            onClick={() => {
              setCurKeyword("");
              setCurHostName("");
              setCurSeverity("");
              setCurCleared("");
              setCurPage(1);
            }}
            disabled={!curKeyword.trim() && !curHostName.trim() && !curSeverity && !curCleared}
          >
            清除筛选
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>time_created</th>
              <th>severity</th>
              <th>ne_id</th>
              <th>host_name</th>
              <th>ne_type</th>
              <th>cause</th>
            </tr>
          </thead>
          <tbody>
            {(currentQuery.data?.items || []).map((x) => (
              <tr key={x.alarm_key}>
                <td>{formatSystemTime(x.time_created)}</td>
                <td>{x.perceived_severity}</td>
                <td>{x.ne_id}</td>
                <td>
                  {(x.host_name || "").trim() ? (
                    <button
                      className="link-btn"
                      type="button"
                      onClick={() => {
                        setCurHostName(x.host_name || "");
                        setCurKeyword("");
                        setCurPage(1);
                      }}
                      title="按该主机名筛选"
                    >
                      {x.host_name}
                    </button>
                  ) : (
                    <span className="muted" title="无 host_name（需先同步网元）">
                      -
                    </span>
                  )}
                </td>
                <td>{x.ne_type ?? ""}</td>
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
    </>
  );
}
