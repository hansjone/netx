import type { TableColumnFilters } from "../../types/tableFilters";
import { appendColumnFilters } from "./tableFilters";
import type {
  FabricEdge,
  FabricSummary,
  LldpCollectDashboard,
  LldpCollectJobSummary,
  LldpCollectPolicy,
  TopologyDiscoverJob,
  TopologyOutsidePeer,
  TopologyTree,
  TopologyViewGraph,
  TopologyViewItem,
} from "../../types";
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "./http";

export const fetchTopologyViews = () =>
  apiGet<{ total: number; items: TopologyViewItem[] }>("/v1/topology/views");

export const fetchTopologyTree = () => apiGet<TopologyTree>("/v1/topology/tree");

export const createTopologyFolder = (body: {
  name: string;
  kind?: string;
  parent_id?: string;
  sort_order?: number;
  /** UI locale so auto-spawned root map is「根图」or "Root map". */
  locale?: string;
}) =>
  apiPost<{
    id: string;
    parent_id: string;
    kind: string;
    name: string;
    sort_order: number;
    is_system: boolean;
  }>("/v1/topology/folders", body);

export const updateTopologyFolder = (
  folderId: string,
  body: { name?: string; parent_id?: string; sort_order?: number },
) =>
  apiPatch<{
    id: string;
    parent_id: string;
    kind: string;
    name: string;
    sort_order: number;
    is_system: boolean;
  }>(`/v1/topology/folders/${encodeURIComponent(folderId)}`, body);

export const deleteTopologyFolder = (folderId: string, force = false) =>
  apiDelete<{ ok: boolean; folder_id: string; deleted: boolean }>(
    `/v1/topology/folders/${encodeURIComponent(folderId)}?force=${force ? "true" : "false"}`,
  );

export const createTopologyView = (body: {
  name: string;
  remark?: string;
  filter?: Record<string, unknown>;
  folder_id?: string;
  kind?: string;
  role?: string;
  sort_order?: number;
}) => apiPost<TopologyViewItem>("/v1/topology/views", body);

export const populateTopologyView = (
  viewId: string,
  body?: { dry_run?: boolean; membership?: Record<string, unknown>; freeze_after?: boolean },
) =>
  apiPost<{
    view_id: string;
    dry_run: boolean;
    candidate_count: number;
    would_add: number;
    added: number;
    max_nodes: number;
    truncated: boolean;
    outside_peers: TopologyOutsidePeer[];
    graph: TopologyViewGraph | null;
  }>(`/v1/topology/views/${encodeURIComponent(viewId)}/populate`, body || {});

export const fetchTopologyViewGraph = (
  viewId: string,
  opts?: {
    min_x?: number;
    max_x?: number;
    min_y?: number;
    max_y?: number;
    sbn_id?: string;
    folder_id?: string;
    lod?: "auto" | "sbn" | "me" | "overview" | "detail";
    status?: string;
  },
) => {
  const q = new URLSearchParams();
  if (opts?.min_x != null) q.set("min_x", String(opts.min_x));
  if (opts?.max_x != null) q.set("max_x", String(opts.max_x));
  if (opts?.min_y != null) q.set("min_y", String(opts.min_y));
  if (opts?.max_y != null) q.set("max_y", String(opts.max_y));
  if (opts?.sbn_id) q.set("sbn_id", opts.sbn_id);
  if (opts?.folder_id) q.set("folder_id", opts.folder_id);
  if (opts?.lod) q.set("lod", opts.lod);
  if (opts?.status) q.set("status", opts.status);
  const qs = q.toString();
  return apiGet<TopologyViewGraph>(
    `/v1/topology/views/${encodeURIComponent(viewId)}${qs ? `?${qs}` : ""}`,
  );
};

export const fetchTopologyWorld = () =>
  apiGet<{ view_id: string; folder_id: string; name: string }>("/v1/topology/world");

export const fetchTopologyFolderBBox = (folderId: string) =>
  apiGet<{ folder_id: string; min_x: number; max_x: number; min_y: number; max_y: number; n: number }>(
    `/v1/topology/folders/${encodeURIComponent(folderId)}/bbox`,
  );

