import type {
  CollectionJobDetail,
  CollectionJobItem,
  CollectionRunList,
  EligibleNeItem,
  IntegrationStatus,
  ManagedNeImportResult,
  ManagedNeItem,
  ManagedNeListResponse,
  ManagedNeMeta,
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

export const apiPatch = async <T,>(path: string, body: unknown): Promise<T> => {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(String((data as { detail?: string }).detail || `${res.status} ${path}`));
  return data as T;
};

export const apiDelete = async <T,>(path: string): Promise<T> => {
  const res = await fetch(path, { method: "DELETE", headers: { accept: "application/json" } });
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

export const fetchManagedNeMeta = () =>
  Promise.all([
    apiGet<ManagedNeMeta>("/v1/managed-ne/meta/device-types"),
    apiGet<{ configured: boolean }>("/v1/managed-ne/meta/credentials-configured"),
  ]).then(([types, creds]) => ({
    device_types: types.device_types,
    vendors: types.vendors,
    credentials_configured: creds.configured,
  }));

export const fetchManagedNe = (params: {
  keyword: string;
  vendor: string;
  connectStatus: string;
  page: number;
  pageSize: number;
}) => {
  const p = new URLSearchParams();
  if (params.keyword) p.set("keyword", params.keyword);
  if (params.vendor) p.set("vendor", params.vendor);
  if (params.connectStatus) p.set("connect_status", params.connectStatus);
  p.set("page", String(Math.max(1, params.page)));
  p.set("page_size", String(Math.max(1, Math.min(500, params.pageSize))));
  return apiGet<ManagedNeListResponse>(`/v1/managed-ne?${p.toString()}`);
};

export const createManagedNe = (body: Record<string, unknown>) =>
  apiPost<ManagedNeItem>("/v1/managed-ne", body);

export const updateManagedNe = (id: string, body: Record<string, unknown>) =>
  apiPatch<ManagedNeItem>(`/v1/managed-ne/${id}`, body);

export const deleteManagedNe = (id: string) => apiDelete<{ ok: boolean }>(`/v1/managed-ne/${id}`);

export const connectTestManagedNe = (ids: string[]) =>
  apiPost<{ ok: boolean; submitted: number }>("/v1/managed-ne/connect-test", { ids });

export const managedNeImportTemplateUrl = (format: "xlsx" | "csv" = "xlsx") =>
  `/v1/managed-ne/import/template?format=${format}`;

export const importManagedNe = async (file: File): Promise<ManagedNeImportResult> => {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/v1/managed-ne/import", { method: "POST", body: form });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(String((data as { detail?: string }).detail || `${res.status} import`));
  return data as ManagedNeImportResult;
};

export const fetchEligibleNe = (params: { page: number; pageSize: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, params.page)));
  p.set("page_size", String(Math.max(1, Math.min(500, params.pageSize))));
  return apiGet<{ total: number; page: number; page_size: number; items: EligibleNeItem[] }>(
    `/v1/ne-collections/eligible-ne?${p.toString()}`,
  );
};

export const createNeCollection = (body: { title?: string; commands: string; ne_ids: string[] }) =>
  apiPost<CollectionJobItem>("/v1/ne-collections", body);

export const fetchNeCollections = (params: { page: number; pageSize: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, params.page)));
  p.set("page_size", String(Math.max(1, Math.min(100, params.pageSize))));
  return apiGet<{ total: number; page: number; page_size: number; items: CollectionJobItem[] }>(
    `/v1/ne-collections?${p.toString()}`,
  );
};

export const fetchCollectionJob = (jobId: string) => apiGet<CollectionJobDetail>(`/v1/ne-collections/${jobId}`);

export const fetchCollectionRuns = (params: {
  jobId: string;
  page: number;
  pageSize: number;
  status?: string;
  keyword?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, params.page)));
  p.set("page_size", String(Math.max(1, Math.min(200, params.pageSize))));
  if (params.status) p.set("status", params.status);
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<CollectionRunList>(`/v1/ne-collections/${params.jobId}/runs?${p.toString()}`);
};

export const pauseCollectionJob = (jobId: string) =>
  apiPost<CollectionJobItem>(`/v1/ne-collections/${jobId}/pause`, {});

export const restartCollectionJob = (jobId: string) =>
  apiPost<CollectionJobItem>(`/v1/ne-collections/${jobId}/restart`, {});

export const retryFailedCollectionJob = (jobId: string) =>
  apiPost<CollectionJobItem>(`/v1/ne-collections/${jobId}/retry-failed`, {});

export const deleteCollectionJob = (jobId: string) =>
  apiDelete<{ ok: boolean }>(`/v1/ne-collections/${jobId}`);

export const collectionRunDownloadUrl = (runId: string) => `/v1/ne-collections/runs/${runId}/download`;

export const collectionJobDownloadUrl = (jobId: string) => `/v1/ne-collections/${jobId}/download`;

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
