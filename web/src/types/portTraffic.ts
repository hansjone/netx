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
