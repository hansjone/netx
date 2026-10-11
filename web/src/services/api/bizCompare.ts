import { apiDelete, apiGet, apiPatch, apiPost, authHeaders, fetchCreds, handleUnauthorized } from "./http";

/* ---- biz_state compare (Phase2) ---- */

export const bizCompareListTemplates = () =>
  apiGet<{ items: Record<string, unknown>[] }>("/v1/biz-state/compare/templates");

export const bizCompareListMetrics = () =>
  apiGet<{
    items: Array<{
      metric_id: string;
      fields: Array<{
        name: string;
        display_name: string;
        dtype: string;
        is_key: boolean;
        is_interface: boolean;
        role: string;
      }>;
    }>;
    row_filter_presets?: Array<{
      id: string;
      label: string;
      row_filters: Array<Record<string, unknown>>;
    }>;
  }>("/v1/biz-state/compare/metrics");

export const bizCompareCreateTemplate = (body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>("/v1/biz-state/compare/templates", body);

export const bizCompareUpdateTemplate = (templateId: string, body: Record<string, unknown>) =>
  apiPatch<Record<string, unknown>>(
    `/v1/biz-state/compare/templates/${encodeURIComponent(templateId)}`,
    body,
  );

export const bizCompareDeleteTemplate = (templateId: string) =>
  apiDelete<{ ok: boolean }>(`/v1/biz-state/compare/templates/${encodeURIComponent(templateId)}`);

export const bizCompareListMappings = () =>
  apiGet<{ items: Record<string, unknown>[] }>("/v1/biz-state/compare/mappings");

export const bizCompareCreateMapping = (body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>("/v1/biz-state/compare/mappings", body);

export const bizCompareUpdateMapping = (mappingId: string, body: Record<string, unknown>) =>
  apiPatch<Record<string, unknown>>(
    `/v1/biz-state/compare/mappings/${encodeURIComponent(mappingId)}`,
    body,
  );

export const bizCompareValidateMapping = (body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>("/v1/biz-state/compare/mappings/validate", body);

export const bizCompareListJobs = () =>
  apiGet<{ items: Record<string, unknown>[] }>("/v1/biz-state/compare/jobs");

export const bizCompareCreateJob = (body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>("/v1/biz-state/compare/jobs", body);

export const bizCompareUpdateJob = (jobId: string, body: Record<string, unknown>) =>
  apiPatch<Record<string, unknown>>(
    `/v1/biz-state/compare/jobs/${encodeURIComponent(jobId)}`,
    body,
  );

export const bizCompareDeleteJob = (jobId: string) =>
  apiDelete<{ ok: boolean }>(`/v1/biz-state/compare/jobs/${encodeURIComponent(jobId)}`);

export const bizCompareRunJob = (jobId: string) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-state/compare/jobs/${encodeURIComponent(jobId)}/run`,
    {},
  );

export const bizCompareListRuns = (jobId: string, limit = 20) =>
  apiGet<{ items: Record<string, unknown>[] }>(
    `/v1/biz-state/compare/jobs/${encodeURIComponent(jobId)}/runs?limit=${limit}`,
  );

export const bizCompareGetRun = (runId: string) =>
  apiGet<Record<string, unknown>>(`/v1/biz-state/compare/runs/${encodeURIComponent(runId)}`);

export const bizCompareCancelRun = (runId: string) =>
  apiPost<Record<string, unknown>>(
    `/v1/biz-state/compare/runs/${encodeURIComponent(runId)}/cancel`,
    {},
  );

export const bizCompareDeleteRun = (runId: string) =>
  apiDelete<{ ok: boolean; job_id?: string; run_id?: string }>(
    `/v1/biz-state/compare/runs/${encodeURIComponent(runId)}`,
  );

export const bizCompareListRunDiffs = (params: {
  runId: string;
  metricId?: string;
  kind?: string;
  kw?: string;
  /** Field filters e.g. { direction: "out", network: "1.1.1.1" } */
  qf?: Record<string, string>;
  page?: number;
  pageSize?: number;
}) => {
  const p = new URLSearchParams();
  if (params.metricId) p.set("metric_id", params.metricId);
  if (params.kind) p.set("kind", params.kind);
  if (params.kw) p.set("kw", params.kw);
  if (params.qf && Object.keys(params.qf).length) {
    const cleaned: Record<string, string> = {};
    for (const [k, v] of Object.entries(params.qf)) {
      const key = String(k || "").trim();
      const val = String(v || "").trim();
      if (key && val) cleaned[key] = val;
    }
    if (Object.keys(cleaned).length) p.set("qf", JSON.stringify(cleaned));
  }
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(500, Number(params.pageSize || 100)))));
  return apiGet<{
    total: number;
    page: number;
    page_size: number;
    metric_id: string;
    items: Record<string, unknown>[];
    source?: string;
    truncated?: boolean;
    live_before_matched?: number;
    live_after_matched?: number;
  }>(`/v1/biz-state/compare/runs/${encodeURIComponent(params.runId)}/diffs?${p.toString()}`);
};

export const bizCompareDownloadRun = async (runId: string): Promise<void> => {
  const path = `/v1/biz-state/compare/runs/${encodeURIComponent(runId)}/export`;
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
    a.download = `biz_compare_${runId}.zip`;
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
};
