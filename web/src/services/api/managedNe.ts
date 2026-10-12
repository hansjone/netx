import type {
  EligibleNeItem,
  ManagedNeImportResult,
  ManagedNeItem,
  ManagedNeListResponse,
  ManagedNeMeta,
} from "../../types";
import type { TableColumnFilters } from "../../types/tableFilters";
import { appendColumnFilters } from "./tableFilters";
import { ApiRequestError, apiDelete, apiGet, apiPatch, apiPost, authHeaders, fetchCreds, handleUnauthorized, parseApiResponse } from "./http";

export const fetchManagedNeMeta = () =>
  Promise.all([
    apiGet<ManagedNeMeta>("/v1/managed-ne/meta/device-types"),
    apiGet<{ configured: boolean }>("/v1/managed-ne/meta/credentials-configured"),
  ]).then(([types, creds]) => ({
    device_types: types.device_types,
    vendors: types.vendors,
    credentials_configured: creds.configured,
    exec_policy_enabled: Boolean(types.exec_policy_enabled),
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
  columnFilters?: TableColumnFilters;
}) => {
  const p = new URLSearchParams();
  if (params.keyword) p.set("keyword", params.keyword);
  if (params.vendor) p.set("vendor", params.vendor);
  if (params.connectStatus) p.set("connect_status", params.connectStatus);
  appendColumnFilters(p, params.columnFilters);
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
    hop_enter_system_view?: boolean;
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
  const res = await fetch(path, { headers: authHeaders(), credentials: fetchCreds });
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
  const res = await fetch(path, { method: "POST", headers: authHeaders(), body: form, credentials: fetchCreds });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("unauthorized");
  }
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} import`);
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

export const fetchManagedNeById = (neId: string) =>
  apiGet<ManagedNeItem>(`/v1/managed-ne/${encodeURIComponent(neId)}`);
