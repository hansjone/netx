import { Fragment, useState } from "react";
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
  fetchUmeAlarmKeywords,
  fetchUmeKeyAlertMonitor,
  fetchUmeNotificationIds,
  fetchUmeSyncStatus,
  fetchUmeTokenStatus,
  refreshUmeToken,
  upsertUmeKeyAlertRule,
  deleteUmeKeyAlertRule,
  updateUmeKeyAlertMonitorConfig,
} from "../services/api";
import { HelpHint } from "../components/HelpHint";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { UmeAlarmSubscriptionStatus } from "../types";
import { pageCount, runtimeIntervalLabel } from "../utils/display";
import { formatSystemTime } from "../utils/time";

export function UmePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
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
  const [nePanelOpen, setNePanelOpen] = useState(false);
  const [curSeverity, setCurSeverity] = useState("");
  const [curCleared, setCurCleared] = useState("");
  const [curHostName, setCurHostName] = useState("");
  const [curKeyword, setCurKeyword] = useState("");
  const [curPage, setCurPage] = useState(1);
  const [curPageSize, setCurPageSize] = useState(50);
  const [alarmsPanelOpen, setAlarmsPanelOpen] = useState(false);
  const [keyAlertMatchType, setKeyAlertMatchType] = useState<"notification_id" | "keyword">("keyword");
  const [keyAlertMatchValue, setKeyAlertMatchValue] = useState("");
  const [keyAlertLabel, setKeyAlertLabel] = useState("");
  const [keyAlertOpError, setKeyAlertOpError] = useState("");
  const [keyAlertKeywordHints, setKeyAlertKeywordHints] = useState<string[]>([]);
  const [keyAlertIdHints, setKeyAlertIdHints] = useState<
    Array<{ notification_id: string; native_probable_cause_sample: string }>
  >([]);

  const syncMutation = useMutation({
    mutationFn: async (domains: string[]) => apiPost<{ ok: boolean; jobs: unknown[] }>("/v1/ume/sync", { domains }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeNEAll });
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeCurrentAlarmsAll });
    },
  });

  const syncStatusQuery = useQuery({
    queryKey: queryKeys.umeSyncStatus(syncPage, syncPageSize),
    queryFn: () => fetchUmeSyncStatus({ page: syncPage, pageSize: syncPageSize }),
    staleTime: 5000,
    refetchInterval: 5000,
  });
  const tokenStatusQuery = useQuery({
    queryKey: queryKeys.umeTokenStatus,
    queryFn: fetchUmeTokenStatus,
    staleTime: 3000,
    refetchInterval: 5000,
  });
  const tokenRefreshMutation = useMutation({
    mutationFn: refreshUmeToken,
    onMutate: () => setTokenOpError(""),
    onSuccess: async (res) => {
      if (!res?.ok) {
        const msg = String(res.error || res.error_kind || "token_refresh_failed");
        setTokenOpError(msg);
        showError(msg);
      } else {
        setTokenOpError("");
        showOk(res.changed ? t("ume.token.renewOkChanged") : t("ume.token.renewOkSame"));
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeTokenStatus });
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
    },
    onError: (err) => {
      const msg = String(err);
      setTokenOpError(msg);
      showError(msg);
    },
  });
  const subscriptionStatusQuery = useQuery({
    queryKey: queryKeys.umeAlarmSubscription,
    queryFn: fetchUmeAlarmSubscriptionStatus,
    staleTime: 3000,
    refetchInterval: 5000,
  });
  const keyAlertMonitorQuery = useQuery({
    queryKey: queryKeys.umeKeyAlertMonitor,
    queryFn: fetchUmeKeyAlertMonitor,
    staleTime: 3000,
    refetchInterval: 5000,
  });

  const confirmClearLocalSubscription = (hint?: string) =>
    window.confirm(hint || t("ume.subscription.confirmClearDefault"));

  const subscriptionEstablishMutation = useMutation({
    mutationFn: (opts?: { forceReestablish?: boolean }) => establishUmeAlarmSubscription(opts),
    onMutate: () => setSubscriptionOpError(""),
    onSuccess: async (res) => {
      if (!res?.active) {
        const msg = t("ume.subscription.establishInvalid");
        setSubscriptionOpError(msg);
        showError(msg);
      } else {
        setSubscriptionOpError("");
        showOk(res.already_exists ? t("ume.subscription.establishExists") : t("ume.subscription.establishOk"));
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeAlarmSubscription });
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
    },
    onError: (err) => {
      const msg = String(err);
      setSubscriptionOpError(msg);
      showError(msg);
    },
  });
  const subscriptionClearLocalMutation = useMutation({
    mutationFn: clearLocalUmeAlarmSubscription,
    onMutate: () => setSubscriptionOpError(""),
    onSuccess: async () => {
      setSubscriptionOpError("");
      showOk(t("ume.subscription.clearLocalOk"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeAlarmSubscription });
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
    },
    onError: (err) => {
      const msg = String(err);
      setSubscriptionOpError(msg);
      showError(msg);
    },
  });
  const subscriptionCancelMutation = useMutation({
    mutationFn: (opts?: { forceClearLocal?: boolean }) => cancelUmeAlarmSubscription(opts),
    onMutate: () => setSubscriptionOpError(""),
    onSuccess: async (res) => {
      if (res?.needs_local_cleanup) {
        const msg = res.message || t("ume.subscription.serverLostMsg");
        if (confirmClearLocalSubscription(msg)) {
          subscriptionCancelMutation.mutate({ forceClearLocal: true });
          return;
        }
        setSubscriptionOpError(msg);
        showError(msg);
        await queryClient.invalidateQueries({ queryKey: queryKeys.umeAlarmSubscription });
        await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
        return;
      }
      if (res?.active) {
        const msg = t("ume.subscription.cancelFailed");
        setSubscriptionOpError(msg);
        showError(msg);
      } else {
        setSubscriptionOpError("");
        showOk(res?.ume_already_missing ? t("ume.subscription.cancelOkMissing") : t("ume.subscription.cancelOk"));
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeAlarmSubscription });
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
    },
    onError: (err) => {
      const msg = String(err);
      setSubscriptionOpError(msg);
      showError(msg);
    },
  });
  const tokenDisconnectMutation = useMutation({
    mutationFn: disconnectUmeToken,
    onMutate: () => setTokenOpError(""),
    onSuccess: async (res) => {
      if (!res?.ok) {
        const msg = String(res.error || res.error_kind || "token_disconnect_failed");
        setTokenOpError(msg);
        showError(msg);
      } else {
        setTokenOpError("");
        showOk(t("ume.token.disconnectOk"));
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeTokenStatus });
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
    },
    onError: (err) => {
      const msg = String(err);
      setTokenOpError(msg);
      showError(msg);
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
    queryKey: queryKeys.umeNE(neKeyword, nePage, nePageSize),
    queryFn: () => fetchUmeNe({ keyword: neKeyword, page: nePage, pageSize: nePageSize }),
    staleTime: 5000,
  });
  const currentQuery = useQuery({
    queryKey: queryKeys.umeCurrentAlarms(curSeverity, curCleared, curHostName, curKeyword, curPage, curPageSize),
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
  const alarmSub: UmeAlarmSubscriptionStatus =
    subscriptionStatusQuery.data ??
    syncStatusQuery.data?.alarm_subscription ??
    { active: false };
  const wsConsumer = runtimeTasks.find((x) => x.task === "alarms_current_ws_consumer");
  const wsConn = subscriptionStatusQuery.data?.ws_connection;
  const wsState = String(wsConn?.state || "");
  const wsPaused = Boolean(wsConsumer?.paused);
  const wsLabel =
    wsConn?.label ||
    (wsPaused ? t("ume.subscription.wssPaused") : wsConsumer?.last_error || wsConsumer?.status || t("common.empty"));
  const subscriptionActive = Boolean(alarmSub.active);
  const serverSubLost = Boolean(
    subscriptionStatusQuery.data?.server_subscription_lost ?? alarmSub.server_subscription_lost,
  );
  const serverSubLostReason = String(
    subscriptionStatusQuery.data?.server_subscription_lost_reason ?? alarmSub.server_subscription_lost_reason ?? "",
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
      apiPost<{ ok: boolean }>(`/v1/ume/runtime/tasks/${encodeURIComponent(vars.task)}/${vars.action}`, {}),
    onMutate: () => setRuntimeTaskError(""),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
    },
    onError: (err) => setRuntimeTaskError(String(err)),
  });

  const keyAlertAddMutation = useMutation({
    mutationFn: () =>
      upsertUmeKeyAlertRule({
        match_type: keyAlertMatchType,
        match_value: keyAlertMatchValue.trim(),
        label: keyAlertLabel.trim(),
        enabled: true,
      }),
    onMutate: () => setKeyAlertOpError(""),
    onSuccess: async () => {
      setKeyAlertMatchValue("");
      setKeyAlertLabel("");
      showOk(t("ume.keyAlert.addOk"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitor });
      await queryClient.invalidateQueries({ queryKey: queryKeys.integrationsStatus });
    },
    onError: (err) => {
      const msg = String(err);
      setKeyAlertOpError(msg);
      showError(msg);
    },
  });

  const keyAlertDeleteMutation = useMutation({
    mutationFn: (notificationId: string) => deleteUmeKeyAlertRule(notificationId),
    onMutate: () => setKeyAlertOpError(""),
    onSuccess: async () => {
      showOk(t("ume.keyAlert.deleteOk"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitor });
      await queryClient.invalidateQueries({ queryKey: queryKeys.integrationsStatus });
    },
    onError: (err) => {
      const msg = String(err);
      setKeyAlertOpError(msg);
      showError(msg);
    },
  });

  const keyAlertConfigMutation = useMutation({
    mutationFn: (forwardOnClear: boolean) => updateUmeKeyAlertMonitorConfig({ forward_on_clear: forwardOnClear }),
    onMutate: () => setKeyAlertOpError(""),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitor });
    },
    onError: (err) => {
      const msg = String(err);
      setKeyAlertOpError(msg);
      showError(msg);
    },
  });

  const syncTotal = Number(syncStatusQuery.data?.total || 0);
  const syncPages = pageCount(syncTotal, syncPageSize);
  const neTotal = Number(neQuery.data?.total || 0);
  const nePages = pageCount(neTotal, nePageSize);
  const curTotal = Number(currentQuery.data?.total || 0);
  const curPages = pageCount(curTotal, curPageSize);

  const keyAlertForwarder = keyAlertMonitorQuery.data?.forwarder;
  const keyAlertRules = keyAlertMonitorQuery.data?.rules || [];
  const keyAlertForwardOnClear = Boolean(keyAlertMonitorQuery.data?.config?.forward_on_clear);
  const oclawWsPill =
    !keyAlertForwarder?.enabled
      ? "unknown"
      : keyAlertForwarder.connected
        ? "up"
        : "down";

  const perPage = (n: number) => t("common.perPage", { n: String(n) });

  return (
    <>
      <section className="cards">
        <article className="card card--full">
          <h3>{t("ume.token.title")}</h3>
          <div className="actions-row actions-row--inline">
            <span
              className={`conn-pill conn-pill--${!hasToken ? "down" : tokenNeedsRenewal ? "warn" : "up"}`}
              title={tokenNeedsRenewal ? t("ume.token.renewHint") : undefined}
            >
              token:{" "}
              {!hasToken
                ? t("ume.token.disconnected")
                : tokenNeedsRenewal
                  ? t("ume.token.connectedRenew")
                  : t("ume.token.connected")}
            </span>
            <span className={`conn-pill conn-pill--${tokenLevel}`}>
              {t("ume.token.expiresIn")}:{" "}
              {typeof tokenStatusQuery.data?.expires_in_s === "number"
                ? tokenNeedsRenewal
                  ? t("ume.token.needsRenew")
                  : `${tokenStatusQuery.data.expires_in_s}s`
                : t("common.empty")}
            </span>
            {tokenStatusQuery.data?.token_preview ? (
              <span className="conn-pill">
                {t("ume.token.preview")}: {tokenStatusQuery.data.token_preview}
              </span>
            ) : null}
          </div>
          <div className="actions-row actions-row--inline">
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeTokenStatus })}
              disabled={tokenStatusQuery.isFetching}
            >
              {tokenStatusQuery.isFetching ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {t("common.refreshing")}
                </>
              ) : (
                t("ume.token.refreshStatus")
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
                  {t("ume.token.renewing")}
                </>
              ) : (
                t("ume.token.renewLogin")
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
                  {t("ume.token.disconnecting")}
                </>
              ) : (
                t("ume.token.disconnect")
              )}
            </button>
          </div>
          {(tokenOpError || tokenRefreshMutation.error || tokenDisconnectMutation.error) && (
            <div className="pill pill--high">
              {t("common.opFailed")}: {tokenOpError || String(tokenRefreshMutation.error || tokenDisconnectMutation.error)}
            </div>
          )}
        </article>

        <article className="card card--full">
          <h3 className="card-title-with-hint">
            {t("ume.subscription.title")}
            <HelpHint text={t("ume.subscription.help")} ariaLabel={t("common.help")} />
          </h3>
          <div className="actions-row actions-row--inline">
            <span className={`conn-pill conn-pill--${subscriptionActive ? "up" : "down"}`}>
              {t("ume.subscription.sub")}: {subscriptionActive ? t("ume.subscription.subActive") : t("ume.subscription.subInactive")}
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
              {t("ume.subscription.wssModeBanner", { mode: currentAlarmsMode })}
            </div>
          ) : null}
          {serverSubLost ? (
            <div className="pill pill--medium" style={{ marginTop: 8 }}>
              {t("ume.subscription.serverLostBanner")}
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
                  if (window.confirm(t("ume.subscription.confirmReestablish"))) {
                    subscriptionEstablishMutation.mutate({ forceReestablish: true });
                  }
                  return;
                }
                subscriptionEstablishMutation.mutate(undefined);
              }}
              disabled={subPending || (!serverSubLost && subscriptionActive) || !hasToken}
              title={
                !hasToken
                  ? t("ume.subscription.titleLoginFirst")
                  : serverSubLost
                    ? t("ume.subscription.titleReestablish")
                    : undefined
              }
            >
              {subscriptionEstablishMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {serverSubLost ? t("ume.subscription.reestablishing") : t("ume.subscription.establishing")}
                </>
              ) : serverSubLost ? (
                t("ume.subscription.reestablish")
              ) : (
                t("ume.subscription.establish")
              )}
            </button>
            {serverSubLost && subscriptionActive ? (
              <button
                type="button"
                onClick={() => {
                  if (confirmClearLocalSubscription()) subscriptionClearLocalMutation.mutate();
                }}
                disabled={subPending}
              >
                {subscriptionClearLocalMutation.isPending ? (
                  <>
                    <span className="inline-spinner" aria-hidden />
                    {t("ume.subscription.clearing")}
                  </>
                ) : (
                  t("ume.subscription.clearLocalOnly")
                )}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => subscriptionCancelMutation.mutate(undefined)}
              disabled={subPending || !subscriptionActive}
            >
              {subscriptionCancelMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {t("ume.subscription.cancelling")}
                </>
              ) : (
                t("ume.subscription.cancel")
              )}
            </button>
            <button
              type="button"
              onClick={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeAlarmSubscription })}
              disabled={subscriptionStatusQuery.isFetching}
            >
              {t("ume.subscription.refreshStatus")}
            </button>
          </div>
          {subscriptionOpError ? (
            <div className="pill pill--high">
              {t("ume.subscription.opFailed")}: {subscriptionOpError}
            </div>
          ) : null}
          <div style={{ marginTop: 12 }}>
            <div className="actions-row actions-row--inline" style={{ marginTop: 0 }}>
              <strong style={{ fontSize: 13 }}>{t("ume.subscription.wsLogs")}</strong>
              <span className="muted" style={{ fontSize: 12 }}>
                {wsConsumer?.last_run_at
                  ? t("ume.subscription.wsActivity", { time: formatSystemTime(String(wsConsumer.last_run_at)) })
                  : t("ume.subscription.wsAutoRefresh")}
              </span>
            </div>
            <div className="ws-log-panel" role="log" aria-live="polite">
              {wsLogs.length === 0 ? (
                <div className="ws-log-line ws-log-line--info">{t("ume.subscription.wsLogsEmpty")}</div>
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
          <h3 className="card-title-with-hint">
            {t("ume.keyAlert.title")}
            <HelpHint text={t("ume.keyAlert.help")} ariaLabel={t("common.help")} />
          </h3>
          <div className="actions-row actions-row--inline">
            <span className={`conn-pill conn-pill--${oclawWsPill}`}>
              {t("ume.keyAlert.ws")}:{" "}
              {!keyAlertForwarder?.enabled
                ? t("ume.keyAlert.wsDisabled")
                : keyAlertForwarder.connected
                  ? t("ume.keyAlert.wsConnected")
                  : t("ume.keyAlert.wsDisconnected")}
            </span>
            {keyAlertForwarder?.enabled ? (
              <>
                <span className="conn-pill">
                  {t("ume.keyAlert.publishedOk")}: {Number(keyAlertForwarder.published_ok || 0)}
                </span>
                <span className="conn-pill">
                  {t("ume.keyAlert.publishedFail")}: {Number(keyAlertForwarder.published_fail || 0)}
                </span>
                <span className="conn-pill">
                  {t("ume.keyAlert.queue")}: {Number(keyAlertForwarder.queue_size || 0)}
                </span>
              </>
            ) : null}
          </div>
          <div style={{ marginTop: 10 }}>
            <div
              className="actions-row actions-row--inline"
              style={{ flexWrap: "wrap", alignItems: "center", gap: 8 }}
            >
              <label className="muted">{t("ume.keyAlert.matchType")}</label>
              <select
                className="input"
                value={keyAlertMatchType}
                onChange={(e) => {
                  setKeyAlertMatchType(e.target.value === "keyword" ? "keyword" : "notification_id");
                  setKeyAlertKeywordHints([]);
                  setKeyAlertIdHints([]);
                }}
              >
                <option value="notification_id">{t("ume.keyAlert.matchNotificationId")}</option>
                <option value="keyword">{t("ume.keyAlert.matchKeyword")}</option>
              </select>
              <input
                className="input"
                style={{ flex: "1 1 180px", minWidth: 160 }}
                placeholder={
                  keyAlertMatchType === "keyword"
                    ? t("ume.keyAlert.matchValuePhKeyword")
                    : t("ume.keyAlert.matchValuePhNotificationId")
                }
                value={keyAlertMatchValue}
                onChange={(e) => setKeyAlertMatchValue(e.target.value)}
                list="ume-key-alert-match-options"
              />
              <input
                className="input"
                style={{ flex: "1 1 140px", minWidth: 120 }}
                placeholder={t("ume.keyAlert.labelPh")}
                value={keyAlertLabel}
                onChange={(e) => setKeyAlertLabel(e.target.value)}
              />
              <datalist id="ume-key-alert-match-options">
                {keyAlertMatchType === "keyword"
                  ? keyAlertKeywordHints.map((kw) => <option key={kw} value={kw} />)
                  : keyAlertIdHints.map((x) => (
                      <option key={x.notification_id} value={x.notification_id}>
                        {x.native_probable_cause_sample || x.notification_id}
                      </option>
                    ))}
              </datalist>
              <button
                type="button"
                onClick={async () => {
                  try {
                    if (keyAlertMatchType === "keyword") {
                      const resp = await fetchUmeAlarmKeywords(100);
                      const hints = (resp.items || []).map((x) => x.keyword).filter(Boolean);
                      setKeyAlertKeywordHints(hints);
                      if (hints.length && !keyAlertMatchValue.trim()) {
                        setKeyAlertMatchValue(hints[0]);
                      }
                    } else {
                      const resp = await fetchUmeNotificationIds(100);
                      const items = resp.items || [];
                      setKeyAlertIdHints(items);
                      if (items.length && !keyAlertMatchValue.trim()) {
                        const first = items[0];
                        setKeyAlertMatchValue(first.notification_id);
                        if (!keyAlertLabel.trim() && first.native_probable_cause_sample) {
                          setKeyAlertLabel(first.native_probable_cause_sample);
                        }
                      }
                    }
                  } catch (err) {
                    showError(String(err));
                  }
                }}
              >
                {t("ume.keyAlert.pickFromAlarms")}
              </button>
              <button
                type="button"
                onClick={() => keyAlertAddMutation.mutate()}
                disabled={
                  keyAlertAddMutation.isPending || !keyAlertMatchValue.trim() || !keyAlertLabel.trim()
                }
              >
                {keyAlertAddMutation.isPending ? t("ume.keyAlert.adding") : t("ume.keyAlert.add")}
              </button>
            </div>
            <div
              className="actions-row actions-row--inline"
              style={{ marginTop: 8, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
                <label className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <input
                    type="checkbox"
                    checked={keyAlertForwardOnClear}
                    disabled={keyAlertConfigMutation.isPending || keyAlertMonitorQuery.isLoading}
                    onChange={(e) => keyAlertConfigMutation.mutate(e.target.checked)}
                  />
                  {t("ume.keyAlert.forwardOnClear")}
                </label>
                <HelpHint
                  text={t("ume.keyAlert.forwardOnClearHelp")}
                  ariaLabel={t("common.help")}
                  align="start"
                />
              </span>
              <button
                type="button"
                onClick={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitor })}
                disabled={keyAlertMonitorQuery.isFetching}
              >
                {keyAlertMonitorQuery.isFetching ? t("common.refreshing") : t("common.refresh")}
              </button>
            </div>
          </div>
          {keyAlertOpError ? (
            <div className="pill pill--high" style={{ marginTop: 8 }}>
              {t("common.opFailed")}: {keyAlertOpError}
            </div>
          ) : null}
          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>{t("ume.keyAlert.colType")}</th>
                <th>{t("ume.keyAlert.colMatch")}</th>
                <th>{t("ume.keyAlert.colLabel")}</th>
                <th>{t("ume.keyAlert.colPublished")}</th>
                <th>{t("ume.keyAlert.colAttempts")}</th>
                <th>{t("ume.keyAlert.colLast")}</th>
                <th>{t("ume.keyAlert.colActions")}</th>
              </tr>
            </thead>
            <tbody>
              {keyAlertRules.map((rule) => (
                <tr key={rule.notification_id}>
                  <td>
                    {rule.match_type === "keyword"
                      ? t("ume.keyAlert.matchKeyword")
                      : t("ume.keyAlert.matchNotificationId")}
                  </td>
                  <td>{rule.match_value || rule.notification_id}</td>
                  <td>{rule.label || t("common.empty")}</td>
                  <td>{Number(rule.forward_stats?.published_ok || 0)}</td>
                  <td>{Number(rule.forward_stats?.attempts || 0)}</td>
                  <td>
                    {rule.forward_stats?.last_forwarded_at
                      ? formatSystemTime(rule.forward_stats.last_forwarded_at)
                      : t("common.empty")}
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(t("ume.keyAlert.confirmDelete"))) {
                          keyAlertDeleteMutation.mutate(rule.notification_id);
                        }
                      }}
                      disabled={keyAlertDeleteMutation.isPending}
                    >
                      {t("ume.keyAlert.delete")}
                    </button>
                  </td>
                </tr>
              ))}
              {!keyAlertMonitorQuery.isLoading && keyAlertRules.length === 0 ? (
                <tr>
                  <td colSpan={7}>{t("ume.keyAlert.emptyRules")}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </article>

        <article className="card card--full">
          <h3>{t("ume.sync.title")}</h3>
          <div className="actions-row actions-row--inline">
            <button onClick={() => syncMutation.mutate(["inventory"])} disabled={syncMutation.isPending}>
              {t("ume.sync.inventory")}
            </button>
            <button onClick={() => syncMutation.mutate(["alarms_current"])} disabled={syncMutation.isPending}>
              {t("ume.sync.alarmsCurrent")}
            </button>
            <button onClick={() => syncMutation.mutate(["inventory", "alarms_current"])} disabled={syncMutation.isPending}>
              {t("ume.sync.full")}
            </button>
          </div>
          {syncMutation.error && (
            <div className="pill pill--high">
              {t("ume.sync.failed")}: {String(syncMutation.error)}
            </div>
          )}
        </article>
      </section>

      <section className="panel">
        <h2>{t("ume.tasks.currentTitle")}</h2>
        <div className="actions-row actions-row--inline">
          <span className={`conn-pill conn-pill--${runningTasks.length > 0 ? "unknown" : "up"}`}>
            {t("ume.tasks.running")}: {runningTasks.length}
          </span>
          <button onClick={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll })} disabled={syncStatusQuery.isFetching}>
            {t("common.refresh")}
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
                <td>{x.error_message || t("common.empty")}</td>
              </tr>
            ))}
            {!syncStatusQuery.isLoading && runningTasks.length === 0 && (
              <tr>
                <td colSpan={6}>{t("ume.tasks.noRunning")}</td>
              </tr>
            )}
          </tbody>
        </table>

        <h3 style={{ marginTop: 12 }}>{t("ume.tasks.runtimeTitle")}</h3>
        {runtimeTaskError ? (
          <div className="pill pill--high" style={{ marginBottom: 8 }}>
            {t("ume.tasks.runtimeOpFailed")}: {runtimeTaskError}
          </div>
        ) : null}
        <table>
          <thead>
            <tr>
              <th>task</th>
              <th title={t("ume.tasks.intervalTitle")}>interval</th>
              <th>status</th>
              <th title={t("ume.tasks.lastRunTitle")}>last_run_at</th>
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
                <td>{x.last_run_at ? formatSystemTime(x.last_run_at) : t("common.empty")}</td>
                <td>{x.last_error || t("common.empty")}</td>
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
                <td colSpan={6}>{t("ume.tasks.noRuntime")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>{t("ume.syncStatus.title")}</h2>
        <div className="actions-row actions-row--inline">
          <button onClick={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll })} disabled={syncStatusQuery.isFetching}>
            {t("common.refresh")}
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
              <th title={t("ume.tasks.deletedTitle")}>deleted</th>
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
                <td>{x.ended_at ? formatSystemTime(x.ended_at) : t("common.empty")}</td>
                <td>{x.error_message || t("common.empty")}</td>
              </tr>
            ))}
            {!syncStatusQuery.isLoading && (syncStatusQuery.data?.items || []).length === 0 && (
              <tr>
                <td colSpan={10}>{t("ume.syncStatus.empty")}</td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">{t("common.pagerMeta", { total: syncTotal, page: syncPage, pages: syncPages })}</div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setSyncPage(Math.max(1, syncPage - 1))} disabled={syncPage <= 1}>
              {t("common.prevPage")}
            </button>
            <button className="pager__btn" onClick={() => setSyncPage(syncPage + 1)} disabled={syncPage >= syncPages}>
              {t("common.nextPage")}
            </button>
            <select
              className="pager__size"
              value={String(syncPageSize)}
              onChange={(e) => {
                setSyncPageSize(Number(e.target.value) || 20);
                setSyncPage(1);
              }}
            >
              <option value="20">{perPage(20)}</option>
              <option value="50">{perPage(50)}</option>
              <option value="100">{perPage(100)}</option>
              <option value="200">{perPage(200)}</option>
            </select>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("ume.ne.title")}</h2>
          <button type="button" className="link-btn" onClick={() => setNePanelOpen((x) => !x)}>
            {nePanelOpen ? t("ume.ne.hidePanel") : t("ume.ne.showPanel")}
          </button>
        </div>
        {nePanelOpen ? (
          <>
        <div className="filter-inline">
          <input value={neKeyword} placeholder={t("ume.ne.keywordPh")} onChange={(e) => setNeKeyword(e.target.value)} />
          <button type="button" onClick={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeNEAll })}>
            {t("common.query")}
          </button>
          <button
            type="button"
            title={t("ume.ne.clearTitle")}
            onClick={() => {
              setNeKeyword("");
              setNePage(1);
            }}
            disabled={!neKeyword.trim()}
          >
            {t("common.clearFilters")}
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
                      title={expandedNeId === x.ne_id ? t("ume.ne.collapse") : t("ume.ne.expand")}
                    >
                      {x.ne_id}
                    </button>
                  </td>
                  <td>{x.user_label}</td>
                  <td>{x.ip_address}</td>
                  <td>{x.ne_type}</td>
                  <td>{x.device_level || t("common.empty")}</td>
                  <td>{x.host_name || t("common.empty")}</td>
                  <td>{x.hardware_version || t("common.empty")}</td>
                  <td>{x.last_seen_at ? formatSystemTime(x.last_seen_at) : t("common.empty")}</td>
                </tr>
                {expandedNeId === x.ne_id ? (
                  <tr>
                    <td colSpan={8}>
                      <div style={{ fontSize: 12, display: "grid", gridTemplateColumns: "repeat(3, minmax(180px, 1fr))", gap: 8 }}>
                        <div>consistent_state: {x.consistent_state || t("common.empty")}</div>
                        <div>admin_status: {x.admin_status || t("common.empty")}</div>
                        <div>connection_status: {x.connection_status || t("common.empty")}</div>
                        <div>maintain_status: {x.maintain_status || t("common.empty")}</div>
                        <div>address_type: {x.address_type || t("common.empty")}</div>
                        <div>location: {x.location || t("common.empty")}</div>
                        <div>loopback: {x.loopback || t("common.empty")}</div>
                        <div>net_mask: {x.net_mask || t("common.empty")}</div>
                        <div>mac: {x.mac || t("common.empty")}</div>
                        <div>interface_version: {x.interface_version || t("common.empty")}</div>
                        <div>create_time: {x.create_time || t("common.empty")}</div>
                        <div>creator: {x.creator || t("common.empty")}</div>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            ))}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">{t("common.pagerMeta", { total: neTotal, page: nePage, pages: nePages })}</div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setNePage(Math.max(1, nePage - 1))} disabled={nePage <= 1}>
              {t("common.prevPage")}
            </button>
            <button className="pager__btn" onClick={() => setNePage(nePage + 1)} disabled={nePage >= nePages}>
              {t("common.nextPage")}
            </button>
            <select
              className="pager__size"
              value={String(nePageSize)}
              onChange={(e) => {
                setNePageSize(Number(e.target.value) || 50);
                setNePage(1);
              }}
            >
              <option value="20">{perPage(20)}</option>
              <option value="50">{perPage(50)}</option>
              <option value="100">{perPage(100)}</option>
              <option value="200">{perPage(200)}</option>
              <option value="500">{perPage(500)}</option>
            </select>
          </div>
        </div>
          </>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("ume.alarms.title")}</h2>
          <button type="button" className="link-btn" onClick={() => setAlarmsPanelOpen((x) => !x)}>
            {alarmsPanelOpen ? t("ume.alarms.hidePanel") : t("ume.alarms.showPanel")}
          </button>
        </div>
        {alarmsPanelOpen ? (
          <>
        <div className="filter-inline">
          <input value={curKeyword} placeholder={t("ume.alarms.keywordPh")} onChange={(e) => setCurKeyword(e.target.value)} />
          <input value={curHostName} placeholder={t("ume.alarms.hostNamePh")} onChange={(e) => setCurHostName(e.target.value)} />
          <select value={curSeverity} onChange={(e) => setCurSeverity(e.target.value)}>
            <option value="">{t("ume.alarms.allSeverity")}</option>
            <option value="critical">critical</option>
            <option value="major">major</option>
            <option value="minor">minor</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
          </select>
          <select value={curCleared} onChange={(e) => setCurCleared(e.target.value)}>
            <option value="">{t("ume.alarms.clearedAll")}</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <button
            type="button"
            title={t("ume.alarms.clearTitle")}
            onClick={() => {
              setCurKeyword("");
              setCurHostName("");
              setCurSeverity("");
              setCurCleared("");
              setCurPage(1);
            }}
            disabled={!curKeyword.trim() && !curHostName.trim() && !curSeverity && !curCleared}
          >
            {t("common.clearFilters")}
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
                      title={t("ume.alarms.filterByHost")}
                    >
                      {x.host_name}
                    </button>
                  ) : (
                    <span className="muted" title={t("ume.alarms.noHostName")}>
                      {t("common.empty")}
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
          <div className="pager__meta">{t("common.pagerMeta", { total: curTotal, page: curPage, pages: curPages })}</div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setCurPage(Math.max(1, curPage - 1))} disabled={curPage <= 1}>
              {t("common.prevPage")}
            </button>
            <button className="pager__btn" onClick={() => setCurPage(curPage + 1)} disabled={curPage >= curPages}>
              {t("common.nextPage")}
            </button>
            <select
              className="pager__size"
              value={String(curPageSize)}
              onChange={(e) => {
                setCurPageSize(Number(e.target.value) || 50);
                setCurPage(1);
              }}
            >
              <option value="50">{perPage(50)}</option>
              <option value="100">{perPage(100)}</option>
              <option value="200">{perPage(200)}</option>
              <option value="500">{perPage(500)}</option>
            </select>
          </div>
        </div>
          </>
        ) : null}
      </section>
    </>
  );
}
