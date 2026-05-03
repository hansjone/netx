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
