import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Modal, Input } from "@heroui/react";
import {
  apiPost,
  applyUmeTopologyToFabric,
  disconnectUmeToken,
  fetchUmeNe,
  cancelUmeAlarmSubscription,
  clearLocalUmeAlarmSubscription,
  establishUmeAlarmSubscription,
  fetchUmeAlarmSubscriptionStatus,
  fetchUmeAlarmKeywords,
  fetchUmeInventoryNeTypes,
  fetchUmeKeyAlertMonitor,
  fetchUmeNotificationIds,
  fetchUmeSyncStatus,
  fetchUmeTokenStatus,
  fetchCliTargets,
  refreshUmeToken,
  upsertUmeKeyAlertRule,
  deleteUmeKeyAlertRule,
  patchUmeKeyAlertRule,
  updateUmeKeyAlertMonitorConfig,
} from "../services/api";
import { HelpHint } from "../components/HelpHint";
import { UmeCliConnectPanel } from "../components/UmeCliConnectPanel";
import { AppModalShell } from "../components/ui/AppModalShell";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { UmeAlarmSubscriptionStatus, UmeKeyAlertRuleItem } from "../types";
import { pageCount, runtimeIntervalLabel } from "../utils/display";
import { runtimeLastError, wsConnectionLabel } from "../utils/runtimeMessages";
import { formatSystemTime } from "../utils/time";
import { openOrFocusModule } from "../utils/moduleWindows";