export const updateTopologyView = (
  viewId: string,
  body: {
    name?: string;
    remark?: string;
    filter?: Record<string, unknown>;
    viewport?: Record<string, unknown>;
    folder_id?: string;
    role?: string;
    sort_order?: number;
  },
) => apiPatch<TopologyViewItem>(`/v1/topology/views/${encodeURIComponent(viewId)}`, body);

export const deleteTopologyView = (viewId: string, force = false) =>
  apiDelete<{ ok: boolean; view_id: string; deleted: boolean }>(
    `/v1/topology/views/${encodeURIComponent(viewId)}?force=${force ? "true" : "false"}`,
  );

export type TopologyPositionsMutation = {
  ok?: boolean;
  skipped_locked?: number;
  updated?: number;
  matched?: number;
  graph?: TopologyViewGraph | null;
};

export const patchTopologyPositions = (
  viewId: string,
  positions: Array<{
    fabric_node_id: string;
    x?: number;
    y?: number;
    label?: string;
    locked?: boolean;
  }>,
  opts?: { return_graph?: boolean },
) => {
  const returnGraph = opts?.return_graph !== false;
  return apiPatch<TopologyViewGraph | TopologyPositionsMutation>(
    `/v1/topology/views/${encodeURIComponent(viewId)}/positions`,
    {
      positions,
      return_graph: returnGraph,
    },
  );
};

export const addTopologyViewNodes = (
  viewId: string,
  body: {
    managed_ne_ids?: string[];
    ume_ne_ids?: string[];
    fabric_node_ids?: string[];
    layout?: string;
  },
) => apiPost<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}/nodes`, body);

export const createTopologyPlaceholder = (
  viewId: string,
  body: { name: string; ip_address?: string; x?: number; y?: number },
) =>
  apiPost<TopologyViewGraph>(
    `/v1/topology/views/${encodeURIComponent(viewId)}/nodes/create-placeholder`,
    body,
  );

export const removeTopologyViewNodes = (viewId: string, fabricNodeIds: string[]) =>
  apiPost<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}/nodes/remove`, {
    fabric_node_ids: fabricNodeIds,
  });

export const projectTopologyNeighbors = (
  viewId: string,
  body?: { seed_fabric_node_ids?: string[]; managed_ne_ids?: string[]; dry_run?: boolean },
) =>
  apiPost<TopologyViewGraph>(
    `/v1/topology/views/${encodeURIComponent(viewId)}/project-neighbors`,
    body || {},
  );

export const patchTopologyEdgeStyle = (
  viewId: string,
  body: { fabric_edge_id: string; stroke_color?: string; stroke_width?: number; line_style?: string },
) =>
  apiPatch<TopologyViewGraph>(`/v1/topology/views/${encodeURIComponent(viewId)}/edge-style`, body);

export const fetchFabricSummary = () => apiGet<FabricSummary>("/v1/topology/fabric/summary");

export const fetchFabricNodes = (params?: {
  columnFilters?: TableColumnFilters;
  keyword?: string;
  role?: string;
  level?: string;
  levelMajor?: string;
  regionFolderId?: string;
  unmatched?: string;
  linkStatus?: string;
  page?: number;
  pageSize?: number;
}) => {
  const p = new URLSearchParams();
  appendColumnFilters(p, params?.columnFilters);
  if (params?.keyword) p.set("keyword", params.keyword);
  if (params?.role) p.set("role", params.role);
  if (params?.level) p.set("level", params.level);
  if (params?.levelMajor) p.set("level_major", params.levelMajor);
  if (params?.regionFolderId) p.set("region_folder_id", params.regionFolderId);
  if (params?.unmatched) p.set("unmatched", params.unmatched);
  if (params?.linkStatus) p.set("link_status", params.linkStatus);
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(500, Number(params?.pageSize || 50)))));
  return apiGet<{
    total: number;
    page: number;
    page_size: number;
    items: import("../../types").FabricNodeSearchHit[];
  }>(`/v1/topology/fabric/nodes?${p.toString()}`);
};

export const matchFabricNodes = (body: {
  pattern: string;
  match_field?: string;
  sample_limit?: number;
}) =>
  apiPost<{
    pattern: string;
    match_field: string;
    total_matched: number;
    samples: Array<Record<string, string>>;
    fabric_node_ids: string[];
  }>("/v1/topology/fabric/nodes/match", body);

