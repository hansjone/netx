import type { TableColumnFilters } from "../../types/tableFilters";
import { appendColumnFilters } from "./tableFilters";
import type {
  CliConnectProfileItem,
  CliMeta,
  CliTargetColumnFilter,
  CliTargetFilterField,
  CliTargetFilterOptions,
  CliTargetListResponse,
  UmeAlarmItem,
  UmeCliOverrideItem,
} from "../../types";
import { apiGet, apiPost } from "./http";

export const fetchCliMeta = () => apiGet<CliMeta>("/v1/cli/meta");

export const fetchCliProfiles = () =>
  apiGet<{ items: CliConnectProfileItem[] }>("/v1/cli/profiles");

export const fetchCliTargetFilterOptions = (params: {
  field: CliTargetFilterField;
  source?: "managed" | "ume" | "webcrt" | "inventory" | "all";
  keyword?: string;
}) => {
  const p = new URLSearchParams({ field: params.field, source: params.source || "inventory" });
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<CliTargetFilterOptions>(`/v1/cli/targets/filter-options?${p.toString()}`);
};

export const fetchCliTargets = (params: {
  source?: "managed" | "ume" | "webcrt" | "inventory" | "all";
  keyword?: string;
  columnFilters?: TableColumnFilters;
  page?: number;
  pageSize?: number;
}) => {
  const p = new URLSearchParams();
  p.set("source", params.source || "all");
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(500, Number(params.pageSize || 50)))));
  const filters = params.columnFilters;
  const filterParams: Record<CliTargetFilterField, string> = {
    source: "source_filter",
    name: "name_filter",
    ip: "ip_filter",
    vendor: "vendor_filter",
    deviceType: "device_type_filter",
    connectStatus: "connect_status_filter",
  };
  for (const [field, filter] of Object.entries(filters || {}) as [CliTargetFilterField, CliTargetColumnFilter][]) {
    if (!filter) continue;
    p.set(`${filterParams[field]}_mode`, filter.mode);
    for (const value of filter.values) p.append(filterParams[field], value);
  }
  return apiGet<CliTargetListResponse>(`/v1/cli/targets?${p.toString()}`);
};

export const postUmeConnectTest = (umeNeIds: string[]) =>
  apiPost<{ ok: boolean; submitted: number }>("/v1/cli/ume-connect-test", { ume_ne_ids: umeNeIds });

export const fetchUmeCliOverride = (umeNeId: string) =>
  apiGet<UmeCliOverrideItem | null>(`/v1/cli/ume-overrides/${encodeURIComponent(umeNeId)}`);

export const fetchUmeCurrentAlarms = (params: {
  columnFilters?: TableColumnFilters;
  severity: string;
  isCleared: string;
  hostName: string;
  keyword: string;
  page: number;
  pageSize: number;
}) => {
  const p = new URLSearchParams();
  appendColumnFilters(p, params?.columnFilters);
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
