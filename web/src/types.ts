export type Batch = {
  batch_id: string;
  source_file: string;
  status: string;
  total_rows: number;
  success_rows: number;
  failed_rows: number;
  created_at: string;
};

export type Alarm = {
  id: number;
  alarm_time: string;
  severity_norm: string;
  ne_name: string;
  alarm_code: string;
};

export type AlarmQueryResponse = {
  total: number;
  page: number;
  page_size: number;
  items: Alarm[];
};

export type Diagnostics = {
  batch_id: string;
  total_alarms: number;
  severity_summary?: Array<{ key: string; count: number }>;
  top_alarm_codes?: Array<{ key: string; count: number }>;
  top_ne?: Array<{ key: string; count: number }>;
  protocol_summary?: Array<{ key: string; count: number }>;
};

export type IntegrationStatus = {
  netx_api: { status: "up" | "down" | "unknown"; [k: string]: unknown };
  db: { status: "up" | "down" | "unknown"; latency_ms?: number; error?: string; [k: string]: unknown };
  oclaw_bridge: {
    status: "up" | "down" | "unknown";
    latency_ms?: number;
    http_status?: number | null;
    error_kind?: string;
    error?: string;
    detail?: unknown;
    [k: string]: unknown;
  };
};

export type ImportHistoryItem = {
  kind: "alarms" | "logs" | "config";
  ts_ms: number;
  file_name: string;
  batch_id?: string;
  ok: boolean;
  summary: string;
};

export type AiAnalyzeHistoryItem = {
  id: number;
  analysis_request_id?: string;
  batch_id: string;
  question: string;
  filters?: Record<string, unknown>;
  ok: boolean;
  answer?: string;
  error?: string;
  created_at: string;
};

export type AiAnalyzeHistoryResponse = {
  total: number;
  page: number;
  page_size: number;
  items: AiAnalyzeHistoryItem[];
};

export type UmeSyncJobItem = {
  id: number;
  domain: string;
  status: string;
  trigger_mode: string;
  pulled_count: number;
  inserted_count: number;
  updated_count: number;
  /** Full-snapshot reconcile deletes (inventory: NE rows; alarms_current: current alarm rows). */
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
