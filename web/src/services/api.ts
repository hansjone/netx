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
  UmeKeyAlertMonitorResponse,
  UmeNeItem,
  UmeSyncStatusResponse,
  UmeTokenStatus,
  CliConnectProfileItem,
  CliMeta,
  CliTargetListResponse,
  UmeCliOverrideItem,
  TopologyDiscoverOut,
  TopologyDiscoverNeResult,
  TopologyDiscoverStreamHandlers,
  TopologyGraph,
  TopologyMapItem,
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
    throw new Error(String(data.detail || "unauthorized"));
  }
  if (!res.ok) throw new Error(String(data.detail || `${res.status} ${path}`));
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
    throw new Error(String(data.detail || "unauthorized"));
  }
  if (!res.ok) throw new Error(String(data.detail || `${res.status} ${path}`));
  return data as T;
};

export const apiDelete = async <T,>(path: string): Promise<T> => {
  const res = await fetch(path, { method: "DELETE", headers: authHeaders() });
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error(String(data.detail || "unauthorized"));
  }
  if (!res.ok) throw new Error(String(data.detail || `${res.status} ${path}`));
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
    throw new Error(String(data.detail || "unauthorized"));
  }
  if (!res.ok) throw new Error(String(data.detail || `${res.status} ${path}`));
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

export const importManagedNe = async (file: File): Promise<ManagedNeImportResult> => {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/v1/managed-ne/import", { method: "POST", body: form });
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
  /** True when session used a vendor CLI hop (nested stelnet/telnet). */
  cli_hop?: boolean;
};

export const createWebcrtSession = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  cols?: number;
  rows?: number;
}) => apiPost<WebcrtSessionCreateResult>("/v1/webcrt/sessions", body);

export const closeWebcrtSession = (sessionId: string) =>
  apiDelete<{ ok: boolean; session_id: string; closed: boolean }>(
    `/v1/webcrt/sessions/${encodeURIComponent(sessionId)}`,
  );

export const webcrtWsUrl = (sessionId: string): string => {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const path = `/v1/webcrt/sessions/${encodeURIComponent(sessionId)}/ws`;
  const tok = getAuthToken();
  const qs = tok ? `?access_token=${encodeURIComponent(tok)}` : "";
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
};

export const fetchCliMeta = () => apiGet<CliMeta>("/v1/cli/meta");

export const fetchCliProfiles = () =>
  apiGet<{ items: CliConnectProfileItem[] }>("/v1/cli/profiles");

export const fetchCliTargets = (params: {
  source?: "managed" | "ume" | "all";
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

export const fetchTopologyMaps = () =>
  apiGet<{ total: number; items: TopologyMapItem[] }>("/v1/topology/maps");

export const createTopologyMap = (body: { name: string; remark?: string }) =>
  apiPost<TopologyMapItem>("/v1/topology/maps", body);

export const fetchTopologyGraph = (mapId: string) =>
  apiGet<TopologyGraph>(`/v1/topology/maps/${encodeURIComponent(mapId)}`);

export const updateTopologyMap = (mapId: string, body: { name?: string; remark?: string }) =>
  apiPatch<TopologyMapItem>(`/v1/topology/maps/${encodeURIComponent(mapId)}`, body);

export const deleteTopologyMap = (mapId: string) =>
  apiDelete<{ ok: boolean; map_id: string; deleted: boolean }>(
    `/v1/topology/maps/${encodeURIComponent(mapId)}`,
  );

export const putTopologyGraph = (
  mapId: string,
  body: {
    nodes: Array<{
      id: string;
      managed_ne_id?: string;
      ume_ne_id?: string;
      label?: string;
      x?: number;
      y?: number;
    }>;
    edges: Array<{
      id: string;
      source_node_id: string;
      target_node_id: string;
      source_port?: string;
      target_port?: string;
      source?: string;
    }>;
  },
) => apiPut<TopologyGraph>(`/v1/topology/maps/${encodeURIComponent(mapId)}/graph`, body);

export const discoverTopologyNeighbors = (
  mapId: string,
  body?: { protocol?: string; ne_ids?: string[] },
) =>
  apiPost<TopologyDiscoverOut>(
    `/v1/topology/maps/${encodeURIComponent(mapId)}/discover`,
    body || {},
  );

function parseSseChunks(buffer: string): { events: Array<{ event: string; data: string }>; rest: string } {
  const events: Array<{ event: string; data: string }> = [];
  let rest = buffer;
  while (true) {
    const sep = rest.indexOf("\n\n");
    if (sep < 0) break;
    const raw = rest.slice(0, sep);
    rest = rest.slice(sep + 2);
    let event = "message";
    const dataLines: string[] = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length) events.push({ event, data: dataLines.join("\n") });
  }
  return { events, rest };
}

export async function discoverTopologyNeighborsStream(
  mapId: string,
  body: { protocol?: string; ne_ids?: string[] } | undefined,
  handlers: TopologyDiscoverStreamHandlers,
  signal?: AbortSignal,
): Promise<TopologyDiscoverOut> {
  const res = await fetch(`/v1/topology/maps/${encodeURIComponent(mapId)}/discover/stream`, {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      "content-type": "application/json",
    },
    body: JSON.stringify(body || {}),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `discover_stream_failed:${res.status}`);
  }
  if (!res.body) throw new Error("discover_stream_empty_body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finalReport: TopologyDiscoverOut | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parsed = parseSseChunks(buf);
    buf = parsed.rest;
    for (const item of parsed.events) {
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(item.data) as Record<string, unknown>;
      } catch {
        continue;
      }
      const type = String(payload.type || item.event || "");
      if (type === "start") {
        handlers.onStart?.({
          map_id: String(payload.map_id || ""),
          protocol: String(payload.protocol || ""),
          total: Number(payload.total || 0),
        });
      } else if (type === "ne_start") {
        handlers.onNeStart?.({
          index: Number(payload.index || 0),
          total: Number(payload.total || 0),
          ne_id: String(payload.ne_id || ""),
          ne_name: String(payload.ne_name || ""),
          ne_ip: String(payload.ne_ip || ""),
        });
      } else if (type === "ne_result") {
        handlers.onNeResult?.({
          index: Number(payload.index || 0),
          total: Number(payload.total || 0),
          result: payload.result as TopologyDiscoverNeResult,
          edges_added: Number(payload.edges_added || 0),
          edges_updated: Number(payload.edges_updated || 0),
        });
      } else if (type === "done") {
        finalReport = payload.report as TopologyDiscoverOut;
        if (finalReport) handlers.onDone?.(finalReport);
      } else if (type === "error") {
        const detail = String(payload.detail || "discover_stream_error");
        handlers.onError?.(detail);
        throw new Error(detail);
      }
    }
  }

  if (!finalReport) throw new Error("discover_stream_incomplete");
  return finalReport;
}
