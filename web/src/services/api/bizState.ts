import { ApiRequestError, apiDelete, apiGet, apiPatch, apiPost, apiPut, authHeaders, fetchCreds, handleUnauthorized, parseApiResponse } from "./http";

/* ---- biz_state (business state monitoring) ---- */

export const bizStateListProfiles = (params?: {
  vendor?: string;
  device_type?: string;
  kind?: string;
}) => {
  const q = new URLSearchParams();
  if (params?.vendor) q.set("vendor", params.vendor);
  if (params?.device_type) q.set("device_type", params.device_type);
  if (params?.kind) q.set("kind", params.kind);
  const qs = q.toString();
  return apiGet<{ items: Record<string, unknown>[] }>(
    `/v1/biz-state/profiles${qs ? `?${qs}` : ""}`,
  );
};

export const bizStateDiscover = (body: Record<string, unknown>) =>
  apiPost<{
    ok: boolean;
    error?: string;
    command?: string;
    cache_hit?: boolean;
    pair_mode?: boolean;
    discover_profile_id?: string;
    candidates?: Array<{
      value: string;
      label: string;
      rd?: string;
      protocols?: string;
      bindings?: Record<string, string>;
    }>;
    raw_preview?: string;
  }>("/v1/biz-state/discover", body);

export const bizStateSetBindings = (
  taskId: string,
  itemId: string,
  bindings: Array<{ placeholder: string; value: string }>,
) =>
  apiPut<Record<string, unknown>>(
    `/v1/biz-state/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}/bindings`,
    { bindings },
  );

export const bizStateListTasks = (purpose = "") => {
  const q = purpose ? `?purpose=${encodeURIComponent(purpose)}` : "";
  return apiGet<{ items: Record<string, unknown>[] }>(`/v1/biz-state/tasks${q}`);
};

export const bizStateCreateTask = (body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>("/v1/biz-state/tasks", body);

export const bizStateGetTask = (taskId: string) =>
  apiGet<Record<string, unknown>>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}`);

export const bizStatePatchTask = (taskId: string, body: Record<string, unknown>) =>
  apiPatch<Record<string, unknown>>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}`, body);

export const bizStatePauseTask = (taskId: string) =>
  apiPost<Record<string, unknown>>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}/pause`, {});

export const bizStateStartTask = (taskId: string) =>
  apiPost<Record<string, unknown>>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}/start`, {});

export const bizStateDeleteTask = (taskId: string) =>
  apiDelete<{ ok: boolean }>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}`);

export const bizStateCollectNow = (taskId: string) =>
  apiPost<{
    ok: boolean;
    started?: boolean;
    queued?: boolean;
    reason?: string;
    batch_id?: string;
    collect_running?: boolean;
  }>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}/collect`, {});

export const bizStateCollectStop = (taskId: string) =>
  apiPost<{
    ok: boolean;
    stopped?: boolean;
    reason?: string;
    cancelled_batches?: string[];
    signaled_batches?: string[];
  }>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}/collect/stop`, {});

type BizStateImportResult = {
  ok: boolean;
  started?: boolean;
  queued?: boolean;
  reason?: string;
  batch_id?: string;
  task_id?: string;
  vendor_key?: string;
  filename?: string;
  ne_name?: string;
  created_task?: boolean;
  segments_preview?: Record<string, number>;
  collect_running?: boolean;
};

export const bizStateImportLog = async (
  taskId: string,
  file: File,
  opts?: { vendor_key?: string },
): Promise<BizStateImportResult> => {
  const form = new FormData();
  form.append("file", file);
  if (opts?.vendor_key?.trim()) form.append("vendor_key", opts.vendor_key.trim());
  const path = `/v1/biz-state/tasks/${encodeURIComponent(taskId)}/import`;
  const res = await fetch(path, {
    method: "POST",
    headers: authHeaders(),
    body: form,
    credentials: fetchCreds,
  });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  const data = await parseApiResponse(res);
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} import`);
  return data as BizStateImportResult;
};

/** Standalone offline import: creates source=import task (no inventory NE). */
export const bizStateImportLogStandalone = async (
  file: File,
  opts?: { vendor_key?: string; ne_name?: string; note?: string },
): Promise<BizStateImportResult> => {
  const form = new FormData();
  form.append("file", file);
  form.append("vendor_key", (opts?.vendor_key || "zte").trim() || "zte");
  if (opts?.ne_name?.trim()) form.append("ne_name", opts.ne_name.trim());
  if (opts?.note?.trim()) form.append("note", opts.note.trim());
  const path = "/v1/biz-state/import";
  const res = await fetch(path, {
    method: "POST",
    headers: authHeaders(),
    body: form,
    credentials: fetchCreds,
  });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  const data = await parseApiResponse(res);
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} import`);
  return data as BizStateImportResult;
};

export const bizStateListBatches = (taskId: string, limit = 50) =>
  apiGet<{ items: Record<string, unknown>[] }>(
    `/v1/biz-state/tasks/${encodeURIComponent(taskId)}/batches?limit=${limit}`,
  );

export const bizStateSetBatchBaseline = (batchId: string, marked: boolean) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-state/batches/${encodeURIComponent(batchId)}/baseline`,
    { marked },
  );

