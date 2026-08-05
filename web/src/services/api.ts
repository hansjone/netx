import type {
  CollectionJobDetail,
  CollectionJobItem,
  CollectionDashboard,
  CollectionRunList,
  EligibleNeItem,
  IntegrationStatus,
  ManagedNeImportResult,
  ManagedNeItem,
  ManagedNeListResponse,
  ManagedNeMeta,
  UmeAlarmItem,
  UmeAlarmSubscriptionStatus,
  UmeKeyAlertMonitorResponse,
  UmeNeItem,
  UmeSyncStatusResponse,
  UmeTokenStatus,
  CliConnectProfileItem,
  CliMeta,
  CliTargetListResponse,
  UmeCliOverrideItem,
  FabricEdge,
  FabricSummary,
  TopologyDiscoverJob,
  TopologyOutsidePeer,
  TopologyTree,
  TopologyViewGraph,
  TopologyViewItem,
  LldpCollectDashboard,
  LldpCollectJobSummary,
  LldpCollectPolicy,
  ConfigSyncCycle,
  ConfigSyncDashboard,
  ConfigSyncPolicy,
  ConfigSyncTask,
  NeConfigSnapshotDetail,
  NeConfigSnapshotMeta,
  PortTrafficCompare,
  PortTrafficDashboard,
  PortTrafficDevice,
  PortTrafficDiscoverResponse,
  PortTrafficEvent,
  PortTrafficBoard,
  PortTrafficBoardPanelIn,
  PortTrafficBoardSummary,
  PortTrafficIfaceIn,
  PortTrafficSamples,
  PortTrafficTarget,
} from "../types";


export const AUTH_TOKEN_KEY = "netx_access_token";

export const getAuthToken = (): string | null => {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
};

export const setAuthToken = (token: string): void => {
  localStorage.setItem(AUTH_TOKEN_KEY, String(token || ""));
};

export const clearAuthToken = (): void => {
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    // ignore
  }
};

const authHeaders = (extra?: Record<string, string>): Record<string, string> => {
  const h: Record<string, string> = { accept: "application/json", ...(extra || {}) };
  const tok = getAuthToken();
  if (tok) h.authorization = `Bearer ${tok}`;
  return h;
};

const handleUnauthorized = (path: string): void => {
  if (path.startsWith("/v1/auth/login")) return;
  clearAuthToken();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    const next = `${window.location.pathname}${window.location.search || ""}`;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
  }
};

const parseApiResponse = async (res: Response): Promise<Record<string, unknown>> => {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    const snippet = text.replace(/\s+/g, " ").trim().slice(0, 160);
    throw new Error(res.ok ? "invalid_json_response" : `${res.status} ${snippet || res.statusText}`);
  }
};

/** Preserve FastAPI structured ``detail`` objects (e.g. connect_failed + ne). */
export class ApiRequestError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(formatApiDetail(detail) || `${status}`);
    this.name = "ApiRequestError";
    this.status = status;
    this.detail = detail;
  }
}

export function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg?: unknown }).msg || "");
        }
        return "";
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.message === "string" && obj.message.trim()) return obj.message;
    if (typeof obj.error === "string" && obj.error.trim()) return obj.error;
    try {
      return JSON.stringify(detail);
    } catch {
      /* fall through */
    }
  }
  return detail == null ? "" : String(detail);
}

