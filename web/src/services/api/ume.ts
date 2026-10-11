import type {
  UmeAlarmSubscriptionStatus,
  UmeKeyAlertMonitorResponse,
  UmeNeItem,
  UmeSyncStatusResponse,
  UmeTokenStatus,
} from "../../types";
import { apiDelete, apiGet, apiPatch, apiPost } from "./http";

export const fetchUmeAlarmSubscriptionStatus = () =>
  apiGet<UmeAlarmSubscriptionStatus>("/v1/ume/alarm-subscription/status");

export const establishUmeAlarmSubscription = (opts?: { forceReestablish?: boolean }) =>
  apiPost<UmeAlarmSubscriptionStatus>("/v1/ume/alarm-subscription/establish", {
    force_reestablish: Boolean(opts?.forceReestablish),
  });

export const cancelUmeAlarmSubscription = (opts?: { forceClearLocal?: boolean }) =>
  apiPost<UmeAlarmSubscriptionStatus>("/v1/ume/alarm-subscription/cancel", {
    force_clear_local: Boolean(opts?.forceClearLocal),
  });

export const clearLocalUmeAlarmSubscription = () =>
  apiPost<UmeAlarmSubscriptionStatus>("/v1/ume/alarm-subscription/clear-local", {});

export const fetchUmeKeyAlertMonitor = (params?: {
  page?: number;
  pageSize?: number;
  keyword?: string;
  enabled?: "" | "true" | "false";
  matchType?: "" | "notification_id" | "keyword";
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params?.pageSize || 50)))));
  const kw = String(params?.keyword || "").trim();
  if (kw) p.set("keyword", kw);
  const en = String(params?.enabled || "").trim();
  if (en) p.set("enabled", en);
  const mt = String(params?.matchType || "").trim();
  if (mt) p.set("match_type", mt);
  return apiGet<UmeKeyAlertMonitorResponse>(`/v1/ume/key-alert-monitor?${p.toString()}`);
};

export const upsertUmeKeyAlertRule = (payload: {
  match_type: "notification_id" | "keyword";
  match_value: string;
  label: string;
  enabled?: boolean;
  ne_types?: string[];
}) => apiPost<{ ok: boolean; item?: unknown }>("/v1/ume/key-alert-rules", payload);

export const updateUmeKeyAlertMonitorConfig = (payload: { forward_on_clear: boolean }) =>
  apiPatch<{ ok: boolean; config: { forward_on_clear: boolean } }>("/v1/ume/key-alert-monitor/config", payload);

export const deleteUmeKeyAlertRule = (ruleKey: string) =>
  apiDelete<{ ok: boolean }>(`/v1/ume/key-alert-rules/${encodeURIComponent(ruleKey)}`);

export const patchUmeKeyAlertRule = (
  ruleKey: string,
  payload: { enabled?: boolean; ne_types?: string[] },
) =>
  apiPatch<{ ok: boolean; item?: unknown }>(
    `/v1/ume/key-alert-rules/${encodeURIComponent(ruleKey)}`,
    payload,
  );

export const fetchUmeNotificationIds = (limit = 200) =>
  apiGet<{ items: Array<{ notification_id: string; native_probable_cause_sample: string }> }>(
    `/v1/ume/notification-ids?limit=${encodeURIComponent(String(limit))}`,
  );

export const fetchUmeAlarmKeywords = (limit = 200) =>
  apiGet<{ items: Array<{ keyword: string; alarm_count: number }> }>(
    `/v1/ume/alarm-keywords?limit=${encodeURIComponent(String(limit))}`,
  );

export const fetchUmeInventoryNeTypes = (limit = 500) =>
  apiGet<{ items: Array<{ ne_type: string; ne_count: number }>; total: number }>(
    `/v1/ume/inventory/ne-types?limit=${encodeURIComponent(String(limit))}`,
  );

export const fetchUmeSyncStatus = (params: { page: number; pageSize: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params.pageSize || 20)))));
  return apiGet<UmeSyncStatusResponse>(`/v1/ume/sync/status?${p.toString()}`);
};

/** Dock tables → Fabric + UME World (does not pull from UME). */
export const applyUmeTopologyToFabric = () =>
  apiPost<{
    ok: boolean;
    fabric_apply?: Record<string, unknown>;
    world?: Record<string, unknown>;
  }>("/v1/topology/world/apply-ume", {});

export const fetchUmeTokenStatus = () => apiGet<UmeTokenStatus>("/v1/ume/token/status");
export const refreshUmeToken = () => apiPost<UmeTokenStatus>("/v1/ume/token/refresh", {});
export const disconnectUmeToken = () => apiPost<UmeTokenStatus>("/v1/ume/token/disconnect", {});

export const fetchUmeNe = (params: { keyword: string; page: number; pageSize: number }) => {
  const p = new URLSearchParams();
  if (params.keyword) p.set("keyword", params.keyword);
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(500, Number(params.pageSize || 50)))));
  return apiGet<{ total: number; page: number; page_size: number; items: UmeNeItem[] }>(
    `/v1/ume/inventory/ne?${p.toString()}`,
  );
};