export const bizStateSetBatchAlias = (batchId: string, alias: string) =>
  apiPatch<Record<string, unknown>>(
    `/v1/biz-state/batches/${encodeURIComponent(batchId)}/alias`,
    { alias },
  );

export const bizStateDeleteBatch = (batchId: string) =>
  apiDelete<{ ok: boolean }>(`/v1/biz-state/batches/${encodeURIComponent(batchId)}`);

export const bizStateBulkDeleteBatches = (batchIds: string[]) =>
  apiPost<{
    ok: boolean;
    deleted: string[];
    skipped: { batch_id: string; reasons: string[] }[];
    deleted_count: number;
  }>("/v1/biz-state/batches/bulk-delete", { batch_ids: batchIds });

export const bizStatePurgeTask = (taskId: string) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-state/tasks/${encodeURIComponent(taskId)}/purge`,
    {},
  );

export const bizStateGetBatch = (batchId: string) =>
  apiGet<Record<string, unknown>>(`/v1/biz-state/batches/${encodeURIComponent(batchId)}`);

export const bizStateGetTaskProgress = (taskId: string) =>
  apiGet<Record<string, unknown>>(`/v1/biz-state/tasks/${encodeURIComponent(taskId)}/progress`);

export const bizStateListBatchMetricRows = (params: {
  batchId: string;
  metricId: string;
  page?: number;
  pageSize?: number;
  kw?: string;
  column?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params.pageSize || 50)))));
  if (params.kw) p.set("kw", params.kw);
  if (params.column) p.set("column", params.column);
  return apiGet<{
    batch_id: string;
    metric_id: string;
    total: number;
    page: number;
    page_size: number;
    pages: number;
    columns: Array<{ key: string; header: string; role?: string; is_key?: boolean }>;
    items: Record<string, unknown>[];
  }>(
    `/v1/biz-state/batches/${encodeURIComponent(params.batchId)}/metrics/${encodeURIComponent(params.metricId)}?${p.toString()}`,
  );
};

export const bizStateGetBatchCommand = (batchId: string, commandId: string) =>
  apiGet<{
    id: string;
    batch_id: string;
    raw_command: string;
    metric_id: string;
    parse_status: string;
    row_count: number;
    raw_line_count?: number;
    declared_total?: number;
    message: string;
    raw_text: string;
    collected_at?: string | null;
    device?: { ne_id?: string; ne_name?: string; ne_ip?: string };
  }>(
    `/v1/biz-state/batches/${encodeURIComponent(batchId)}/commands/${encodeURIComponent(commandId)}`,
  );

/** Download one command's raw CLI output as .txt */
export const bizStateDownloadCommandRaw = async (batchId: string, commandId: string): Promise<void> => {
  const path =
    `/v1/biz-state/batches/${encodeURIComponent(batchId)}/commands/${encodeURIComponent(commandId)}/raw.txt`;
  const res = await fetch(path, { method: "GET", credentials: fetchCreds, headers: authHeaders() });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} download raw`);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename="?([^";]+)"?/i.exec(cd);
  const filename = m?.[1] || `biz_state_${commandId}.txt`;
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

export const bizStateDownloadExport = async (batchId: string): Promise<void> => {
  const path = `/v1/biz-state/batches/${encodeURIComponent(batchId)}/export`;
  const res = await fetch(path, { method: "GET", credentials: fetchCreds, headers: authHeaders() });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} export`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = `biz_state_${batchId}.zip`;
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
};

export const bizStateDownloadTaskCommands = async (
  taskId: string,
  opts?: { includeAux?: boolean; enabledOnly?: boolean },
): Promise<void> => {
  const q = new URLSearchParams();
  // Default includes aux (matches live collect); pass includeAux:false to omit.
  q.set("include_aux", opts?.includeAux === false ? "false" : "true");
  if (opts?.enabledOnly === false) q.set("enabled_only", "false");
  const qs = q.toString();
  const path =
    `/v1/biz-state/tasks/${encodeURIComponent(taskId)}/commands/export` +
    (qs ? `?${qs}` : "");
  const res = await fetch(path, { method: "GET", credentials: fetchCreds, headers: authHeaders() });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    let detail = `${res.status} export commands`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (j?.detail != null) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = `biz_state_commands_${taskId}.txt`;
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
};
