import type { ConfigSyncTargetRef } from "./configSync";

export type TopologyViewRole = "core" | "aggregation" | "access";
export type TopologyViewKind = "physical" | "custom";

export type TopologyViewItem = {
  id: string;
  name: string;
  remark: string;
  folder_id?: string;
  kind?: TopologyViewKind | string;
  role?: TopologyViewRole | string;
  sort_order?: number;
  filter?: Record<string, unknown>;
  viewport?: Record<string, unknown>;
  node_count: number;
  edge_count?: number;
  created_at?: string | null;
  updated_at?: string | null;
};

export type TopologyTreeViewItem = {
  id: string;
  name: string;
  kind: TopologyViewKind | string;
  role?: TopologyViewRole | string;
  sort_order: number;
  node_count: number;
  updated_at?: string | null;
};

export type TopologyTreeFolderItem = {
  id: string;
  parent_id: string;
  kind: "root" | "region" | string;
  name: string;
  sort_order: number;
  is_system: boolean;
  external_ref?: string;
  /** Distinct fabric NE count in this folder subtree (API). Region icons excluded. */
  ne_count?: number;
  views: TopologyTreeViewItem[];
  children: TopologyTreeFolderItem[];
};

export type TopologyTree = {
  root: TopologyTreeFolderItem | null;
};

export type ClassifyRuleScope = "role" | "region";

