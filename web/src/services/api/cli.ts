import type {
  CliConnectProfileItem,
  CliMeta,
  CliTargetListResponse,
  UmeAlarmItem,
  UmeCliOverrideItem,
} from "../../types";
import { apiGet, apiPost } from "./http";

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
