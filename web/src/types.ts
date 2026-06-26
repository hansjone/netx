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

export type UmeSyncStatusResponse = {
  total?: number;
  page?: number;
  page_size?: number;
  items: UmeSyncJobItem[];
  latest_by_domain?: Record<string, UmeSyncJobItem>;
  alarm_subscription?: UmeAlarmSubscriptionStatus;
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
  connect_status: ConnectStatus;
  connect_message: string;
  connect_detail: string;
  connect_tested_at: string | null;
  tags: string;
  remark: string;
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
  connect_status: string;
  cli_profile_ready?: boolean;
};

export type CliTargetListResponse = {
  total: number;
  page: number;
  page_size: number;
  items: CliTargetItem[];
};

export type EligibleNeItem = {
  id: string;
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

export type CollectionRunItem = {
  id: string;
  job_id: string;
  ne_id: string;
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
