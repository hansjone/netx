export type IntegrationStatus = {
  netx_api: { status: "up" | "down" | "unknown"; [k: string]: unknown };
  db: { status: "up" | "down" | "unknown"; latency_ms?: number; error?: string; [k: string]: unknown };
  oclaw_bridge?: {
    status: "up" | "down" | "unknown";
    mode?: string;
    enabled?: boolean;
    connected?: boolean;
    queue_size?: number;
    published_ok?: number;
    published_fail?: number;
    latency_ms?: number;
    error_kind?: string;
    error?: string;
    [k: string]: unknown;
  };
};

export type UmeKeyAlertRuleItem = {
  notification_id: string;
  match_type: "notification_id" | "keyword";
  match_value: string;
  enabled: boolean;
  label: string;
  ne_types?: string[];
  created_at: string;
  updated_at: string;
  forward_stats?: {
    attempts: number;
    published_ok: number;
    last_forwarded_at: string;
  };
};

export type UmeInventoryNeTypeItem = {
  ne_type: string;
  ne_count: number;
};

export type UmeKeyAlertForwarderStatus = {
  enabled: boolean;
  operational?: boolean;
  paused?: boolean;
  connected: boolean;
  queue_size: number;
  url: string;
  published_ok?: number;
  published_fail?: number;
  queued_total?: number;
};

export type UmeKeyAlertMonitorResponse = {
  ok: boolean;
  rules: UmeKeyAlertRuleItem[];
  total?: number;
  page?: number;
  page_size?: number;
  config?: {
    forward_on_clear: boolean;
  };
  forwarder: UmeKeyAlertForwarderStatus;
};

export type UmeSyncJobItem = {
  id: number;
  domain: string;
  status: string;
  trigger_mode: string;
  pulled_count: number;
  inserted_count: number;
  updated_count: number;
  deleted?: number;
  error_message?: string;
  started_at: string;
  ended_at?: string | null;
};

export type UmeWsLogEntry = {
  ts: string;
  level: string;
  message: string;
  subscription_id?: string;
};

export type UmeWsConnectionStatus = {
  state: string;
  label: string;
  detail?: string;
};

export type UmeAlarmSubscriptionStatus = {
  ok?: boolean;
  created?: boolean;
  already_exists?: boolean;
  active: boolean;
  subscription_id?: string;
  wss_uri?: string;
  topic?: string;
  server_subscription_lost?: boolean;
  server_subscription_lost_reason?: string;
  current_alarms_mode?: "wss" | "rest";
  wss_active_for_current_alarms?: boolean;
  scheduled_sync_skipped?: boolean;
  needs_local_cleanup?: boolean;
  ume_already_missing?: boolean;
  cleared_local?: boolean;
  message?: string;
  ws_connection?: UmeWsConnectionStatus;
  ws_consumer_status?: string;
  ws_consumer_last_error?: string;
  ws_consumer_last_run_at?: string | null;
  ws_logs?: UmeWsLogEntry[];
};

export type UmeTopologyFabricGap = {
  dock_me_count?: number;
  fabric_ume_count?: number;
  world_exists?: boolean;
  world_folder_id?: string;
  latest_topology_status?: string;
  latest_topology_error?: string;
  partial_apply?: boolean;
  needs_apply?: boolean;
  error?: string;
};

export type UmeSyncStatusResponse = {
  total?: number;
  page?: number;
  page_size?: number;
  items: UmeSyncJobItem[];
  latest_by_domain?: Record<string, UmeSyncJobItem>;
  alarm_subscription?: UmeAlarmSubscriptionStatus;
  topology_fabric?: UmeTopologyFabricGap;
  runtime_tasks?: Array<{
    task: string;
    status: string;
    paused?: boolean;
    last_run_at?: string | null;
    last_error?: string;
    interval_s?: number | null;
    interval_label?: string;
  }>;
};

