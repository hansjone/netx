import type {
  IntegrationStatus,
  UmeAlarmItem,
  UmeAlarmSubscriptionStatus,
  UmeNeItem,
  UmeSyncStatusResponse,
  UmeTokenStatus,
} from "../types";

export const apiGet = async <T,>(path: string): Promise<T> => {
  const res = await fetch(path, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return (await res.json()) as T;
};

export const apiPost = async <T,>(path: string, body: unknown): Promise<T> => {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(String((data as { detail?: string }).detail || `${res.status} ${path}`));
  return data as T;
};

export const fetchIntegrationStatus = () => apiGet<IntegrationStatus>("/v1/integrations/status");

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

export const fetchUmeSyncStatus = (params: { page: number; pageSize: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params.pageSize || 20)))));
  return apiGet<UmeSyncStatusResponse>(`/v1/ume/sync/status?${p.toString()}`);
};

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

export const fetchUmeCurrentAlarms = (params: {
  severity: string;
  isCleared: string;
  hostName: string;
  keyword: string;
  page: number;
  pageSize: number;
}) => {
  const p = new URLSearchParams();
  if (params.severity) p.set("severity", params.severity);
  if (params.isCleared) p.set("is_cleared", params.isCleared);
  if (params.hostName) p.set("host_name", params.hostName);
  if (params.keyword) p.set("keyword", params.keyword);
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(500, Number(params.pageSize || 50)))));
  return apiGet<{ total: number; page: number; page_size: number; items: UmeAlarmItem[] }>(
    `/v1/ume/alarms?${p.toString()}`,
  );
};
