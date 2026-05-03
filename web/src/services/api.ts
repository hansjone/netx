import type { AiAnalyzeHistoryResponse, AlarmQueryResponse, Batch, Diagnostics, ImportHistoryItem, IntegrationStatus } from "../types";

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

export const apiPostForm = async <T,>(path: string, form: FormData): Promise<T> => {
  const res = await fetch(path, {
    method: "POST",
    headers: { accept: "application/json" },
    body: form,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(String((data as { detail?: string }).detail || `${res.status} ${path}`));
  return data as T;
};

export const apiDelete = async <T,>(path: string): Promise<T> => {
  const res = await fetch(path, {
    method: "DELETE",
    headers: { accept: "application/json" },
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(String((data as { detail?: string }).detail || `${res.status} ${path}`));
  return data as T;
};

export const fetchBatches = () => apiGet<{ items: Batch[] }>("/v1/batches?limit=30");

export const fetchIntegrationStatus = () => apiGet<IntegrationStatus>("/v1/integrations/status");

export const fetchJobs = () =>
  apiGet<{ items: Array<{ id: number; kind: string; file_name: string; batch_id?: string | null; ok: boolean; summary: string; created_at: string }> }>(
    "/v1/jobs?limit=20",
  ).then((x) => {
    const items: ImportHistoryItem[] = (x.items || []).map((j) => ({
      kind: (j.kind as ImportHistoryItem["kind"]) || "alarms",
      ts_ms: Date.parse(j.created_at) || Date.now(),
      file_name: j.file_name || "",
      batch_id: j.batch_id || undefined,
      ok: Boolean(j.ok),
      summary: j.summary || "",
    }));
    return { items };
  });

export const fetchDiagnostics = (batchId: string) =>
  apiGet<Diagnostics>(`/v1/diagnostics?batch_id=${encodeURIComponent(batchId)}`);

export const fetchAiAnalyzeHistory = (params: { batchId?: string; page: number; pageSize: number }) => {
  const p = new URLSearchParams();
  if (params.batchId) p.set("batch_id", params.batchId);
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  return apiGet<AiAnalyzeHistoryResponse>(`/v1/ap/history?${p.toString()}`);
};

export const fetchAlarms = (params: {
  batchId: string;
  alarmCode: string;
  neName: string;
  severity: string;
  page: number;
  pageSize: number;
}) => {
  const p = new URLSearchParams();
  if (params.batchId) p.set("batch_id", params.batchId);
  if (params.alarmCode) p.set("alarm_code", params.alarmCode);
  if (params.neName) p.set("ne_name", params.neName);
  if (params.severity) p.set("severity", params.severity);
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params.pageSize || 80)))));
  return apiGet<AlarmQueryResponse>(`/v1/alarms?${p.toString()}`);
};