export const bulkTagFabricNodes = (body: {
  fabric_node_ids?: string[];
  pattern?: string;
  match_field?: string;
  level?: number | null;
  role?: string | null;
  region_folder_id?: string | null;
  dry_run?: boolean;
}) =>
  apiPost<{
    dry_run: boolean;
    matched: number;
    updated: number;
    level?: number | null;
    role?: string | null;
    region_folder_id?: string | null;
    samples: Array<Record<string, string>>;
  }>("/v1/topology/fabric/nodes/tags/bulk", body);

export const patchFabricNodeTags = (
  fabricNodeId: string,
  body: { level?: number | null; role?: string | null; region_folder_id?: string | null },
) =>
  apiPatch<import("../../types").FabricNodeSearchHit>(
    `/v1/topology/fabric/nodes/${encodeURIComponent(fabricNodeId)}/tags`,
    body,
  );

export const deleteFabricNode = (fabricNodeId: string) =>
  apiDelete<{ deleted: number; edges_deleted: number; placements_deleted: number }>(
    `/v1/topology/fabric/nodes/${encodeURIComponent(fabricNodeId)}`,
  );

export const deleteFabricNodes = (fabricNodeIds: string[]) =>
  apiPost<{ deleted: number; edges_deleted: number; placements_deleted: number }>(
    "/v1/topology/fabric/nodes/delete",
    { fabric_node_ids: fabricNodeIds },
  );

/** Hard-delete topology/LLDP placeholders (ManagedNE + fabric + edges). */
export const purgePlaceholderFabricNodes = (fabricNodeIds: string[]) =>
  apiPost<{
    deleted: number;
    edges_deleted: number;
    placements_deleted: number;
    managed_deleted: number;
    membership_views: number;
  }>("/v1/topology/fabric/nodes/purge-placeholders", {
    fabric_node_ids: fabricNodeIds,
  });

export const searchFabricNodes = (params?: { q?: string; page?: number; pageSize?: number }) => {
  const p = new URLSearchParams();
  if (params?.q) p.set("q", params.q);
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params?.pageSize || 50)))));
  return apiGet<{
    total: number;
    page: number;
    page_size: number;
    items: import("../../types").FabricNodeSearchHit[];
  }>(`/v1/topology/fabric/nodes/search?${p.toString()}`);
};

export const fetchFabricEdges = (params?: {
  columnFilters?: TableColumnFilters;
  keyword?: string;
  status?: string;
  source?: string;
  nodeId?: string;
  page?: number;
  pageSize?: number;
}) => {
  const p = new URLSearchParams();
  appendColumnFilters(p, params?.columnFilters);
  if (params?.keyword) p.set("keyword", params.keyword);
  if (params?.status) p.set("status", params.status);
  if (params?.source) p.set("source", params.source);
  if (params?.nodeId) p.set("node_id", params.nodeId);
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(200, Number(params?.pageSize || 20)))));
  return apiGet<{ total: number; page: number; page_size: number; items: FabricEdge[] }>(
    `/v1/topology/fabric/edges?${p.toString()}`,
  );
};

export const startLldpDiscover = (body?: {
  scope?: "all_inventory" | "ne_ids";
  ne_ids?: string[];
  auto_add_unmatched?: boolean;
  concurrency?: number;
  trigger_mode?: "manual" | "schedule" | "topology";
}) => apiPost<TopologyDiscoverJob>("/v1/topology/fabric/discover", body || {});

export const fetchLldpDiscoverJob = (
  jobId: string,
  params?: { page?: number; pageSize?: number },
) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params?.pageSize || 20)))));
  return apiGet<TopologyDiscoverJob>(
    `/v1/topology/fabric/discover/${encodeURIComponent(jobId)}?${p.toString()}`,
  );
};

export const fetchLldpCollectDashboard = () =>
  apiGet<LldpCollectDashboard>("/v1/topology/lldp-collect/dashboard");

export const updateLldpCollectPolicy = (body: Partial<LldpCollectPolicy>) =>
  apiPut<LldpCollectPolicy>("/v1/topology/lldp-collect/policy", body);

