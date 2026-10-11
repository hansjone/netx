import type {
  PortTrafficBoard,
  PortTrafficBoardPanelIn,
  PortTrafficBoardSummary,
  PortTrafficCompare,
  PortTrafficDashboard,
  PortTrafficDevice,
  PortTrafficDiscoverResponse,
  PortTrafficEvent,
  PortTrafficIfaceIn,
  PortTrafficSamples,
  PortTrafficTarget,
} from "../../types";
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "./http";

export const fetchPortTrafficDashboard = () =>
  apiGet<PortTrafficDashboard>("/v1/port-traffic/dashboard");

export const fetchPortTrafficDevices = (params: {
  page?: number;
  pageSize?: number;
  status?: string;
  keyword?: string;
}) => {
  const p = new URLSearchParams();
  p.set("page", String(Math.max(1, Number(params.page || 1))));
  p.set("page_size", String(Math.max(1, Math.min(100, Number(params.pageSize || 20)))));
  if (params.status?.trim()) p.set("status", params.status.trim());
  if (params.keyword?.trim()) p.set("keyword", params.keyword.trim());
  return apiGet<{ total: number; page: number; page_size: number; items: PortTrafficDevice[] }>(
    `/v1/port-traffic/devices?${p.toString()}`,
  );
};

/** @deprecated */
export const fetchPortTrafficTasks = fetchPortTrafficDevices;

export const createPortTrafficDevice = (body: {
  source: "managed" | "ume";
  ne_id: string;
  ne_name?: string;
  ne_ip?: string;
  vendor?: string;
  note?: string;
  interval_sec?: number;
  retention_days?: number;
  concurrency?: number;
  interfaces?: PortTrafficIfaceIn[];
  start_now?: boolean;
}) => apiPost<PortTrafficDevice>("/v1/port-traffic/devices", body);

export const updatePortTrafficDevice = (
  deviceId: string,
  body: {
    note?: string;
    interval_sec?: number;
    retention_days?: number;
    concurrency?: number;
    ne_name?: string;
    ne_ip?: string;
    vendor?: string;
  },
) => apiPatch<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}`, body);

export const startPortTrafficDevice = (deviceId: string) =>
  apiPost<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/start`, {});

export const pausePortTrafficDevice = (deviceId: string) =>
  apiPost<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/pause`, {});

export const stopPortTrafficDevice = (deviceId: string) =>
  apiPost<PortTrafficDevice>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/stop`, {});

export const collectPortTrafficNow = (deviceId: string) =>
  apiPost<PortTrafficDevice & { ok: boolean; started: boolean; reason?: string }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/collect-now`,
    {},
  );

export const deletePortTrafficDevice = (deviceId: string) =>
  apiDelete<{ ok: boolean; id: string }>(`/v1/port-traffic/devices/${encodeURIComponent(deviceId)}`);

export const rebindPortTrafficDevice = (deviceId: string, body: { ne_id: string }) =>
  apiPost<PortTrafficDevice>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/rebind`,
    body,
  );

export const fetchPortTrafficTargets = (deviceId: string) =>
  apiGet<{ items: PortTrafficTarget[] }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/targets`,
  );

export const fetchPortTrafficEvents = (deviceId: string, limit = 100) =>
  apiGet<{ items: PortTrafficEvent[]; total: number }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/events?limit=${limit}`,
  );

export const putPortTrafficInterfaces = (deviceId: string, interfaces: PortTrafficIfaceIn[]) =>
  apiPut<{ items: PortTrafficTarget[] }>(
    `/v1/port-traffic/devices/${encodeURIComponent(deviceId)}/interfaces`,
    { interfaces },
  );

export const discoverPortTrafficPorts = (body: { source: "managed" | "ume"; id: string }) =>
  apiPost<PortTrafficDiscoverResponse>("/v1/port-traffic/discover/ports", body);

export const fetchPortTrafficSamples = (params: {
  targetId: string;
  from?: string;
  to?: string;
}) => {
  const p = new URLSearchParams();
  p.set("target_id", params.targetId);
  if (params.from) p.set("from", params.from);
  if (params.to) p.set("to", params.to);
  return apiGet<PortTrafficSamples>(`/v1/port-traffic/samples?${p.toString()}`);
};

export const fetchPortTrafficCompare = (params: {
  targetId: string;
  rangeHours: number;
  baseline: string;
  offsetHours?: number;
  aheadHours?: number;
  baselineTargetId?: string;
  to?: string;
}) => {
  const p = new URLSearchParams();
  p.set("target_id", params.targetId);
  p.set("range_hours", String(params.rangeHours));
  p.set("baseline", params.baseline);
  if (params.offsetHours != null) p.set("offset_hours", String(params.offsetHours));
  if (params.aheadHours != null && params.aheadHours > 0) {
    p.set("ahead_hours", String(params.aheadHours));
  }
  if (params.baselineTargetId) p.set("baseline_target_id", params.baselineTargetId);
  if (params.to) p.set("to", params.to);
  return apiGet<PortTrafficCompare>(`/v1/port-traffic/compare?${p.toString()}`);
};

export const fetchPortTrafficBoards = () =>
  apiGet<{ items: PortTrafficBoardSummary[] }>("/v1/port-traffic/boards");

export const fetchPortTrafficBoard = (boardId: string) =>
  apiGet<PortTrafficBoard>(`/v1/port-traffic/boards/${encodeURIComponent(boardId)}`);

export const createPortTrafficBoard = (body: {
  name: string;
  remark?: string;
  cols?: number;
  panels?: PortTrafficBoardPanelIn[];
}) => apiPost<PortTrafficBoard>("/v1/port-traffic/boards", body);

export const updatePortTrafficBoard = (
  boardId: string,
  body: { name?: string; remark?: string; cols?: number },
) => apiPatch<PortTrafficBoard>(`/v1/port-traffic/boards/${encodeURIComponent(boardId)}`, body);

export const putPortTrafficBoardPanels = (boardId: string, panels: PortTrafficBoardPanelIn[]) =>
  apiPut<PortTrafficBoard>(`/v1/port-traffic/boards/${encodeURIComponent(boardId)}/panels`, {
    panels,
  });

export const deletePortTrafficBoard = (boardId: string) =>
  apiDelete<{ ok: boolean; id: string }>(
    `/v1/port-traffic/boards/${encodeURIComponent(boardId)}`,
  );
