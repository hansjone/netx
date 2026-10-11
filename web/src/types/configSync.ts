export type ConfigSyncTargetRef = {
  source: "managed" | "ume";
  id: string;
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
