import type {
  CollectionDashboard,
  CollectionJobDetail,
  CollectionJobItem,
  CollectionPolicy,
  CollectionRunList,
} from "../../types";
import { apiDelete, apiGet, apiPost, apiPut } from "./http";

export const createNeCollection = (body: {
  title?: string;
  commands: string;
  ne_ids?: string[];
  ume_ne_ids?: string[];
}) =>
  apiPost<CollectionJobItem>("/v1/ne-collections", body);

export const fetchNeCollections = (params: {
  page: number;
  pageSize: number;
  status?: string;
  keyword?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, params.page)));
  p.set("page_size", String(Math.max(1, Math.min(100, params.pageSize))));
  if (params.status?.trim()) p.set("status", params.status.trim());
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: CollectionJobItem[] }>(
    `/v1/ne-collections?${p.toString()}`,
  );
};

export const fetchCollectionDashboard = () =>
  apiGet<CollectionDashboard>("/v1/ne-collections/dashboard");

export const fetchCollectionPolicy = () => apiGet<CollectionPolicy>("/v1/ne-collections/policy");

export const updateCollectionPolicy = (body: Partial<CollectionPolicy>) =>
  apiPut<CollectionPolicy>("/v1/ne-collections/policy", body);

export const startCollectionFromPolicy = () =>
  apiPost<CollectionJobItem>("/v1/ne-collections/start-from-policy", {});

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