export type UmeNeItem = {
  ne_id: string;
  ne_name: string;
  user_label: string;
  ip_address: string;
  ipv6_address?: string;
  ne_type: string;
  device_level?: string;
  host_name?: string;
  location?: string;
  hardware_version?: string;
  loopback?: string;
  consistent_state?: string;
  interface_version?: string;
  mac?: string;
  admin_status?: string;
  address_type?: string;
  connection_status?: string;
  maintain_status?: string;
  net_mask?: string;
  create_time?: string;
  creator?: string;
  last_seen_at?: string;
};

export type UmeAlarmItem = {
  alarm_key: string;
  ne_id: string;
  ne_name: string;
  user_label: string;
  host_name?: string;
  ne_type?: string;
  object_name: string;
  event_type: string;
  native_probable_cause: string;
  perceived_severity: string;
  is_cleared: string;
  time_created: string;
  last_seen_at?: string;
};

export type ConnectStatus = "unknown" | "testing" | "pass" | "fail";

export type ManagedNeItem = {
  id: string;
  name: string;
  vendor: string;
  device_type: string;
  ip_address: string;
  port: number;
  protocol: string;
  username: string;
  /** True when a password is stored server-side (secret never returned). */
  has_password?: boolean;
  connect_status: ConnectStatus;
  connect_message: string;
  connect_detail: string;
  connect_tested_at: string | null;
  tags: string;
  remark: string;
  source?: string;
  source_ref?: string;
  hop_enabled: boolean;
  hop_vendor: string;
  hop_host: string;
  hop_port: number;
  hop_protocol: string;
  hop_username: string;
  hop_command_template: string;
  hop_vrf: string;
  hop_target_auth_mode: string;
  created_at: string;
  updated_at: string;
};

export type ManagedNeListResponse = {
  total: number;
  page: number;
  page_size: number;
  items: ManagedNeItem[];
};

export type ManagedNeMeta = {
  device_types: string[];
  vendors: string[];
};

export type ManagedNeImportResult = {
  inserted: number;
  updated: number;
  failed: Array<{ row: number; reason: string }>;
};

export type CliConnectProfileItem = {
  id: string;
  name: string;
  is_default: boolean;
  username: string;
  port: number;
  protocol: string;
  device_type_default: string;
  vendor_default: string;
  ne_type_rules: string;
  hop_enabled: boolean;
  hop_vendor: string;
  hop_host: string;
  hop_port: number;
  hop_protocol: string;
  hop_username: string;
  hop_command_template: string;
  hop_vrf: string;
  hop_target_auth_mode: string;
  created_at: string;
  updated_at: string;
};

export type CliMeta = {
  credentials_configured: boolean;
  default_profile_configured: boolean;
  cli_profile_ready: boolean;
};

export type CliTargetItem = {
  source: string;
  id: string;
  ume_ne_id?: string | null;
  name: string;
  ip_address: string;
  ne_type?: string;
  vendor?: string;
  device_type?: string;
  protocol?: string;
  port?: number;
  username?: string;
  has_password?: boolean;
  hop_enabled?: boolean;
  connect_status: string;
  cli_profile_ready?: boolean;
  /** ManagedNE.source (lldp / webcrt / ume_sync / …). */
  ne_source?: string;
};

export type CliTargetListResponse = {
  total: number;
  page: number;
  page_size: number;
  items: CliTargetItem[];
};

export type UmeCliOverrideItem = {
  ume_ne_id: string;
  profile_id: string | null;
  username_override: string;
  device_type_override: string;
  vendor_override: string;
  connect_status: string;
  connect_message: string;
  connect_detail: string;
  connect_tested_at: string | null;
  updated_at: string;
};

export type EligibleNeItem = {
  id: string;
  source?: "managed" | "ume" | string;
  name: string;
  vendor: string;
  device_type: string;
  ip_address: string;
  connect_status: string;
  connect_tested_at: string | null;
};