export const apiGet = async <T,>(path: string): Promise<T> => {
  const res = await fetch(path, { headers: authHeaders() });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("401 unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return (await res.json()) as T;
};

export const apiPost = async <T,>(path: string, body: unknown): Promise<T> => {
  const res = await fetch(path, {
    method: "POST",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const apiPatch = async <T,>(path: string, body: unknown): Promise<T> => {
  const res = await fetch(path, {
    method: "PATCH",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const apiDelete = async <T,>(path: string): Promise<T> => {
  const res = await fetch(path, { method: "DELETE", headers: authHeaders() });
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const apiPut = async <T,>(path: string, body: unknown): Promise<T> => {
  const res = await fetch(path, {
    method: "PUT",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const fetchIntegrationStatus = () => apiGet<IntegrationStatus>("/v1/integrations/status");

export type OpsTaskItem = {
  kind: string;
  id: string;
  title: string;
  status: string;
  trigger: string;
  actor: string;
  started_at: string | null;
  updated_at: string | null;
  progress: string;
  inflight?: number;
  detail: string;
  href: string;
};

export type OpsTasksResponse = {
  generated_at: string;
  total: number;
  active: number;
  by_kind: Record<string, number>;
  by_status: Record<string, number>;
  items: OpsTaskItem[];
};

export const fetchOpsTasks = () => apiGet<OpsTasksResponse>("/v1/ops/tasks");

export type RuntimeMetrics = {
  uptime_sec?: number;
  pid?: number;
  thread_count?: number;
  host?: {
    source?: string;
    platform?: string;
    cpu_percent?: number;
    mem_percent?: number;
    mem_used_bytes?: number;
    mem_total_bytes?: number;
    mem_available_bytes?: number;
    error?: string;
  };
  db_pool?: {
    checked_out?: number;
    checked_in?: number;
    overflow?: number;
    size?: number;
  };
  cli_budget?: {
    limit?: number;
    in_use?: number;
    available?: number;
  };
  webcrt?: {
    active_sessions?: number;
    max_sessions?: number;
  };
  config_sync?: { running?: boolean };
  lldp_collect?: { running?: boolean };
  port_traffic?: { running?: boolean };
  device_schedulers?: {
    stale?: boolean;
    config_sync?: { running?: boolean };
    lldp_collect?: { running?: boolean };
    port_traffic?: { running?: boolean };
  };
  [key: string]: unknown;
};

export const fetchRuntimeMetrics = () => apiGet<RuntimeMetrics>("/v1/metrics/json");

export type AuditLogItem = {
  id: string;
  ts: string | null;
  actor_username: string;
  action: string;
  method: string;
  path: string;
  status_code: number;
  client_ip: string;
  detail: Record<string, unknown>;
};

export const fetchAuditLogs = (params?: {
  page?: number;
  pageSize?: number;
  username?: string;
  action?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params?.pageSize || 20)))));
  if (params?.username?.trim()) p.set("username", params.username.trim());
  if (params?.action?.trim()) p.set("action", params.action.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: AuditLogItem[] }>(
    `/v1/audit-logs?${p.toString()}`,
  );
};

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

export type ManagedNeStats = {
  total: number;
  by_status: Record<string, number>;
  tags: string[];
  no_tag_count: number;
  tag_counts: Record<string, number>;
  per_tag: Record<
    string,
    {
      total: number;
      by_status: Record<string, number>;
    }
  >;
};

export const fetchManagedNeStats = () => apiGet<ManagedNeStats>("/v1/managed-ne/meta/stats");

export const fetchIdsByTag = (tag: string | null) => {
  const url = tag ? `/v1/managed-ne/meta/ids-by-tag?tag=${encodeURIComponent(tag)}` : "/v1/managed-ne/meta/ids-by-tag";
  return apiGet<{ ids: string[] }>(url);
};

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

export const batchDeleteManagedNe = (ids: string[]) =>
  apiPost<{ ok: boolean; deleted: number }>("/v1/managed-ne/batch-delete", { ids });

export const connectTestManagedNe = (ids: string[]) =>
  apiPost<{ ok: boolean; submitted: number }>("/v1/managed-ne/connect-test", { ids });

export const batchApplyHopManagedNe = (
  ids: string[],
  hop: {
    hop_host: string;
    hop_port: number;
    hop_protocol: string;
    hop_username: string;
    hop_password: string;
    hop_command_template: string;
    hop_vrf: string;
    hop_vendor?: string;
    hop_target_auth_mode?: string;
  },
) => apiPost<{ ok: boolean; updated: number }>("/v1/managed-ne/batch-hop", { ids, hop });

export const batchApplyAccountManagedNe = (
  ids: string[],
  account: {
    username?: string;
    password?: string;
  },
) => apiPost<{ ok: boolean; updated: number }>("/v1/managed-ne/batch-account", { ids, account });

export const syncUmeManagedNe = () =>
  apiPost<{ inserted: number; updated: number; deleted: number; total_inventory: number }>("/v1/managed-ne/ume-sync", {});

export const deleteUmeManagedNe = () =>
  apiDelete<{ deleted: number }>("/v1/managed-ne/ume-sync");

export const managedNeImportTemplateUrl = (format: "xlsx" | "csv" = "xlsx") =>
  `/v1/managed-ne/import/template?format=${format}`;

export const downloadManagedNeImportTemplate = async (format: "xlsx" | "csv" = "xlsx"): Promise<void> => {
  const path = managedNeImportTemplateUrl(format);
  const res = await fetch(path, { headers: authHeaders() });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} template`);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const matched = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(cd);
  const filename = matched
    ? decodeURIComponent(matched[1].replace(/"/g, ""))
    : `managed_ne_import_template.${format}`;
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
};

export const importManagedNe = async (file: File): Promise<ManagedNeImportResult> => {
  const form = new FormData();
  form.append("file", file);
  const path = "/v1/managed-ne/import";
  // Do not set content-type — browser must add multipart boundary.
  const res = await fetch(path, { method: "POST", headers: authHeaders(), body: form });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(String((data as { detail?: string }).detail || `${res.status} import`));
  return data as ManagedNeImportResult;
};

export const fetchEligibleNe = (params: { page: number; pageSize: number; keyword?: string }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, params.page)));
  p.set("page_size", String(Math.max(1, Math.min(500, params.pageSize))));
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: EligibleNeItem[] }>(
    `/v1/ne-collections/eligible-ne?${p.toString()}`,
  );
};

export const createNeCollection = (body: {
  title?: string;
  commands: string;
  ne_ids?: string[];
  ume_ne_ids?: string[];
}) =>
  apiPost<CollectionJobItem>("/v1/ne-collections", body);

export const fetchNeCollections = (params: { page: number; pageSize: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, params.page)));
  p.set("page_size", String(Math.max(1, Math.min(100, params.pageSize))));
  return apiGet<{ total: number; page: number; page_size: number; items: CollectionJobItem[] }>(
    `/v1/ne-collections?${p.toString()}`,
  );
};

export const fetchCollectionDashboard = () =>
  apiGet<CollectionDashboard>("/v1/ne-collections/dashboard");

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

export const startCollectionJob = (jobId: string) =>
  apiPost<CollectionJobItem>(`/v1/ne-collections/${jobId}/start`, {});

export const restartCollectionJob = (jobId: string) =>
  apiPost<CollectionJobItem>(`/v1/ne-collections/${jobId}/restart`, {});

export const retryFailedCollectionJob = (jobId: string) =>
  apiPost<CollectionJobItem>(`/v1/ne-collections/${jobId}/retry-failed`, {});

export const deleteCollectionJob = (jobId: string) =>
  apiDelete<{ ok: boolean }>(`/v1/ne-collections/${jobId}`);

export const collectionRunDownloadUrl = (runId: string) => `/v1/ne-collections/runs/${runId}/download`;

export const collectionJobDownloadUrl = (jobId: string) => `/v1/ne-collections/${jobId}/download`;

export const fetchManagedNeById = (neId: string) =>
  apiGet<ManagedNeItem>(`/v1/managed-ne/${encodeURIComponent(neId)}`);

export type WebcrtSessionCreateResult = {
  session_id: string;
  ne_id: string;
  ne_name: string;
  ne_ip: string;
  protocol: string;
  cols: number;
  rows: number;
  ws_path: string;
  encoding?: string;
  state?: string;
  /** True when session used a vendor CLI hop (nested stelnet/telnet). */
  cli_hop?: boolean;
  /** True when SFTP channel opened on the same SSH transport. */
  sftp_ready?: boolean;
};

export const createWebcrtSession = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  cols?: number;
  rows?: number;
  encoding?: string;
  keepalive_sec?: number;
  post_login_commands?: string[];
  async_connect?: boolean;
  username?: string;
  password?: string;
}) => apiPost<WebcrtSessionCreateResult>("/v1/webcrt/sessions", body);

export type WebcrtQuickConnectResult = WebcrtSessionCreateResult & {
  ne: ManagedNeItem;
  ne_action: "created" | "updated" | "reused" | string;
  list_source: "webcrt" | "managed" | string;
};

export const quickConnectWebcrtSession = (body: {
  name?: string;
  ip_address: string;
  port?: number;
  protocol?: string;
  username?: string;
  password?: string;
  save_password?: boolean;
  /** Claim existing LLDP / incomplete ManagedNE → promote to webcrt. */
  ne_id?: string;
  cols?: number;
  rows?: number;
  encoding?: string;
  keepalive_sec?: number;
  async_connect?: boolean;
}) => apiPost<WebcrtQuickConnectResult>("/v1/webcrt/sessions/quick-connect", body);

export const closeWebcrtSession = (sessionId: string) =>
  apiDelete<{ ok: boolean; session_id: string; closed: boolean }>(
    `/v1/webcrt/sessions/${encodeURIComponent(sessionId)}`,
  );

export type WebcrtSftpItem = {
  name: string;
  size: number;
  mtime: number;
  is_dir: boolean;
  mode?: string;
  owner?: string;
  group?: string;
  uid?: number;
  gid?: number;
};

export type WebcrtSftpListResult = {
  ne_id: string;
  ne_name: string;
  path: string;
  items: WebcrtSftpItem[];
  truncated?: boolean;
  max_entries?: number;
};

export const webcrtSftpList = (body: { ne_id?: string; ume_ne_id?: string; path?: string }) =>
  apiPost<WebcrtSftpListResult>("/v1/webcrt/sftp/list", body);

export const webcrtSftpMkdir = (body: { ne_id?: string; ume_ne_id?: string; path: string }) =>
  apiPost<{ ok: boolean; path: string; ne_id: string }>("/v1/webcrt/sftp/mkdir", body);

export const webcrtSftpRemove = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  path: string;
  recursive?: boolean;
}) => apiPost<{ ok: boolean; path: string; ne_id: string }>("/v1/webcrt/sftp/remove", body);

export const webcrtSftpRename = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  old_path: string;
  new_path: string;
}) => apiPost<{ ok: boolean; old_path: string; new_path: string; ne_id: string }>("/v1/webcrt/sftp/rename", body);

export const webcrtSftpChmod = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  path: string;
  mode: string;
}) => apiPost<{ ok: boolean; path: string; mode: string; ne_id: string }>("/v1/webcrt/sftp/chmod", body);

export type WebcrtSftpTransferProgress = {
  loaded: number;
  total: number;
};

export type WebcrtSftpTransferOpts = {
  signal?: AbortSignal;
  onProgress?: (p: WebcrtSftpTransferProgress) => void;
  /** Extra attempts after the first failure. Default 2 (3 tries total). */
  retries?: number;
  onRetry?: (attempt: number, err: unknown) => void;
};

const isAbortError = (err: unknown): boolean => {
  const raw = String(err || "").toLowerCase();
  return raw.includes("aborted") || raw.includes("abort");
};

async function withSftpRetries<T>(
  run: () => Promise<T>,
  opts?: Pick<WebcrtSftpTransferOpts, "retries" | "signal" | "onRetry">,
): Promise<T> {
  const retries = Math.max(0, Number(opts?.retries ?? 2));
  let lastErr: unknown;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    if (opts?.signal?.aborted) throw new Error("aborted");
    try {
      return await run();
    } catch (err) {
      lastErr = err;
      if (isAbortError(err) || attempt >= retries) break;
      opts?.onRetry?.(attempt + 1, err);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr || "transfer_failed"));
}

async function webcrtSftpDownloadOnce(
  body: { ne_id?: string; ume_ne_id?: string; path: string },
  opts?: Pick<WebcrtSftpTransferOpts, "signal" | "onProgress">,
): Promise<Blob> {
  const path = "/v1/webcrt/sftp/download";
  const res = await fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal: opts?.signal,
  });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("401 unauthorized");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  const total = Number(res.headers.get("content-length") || 0);
  if (!res.body) {
    const blob = await res.blob();
    opts?.onProgress?.({ loaded: blob.size, total: total || blob.size });
    return blob;
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  try {
    for (;;) {
      if (opts?.signal?.aborted) {
        await reader.cancel().catch(() => undefined);
        throw new Error("aborted");
      }
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        loaded += value.byteLength;
        opts?.onProgress?.({ loaded, total });
      }
    }
  } catch (err) {
    if (opts?.signal?.aborted || isAbortError(err)) throw new Error("aborted");
    throw err;
  }
  return new Blob(chunks as BlobPart[], { type: "application/octet-stream" });
}

export async function webcrtSftpDownload(
  body: {
    ne_id?: string;
    ume_ne_id?: string;
    path: string;
  },
  optsOrProgress?: WebcrtSftpTransferOpts | ((p: WebcrtSftpTransferProgress) => void),
): Promise<Blob> {
  const opts: WebcrtSftpTransferOpts =
    typeof optsOrProgress === "function" ? { onProgress: optsOrProgress } : optsOrProgress || {};
  return withSftpRetries(
    () => webcrtSftpDownloadOnce(body, opts),
    opts,
  );
}

function webcrtSftpUploadOnce(
  body: {
    ne_id?: string;
    ume_ne_id?: string;
    remote_path: string;
    file: File;
  },
  opts?: Pick<WebcrtSftpTransferOpts, "signal" | "onProgress">,
): Promise<{ ok: boolean; path: string; size: number }> {
  const path = "/v1/webcrt/sftp/upload";
  const fd = new FormData();
  if (body.ne_id) fd.append("ne_id", body.ne_id);
  if (body.ume_ne_id) fd.append("ume_ne_id", body.ume_ne_id);
  fd.append("remote_path", body.remote_path);
  fd.append("file", body.file);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const onAbort = () => {
      try {
        xhr.abort();
      } catch {
        /* ignore */
      }
    };
    if (opts?.signal) {
      if (opts.signal.aborted) {
        reject(new Error("aborted"));
        return;
      }
      opts.signal.addEventListener("abort", onAbort, { once: true });
    }
    xhr.open("POST", path);
    const tok = getAuthToken();
    if (tok) xhr.setRequestHeader("Authorization", `Bearer ${tok}`);
    xhr.responseType = "text";
    xhr.upload.onprogress = (ev) => {
      if (!opts?.onProgress) return;
      opts.onProgress({
        loaded: Number(ev.loaded || 0),
        total: ev.lengthComputable ? Number(ev.total || 0) : body.file.size || 0,
      });
    };
    xhr.onload = () => {
      opts?.signal?.removeEventListener("abort", onAbort);
      if (xhr.status === 401) {
        handleUnauthorized(path);
        reject(new Error("401 unauthorized"));
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText || "{}") as { ok: boolean; path: string; size: number });
      } catch {
        reject(new Error("invalid_upload_response"));
      }
    };
    xhr.onerror = () => {
      opts?.signal?.removeEventListener("abort", onAbort);
      reject(new Error("network_error"));
    };
    xhr.onabort = () => {
      opts?.signal?.removeEventListener("abort", onAbort);
      reject(new Error("aborted"));
    };
    xhr.send(fd);
  });
}

export function webcrtSftpUpload(
  body: {
    ne_id?: string;
    ume_ne_id?: string;
    remote_path: string;
    file: File;
  },
  optsOrProgress?: WebcrtSftpTransferOpts | ((p: WebcrtSftpTransferProgress) => void),
): Promise<{ ok: boolean; path: string; size: number }> {
  const opts: WebcrtSftpTransferOpts =
    typeof optsOrProgress === "function" ? { onProgress: optsOrProgress } : optsOrProgress || {};
  return withSftpRetries(() => webcrtSftpUploadOnce(body, opts), opts);
}

export async function webcrtWsUrl(sessionId: string): Promise<string> {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const path = `/v1/webcrt/sessions/${encodeURIComponent(sessionId)}/ws`;
  let qs = "";
  try {
    const ticketResp = await apiPost<{ ticket: string; expires_in: number }>("/v1/webcrt/ws-ticket", {});
    if (ticketResp?.ticket) {
      qs = `?ws_ticket=${encodeURIComponent(ticketResp.ticket)}`;
    }
  } catch {
    qs = "";
  }
  // Optional override, e.g. ws://127.0.0.1:8890
  const override = String((import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_NETX_WS_BASE || "").trim();
  if (override) {
    return `${override.replace(/\/$/, "")}${path}${qs}`;
  }
  // Vite/preview: HTTP is proxied, but WS proxy is often flaky — hit API directly.
  const port = window.location.port;
  if (port === "5173" || port === "4173") {
    const apiHost = window.location.hostname === "localhost" ? "127.0.0.1" : window.location.hostname;
    return `${proto}//${apiHost}:8890${path}${qs}`;
  }
  return `${proto}//${window.location.host}${path}${qs}`;
}

export const fetchCliMeta = () => apiGet<CliMeta>("/v1/cli/meta");

export const fetchCliProfiles = () =>
  apiGet<{ items: CliConnectProfileItem[] }>("/v1/cli/profiles");

export const fetchCliTargets = (params: {
  source?: "managed" | "ume" | "webcrt" | "all";
  keyword?: string;
  page?: number;
  pageSize?: number;
}) => {
  const p = new URLSearchParams();
  p.set("source", params.source || "all");
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(500, Number(params.pageSize || 50)))));
  return apiGet<CliTargetListResponse>(`/v1/cli/targets?${p.toString()}`);
};

export const postUmeConnectTest = (umeNeIds: string[]) =>
  apiPost<{ ok: boolean; submitted: number }>("/v1/cli/ume-connect-test", { ume_ne_ids: umeNeIds });

export const fetchUmeCliOverride = (umeNeId: string) =>
  apiGet<UmeCliOverrideItem | null>(`/v1/cli/ume-overrides/${encodeURIComponent(umeNeId)}`);

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

export const fetchTopologyViews = () =>
  apiGet<{ total: number; items: TopologyViewItem[] }>("/v1/topology/views");

export const fetchTopologyTree = () => apiGet<TopologyTree>("/v1/topology/tree");

export const createTopologyFolder = (body: { name: string; kind?: string; parent_id?: string; sort_order?: number }) =>
  apiPost<{
    id: string;
    parent_id: string;
    kind: string;
    name: string;
    sort_order: number;
    is_system: boolean;
  }>("/v1/topology/folders", body);

export const updateTopologyFolder = (
  folderId: string,
  body: { name?: string; parent_id?: string; sort_order?: number },
) =>
  apiPatch<{
    id: string;
    parent_id: string;
    kind: string;
    name: string;
    sort_order: number;
    is_system: boolean;
  }>(`/v1/topology/folders/${encodeURIComponent(folderId)}`, body);

export const deleteTopologyFolder = (folderId: string, force = false) =>
  apiDelete<{ ok: boolean; folder_id: string; deleted: boolean }>(
    `/v1/topology/folders/${encodeURIComponent(folderId)}?force=${force ? "true" : "false"}`,
  );

export const createTopologyView = (body: {
  name: string;
  remark?: string;
  filter?: Record<string, unknown>;
  folder_id?: string;
  kind?: string;
  role?: string;
  sort_order?: number;
}) => apiPost<TopologyViewItem>("/v1/topology/views", body);

export const populateTopologyView = (
  viewId: string,
  body?: { dry_run?: boolean; membership?: Record<string, unknown>; freeze_after?: boolean },
) =>
  apiPost<{
    view_id: string;
    dry_run: boolean;
    candidate_count: number;
    would_add: number;
    added: number;
    max_nodes: number;
    truncated: boolean;
    outside_peers: TopologyOutsidePeer[];
    graph: TopologyViewGraph | null;
  }>(`/v1/topology/views/${encodeURIComponent(viewId)}/populate`, body || {});

export const fetchTopologyViewGraph = (viewId: string) =>
  apiGet<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}`);

export const updateTopologyView = (
  viewId: string,
  body: {
    name?: string;
    remark?: string;
    filter?: Record<string, unknown>;
    viewport?: Record<string, unknown>;
    folder_id?: string;
    role?: string;
    sort_order?: number;
  },
) => apiPatch<TopologyViewItem>(`/v1/topology/views/${encodeURIComponent(viewId)}`, body);

export const deleteTopologyView = (viewId: string, force = false) =>
  apiDelete<{ ok: boolean; view_id: string; deleted: boolean }>(
    `/v1/topology/views/${encodeURIComponent(viewId)}?force=${force ? "true" : "false"}`,
  );

export const patchTopologyPositions = (
  viewId: string,
  positions: Array<{
    fabric_node_id: string;
    x?: number;
    y?: number;
    label?: string;
    locked?: boolean;
  }>,
) =>
  apiPatch<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}/positions`, {
    positions,
  });