export function UmePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [tokenOpError, setTokenOpError] = useState("");
  const [runtimeTaskError, setRuntimeTaskError] = useState("");
  const [subscriptionOpError, setSubscriptionOpError] = useState("");
  const [syncPage, setSyncPage] = useState(1);
  const [syncPageSize, setSyncPageSize] = useState(10);
  const [neKeyword, setNeKeyword] = useState("");
  const [nePage, setNePage] = useState(1);
  const [nePageSize, setNePageSize] = useState(10);
  const [expandedNeId, setExpandedNeId] = useState("");
  const [nePanelOpen, setNePanelOpen] = useState(false);
  const [syncStatusPanelOpen, setSyncStatusPanelOpen] = useState(false);
  const [keyAlertPanelOpen, setKeyAlertPanelOpen] = useState(false);
  const [keyAlertMatchType, setKeyAlertMatchType] = useState<"notification_id" | "keyword">("keyword");
  const [keyAlertMatchValue, setKeyAlertMatchValue] = useState("");
  const [keyAlertLabel, setKeyAlertLabel] = useState("");
  const [keyAlertOpError, setKeyAlertOpError] = useState("");
  const [keyAlertKeywordHints, setKeyAlertKeywordHints] = useState<string[]>([]);
  const [keyAlertIdHints, setKeyAlertIdHints] = useState<
    Array<{ notification_id: string; native_probable_cause_sample: string }>
  >([]);
  const [keyAlertPage, setKeyAlertPage] = useState(1);
  const [keyAlertPageSize, setKeyAlertPageSize] = useState(10);
  const [keyAlertFilterKeyword, setKeyAlertFilterKeyword] = useState("");
  const [keyAlertFilterEnabled, setKeyAlertFilterEnabled] = useState<"" | "true" | "false">("");
  const [keyAlertFilterMatchType, setKeyAlertFilterMatchType] = useState<"" | "notification_id" | "keyword">("");
  const [keyAlertNeTypes, setKeyAlertNeTypes] = useState<string[]>([]);
  const [keyAlertEditRule, setKeyAlertEditRule] = useState<UmeKeyAlertRuleItem | null>(null);
  const [keyAlertEditNeTypes, setKeyAlertEditNeTypes] = useState<string[]>([]);
  const [cliPanelOpen, setCliPanelOpen] = useState(false);

  const syncStatusQuery = useQuery({
    queryKey: queryKeys.umeSyncStatus(syncPage, syncPageSize),
    queryFn: () => fetchUmeSyncStatus({ page: syncPage, pageSize: syncPageSize }),
    staleTime: 10_000,
    // Panel open or active sync → keep snappy; otherwise idle cards only need a slow heartbeat.
    refetchInterval: (q) => {
      const items = q.state.data?.items || [];
      const running = items.some((x) => String(x.status || "").toLowerCase() === "running");
      if (syncStatusPanelOpen || running) return 5000;
      return 20_000;
    },
    refetchIntervalInBackground: false,
  });
  const tokenStatusQuery = useQuery({
    queryKey: queryKeys.umeTokenStatus,
    queryFn: fetchUmeTokenStatus,
    staleTime: 15_000,
    refetchInterval: 20_000,
    refetchIntervalInBackground: false,
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
    staleTime: 10_000,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });
  const keyAlertMonitorQuery = useQuery({
    queryKey: queryKeys.umeKeyAlertMonitor(
      keyAlertPage,
      keyAlertPageSize,
      keyAlertFilterKeyword,
      keyAlertFilterEnabled,
      keyAlertFilterMatchType,
    ),
    queryFn: () =>
      fetchUmeKeyAlertMonitor({
        page: keyAlertPage,
        pageSize: keyAlertPageSize,
        keyword: keyAlertFilterKeyword,
        enabled: keyAlertFilterEnabled,
        matchType: keyAlertFilterMatchType,
      }),
    staleTime: keyAlertPanelOpen ? 3000 : 15_000,
    refetchInterval: keyAlertPanelOpen ? 5000 : 20_000,
    refetchIntervalInBackground: false,
  });
  const keyAlertNeTypesQuery = useQuery({
    queryKey: queryKeys.umeInventoryNeTypes,
    queryFn: () => fetchUmeInventoryNeTypes(),
    staleTime: 60_000,
    enabled: keyAlertPanelOpen || Boolean(keyAlertEditRule),
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
  const cliTargetsQuery = useQuery({
    queryKey: queryKeys.cliTargets(neKeyword, nePage, nePageSize),
    queryFn: () =>
      fetchCliTargets({ source: "ume", keyword: neKeyword, page: nePage, pageSize: nePageSize }),
    enabled: nePanelOpen,
    staleTime: 5000,
  });
  const cliStatusByNeId = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of cliTargetsQuery.data?.items || []) {
      map.set(row.id, row.connect_status);
    }
    return map;
  }, [cliTargetsQuery.data]);

  const runningTasks = (syncStatusQuery.data?.items || []).filter((x) => String(x.status || "").toLowerCase() === "running");
  const runtimeTasks = syncStatusQuery.data?.runtime_tasks || [];
  const topologyFabric = syncStatusQuery.data?.topology_fabric;
  const needsFabricApply = Boolean(topologyFabric?.needs_apply);
  const applyFabricMut = useMutation({
    mutationFn: () => applyUmeTopologyToFabric(),
    onSuccess: async () => {
      showOk(t("ume.syncStatus.applyFabricOk"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll });
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
    },
    onError: (err) => showError(String(err)),
  });
  const alarmSub: UmeAlarmSubscriptionStatus =
    subscriptionStatusQuery.data ??
    syncStatusQuery.data?.alarm_subscription ??
    { active: false };
  const wsConsumer = runtimeTasks.find((x) => x.task === "alarms_current_ws_consumer");
  const wsConn = subscriptionStatusQuery.data?.ws_connection;
  const wsState = String(wsConn?.state || "");
  const wsPaused = Boolean(wsConsumer?.paused);
  const wsLabel =
    (wsPaused
      ? t("ume.subscription.wssPaused")
      : wsConnectionLabel(wsState, wsConn?.label, t)) ||
    runtimeLastError(wsConsumer?.last_error, t) ||
    wsConsumer?.status ||
    t("common.empty");
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
              : String(wsConsumer?.last_error || "").includes("connected") ||
                  String(wsConsumer?.last_error || "").includes("ws:connected")
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
        ne_types: keyAlertNeTypes,
      }),
    onMutate: () => setKeyAlertOpError(""),
    onSuccess: async () => {
      setKeyAlertMatchValue("");
      setKeyAlertLabel("");
      setKeyAlertNeTypes([]);
      showOk(t("ume.keyAlert.addOk"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitorAll });
      await queryClient.invalidateQueries({ queryKey: ["opsTasks"] });
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
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitorAll });
      await queryClient.invalidateQueries({ queryKey: ["opsTasks"] });
    },
    onError: (err) => {
      const msg = String(err);
      setKeyAlertOpError(msg);
      showError(msg);
    },
  });

  const keyAlertToggleMutation = useMutation({
    mutationFn: (vars: { ruleKey: string; enabled: boolean }) =>
      patchUmeKeyAlertRule(vars.ruleKey, { enabled: vars.enabled }),
    onMutate: () => setKeyAlertOpError(""),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitorAll });
    },
    onError: (err) => {
      const msg = String(err);
      setKeyAlertOpError(msg);
      showError(msg);
    },
  });

  const keyAlertEditMutation = useMutation({
    mutationFn: (vars: { ruleKey: string; ne_types: string[] }) =>
      patchUmeKeyAlertRule(vars.ruleKey, { ne_types: vars.ne_types }),
    onMutate: () => setKeyAlertOpError(""),
    onSuccess: async () => {
      setKeyAlertEditRule(null);
      showOk(t("ume.keyAlert.editOk"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitorAll });
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
      await queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitorAll });
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
  const expandedNe = useMemo(
    () => (neQuery.data?.items || []).find((x) => x.ne_id === expandedNeId) || null,
    [neQuery.data, expandedNeId],
  );

  const keyAlertHub = keyAlertMonitorQuery.data?.dsh_alarm_hub;
  const keyAlertHubConnections = keyAlertHub?.connections || [];
  const keyAlertHubSubscribers = Number(keyAlertHub?.subscribers || keyAlertHubConnections.length || 0);
  const keyAlertForwarder = keyAlertMonitorQuery.data?.forwarder;
  const keyAlertRules = keyAlertMonitorQuery.data?.rules || [];
  const keyAlertTotal = Number(keyAlertMonitorQuery.data?.total || keyAlertRules.length);
  const keyAlertPages = pageCount(keyAlertTotal, keyAlertPageSize);
  const keyAlertForwardOnClear = Boolean(keyAlertMonitorQuery.data?.config?.forward_on_clear);
  const hubWsPill = keyAlertHubSubscribers > 0 ? "up" : "down";
  const oclawWsPill =
    !keyAlertForwarder?.enabled
      ? "unknown"
      : keyAlertForwarder.paused
        ? "warn"
        : keyAlertForwarder.connected
          ? "up"
          : "down";
  const showLegacyOclaw = Boolean(keyAlertForwarder?.enabled);

  const runtimeTaskLabel = (task: string) => {
    const key = `ume.tasks.runtimeTask.${task}`;
    const label = t(key);
    return label === key ? task : label;
  };

  const perPage = (n: number) => t("common.perPage", { n: String(n) });

  return (
    <>
      <div className="page-stack ume-page">
      <section className="cards ume-cards">
        <article className="panel">
          <div className="panel__toolbar">
            <h2>{t("ume.token.title")}</h2>
          </div>
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
            <Button
              size="sm"
              variant="secondary"
              isDisabled={tokenStatusQuery.isFetching}
              onPress={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeTokenStatus })}
            >
              {tokenStatusQuery.isFetching ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {t("common.refreshing")}
                </>
              ) : (
                t("ume.token.refreshStatus")
              )}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              isDisabled={tokenRefreshMutation.isPending || tokenDisconnectMutation.isPending}
              onPress={() => tokenRefreshMutation.mutate()}
            >
              {tokenRefreshMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {t("ume.token.renewing")}
                </>
              ) : (
                t("ume.token.renewLogin")
              )}
            </Button>
            <Button
              size="sm"
              variant="danger"
              isDisabled={tokenRefreshMutation.isPending || tokenDisconnectMutation.isPending}
              onPress={() => tokenDisconnectMutation.mutate()}
            >
              {tokenDisconnectMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {t("ume.token.disconnecting")}
                </>
              ) : (
                t("ume.token.disconnect")
              )}
            </Button>
          </div>
          {(tokenOpError || tokenRefreshMutation.error || tokenDisconnectMutation.error) && (
            <div className="pill pill--high">
              {t("common.opFailed")}: {tokenOpError || String(tokenRefreshMutation.error || tokenDisconnectMutation.error)}
            </div>
          )}
        </article>

        <article className="panel">
          <div className="panel__toolbar">
            <h2>{t("ume.tasks.currentTitle")}</h2>
            <div className="panel__toolbar-end">
              <span className={`conn-pill conn-pill--${runningTasks.length > 0 ? "unknown" : "up"}`}>
                {t("ume.tasks.running")}: {runningTasks.length}
              </span>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={syncStatusQuery.isFetching}
                onPress={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll })}
              >
                {t("common.refresh")}
              </Button>
            </div>
          </div>
          {needsFabricApply ? (
            <div className="pill pill--high" style={{ marginBottom: 10 }} role="status">
              <div style={{ fontWeight: 600 }}>{t("ume.syncStatus.fabricGapTitle")}</div>
              <div className="muted" style={{ marginTop: 4 }}>
                {t("ume.syncStatus.fabricGapHint")
                  .replace("{{dock}}", String(topologyFabric?.dock_me_count ?? 0))
                  .replace("{{fabric}}", String(topologyFabric?.fabric_ume_count ?? 0))
                  .replace(
                    "{{status}}",
                    String(topologyFabric?.latest_topology_status || topologyFabric?.latest_topology_error || "—"),
                  )}
              </div>
              <div className="btn-row" style={{ marginTop: 8 }}>
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={applyFabricMut.isPending}
                  onPress={() => applyFabricMut.mutate()}
                >
                  {applyFabricMut.isPending
                    ? t("ume.syncStatus.applyingFabric")
                    : t("ume.syncStatus.applyFabric")}
                </Button>
              </div>
            </div>
          ) : null}
          <div className="pt-list">
          <div className="pt-list-table-wrap">