export type CollectionJobItem = {
  id: string;
  title: string;
  commands: string;
  status: string;
  ne_count: number;
  success_count: number;
  fail_count: number;
  output_count: number;
  error_message: string;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  last_run_at: string | null;
};

export type CollectionJobSummary = {
  id: string;
  title: string;
  status: string;
  ne_count: number;
  success_count: number;
  fail_count: number;
  created_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  last_run_at?: string | null;
};

export type CollectionDashboard = {
  job_count: number;
  active_count: number;
  running_job: CollectionJobSummary | null;
  last_job: CollectionJobSummary | null;
};

export type CollectionRunItem = {
  id: string;
  job_id: string;
  ne_id: string;
  ne_source?: string;
  ne_name: string;
  ne_ip: string;
  status: string;
  message: string;
  output_rel_path: string;
  has_output: boolean;
  started_at: string | null;
  ended_at: string | null;
};

export type CollectionJobDetail = {
  job: CollectionJobItem;
};

export type CollectionRunList = {
  total: number;
  page: number;
  page_size: number;
  items: CollectionRunItem[];
};

export type UmeTokenStatus = {
  ok?: boolean;
  has_token: boolean;
  expires_in_s: number;
  expires_at_epoch_s: number;
  auth_header: string;
  token_preview?: string;
  changed?: boolean;
  error_kind?: string;
  error?: string;
};

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