export const addTopologyViewNodes = (
  viewId: string,
  body: {
    managed_ne_ids?: string[];
    ume_ne_ids?: string[];
    fabric_node_ids?: string[];
    layout?: string;
  },
) => apiPost<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}/nodes`, body);

export const createTopologyPlaceholder = (
  viewId: string,
  body: { name: string; ip_address?: string; x?: number; y?: number },
) =>
  apiPost<TopologyViewGraph>(
    `/v1/topology/views/${encodeURIComponent(viewId)}/nodes/create-placeholder`,
    body,
  );

export const removeTopologyViewNodes = (viewId: string, fabricNodeIds: string[]) =>
  apiPost<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}/nodes/remove`, {
    fabric_node_ids: fabricNodeIds,
  });

export const projectTopologyNeighbors = (
  viewId: string,
  body?: { seed_fabric_node_ids?: string[]; managed_ne_ids?: string[]; dry_run?: boolean },
) =>
  apiPost<TopologyViewGraph>(
    `/v1/topology/views/${encodeURIComponent(viewId)}/project-neighbors`,
    body || {},
  );

export const patchTopologyEdgeStyle = (
  viewId: string,
  body: { fabric_edge_id: string; stroke_color?: string; stroke_width?: number; line_style?: string },
) =>
  apiPatch<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}/edge-style`, body);

export const fetchFabricSummary = () => apiGet<FabricSummary>("/v1/topology/fabric/summary");

export const fetchFabricNodes = (params?: {
  keyword?: string;
  role?: string;
  regionFolderId?: string;
  unmatched?: string;
  linkStatus?: string;
  page?: number;
  pageSize?: number;
}) => {
  const p = new URLSearchParams();
  if (params?.keyword) p.set("keyword", params.keyword);
  if (params?.role) p.set("role", params.role);
  if (params?.regionFolderId) p.set("region_folder_id", params.regionFolderId);
  if (params?.unmatched) p.set("unmatched", params.unmatched);
  if (params?.linkStatus) p.set("link_status", params.linkStatus);
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(500, Number(params?.pageSize || 50)))));
  return apiGet<{
    total: number;
    page: number;
    page_size: number;
    items: import("../types").FabricNodeSearchHit[];
  }>(`/v1/topology/fabric/nodes?${p.toString()}`);
};

export const matchFabricNodes = (body: {
  pattern: string;
  match_field?: string;
  sample_limit?: number;
}) =>
  apiPost<{
    pattern: string;
    match_field: string;
    total_matched: number;
    samples: Array<Record<string, string>>;
    fabric_node_ids: string[];
  }>("/v1/topology/fabric/nodes/match", body);

export const bulkTagFabricNodes = (body: {
  fabric_node_ids?: string[];
  pattern?: string;
  match_field?: string;
  role?: string | null;
  region_folder_id?: string | null;
  dry_run?: boolean;
}) =>
  apiPost<{
    dry_run: boolean;
    matched: number;
    updated: number;
    role?: string | null;
    region_folder_id?: string | null;
    samples: Array<Record<string, string>>;
  }>("/v1/topology/fabric/nodes/tags/bulk", body);

export const patchFabricNodeTags = (
  fabricNodeId: string,
  body: { role?: string | null; region_folder_id?: string | null },
) =>
  apiPatch<import("../types").FabricNodeSearchHit>(
    `/v1/topology/fabric/nodes/${encodeURIComponent(fabricNodeId)}/tags`,
    body,
  );

export const deleteFabricNode = (fabricNodeId: string) =>
  apiDelete<{ deleted: number; edges_deleted: number; placements_deleted: number }>(
    `/v1/topology/fabric/nodes/${encodeURIComponent(fabricNodeId)}`,
  );

export const deleteFabricNodes = (fabricNodeIds: string[]) =>
  apiPost<{ deleted: number; edges_deleted: number; placements_deleted: number }>(
    "/v1/topology/fabric/nodes/delete",
    { fabric_node_ids: fabricNodeIds },
  );

/** Hard-delete topology/LLDP placeholders (ManagedNE + fabric + edges). */
export const purgePlaceholderFabricNodes = (fabricNodeIds: string[]) =>
  apiPost<{
    deleted: number;
    edges_deleted: number;
    placements_deleted: number;
    managed_deleted: number;
    membership_views: number;
  }>("/v1/topology/fabric/nodes/purge-placeholders", {
    fabric_node_ids: fabricNodeIds,
  });

export const generateTopologySlices = (body: {
  folder_id: string;
  template: "core_only" | "core_agg" | "agg_access";
  dry_run?: boolean;
  max_nodes?: number;
  seed_physical_cores?: boolean;
}) => apiPost<import("../types").SliceGenerateResult>("/v1/topology/slices/generate", body);

export const searchFabricNodes = (params?: { q?: string; page?: number; pageSize?: number }) => {
  const p = new URLSearchParams();
  if (params?.q) p.set("q", params.q);
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params?.pageSize || 50)))));
  return apiGet<{
    total: number;
    page: number;
    page_size: number;
    items: import("../types").FabricNodeSearchHit[];
  }>(`/v1/topology/fabric/nodes/search?${p.toString()}`);
};

export const fetchFabricEdges = (params?: {
  keyword?: string;
  status?: string;
  source?: string;
  nodeId?: string;
  page?: number;
  pageSize?: number;
}) => {
  const p = new URLSearchParams();
  if (params?.keyword) p.set("keyword", params.keyword);
  if (params?.status) p.set("status", params.status);
  if (params?.source) p.set("source", params.source);
  if (params?.nodeId) p.set("node_id", params.nodeId);
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params?.pageSize || 20)))));
  return apiGet<{ total: number; page: number; page_size: number; items: FabricEdge[] }>(
    `/v1/topology/fabric/edges?${p.toString()}`,
  );
};

export const startLldpDiscover = (body?: {
  scope?: "all_inventory" | "ne_ids";
  ne_ids?: string[];
  auto_add_unmatched?: boolean;
  concurrency?: number;
  trigger_mode?: "manual" | "schedule" | "topology";
}) => apiPost<TopologyDiscoverJob>("/v1/topology/fabric/discover", body || {});

export const fetchLldpDiscoverJob = (
  jobId: string,
  params?: { page?: number; pageSize?: number },
) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params?.pageSize || 20)))));
  return apiGet<TopologyDiscoverJob>(
    `/v1/topology/fabric/discover/${encodeURIComponent(jobId)}?${p.toString()}`,
  );
};

export const fetchLldpCollectDashboard = () =>
  apiGet<LldpCollectDashboard>("/v1/topology/lldp-collect/dashboard");

export const updateLldpCollectPolicy = (body: Partial<LldpCollectPolicy>) =>
  apiPut<LldpCollectPolicy>("/v1/topology/lldp-collect/policy", body);

export const startLldpCollect = () =>
  apiPost<{ ok: boolean; job: TopologyDiscoverJob }>("/v1/topology/lldp-collect/start", {});

export const pauseLldpCollectJob = (jobId: string) =>
  apiPost<TopologyDiscoverJob>(`/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}/pause`, {});

export const resumeLldpCollectJob = (jobId: string) =>
  apiPost<TopologyDiscoverJob>(`/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}/resume`, {});

export const stopLldpCollectJob = (jobId: string) =>
  apiPost<TopologyDiscoverJob>(`/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}/stop`, {});

export const fetchLldpCollectJobs = (params: { page?: number; pageSize?: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  return apiGet<{ total: number; page: number; page_size: number; items: LldpCollectJobSummary[] }>(
    `/v1/topology/lldp-collect/jobs?${p.toString()}`,
  );
};

export const fetchLldpCollectJob = (
  jobId: string,
  params?: { page?: number; pageSize?: number },
) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params?.pageSize || 20)))));
  return apiGet<TopologyDiscoverJob>(
    `/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}?${p.toString()}`,
  );
};

export const createFabricManualEdge = (body: {
  a_node_id: string;
  b_node_id: string;
  a_port?: string;
  b_port?: string;
}) => apiPost<{ ok: boolean; action: string; edge: Record<string, unknown> }>("/v1/topology/fabric/edges", body);

export const deleteFabricEdges = (edgeIds: string[]) =>
  apiPost<{ ok: boolean; deleted: number }>("/v1/topology/fabric/edges/delete", {
    edge_ids: edgeIds,
  });

export const deleteFabricEdge = (edgeId: string) =>
  apiDelete<{ ok: boolean; deleted: number }>(
    `/v1/topology/fabric/edges/${encodeURIComponent(edgeId)}`,
  );

/** Back-compat aliases used by older call sites during cutover. */
export const fetchTopologyMaps = fetchTopologyViews;
export const createTopologyMap = createTopologyView;
export const fetchTopologyGraph = fetchTopologyViewGraph;
export const updateTopologyMap = updateTopologyView;
export const deleteTopologyMap = deleteTopologyView;

export const fetchConfigSyncDashboard = () =>
  apiGet<ConfigSyncDashboard>("/v1/config-sync/dashboard");

export const fetchConfigSyncPolicy = () => apiGet<ConfigSyncPolicy>("/v1/config-sync/policy");

export const updateConfigSyncPolicy = (body: Partial<ConfigSyncPolicy>) =>
  apiPut<ConfigSyncPolicy>("/v1/config-sync/policy", body);

export const fetchConfigSyncCycles = (params: { page?: number; pageSize?: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  return apiGet<{ total: number; page: number; page_size: number; items: ConfigSyncCycle[] }>(
    `/v1/config-sync/cycles?${p.toString()}`,
  );
};

export const createConfigSyncCycle = (body: { mode: "full" | "retry_failed"; cycle_id?: string }) =>
  apiPost<ConfigSyncCycle>("/v1/config-sync/cycles", body);

export const pauseConfigSyncCycle = (cycleId: string) =>
  apiPost<ConfigSyncCycle>(`/v1/config-sync/cycles/${encodeURIComponent(cycleId)}/pause`, {});

export const resumeConfigSyncCycle = (cycleId: string) =>
  apiPost<ConfigSyncCycle>(`/v1/config-sync/cycles/${encodeURIComponent(cycleId)}/resume`, {});

export const stopConfigSyncCycle = (cycleId: string) =>
  apiPost<ConfigSyncCycle>(`/v1/config-sync/cycles/${encodeURIComponent(cycleId)}/stop`, {});

export const fetchConfigSyncCycleTasks = (params: {
  cycleId: string;
  page?: number;
  pageSize?: number;
  status?: string;
  keyword?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params.pageSize || 20)))));
  if (params.status) p.set("status", params.status);
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: ConfigSyncTask[] }>(
    `/v1/config-sync/cycles/${encodeURIComponent(params.cycleId)}/tasks?${p.toString()}`,
  );
};

export const fetchNeConfigSnapshots = (params: {
  page?: number;
  pageSize?: number;
  keyword?: string;
  source?: string;
  vendor?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  if (params.source) p.set("source", params.source);
  if (params.vendor?.trim()) p.set("vendor", params.vendor.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: NeConfigSnapshotMeta[] }>(
    `/v1/config-sync/snapshots?${p.toString()}`,
  );
};

export const fetchNeConfigSnapshotDetail = (
  source: string,
  targetId: string,
  field: "primary" | "alt" | "both" = "both",
) =>
  apiGet<NeConfigSnapshotDetail>(
    `/v1/config-sync/snapshots/${encodeURIComponent(source)}/${encodeURIComponent(targetId)}?field=${field}`,
  );

export const downloadNeConfigSnapshot = async (
  source: string,
  targetId: string,
  field: "primary" | "alt" | "both" = "primary",
): Promise<void> => {
  const path =
    `/v1/config-sync/snapshots/${encodeURIComponent(source)}/${encodeURIComponent(targetId)}` +
    `/download?field=${encodeURIComponent(field)}`;
  const res = await fetch(path, { headers: authHeaders() });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} download`);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  const filename = star
    ? decodeURIComponent(star[1])
    : plain
      ? plain[1]
      : `ne-config-${source}-${targetId}.txt`;
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
};

