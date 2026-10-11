import type {
  IntegrationStatus,
} from "../../types";
import { apiGet } from "./http";

export const fetchIntegrationStatus = () => apiGet<IntegrationStatus>("/v1/integrations/status");

export type OpsTaskItem = {
  kind: string;
  id: string;
  title: string;
  status: string;
  trigger: string;
  actor: string;
  started_at: string | null;
  updated_at: string | null;
  progress: string;
  inflight?: number;
  detail: string;
  href: string;
};

export type OpsTasksResponse = {
  generated_at: string;
  total: number;
  active: number;
  by_kind: Record<string, number>;
  by_status: Record<string, number>;
  items: OpsTaskItem[];
};

export const fetchOpsTasks = () => apiGet<OpsTasksResponse>("/v1/ops/tasks");

export type RuntimeMetrics = {
  uptime_sec?: number;
  pid?: number;
  thread_count?: number;
  host?: {
    source?: string;
    platform?: string;
    cpu_percent?: number;
    mem_percent?: number;
    mem_used_bytes?: number;
    mem_total_bytes?: number;
    mem_available_bytes?: number;
    error?: string;
  };
  db_pool?: {
    checked_out?: number;
    checked_in?: number;
    overflow?: number;
    size?: number;
  };
  db_storage?: {
    db_name?: string;
    used_bytes?: number;
    source?: string;
    error?: string;
  };
  cli_budget?: {
    limit?: number;
    in_use?: number;
    available?: number;
  };
  webcrt?: {
    active_sessions?: number;
    max_sessions?: number;
  };
  config_sync?: { running?: boolean };
  lldp_collect?: { running?: boolean };
  port_traffic?: { running?: boolean };
  device_schedulers?: {
    stale?: boolean;
    mode?: string;
    source?: string;
    age_sec?: number | null;
    hint?: string | null;
    config_sync?: { running?: boolean; last_tick_age_sec?: number | null };
    lldp_collect?: { running?: boolean; last_tick_age_sec?: number | null };
    ne_collect?: { running?: boolean; last_tick_age_sec?: number | null };
    port_traffic?: { running?: boolean; last_tick_age_sec?: number | null };
    fabric_reconcile?: { running?: boolean; last_run_age_sec?: number | null };
  };
  [key: string]: unknown;
};

export const fetchRuntimeMetrics = () => apiGet<RuntimeMetrics>("/v1/metrics/json");

export type AuditLogItem = {
  id: string;
  ts: string | null;
  actor_username: string;
  action: string;
  method: string;
  path: string;
  status_code: number;
  client_ip: string;
  detail: Record<string, unknown>;
};

export const fetchAuditLogs = (params?: {
  page?: number;
  pageSize?: number;
  username?: string;
  action?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params?.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params?.pageSize || 20)))));
  if (params?.username?.trim()) p.set("username", params.username.trim());
  if (params?.action?.trim()) p.set("action", params.action.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: AuditLogItem[] }>(
    `/v1/audit-logs?${p.toString()}`,
  );
};
