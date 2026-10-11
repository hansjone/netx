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
  trigger_mode?: string;
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
  trigger_mode?: string;
  ne_count: number;
  success_count: number;
  fail_count: number;
  created_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  last_run_at?: string | null;
};

export type CollectionTargetRef = {
  source: "managed" | "ume" | string;
  id: string;
};

export type CollectionPolicy = {
  enabled: boolean;
  interval_days: number;
  interval_hours: number;
  scope_mode: "all" | "selected" | string;
  selected_targets: CollectionTargetRef[];
  title: string;
  commands: string;
  history_keep: number;
  updated_at?: string | null;
};

export type CollectionDashboard = {
  job_count: number;
  active_count: number;
  running_job: CollectionJobSummary | null;
  last_job: CollectionJobSummary | null;
  next_due_at?: string | null;
  policy?: CollectionPolicy | null;
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