export const fetchPortTrafficDashboard = () =>
  apiGet<PortTrafficDashboard>("/v1/port-traffic/dashboard");

export const fetchPortTrafficDevices = (params: { page?: number; pageSize?: number }) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  return apiGet<{ total: number; page: number; page_size: number; items: PortTrafficDevice[] }>(
    `/v1/port-traffic/devices?${p.toString()}`,
  );
};

/** @deprecated */
export const fetchPortTrafficTasks = fetchPortTrafficDevices;

export const createPortTrafficDevice = (body: {
  source: "managed" | "ume";
  ne_id: string;
  ne_name?: string;
  ne_ip?: string;
  vendor?: string;
  note?: string;
  interval_sec?: number;
  retention_days?: number;
  concurrency?: number;
  interfaces?: PortTrafficIfaceIn[];
  start_now?: boolean;
}) => apiPost<PortTrafficDevice>("/v1/port-traffic/devices", body);

export const updatePortTrafficDevice = (
  deviceId: string,
  body: {
    note?: string;
    interval_sec?: number;
    retention_days?: number;
    concurrency?: number;
    ne_name?: string;
    ne_ip?: string;
    vendor?: string;
  },
) => apiPatch<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}`, body);

export const startPortTrafficDevice = (deviceId: string) =>
  apiPost<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/start`, {});