<table className="data-table pt-list-table">
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
</div>

          <h3 className="card__section-title">{t("ume.tasks.runtimeTitle")}</h3>
          {runtimeTaskError ? (
            <div className="pill pill--high" style={{ marginBottom: 8 }}>
              {t("ume.tasks.runtimeOpFailed")}: {runtimeTaskError}
            </div>
          ) : null}
          <div className="pt-list-table-wrap">
<table className="data-table pt-list-table">
            <thead>
              <tr>
                <th>task</th>
                <th title={t("ume.tasks.intervalTitle")}>interval</th>
                <th>status</th>
                <th title={t("ume.tasks.lastRunTitle")}>last_run_at</th>
                <th>{t("ume.tasks.lastErrorCol")}</th>
                <th>actions</th>
              </tr>
            </thead>
            <tbody>
              {runtimeTasks.map((x) => (
                <tr key={`runtime-${x.task}`}>
                  <td title={x.task}>{runtimeTaskLabel(x.task)}</td>
                  <td title={typeof x.interval_s === "number" ? `${x.interval_s}s` : undefined}>
                    {runtimeIntervalLabel(x.interval_label)}
                  </td>
                  <td>{x.status}</td>
                  <td>{x.last_run_at ? formatSystemTime(x.last_run_at) : t("common.empty")}</td>
                  <td>{runtimeLastError(x.last_error, t) || t("common.empty")}</td>
                  <td>
                    {Boolean(x.paused) ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="link-btn"
                        isDisabled={runtimeTaskMutation.isPending}
                        onPress={() => runtimeTaskMutation.mutate({ task: x.task, action: "resume" })}
                      >
                        resume
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="link-btn"
                        isDisabled={runtimeTaskMutation.isPending}
                        onPress={() => runtimeTaskMutation.mutate({ task: x.task, action: "pause" })}
                      >
                        pause
                      </Button>
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
</div>
          </div>
        </article>

        <article className="panel">
          <div className="panel__toolbar">
            <h2 className="card-title-with-hint">
              {t("ume.subscription.title")}
              <HelpHint text={t("ume.subscription.help")} ariaLabel={t("common.help")} />
            </h2>
          </div>
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
                title={wsConn?.detail || runtimeLastError(wsConsumer?.last_error, t) || alarmSub.wss_uri || ""}
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
            <Button
              size="sm"
              variant="secondary"
              isDisabled={subPending || (!serverSubLost && subscriptionActive) || !hasToken}
              onPress={() => {
                if (serverSubLost && subscriptionActive) {
                  if (window.confirm(t("ume.subscription.confirmReestablish"))) {
                    subscriptionEstablishMutation.mutate({ forceReestablish: true });
                  }
                  return;
                }
                subscriptionEstablishMutation.mutate(undefined);
              }}
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
            </Button>
            {serverSubLost && subscriptionActive ? (
              <Button
                size="sm"
                variant="secondary"
                isDisabled={subPending}
                onPress={() => {
                  if (confirmClearLocalSubscription()) subscriptionClearLocalMutation.mutate();
                }}
              >
                {subscriptionClearLocalMutation.isPending ? (
                  <>
                    <span className="inline-spinner" aria-hidden />
                    {t("ume.subscription.clearing")}
                  </>
                ) : (
                  t("ume.subscription.clearLocalOnly")
                )}
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="danger"
              isDisabled={subPending || !subscriptionActive}
              onPress={() => subscriptionCancelMutation.mutate(undefined)}
            >
              {subscriptionCancelMutation.isPending ? (
                <>
                  <span className="inline-spinner" aria-hidden />
                  {t("ume.subscription.cancelling")}
                </>
              ) : (
                t("ume.subscription.cancel")
              )}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              isDisabled={subscriptionStatusQuery.isFetching}
              onPress={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeAlarmSubscription })}
            >
              {t("ume.subscription.refreshStatus")}
            </Button>
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

      </section>

      <section className="ume-entry-grid">
        <article className="panel ume-entry">
          <div className="panel__toolbar">
            <h2 className="card-title-with-hint">
              {t("ume.cli.title")}
              <HelpHint text={t("ume.cli.hint")} ariaLabel={t("common.help")} />
            </h2>
            <Button size="sm" variant="secondary" onPress={() => setCliPanelOpen(true)}>
              {t("ume.cli.showPanel")}
            </Button>
          </div>
          <p className="ume-entry__summary muted">{t("ume.cli.hint")}</p>
        </article>

        <article className="panel ume-entry">
          <div className="panel__toolbar">
            <h2 className="card-title-with-hint">
              {t("ume.keyAlert.title")}
              <HelpHint text={t("ume.keyAlert.help")} ariaLabel={t("common.help")} />
            </h2>
            <Button size="sm" variant="secondary" onPress={() => setKeyAlertPanelOpen(true)}>
              {t("ume.keyAlert.showPanel")}
            </Button>
          </div>
          <div className="ume-entry__pills actions-row actions-row--inline">
            <span className={`conn-pill conn-pill--${hubWsPill}`}>
              {t("ume.keyAlert.hub")}:{" "}
              {keyAlertHubSubscribers > 0
                ? t("ume.keyAlert.hubConnected")
                : t("ume.keyAlert.hubEmpty")}
            </span>
            <span className="conn-pill">
              {t("ume.keyAlert.hubSubscribers")}: {keyAlertHubSubscribers}
            </span>
            <span className="conn-pill">
              {t("ume.keyAlert.hubPublished")}: {Number(keyAlertHub?.published || 0)}
            </span>
            <span className="conn-pill">
              {t("ume.keyAlert.hubDeliverOk")}: {Number(keyAlertHub?.deliver_ok || 0)}
            </span>
            {showLegacyOclaw ? (
              <span className={`conn-pill conn-pill--${oclawWsPill}`} title={t("ume.keyAlert.legacyOclaw")}>
                {t("ume.keyAlert.ws")}:{" "}
                {keyAlertForwarder?.paused
                  ? t("ume.keyAlert.wsPaused")
                  : keyAlertForwarder?.connected
                    ? t("ume.keyAlert.wsConnected")
                    : t("ume.keyAlert.wsDisconnected")}
              </span>
            ) : null}
          </div>
        </article>

        <article className="panel ume-entry">
          <div className="panel__toolbar">
            <h2>{t("ume.syncStatus.title")}</h2>
            <Button size="sm" variant="secondary" onPress={() => setSyncStatusPanelOpen(true)}>
              {t("ume.syncStatus.showPanel")}
            </Button>
          </div>
          <p className="ume-entry__summary muted">
            {syncTotal
              ? t("ume.syncStatus.summary", {
                  total: String(syncTotal),
                  running: String(runningTasks.length),
                })
              : t("ume.syncStatus.empty")}
          </p>
        </article>

        <article className="panel ume-entry">
          <div className="panel__toolbar">
            <h2>{t("ume.ne.title")}</h2>
            <Button size="sm" variant="secondary" onPress={() => setNePanelOpen(true)}>
              {t("ume.ne.showPanel")}
            </Button>
          </div>
          <p className="ume-entry__summary muted">
            {t("ume.ne.summary", { total: String(neTotal) })}
          </p>
        </article>

        <article className="panel ume-entry">
          <div className="panel__toolbar">
            <h2>{t("ume.alarms.title")}</h2>
          </div>
          <p className="ume-entry__summary muted">
            {t("ume.alarms.movedHint")}{" "}
            <a
              href="/network/alarms"
              onClick={(e) => {
                e.preventDefault();
                openOrFocusModule({ moduleId: "network", path: "/network/alarms" });
              }}
            >
              {t("network.nav.alarms")}
            </a>
          </p>
        </article>
      </section>
      </div>

      <AppModalShell
        open={cliPanelOpen}
        onClose={() => setCliPanelOpen(false)}
        size="lg"
        className="app-heroui-modal--xl"
      >
        <Modal.Header>
          <Modal.Heading>{t("ume.cli.title")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="muted">{t("ume.cli.hint")}</p>
          <UmeCliConnectPanel embedded enabled />
        </Modal.Body>
      </AppModalShell>

      <AppModalShell
        open={keyAlertPanelOpen}
        onClose={() => setKeyAlertPanelOpen(false)}
        size="lg"
        className="app-heroui-modal--xl"
      >
        <Modal.Header>
          <Modal.Heading>{t("ume.keyAlert.title")}</Modal.Heading>
          <Button
            size="sm"
            variant="secondary"
            isDisabled={keyAlertMonitorQuery.isFetching}
            onPress={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeKeyAlertMonitorAll })}
          >
            {keyAlertMonitorQuery.isFetching ? t("common.refreshing") : t("common.refresh")}
          </Button>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="muted">
            {t("ume.keyAlert.hub")}:{" "}
            {keyAlertHubSubscribers > 0
              ? t("ume.keyAlert.hubConnected")
              : t("ume.keyAlert.hubEmpty")}
            {` · ${t("ume.keyAlert.hubSubscribers")} ${keyAlertHubSubscribers}`}
            {` · ${t("ume.keyAlert.hubPublished")} ${Number(keyAlertHub?.published || 0)}`}
            {` · ${t("ume.keyAlert.hubDeliverOk")} ${Number(keyAlertHub?.deliver_ok || 0)}`}
            {keyAlertHub?.path ? ` · ${t("ume.keyAlert.hubPath")} ${keyAlertHub.path}` : ""}
            {showLegacyOclaw
              ? ` · ${t("ume.keyAlert.legacyOclaw")}: ${
                  keyAlertForwarder?.paused
                    ? t("ume.keyAlert.wsPaused")
                    : keyAlertForwarder?.connected
                      ? t("ume.keyAlert.wsConnected")
                      : t("ume.keyAlert.wsDisconnected")
                }`
              : ""}
          </p>
              <div className="ume-modal-form">
                <div className="muted ume-modal-form__label">{t("ume.keyAlert.hubConnections")}</div>
                {keyAlertHubConnections.length === 0 ? (
                  <p className="muted">{t("ume.keyAlert.hubEmptyConnections")}</p>
                ) : (
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t("ume.keyAlert.hubColId")}</th>
                          <th>{t("ume.keyAlert.hubColUser")}</th>
                          <th>{t("ume.keyAlert.hubColRemote")}</th>
                          <th>{t("ume.keyAlert.hubColClient")}</th>
                          <th>{t("ume.keyAlert.hubColConnected")}</th>
                          <th>{t("ume.keyAlert.hubColLastSeen")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {keyAlertHubConnections.map((conn) => (
                          <tr key={conn.id}>
                            <td>
                              <code>{conn.id}</code>
                            </td>
                            <td>{conn.user || "-"}</td>
                            <td>{conn.remote || "-"}</td>
                            <td>{conn.client || "-"}</td>
                            <td>
                              {conn.connected_at
                                ? formatSystemTime(conn.connected_at)
                                : "-"}
                            </td>
                            <td>
                              {conn.last_seen_at
                                ? formatSystemTime(conn.last_seen_at)
                                : "-"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {showLegacyOclaw ? (
                  <p className="muted ume-modal-form__hint">
                    {t("ume.keyAlert.legacyOclaw")}:{" "}
                    {keyAlertForwarder?.paused
                      ? t("ume.keyAlert.wsPaused")
                      : keyAlertForwarder?.connected
                        ? t("ume.keyAlert.wsConnected")
                        : t("ume.keyAlert.wsDisconnected")}
                    {` · ${t("ume.keyAlert.publishedOk")} ${Number(keyAlertForwarder?.published_ok || 0)}`}
                    {` · ${t("ume.keyAlert.queue")} ${Number(keyAlertForwarder?.queue_size || 0)}`}
                  </p>
                ) : null}
              </div>

              <div className="ume-modal-form">
                <div className="ops-detail-modal__toolbar filter-inline">
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
                    className="input ume-modal-form__grow"
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
                    className="input ume-modal-form__grow"
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
                  <Button
                    size="sm"
                    variant="secondary"
                    onPress={async () => {
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
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={
                      keyAlertAddMutation.isPending || !keyAlertMatchValue.trim() || !keyAlertLabel.trim()
                    }
                    onPress={() => keyAlertAddMutation.mutate()}
                  >
                    {keyAlertAddMutation.isPending ? t("ume.keyAlert.adding") : t("ume.keyAlert.add")}
                  </Button>
                </div>

                <div className="muted ume-modal-form__label">{t("ume.keyAlert.neTypesLabel")}</div>
                {keyAlertNeTypesQuery.isLoading ? (
                  <span className="muted">{t("common.refreshing")}</span>
                ) : (keyAlertNeTypesQuery.data?.items || []).length === 0 ? (
                  <span className="muted">{t("ume.keyAlert.neTypesEmpty")}</span>
                ) : (
                  <div className="ume-ne-types">
                    {(keyAlertNeTypesQuery.data?.items || []).map((item) => (
                      <label key={item.ne_type} className="ume-ne-types__item">
                        <input
                          type="checkbox"
                          checked={keyAlertNeTypes.includes(item.ne_type)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setKeyAlertNeTypes((prev) => [...prev, item.ne_type]);
                            } else {
                              setKeyAlertNeTypes((prev) => prev.filter((x) => x !== item.ne_type));
                            }
                          }}
                        />
                        {item.ne_type}
                        <span className="muted">({item.ne_count})</span>
                      </label>
                    ))}
                  </div>
                )}
                <p className="muted ume-modal-form__hint">{t("ume.keyAlert.neTypesHint")}</p>

                <div className="ops-detail-modal__toolbar ume-modal-form__opts">
                  <label className="ume-ne-types__item muted">
                    <input
                      type="checkbox"
                      checked={keyAlertForwardOnClear}
                      disabled={keyAlertConfigMutation.isPending || keyAlertMonitorQuery.isLoading}
                      onChange={(e) => keyAlertConfigMutation.mutate(e.target.checked)}
                    />
                    {t("ume.keyAlert.forwardOnClear")}
                    <HelpHint
                      text={t("ume.keyAlert.forwardOnClearHelp")}
                      ariaLabel={t("common.help")}
                      align="start"
                    />
                  </label>
                </div>
                {keyAlertOpError ? (
                  <p className="ops-detail-modal__error">
                    {t("common.opFailed")}: {keyAlertOpError}
                  </p>
                ) : null}
              </div>

              <div className="ops-detail-modal__toolbar filter-inline">
                <input
                  type="search"
                  placeholder={t("ume.keyAlert.filterKeywordPh")}
                  value={keyAlertFilterKeyword}
                  onChange={(e) => {
                    setKeyAlertFilterKeyword(e.target.value);
                    setKeyAlertPage(1);
                  }}
                />
                <select
                  value={keyAlertFilterMatchType}
                  onChange={(e) => {
                    setKeyAlertFilterMatchType(e.target.value as "" | "notification_id" | "keyword");
                    setKeyAlertPage(1);
                  }}
                >
                  <option value="">{t("ume.keyAlert.filterMatchAll")}</option>
                  <option value="notification_id">{t("ume.keyAlert.matchNotificationId")}</option>
                  <option value="keyword">{t("ume.keyAlert.matchKeyword")}</option>
                </select>
                <select
                  value={keyAlertFilterEnabled}
                  onChange={(e) => {
                    setKeyAlertFilterEnabled(e.target.value as "" | "true" | "false");
                    setKeyAlertPage(1);
                  }}
                >
                  <option value="">{t("ume.keyAlert.filterEnabledAll")}</option>
                  <option value="true">{t("ume.keyAlert.filterEnabledOn")}</option>
                  <option value="false">{t("ume.keyAlert.filterEnabledOff")}</option>
                </select>
              </div>

              <div className="pt-list-table-wrap">
                <table className="data-table pt-list-table">
                  <thead>
                    <tr>
                      <th>{t("ume.keyAlert.colMonitor")}</th>
                      <th>{t("ume.keyAlert.colType")}</th>
                      <th>{t("ume.keyAlert.colMatch")}</th>
                      <th>{t("ume.keyAlert.colLabel")}</th>
                      <th>{t("ume.keyAlert.colNeTypes")}</th>
                      <th>{t("ume.keyAlert.colPublished")}</th>
                      <th>{t("ume.keyAlert.colAttempts")}</th>
                      <th>{t("ume.keyAlert.colLast")}</th>
                      <th>{t("ume.keyAlert.colActions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {keyAlertRules.map((rule) => (
                      <tr key={rule.notification_id} className={rule.enabled ? undefined : "row--muted"}>
                        <td className="nowrap">
                          <label className="ume-ne-types__item">
                            <input
                              type="checkbox"
                              checked={Boolean(rule.enabled)}
                              disabled={keyAlertToggleMutation.isPending}
                              onChange={(e) =>
                                keyAlertToggleMutation.mutate({
                                  ruleKey: rule.notification_id,
                                  enabled: e.target.checked,
                                })
                              }
                            />
                            {rule.enabled ? t("ume.keyAlert.monitorOn") : t("ume.keyAlert.monitorOff")}
                          </label>
                        </td>
                        <td>
                          {rule.match_type === "keyword"
                            ? t("ume.keyAlert.matchKeyword")
                            : t("ume.keyAlert.matchNotificationId")}
                        </td>
                        <td>{rule.match_value || rule.notification_id}</td>
                        <td>{rule.label || t("common.empty")}</td>
                        <td>
                          {rule.ne_types?.length ? rule.ne_types.join(", ") : t("ume.keyAlert.neTypesAll")}
                        </td>
                        <td>{Number(rule.forward_stats?.published_ok || 0)}</td>
                        <td>{Number(rule.forward_stats?.attempts || 0)}</td>
                        <td>
                          {rule.forward_stats?.last_forwarded_at
                            ? formatSystemTime(rule.forward_stats.last_forwarded_at)
                            : t("common.empty")}
                        </td>
                        <td>
                          <div className="btn-row">
                            <Button
                              size="sm"
                              variant="secondary"
                              isDisabled={keyAlertEditMutation.isPending}
                              onPress={() => {
                                setKeyAlertEditRule(rule);
                                setKeyAlertEditNeTypes(rule.ne_types || []);
                              }}
                            >
                              {t("ume.keyAlert.edit")}
                            </Button>
                            <Button
                              size="sm"
                              variant="danger"
                              isDisabled={keyAlertDeleteMutation.isPending}
                              onPress={() => {
                                if (window.confirm(t("ume.keyAlert.confirmDelete"))) {
                                  keyAlertDeleteMutation.mutate(rule.notification_id);
                                }
                              }}
                            >
                              {t("ume.keyAlert.delete")}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {!keyAlertMonitorQuery.isLoading && keyAlertRules.length === 0 ? (
                      <tr>
                        <td colSpan={9} className="muted">
                          {t("ume.keyAlert.emptyRules")}
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

              <div className="ops-detail-modal__foot">
                <span className="muted">
                  {t("common.pagerMeta", {
                    total: String(keyAlertTotal),
                    page: String(keyAlertPage),
                    pages: String(keyAlertPages),
                  })}
                </span>
                <div className="btn-row">
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={keyAlertPage <= 1}
                    onPress={() => setKeyAlertPage(Math.max(1, keyAlertPage - 1))}
                  >
                    {t("common.prevPage")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={keyAlertPage >= keyAlertPages}
                    onPress={() => setKeyAlertPage(keyAlertPage + 1)}
                  >
                    {t("common.nextPage")}
                  </Button>
                  <select
                    className="pager__size"
                    value={String(keyAlertPageSize)}
                    onChange={(e) => {
                      setKeyAlertPageSize(Number(e.target.value) || 10);
                      setKeyAlertPage(1);
                    }}
                  >
                    <option value="10">{perPage(10)}</option>
                    <option value="20">{perPage(20)}</option>
                    <option value="50">{perPage(50)}</option>
                  </select>
                </div>
              </div>
        </Modal.Body>
      </AppModalShell>

      <AppModalShell
        open={syncStatusPanelOpen}
        onClose={() => setSyncStatusPanelOpen(false)}
        size="lg"
        className="app-heroui-modal--xl"
      >
        <Modal.Header>
          <Modal.Heading>{t("ume.syncStatus.title")}</Modal.Heading>
          <Button
            size="sm"
            variant="secondary"
            isDisabled={syncStatusQuery.isFetching}
            onPress={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeSyncStatusAll })}
          >
            {syncStatusQuery.isFetching ? t("common.refreshing") : t("common.refresh")}
          </Button>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="muted">
            {t("ume.syncStatus.summary", {
              total: String(syncTotal),
              running: String(runningTasks.length),
            })}
          </p>
            <div className="pt-list-table-wrap">
                <table className="data-table pt-list-table">
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
                      <th className="ume-col-error">error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(syncStatusQuery.data?.items || []).map((x) => (
                      <tr key={x.id}>
                        <td className="pt-list-num" title={String(x.id)}>
                          {String(x.id).slice(0, 8)}
                        </td>
                        <td>{x.domain}</td>
                        <td>{x.status}</td>
                        <td>{x.pulled_count}</td>
                        <td>{x.inserted_count}</td>
                        <td>{x.updated_count}</td>
                        <td>{Number(x.deleted ?? 0)}</td>
                        <td>{formatSystemTime(x.started_at)}</td>
                        <td>{x.ended_at ? formatSystemTime(x.ended_at) : t("common.empty")}</td>
                        <td className="ume-col-error" title={x.error_message || ""}>
                          {x.error_message || t("common.empty")}
                        </td>
                      </tr>
                    ))}
                    {!syncStatusQuery.isLoading && (syncStatusQuery.data?.items || []).length === 0 ? (
                      <tr>
                        <td colSpan={10} className="muted">
                          {t("ume.syncStatus.empty")}
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

            <div className="ops-detail-modal__foot">
              <span className="muted">
                {t("common.pagerMeta", {
                  total: String(syncTotal),
                  page: String(syncPage),
                  pages: String(syncPages),
                })}
              </span>
              <div className="btn-row">
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={syncPage <= 1}
                  onPress={() => setSyncPage(Math.max(1, syncPage - 1))}
                >
                  {t("common.prevPage")}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={syncPage >= syncPages}
                  onPress={() => setSyncPage(syncPage + 1)}
                >
                  {t("common.nextPage")}
                </Button>
                <select
                  className="pager__size"
                  value={String(syncPageSize)}
                  onChange={(e) => {
                    setSyncPageSize(Number(e.target.value) || 10);
                    setSyncPage(1);
                  }}
                >
                  <option value="10">{perPage(10)}</option>
                  <option value="20">{perPage(20)}</option>
                  <option value="50">{perPage(50)}</option>
                  <option value="100">{perPage(100)}</option>
                  <option value="200">{perPage(200)}</option>
                </select>
              </div>
            </div>
        </Modal.Body>
      </AppModalShell>

      <AppModalShell
        open={nePanelOpen}
        onClose={() => {
          setNePanelOpen(false);
          setExpandedNeId("");
        }}
        size="lg"
        className="app-heroui-modal--xl"
      >
        <Modal.Header>
          <Modal.Heading>{t("ume.ne.title")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="muted">{t("ume.ne.summary", { total: String(neTotal) })}</p>

            <div className="ops-detail-modal__toolbar filter-inline">
              <Input
                value={neKeyword}
                placeholder={t("ume.ne.keywordPh")}
                onChange={(e) => {
                  setNeKeyword(e.target.value);
                  setNePage(1);
                }}
              />
              <Button
                size="sm"
                variant="secondary"
                onPress={() => queryClient.invalidateQueries({ queryKey: queryKeys.umeNEAll })}
              >
                {t("common.query")}
              </Button>
              <Button
                size="sm"
                variant="tertiary"
                isDisabled={!neKeyword.trim()}
                onPress={() => {
                  setNeKeyword("");
                  setNePage(1);
                }}
              >
                {t("common.clearFilters")}
              </Button>
            </div>

              <div className="pt-list-table-wrap">
                <table className="data-table pt-list-table">
                  <thead>
                    <tr>
                      <th>ne_id</th>
                      <th>user_label</th>
                      <th>ip</th>
                      <th>type</th>
                      <th>device_level</th>
                      <th>host_name</th>
                      <th>{t("ume.ne.cliConnect")}</th>
                      <th>hw_ver</th>
                      <th>last_seen</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {(neQuery.data?.items || []).map((x) => (
                      <tr key={x.ne_id}>
                        <td className="pt-list-num" title={x.ne_id}>
                          {x.ne_id}
                        </td>
                        <td>{x.user_label}</td>
                        <td>{x.ip_address}</td>
                        <td>{x.ne_type}</td>
                        <td>{x.device_level || t("common.empty")}</td>
                        <td>{x.host_name || t("common.empty")}</td>
                        <td>{cliStatusByNeId.get(x.ne_id) || "unknown"}</td>
                        <td>{x.hardware_version || t("common.empty")}</td>
                        <td>{x.last_seen_at ? formatSystemTime(x.last_seen_at) : t("common.empty")}</td>
                        <td>
                          <Button size="sm" variant="ghost" onPress={() => setExpandedNeId(x.ne_id)}>
                            {t("ume.ne.expand")}
                          </Button>
                        </td>
                      </tr>
                    ))}
                    {!neQuery.isLoading && !(neQuery.data?.items || []).length ? (
                      <tr>
                        <td colSpan={10} className="muted">
                          {t("common.empty")}
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

            <div className="ops-detail-modal__foot">
              <span className="muted">
                {t("common.pagerMeta", {
                  total: String(neTotal),
                  page: String(nePage),
                  pages: String(nePages),
                })}
              </span>
              <div className="btn-row">
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={nePage <= 1}
                  onPress={() => setNePage(Math.max(1, nePage - 1))}
                >
                  {t("common.prevPage")}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={nePage >= nePages}
                  onPress={() => setNePage(nePage + 1)}
                >
                  {t("common.nextPage")}
                </Button>
                <select
                  className="pager__size"
                  value={String(nePageSize)}
                  onChange={(e) => {
                    setNePageSize(Number(e.target.value) || 10);
                    setNePage(1);
                  }}
                >
                  <option value="10">{perPage(10)}</option>
                  <option value="20">{perPage(20)}</option>
                  <option value="50">{perPage(50)}</option>
                  <option value="100">{perPage(100)}</option>
                  <option value="200">{perPage(200)}</option>
                  <option value="500">{perPage(500)}</option>
                </select>
              </div>
            </div>
        </Modal.Body>
      </AppModalShell>

      <AppModalShell
        open={Boolean(expandedNe)}
        onClose={() => setExpandedNeId("")}
        size="lg"
      >
        <Modal.Header>
          <Modal.Heading>{expandedNe?.user_label || expandedNe?.ne_id || t("ume.ne.expand")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {expandedNe ? (
            <>
              <p className="muted">
                {expandedNe.ne_id}
                {expandedNe.ip_address ? ` · ${expandedNe.ip_address}` : ""}
                {expandedNe.ne_type ? ` · ${expandedNe.ne_type}` : ""}
              </p>
              <div className="ume-ne-detail-grid">
                <div>
                  <span className="muted">consistent_state</span>
                  <div>{expandedNe.consistent_state || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">admin_status</span>
                  <div>{expandedNe.admin_status || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">connection_status</span>
                  <div>{expandedNe.connection_status || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">maintain_status</span>
                  <div>{expandedNe.maintain_status || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">address_type</span>
                  <div>{expandedNe.address_type || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">location</span>
                  <div>{expandedNe.location || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">loopback</span>
                  <div>{expandedNe.loopback || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">net_mask</span>
                  <div>{expandedNe.net_mask || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">mac</span>
                  <div>{expandedNe.mac || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">interface_version</span>
                  <div>{expandedNe.interface_version || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">create_time</span>
                  <div>{expandedNe.create_time || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">creator</span>
                  <div>{expandedNe.creator || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">host_name</span>
                  <div>{expandedNe.host_name || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">hardware_version</span>
                  <div>{expandedNe.hardware_version || t("common.empty")}</div>
                </div>
                <div>
                  <span className="muted">{t("ume.ne.cliConnect")}</span>
                  <div>{cliStatusByNeId.get(expandedNe.ne_id) || "unknown"}</div>
                </div>
                <div>
                  <span className="muted">last_seen</span>
                  <div>
                    {expandedNe.last_seen_at
                      ? formatSystemTime(expandedNe.last_seen_at)
                      : t("common.empty")}
                  </div>
                </div>
              </div>
            </>
          ) : null}
        </Modal.Body>
      </AppModalShell>

      <AppModalShell
        open={Boolean(keyAlertEditRule)}
        onClose={() => {
          if (!keyAlertEditMutation.isPending) setKeyAlertEditRule(null);
        }}
        dismissible={!keyAlertEditMutation.isPending}
        size="lg"
      >
        <Modal.Header>
          <Modal.Heading>{t("ume.keyAlert.editTitle")}</Modal.Heading>
          <Modal.CloseTrigger isDisabled={keyAlertEditMutation.isPending} />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {keyAlertEditRule ? (
            <>
              <p className="muted">
                {keyAlertEditRule.match_type === "keyword"
                  ? t("ume.keyAlert.matchKeyword")
                  : t("ume.keyAlert.matchNotificationId")}
                : {keyAlertEditRule.match_value || keyAlertEditRule.notification_id}
                {keyAlertEditRule.label ? ` · ${keyAlertEditRule.label}` : ""}
              </p>
              <div className="muted ume-modal-form__label">{t("ume.keyAlert.neTypesLabel")}</div>
              {keyAlertNeTypesQuery.isLoading ? (
                <span className="muted">{t("common.refreshing")}</span>
              ) : (keyAlertNeTypesQuery.data?.items || []).length === 0 ? (
                <span className="muted">{t("ume.keyAlert.neTypesEmpty")}</span>
              ) : (
                <div className="ume-ne-types ume-ne-types--tall">
                  {(keyAlertNeTypesQuery.data?.items || []).map((item) => (
                    <label key={item.ne_type} className="ume-ne-types__item">
                      <input
                        type="checkbox"
                        checked={keyAlertEditNeTypes.includes(item.ne_type)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setKeyAlertEditNeTypes((prev) => [...prev, item.ne_type]);
                          } else {
                            setKeyAlertEditNeTypes((prev) => prev.filter((x) => x !== item.ne_type));
                          }
                        }}
                      />
                      {item.ne_type}
                      <span className="muted">({item.ne_count})</span>
                    </label>
                  ))}
                </div>
              )}
              <p className="muted ume-modal-form__hint">{t("ume.keyAlert.neTypesHint")}</p>
            </>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          <Button
            variant="tertiary"
            isDisabled={keyAlertEditMutation.isPending}
            onPress={() => setKeyAlertEditRule(null)}
          >
            {t("ume.keyAlert.cancel")}
          </Button>
          <Button
            variant="primary"
            isDisabled={keyAlertEditMutation.isPending || !keyAlertEditRule}
            onPress={() => {
              if (!keyAlertEditRule) return;
              keyAlertEditMutation.mutate({
                ruleKey: keyAlertEditRule.notification_id,
                ne_types: keyAlertEditNeTypes,
              });
            }}
          >
            {keyAlertEditMutation.isPending ? t("ume.keyAlert.saving") : t("ume.keyAlert.save")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </>
  );
}
