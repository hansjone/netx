import type { TableColumnFilters } from "../../types/tableFilters";
import { appendColumnFilters } from "./tableFilters";
import type {
  ConfigSyncCycle,
  ConfigSyncDashboard,
  ConfigSyncPolicy,
  ConfigSyncTask,
  NeConfigSnapshotDetail,
  NeConfigSnapshotMeta,
} from "../../types";
import { apiGet, apiPost, apiPut, authHeaders, fetchCreds, handleUnauthorized } from "./http";

export const fetchConfigSyncDashboard = () =>
  apiGet<ConfigSyncDashboard>("/v1/config-sync/dashboard");

export const fetchConfigSyncPolicy = () => apiGet<ConfigSyncPolicy>("/v1/config-sync/policy");

export const updateConfigSyncPolicy = (body: Partial<ConfigSyncPolicy>) =>
  apiPut<ConfigSyncPolicy>("/v1/config-sync/policy", body);

export const fetchConfigSyncCycles = (params: {
  page?: number;
  pageSize?: number;
  status?: string;
  keyword?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  if (params.status?.trim()) p.set("status", params.status.trim());
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
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
  columnFilters?: TableColumnFilters;
  page?: number;
  pageSize?: number;
  keyword?: string;
  source?: string;
  vendor?: string;
}) => {
  const p = new URLSearchParams();
  appendColumnFilters(p, params?.columnFilters);
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
  const res = await fetch(path, { headers: authHeaders(), credentials: fetchCreds });
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