export const pausePortTrafficDevice = (deviceId: string) =>
  apiPost<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/pause`, {});

export const stopPortTrafficDevice = (deviceId: string) =>
  apiPost<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/stop`, {});

export const collectPortTrafficNow = (deviceId: string) =>
  apiPost<PortTrafficDevice & { ok: boolean; started: boolean; reason?: string }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/collect-now`,
    {},
  );

export const deletePortTrafficDevice = (deviceId: string) =>
  apiDelete<{ ok: boolean; id: string }>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}`);

export const rebindPortTrafficDevice = (deviceId: string, body: { ne_id: string }) =>
  apiPost<PortTrafficDevice>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/rebind`,
    body,
  );

export const fetchPortTrafficTargets = (deviceId: string) =>
  apiGet<{ items: PortTrafficTarget[] }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/targets`,
  );

export const fetchPortTrafficEvents = (deviceId: string, limit = 100) =>
  apiGet<{ items: PortTrafficEvent[]; total: number }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/events?limit=${limit}`,
  );

export const putPortTrafficInterfaces = (deviceId: string, interfaces: PortTrafficIfaceIn[]) =>
  apiPut<{ items: PortTrafficTarget[] }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/interfaces`,
    { interfaces },
  );

export const discoverPortTrafficPorts = (body: { source: "managed" | "ume"; id: string }) =>
  apiPost<PortTrafficDiscoverResponse>("/v1/port-traffic/discover/ports", body);

