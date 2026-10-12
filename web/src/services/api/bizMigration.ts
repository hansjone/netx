import { apiDelete, apiGet, apiPatch, apiPost } from "./http";

// --- biz-migration (cutover monitor, separate from compare) ---

export const bizMigrationListProjects = () =>
  apiGet<{ items: Record<string, unknown>[] }>("/v1/biz-migration/projects");

export const bizMigrationNePortrait = (params: { source: string; ne_id: string; limit?: number }) => {
  const q = new URLSearchParams();
  q.set("source", params.source || "managed");
  q.set("ne_id", params.ne_id);
  if (params.limit != null) q.set("limit", String(params.limit));
  return apiGet<{
    source: string;
    ne_id: string;
    task: Record<string, unknown> | null;
    batches: Array<{
      id: string;
      started_at?: string | null;
      status?: string;
      row_count?: number;
      alias?: string;
    }>;
    hint?: string;
  }>(`/v1/biz-migration/ne-portrait?${q.toString()}`);
};

export const bizMigrationCreateProject = (body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>("/v1/biz-migration/projects", body);

export const bizMigrationPatchProject = (projectId: string, body: Record<string, unknown>) =>
  apiPatch<Record<string, unknown>>(
    `/v1/biz-migration/projects/${encodeURIComponent(projectId)}`,
    body,
  );

export const bizMigrationDeleteProject = (projectId: string) =>
  apiDelete<{ ok?: boolean }>(`/v1/biz-migration/projects/${encodeURIComponent(projectId)}`);

export const bizMigrationListBatches = (projectId: string) =>
  apiGet<{ items: Record<string, unknown>[] }>(
    `/v1/biz-migration/projects/${encodeURIComponent(projectId)}/batches`,
  );

export const bizMigrationListBaselinePorts = (projectId: string) =>
  apiGet<{
    batch_id: string;
    metric_id: string;
    ports: {
      interface: string;
      admin?: string;
      phy?: string;
      prot?: string;
      description?: string;
      mapped_to?: string;
    }[];
  }>(`/v1/biz-migration/projects/${encodeURIComponent(projectId)}/baseline-ports`);

export const bizMigrationListBaselineExpect = (projectId: string) =>
  apiGet<{
    batch_id: string;
    monitor_template_id?: string;
    sheets: Array<{
      metric_id: string;
      key_fields: string[];
      iface_fields: string[];
      items: Array<{
        key: string;
        keys: string[];
        mapped_to?: string;
        label?: string;
        row?: Record<string, unknown>;
      }>;
    }>;
    mapped?: Record<string, string>;
  }>(`/v1/biz-migration/projects/${encodeURIComponent(projectId)}/baseline-expect`);

export const bizMigrationEnsureHighfreq = (
  projectId: string,
  body: {
    interval_sec?: number;
    retention_days?: number;
    collect_now?: boolean;
    collect_metric_ids?: string[];
    hf_start_at?: string | null;
    hf_end_at?: string | null;
    old_ne?: Record<string, unknown>;
    new_ne?: Record<string, unknown>;
  } = {},
) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-migration/projects/${encodeURIComponent(projectId)}/ensure-highfreq`,
    body,
  );

/** @deprecated use bizMigrationEnsureHighfreq */
export const bizMigrationEnsurePortHighfreq = bizMigrationEnsureHighfreq;

export const bizMigrationCollectNow = (projectId: string) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-migration/projects/${encodeURIComponent(projectId)}/collect-now`,
    {},
  );

export const bizMigrationFinishBatch = (batchId: string, markDone = false) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-migration/batches/${encodeURIComponent(batchId)}/finish?mark_done=${markDone ? "true" : "false"}`,
    {},
  );

export const bizMigrationListRedTickets = (projectId: string, status = "") => {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiGet<{ open_count: number; items: Record<string, unknown>[] }>(
    `/v1/biz-migration/projects/${encodeURIComponent(projectId)}/red-tickets${q}`,
  );
};

export const bizMigrationResolveRedTicket = (ticketId: string, note = "") =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-migration/red-tickets/${encodeURIComponent(ticketId)}/resolve`,
    { note },
  );

export const bizMigrationCreateBatch = (projectId: string, body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-migration/projects/${encodeURIComponent(projectId)}/batches`,
    body,
  );

export const bizMigrationPatchBatch = (batchId: string, body: Record<string, unknown>) =>
  apiPatch<Record<string, unknown>>(
    `/v1/biz-migration/batches/${encodeURIComponent(batchId)}`,
    body,
  );

export const bizMigrationEvaluate = (batchId: string, body: Record<string, unknown> = {}) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-migration/batches/${encodeURIComponent(batchId)}/evaluate`,
    body,
  );

export const bizMigrationListRuns = (batchId: string, limit = 20) =>
  apiGet<{ items?: Array<Record<string, unknown>> }>(
    `/v1/biz-migration/batches/${encodeURIComponent(batchId)}/runs?limit=${limit}`,
  );

export const bizMigrationGetBoard = (batchId: string, runId = "") => {
  const q = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return apiGet<Record<string, unknown>>(
    `/v1/biz-migration/batches/${encodeURIComponent(batchId)}/board${q}`,
  );
};

export const bizMigrationListDiffs = (params: {
  runId: string;
  metricId?: string;
  sheetId?: string;
  onlyExpect?: boolean;
  verdict?: string;
  color?: string;
  kw?: string;
  offset?: number;
  limit?: number;
}) => {
  const p = new URLSearchParams();
  if (params.metricId) p.set("metric_id", params.metricId);
  if (params.sheetId) p.set("sheet_id", params.sheetId);
  if (params.onlyExpect) p.set("only_expect", "true");
  if (params.verdict) p.set("verdict", params.verdict);
  if (params.color) p.set("color", params.color);
  if (params.kw) p.set("kw", params.kw);
  if (params.offset != null) p.set("offset", String(params.offset));
  if (params.limit != null) p.set("limit", String(params.limit));
  const qs = p.toString();
  return apiGet<{ items: Record<string, unknown>[]; total: number }>(
    `/v1/biz-migration/runs/${encodeURIComponent(params.runId)}/diffs${qs ? `?${qs}` : ""}`,
  );
};


/** Evaluate / finish run in the background: poll this until ``status`` leaves ``running``. */
export const bizMigrationGetRun = (runId: string) =>
  apiGet<Record<string, unknown>>(`/v1/biz-migration/runs/${encodeURIComponent(runId)}`);

export const bizMigrationPreflight = (projectId: string) =>
  apiGet<Record<string, unknown>>(`/v1/biz-migration/projects/${encodeURIComponent(projectId)}/preflight`);

export const bizMigrationResolveResolvable = (projectId: string) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-migration/projects/${encodeURIComponent(projectId)}/red-tickets/resolve-resolvable`,
    {},
  );