export type ClassifyRule = {
  id: string;
  scope: ClassifyRuleScope | string;
  name: string;
  pattern: string;
  match_field: string;
  priority: number;
  enabled: boolean;
  payload: Record<string, unknown>;
  remark?: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ClassifyPreview = {
  total_nodes: number;
  role_matched: number;
  role_unmatched: number;
  role_conflicts: number;
  region_matched: number;
  region_unmatched: number;
  region_conflicts: number;
  role_samples: Array<Record<string, unknown>>;
  region_samples: Array<Record<string, unknown>>;
  unmatched_samples: Array<Record<string, unknown>>;
};

export type ClassifyApplyResult = {
  role_updated: number;
  region_updated: number;
  skipped_manual: number;
  total_nodes: number;
};

export type FabricNodeSearchHit = {
  id: string;
  name: string;
  ip: string;
  vendor: string;
  managed_ne_id?: string;
  ume_ne_id?: string;
  /** Layout rank major.minor; null/undefined = unclassified */
  level?: number | null;
  role?: string;
  region_folder_id?: string | null;
  world_x?: number | null;
  world_y?: number | null;
  link_status?: "managed" | "ume" | "both" | "orphaned" | string;
  managed_alive?: boolean;
  ume_alive?: boolean;
  managed_source?: string;
  deletable?: boolean;
  views?: Array<{
    view_id: string;
    view_name: string;
    folder_id: string;
    folder_name: string;
    kind: string;
  }>;
};

export type TopologyViewNodeItem = {
  fabric_node_id: string;
  managed_ne_id: string;
  ume_ne_id: string;
  label: string;
  x: number;
  y: number;
  locked: boolean;
  name: string;
  ip: string;
  vendor: string;
  device_type: string;
  connect_status: string;
  managed_source?: string;
  /** Fabric classify rank; smaller = closer to external/WAN. */
  level?: number | null;
  /** Synced alias from floor(level): external|core|aggregation|access|edge|"" */
  role?: string;
  /** ne | region — region drills into a child canvas */
  kind?: string;
  folder_id?: string;
  view_id?: string;
  node_count?: number;
};

export type TopologyViewEdgeItem = {
  id: string;
  a_node_id: string;
  b_node_id: string;
  a_port: string;
  b_port: string;
  source: string;
  status: string;
  layer: string;
  stroke_color?: string;
  stroke_width?: number;
  line_style?: string;
  discovered_at?: string | null;
  /** UME userLabel when no EQ+PTP ports. */
  display_label?: string;
};

export type TopologyOutsidePeer = {
  fabric_node_id: string;
  name: string;
  ip: string;
  via_node_id: string;
};

export type TopologyWorldTransform = {
  origin_x: number;
  origin_y: number;
  scale: number;
  full_min_x: number;
  full_max_x: number;
  full_min_y: number;
  full_max_y: number;
  total: number;
  lod: string;
  dock_me_count?: number;
};

export type TopologyWorldScatterPoint = {
  x: number;
  y: number;
};

export type TopologyViewGraph = {
  view: TopologyViewItem;
  nodes: TopologyViewNodeItem[];
  edges: TopologyViewEdgeItem[];
  truncated?: boolean;
  truncate_reason?: string;
  outside_peers?: TopologyOutsidePeer[];
  world_transform?: TopologyWorldTransform | null;
  scatter?: TopologyWorldScatterPoint[];
};

export type FabricSummary = {
  node_count: number;
  edge_count: number;
  edge_active: number;
  edge_stale: number;
  edge_missing?: number;
  last_discover_at?: string | null;
  updated_at?: string | null;
};

export type FabricEdge = {
  id: string;
  layer: string;
  a_node_id: string;
  b_node_id: string;
  a_port: string;
  b_port: string;
  a_name?: string;
  b_name?: string;
  a_ip?: string;
  b_ip?: string;
  source: string;
  status: string;
  attrs?: Record<string, unknown>;
  discovered_at?: string | null;
  last_seen_at?: string | null;
  updated_at?: string | null;
};

export type TopologyDiscoverUnmatched = {
  remote_name: string;
  remote_ip: string;
  local_port: string;
  remote_port: string;
};

export type TopologyDiscoverJobItem = {
  id: string;
  job_id: string;
  ne_id: string;
  ume_ne_id: string;
  fabric_node_id: string;
  ne_name: string;
  ne_ip: string;
  ok: boolean;
  command: string;
  neighbors: number;
  edges_added: number;
  edges_updated: number;
  unmatched_count: number;
  unmatched?: TopologyDiscoverUnmatched[];
  parser_key?: string;
  parser_stub?: boolean;
  error: string;
  raw_preview: string;
};

export type TopologyDiscoverJob = {
  id: string;
  scope: string;
  trigger_mode?: string;
  status: string;
  total: number;
  done: number;
  edges_added: number;
  edges_updated: number;
  edges_stale: number;
  edges_missing?: number;
  error: string;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
  items: TopologyDiscoverJobItem[];
  items_total?: number;
  items_page?: number;
  items_page_size?: number;
};

/** @deprecated alias — prefer TopologyViewItem */
export type TopologyMapItem = TopologyViewItem;

/** UI-compat aliases for restored TopologyPage. */
export type TopologyNodeItem = TopologyViewNodeItem & { id?: string; map_id?: string; ne_name?: string; ne_ip?: string; protocol?: string };
export type TopologyEdgeItem = TopologyViewEdgeItem & {
  source_node_id?: string;
  target_node_id?: string;
  map_id?: string;
};
export type TopologyDiscoverNeResult = TopologyDiscoverJobItem & {
  links?: Array<Record<string, unknown>>;
};
export type TopologyDiscoverOut = {
  map_id?: string;
  protocol?: string;
  job_id?: string;
  scanned: number;
  edges_added: number;
  edges_updated: number;
  edges_stale?: number;
  results: TopologyDiscoverNeResult[];
  graph: TopologyViewGraph | null;
};

export type LldpCollectPolicy = {
  enabled: boolean;
  interval_days: number;
  interval_hours: number;
  concurrency: number;
  scope_mode: string;
  selected_targets: ConfigSyncTargetRef[];
  auto_add_unmatched: boolean;
  history_keep: number;
  updated_at?: string | null;
};

export type LldpCollectJobSummary = {
  id: string;
  scope: string;
  trigger_mode: string;
  status: string;
  total: number;
  done: number;
  success_count?: number;
  fail_count?: number;
  edges_added: number;
  edges_updated: number;
  edges_stale: number;
  edges_missing?: number;
  error: string;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
};

export type LldpCollectDashboard = {
  policy: LldpCollectPolicy;
  fabric_node_count: number;
  fabric_edge_count: number;
  fabric_edge_active: number;
  fabric_edge_stale: number;
  fabric_edge_missing?: number;
  last_discover_at?: string | null;
  running_job: LldpCollectJobSummary | null;
  last_job: LldpCollectJobSummary | null;
  next_due_at?: string | null;
};