export const fetchPortTrafficSamples = (params: {
  targetId: string;
  from?: string;
  to?: string;
}) => {
  const p = new URLSearchParams();
  p.set("target_id", params.targetId);
  if (params.from) p.set("from", params.from);
  if (params.to) p.set("to", params.to);
  return apiGet<PortTrafficSamples>(`/v1/port-traffic/samples?${p.toString()}`);
};

export const fetchPortTrafficCompare = (params: {
  targetId: string;
  rangeHours: number;
  baseline: string;
  offsetHours?: number;
  aheadHours?: number;
  baselineTargetId?: string;
  to?: string;
}) => {
  const p = new URLSearchParams();
  p.set("target_id", params.targetId);
  p.set("range_hours", String(params.rangeHours));
  p.set("baseline", params.baseline);
  if (params.offsetHours != null) p.set("offset_hours", String(params.offsetHours));
  if (params.aheadHours != null && params.aheadHours > 0) {
    p.set("ahead_hours", String(params.aheadHours));
  }
  if (params.baselineTargetId) p.set("baseline_target_id", params.baselineTargetId);
  if (params.to) p.set("to", params.to);
  return apiGet<PortTrafficCompare>(`/v1/port-traffic/compare?${p.toString()}`);
};

export const fetchPortTrafficBoards = () =>
  apiGet<{ items: PortTrafficBoardSummary[] }>("/v1/port-traffic/boards");

export const fetchPortTrafficBoard = (boardId: string) =>
  apiGet<PortTrafficBoard>(`/v1/port-traffic/boards/${encodeURIComponent(boardId)}`);

export const createPortTrafficBoard = (body: {
  name: string;
  remark?: string;
  cols?: number;
  panels?: PortTrafficBoardPanelIn[];
}) => apiPost<PortTrafficBoard>("/v1/port-traffic/boards", body);

export const updatePortTrafficBoard = (
  boardId: string,
  body: { name?: string; remark?: string; cols?: number },
) => apiPatch<PortTrafficBoard>(`/v1/port-traffic/boards/${encodeURIComponent(boardId)}`, body);

export const putPortTrafficBoardPanels = (boardId: string, panels: PortTrafficBoardPanelIn[]) =>
  apiPut<PortTrafficBoard>(`/v1/port-traffic/boards/${encodeURIComponent(boardId)}/panels`, {
    panels,
  });

export const deletePortTrafficBoard = (boardId: string) =>
  apiDelete<{ ok: boolean; id: string }>(
    `/v1/port-traffic/boards/${encodeURIComponent(boardId)}`,
  );