export type SliceGenerateResult = {
  folder_id: string;
  template: string;
  dry_run: boolean;
  map_count: number;
  overlap_node_count: number;
  created_view_ids: string[];
  maps: Array<{
    name: string;
    role: string;
    node_count: number;
    seed_fabric_node_ids: string[];
    member_fabric_node_ids: string[];
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

export type ConfigSyncTargetRef = {
  source: "managed" | "ume";
  id: string;
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

export type ConfigSyncPolicy = {
  enabled: boolean;
  interval_days: number;
  concurrency: number;
  scope_mode: string;
  selected_targets: ConfigSyncTargetRef[];
  history_keep: number;
  cycle_keep: number;
  updated_at?: string | null;
};

export type ConfigSyncCycle = {
  id: string;
  trigger_mode: string;
  status: string;
  concurrency: number;
  planned_count: number;
  success_count: number;
  fail_count: number;
  skip_count: number;
  error_message: string;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
};

export type ConfigSyncTask = {
  id: string;
  cycle_id: string;
  source: string;
  target_id: string;
  ne_name: string;
  ne_ip: string;
  vendor: string;
  status: string;
  message: string;
  started_at?: string | null;
  ended_at?: string | null;
};

export type ConfigSyncDashboard = {
  policy: ConfigSyncPolicy;
  snapshot_count: number;
  last_cycle: ConfigSyncCycle | null;
  running_cycle: ConfigSyncCycle | null;
  next_due_at?: string | null;
  fail_by_vendor: Record<string, number>;
};

export type NeConfigSnapshotMeta = {
  source: string;
  target_id: string;
  vendor: string;
  device_type: string;
  ne_name: string;
  ne_ip: string;
  config_sha256: string;
  config_alt_sha256: string;
  plain_size: number;
  plain_alt_size: number;
  zlib_size: number;
  zlib_alt_size: number;
  has_alt: boolean;
  commands: string[];
  collected_at?: string | null;
  last_cycle_id: string;
};

export type NeConfigSnapshotDetail = NeConfigSnapshotMeta & {
  config_text: string;
  config_alt_text: string;
};

export type PortTrafficDevice = {
  id: string;
  source: string;
  ne_id: string;
  ne_name: string;
  ne_ip: string;
  vendor: string;
  note: string;
  status: string;
  interval_sec: number;
  retention_days: number;
  concurrency: number;
  collect_running: boolean;
  target_count: number;
  active_target_count: number;
  last_collect_started_at?: string | null;
  last_collect_ended_at?: string | null;
  last_error: string;
  created_at?: string | null;
  updated_at?: string | null;
};

/** @deprecated use PortTrafficDevice */
export type PortTrafficTask = PortTrafficDevice;

export type PortTrafficTarget = {
  id: string;
  device_id: string;
  task_id?: string;
  series_id?: string;
  source: string;
  target_id: string;
  ne_name: string;
  ne_ip: string;
  vendor: string;
  ifname: string;
  if_description: string;
  bw_bps: number;
  status: string;
  last_error: string;
  last_sample_at?: string | null;
  created_at?: string | null;
};

export type PortTrafficEvent = {
  id: string;
  device_id: string;
  target_row_id: string;
  ifname: string;
  level: string;
  message: string;
  created_at?: string | null;
};

export type PortTrafficBoardPanel = {
  id: string;
  board_id: string;
  title: string;
  target_id: string;
  range_hours: number;
  baseline: "off" | "day" | "week" | "shift" | "custom" | string;
  offset_hours: number;
  ahead_hours: number;
  baseline_target_id: string;
  y_mode: "auto" | "current" | "util" | string;
  ord: number;
  col_span: number;
  row_span: number;
  stale: boolean;
  target: PortTrafficTarget | null;
  baseline_target: PortTrafficTarget | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type PortTrafficBoardPanelIn = {
  id?: string;
  title?: string;
  target_id: string;
  range_hours?: number;
  baseline?: string;
  offset_hours?: number;
  ahead_hours?: number;
  baseline_target_id?: string;
  y_mode?: string;
  ord?: number;
  col_span?: number;
  row_span?: number;
};

export type PortTrafficBoardSummary = {
  id: string;
  name: string;
  remark: string;
  cols: number;
  panel_count: number;
  created_by: string;
  updated_by: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type PortTrafficBoard = PortTrafficBoardSummary & {
  panels: PortTrafficBoardPanel[];
};

export type PortTrafficSeries = {
  id: string;
  device_id: string;
  task_id?: string;
  title: string;
  status: string;
  active_target: PortTrafficTarget | null;
  retired_target_count: number;
  created_at?: string | null;
};

export type PortTrafficIfaceIn = {
  ifname: string;
  if_description?: string;
  bw_bps?: number;
};

export type PortTrafficTargetIn = PortTrafficIfaceIn & {
  source?: "managed" | "ume";
  target_id?: string;
  ne_name?: string;
  ne_ip?: string;
  vendor?: string;
};

export type PortTrafficDiscoverPort = {
  ifname: string;
  attribute: string;
  mode: string;
  bw_raw: string;
  bw_bps: number;
  admin: string;
  phy: string;
  prot: string;
  description: string;
};

export type PortTrafficDiscoverResponse = {
  source: string;
  id: string;
  ne_name: string;
  ne_ip: string;
  vendor: string;
  vendor_key: string;
  ports: PortTrafficDiscoverPort[];
  ok?: boolean;
  command?: string;
  raw_preview?: string;
  error?: string;
};

export type PortTrafficSamplePoint = {
  ts: string;
  in_bps: number;
  out_bps: number;
  in_util_pct: number;
  out_util_pct: number;
  bw_bps: number;
  rate_period_sec: number;
  ts_raw?: string | null;
};

export type PortTrafficSamples = {
  target: PortTrafficTarget;
  points: PortTrafficSamplePoint[];
};

export type PortTrafficCompare = {
  meta: {
    target_id: string;
    baseline: string;
    offset_hours: number;
    range_hours: number;
    ahead_hours?: number;
    current_target: PortTrafficTarget | null;
    baseline_target: PortTrafficTarget | null;
    baseline_target_id: string;
  };
  current: PortTrafficSamplePoint[];
  baseline: PortTrafficSamplePoint[];
};

export type PortTrafficDashboard = {
  device_count: number;
  running_device_count: number;
  active_target_count: number;
  sample_count_24h: number;
  last_sample_at?: string | null;
  task_count?: number;
  running_task_count?: number;
};

