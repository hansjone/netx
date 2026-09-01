import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Node } from "@xyflow/react";
import { fetchUmeCurrentAlarms } from "../../../services/api";
import { queryKeys } from "../../../constants/queryKeys";
import type { UmeAlarmItem } from "../../../types";
import { fetchAllPages } from "../../../utils/csvExport";
import {
  isStrongHostKey,
  looksLikeIp,
  normalizeAlarmSeverity,
  normalizeHostKey,
  worseSeverity,
  type NodeAlarmSummary,
} from "../alarmOverlay";
import type { NeNodeData } from "../TopologyReactFlowView";

function accumulate(
  map: Map<string, NodeAlarmSummary>,
  key: string,
  severity: string,
) {
  const k = key.trim();
  if (!k) return;
  const sev = normalizeAlarmSeverity(severity);
  if (sev === "cleared") return;
  const prev = map.get(k);
  if (!prev) {
    map.set(k, { severity: sev, count: 1 });
    return;
  }
  map.set(k, {
    severity: worseSeverity(prev.severity, sev),
    count: prev.count + 1,
  });
}

function buildIndexes(items: UmeAlarmItem[]) {
  const byNeId = new Map<string, NodeAlarmSummary>();
  const byHost = new Map<string, NodeAlarmSummary>();
  for (const item of items) {
    if (String(item.is_cleared || "").toLowerCase() === "true" || item.is_cleared === "1") {
      continue;
    }
    accumulate(byNeId, String(item.ne_id || "").trim(), item.perceived_severity);
    // Only index strong host keys — skip short names that false-match canvas labels.
    for (const raw of [item.host_name, item.ne_name, item.user_label]) {
      const key = normalizeHostKey(raw);
      if (isStrongHostKey(key)) {
        accumulate(byHost, key, item.perceived_severity);
      }
    }
  }
  return { byNeId, byHost };
}

type Args = {
  enabled: boolean;
  nodes: Node<NeNodeData>[];
};

const ALARM_PAGE_SIZE = 500;
const ALARM_MAX_ROWS = 5000;

/** Poll uncleared UME alarms and join onto canvas nodes by ume_ne_id / strong name / IP. */
export function useTopologyAlarmOverlay({ enabled, nodes }: Args) {
  const query = useQuery({
    queryKey: [...queryKeys.umeCurrentAlarmsAll, "topo-overlay", "uncleared"],
    queryFn: () =>
      fetchAllPages<UmeAlarmItem>({
        pageSize: ALARM_PAGE_SIZE,
        maxRows: ALARM_MAX_ROWS,
        fetchPage: (page, pageSize) =>
          fetchUmeCurrentAlarms({
            severity: "",
            isCleared: "false",
            hostName: "",
            keyword: "",
            page,
            pageSize,
          }),
      }),
    enabled,
    refetchInterval: enabled ? 30_000 : false,
    staleTime: 15_000,
  });

  const alarmByNodeId = useMemo(() => {
    const out = new Map<string, NodeAlarmSummary>();
    if (!enabled) return out;
    const items = query.data || [];
    if (!items.length || !nodes.length) return out;
    const { byNeId, byHost } = buildIndexes(items);

    // Host keys claimed by >1 canvas node are ambiguous → skip name/IP join for those keys.
    const hostClaimCount = new Map<string, number>();
    for (const n of nodes) {
      if (n.data.kind === "region") continue;
      for (const raw of [n.data.label, n.data.ne_ip]) {
        const key = normalizeHostKey(raw);
        if (!isStrongHostKey(key)) continue;
        hostClaimCount.set(key, (hostClaimCount.get(key) || 0) + 1);
      }
    }

    for (const n of nodes) {
      if (n.data.kind === "region") continue;
      const umeId = String(n.data.ume_ne_id || "").trim();
      let hit = umeId ? byNeId.get(umeId) : undefined;
      if (!hit) {
        const candidates = [normalizeHostKey(n.data.ne_ip), normalizeHostKey(n.data.label)].filter(
          (k) => isStrongHostKey(k) && (hostClaimCount.get(k) || 0) === 1,
        );
        // Prefer IP over label when both present.
        candidates.sort((a, b) => Number(looksLikeIp(b)) - Number(looksLikeIp(a)));
        for (const key of candidates) {
          hit = byHost.get(key);
          if (hit) break;
        }
      }
      if (hit) out.set(n.id, hit);
    }
    return out;
  }, [enabled, query.data, nodes]);

  return {
    alarmByNodeId,
    alarmLoading: query.isLoading,
    alarmError: query.error ? String(query.error) : "",
  };
}