export const startLldpCollect = (body?: { mode?: "full" | "retry_failed"; job_id?: string }) =>
  apiPost<{ ok: boolean; job: TopologyDiscoverJob }>("/v1/topology/lldp-collect/start", {
    mode: body?.mode || "full",
    ...(body?.job_id ? { job_id: body.job_id } : {}),
  });

export const pauseLldpCollectJob = (jobId: string) =>
  apiPost<TopologyDiscoverJob>(`/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}/pause`, {});

export const resumeLldpCollectJob = (jobId: string) =>
  apiPost<TopologyDiscoverJob>(`/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}/resume`, {});

export const stopLldpCollectJob = (jobId: string) =>
  apiPost<TopologyDiscoverJob>(`/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}/stop`, {});

export const fetchLldpCollectJobs = (params: {
  columnFilters?: TableColumnFilters;
  page?: number;
  pageSize?: number;
  status?: string;
  keyword?: string;
}) => {
  const p = new URLSearchParams();
  appendColumnFilters(p, params?.columnFilters);
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  if (params.status?.trim()) p.set("status", params.status.trim());
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: LldpCollectJobSummary[] }>(
    `/v1/topology/lldp-collect/jobs?${p.toString()}`,
  );
};

export const fetchLldpCollectJob = (
  jobId: string,
  params?: { page?: number; pageSize?: number; status?: string; keyword?: string; columnFilters?: TableColumnFilters },
) => {
  const p = new URLSearchParams();
  appendColumnFilters(p, params?.columnFilters);
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params?.pageSize || 20)))));
  if (params?.status?.trim()) p.set("status", params.status.trim());
  if (params?.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<TopologyDiscoverJob>(
    `/v1/topology/lldp-collect/jobs/${encodeURIComponent(jobId)}?${p.toString()}`,
  );
};

export const createFabricManualEdge = (body: {
  a_node_id: string;
  b_node_id: string;
  a_port?: string;
  b_port?: string;
}) => apiPost<{ ok: boolean; action: string; edge: Record<string, unknown> }>("/v1/topology/fabric/edges", body);

export const deleteFabricEdges = (edgeIds: string[]) =>
  apiPost<{ ok: boolean; deleted: number }>("/v1/topology/fabric/edges/delete", {
    edge_ids: edgeIds,
  });

export const deleteFabricEdge = (edgeId: string) =>
  apiDelete<{ ok: boolean; deleted: number }>(
    `/v1/topology/fabric/edges/${encodeURIComponent(edgeId)}`,
  );

export const patchFabricEdgePorts = (
  edgeId: string,
  body: { a_port?: string; b_port?: string },
) =>
  apiPatch<{
    id: string;
    a_node_id: string;
    b_node_id: string;
    a_port: string;
    b_port: string;
    source: string;
    status: string;
    layer: string;
  }>(`/v1/topology/fabric/edges/${encodeURIComponent(edgeId)}`, body);

export type TopologyViewCommitBody = {
  edge_creates?: Array<{
    client_id?: string;
    a_node_id: string;
    b_node_id: string;
    a_port?: string;
    b_port?: string;
  }>;
  edge_deletes?: string[];
  edge_port_edits?: Array<{ id: string; a_port?: string; b_port?: string }>;
  edge_styles?: Array<{
    fabric_edge_id: string;
    stroke_color?: string;
    stroke_width?: number;
    line_style?: string;
  }>;
  node_adds?: string[];
  node_removes?: string[];
  positions?: Array<{
    fabric_node_id: string;
    x?: number;
    y?: number;
    label?: string;
    locked?: boolean;
  }>;
};

export type TopologyViewCommitResult = {
  ok: boolean;
  view_id: string;
  edge_id_map: Record<string, string>;
  skipped_locked: number;
  graph: TopologyViewGraph | null;
};

export const commitTopologyView = (viewId: string, body: TopologyViewCommitBody) =>
  apiPost<TopologyViewCommitResult>(
    `/v1/topology/views/${encodeURIComponent(viewId)}/commit`,
    body,
  );

/** Back-compat aliases used by older call sites during cutover. */
export const fetchTopologyMaps = fetchTopologyViews;
export const createTopologyMap = createTopologyView;
export const fetchTopologyGraph = fetchTopologyViewGraph;
export const updateTopologyMap = updateTopologyView;
export const deleteTopologyMap = deleteTopologyView;
