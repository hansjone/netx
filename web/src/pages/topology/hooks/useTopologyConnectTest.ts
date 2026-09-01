import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import type { Node } from "@xyflow/react";
import { useQueryClient } from "@tanstack/react-query";
import { connectTestManagedNe, fetchManagedNeById } from "../../../services/api";
import { queryKeys } from "../../../constants/queryKeys";
import { useI18n } from "../../../i18n";
import { useToast } from "../../../hooks/useToast";
import type { ManagedNeItem } from "../../../types";
import type { NeNodeData } from "../TopologyReactFlowView";

type Args = {
  closeCtxMenu: () => void;
  mapId?: string | null;
  setNodes: Dispatch<SetStateAction<Node<NeNodeData>[]>>;
};

/** Patch canvas node connect_status without wiping dirty local edits. */
function patchNodesConnectStatus(
  setNodes: Dispatch<SetStateAction<Node<NeNodeData>[]>>,
  updates: Array<{ id: string; status: string }>,
) {
  if (!updates.length) return;
  const byId = new Map(updates.map((u) => [u.id, u.status]));
  setNodes((ns) =>
    ns.map((n) => {
      const mid = String(n.data.managed_ne_id || "").trim();
      if (!mid || !byId.has(mid)) return n;
      const status = byId.get(mid) || "unknown";
      if (n.data.connect_status === status) return n;
      return { ...n, data: { ...n.data, connect_status: status } };
    }),
  );
}

export function useTopologyConnectTest({ closeCtxMenu, mapId, setNodes }: Args) {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [detailRow, setDetailRow] = useState<ManagedNeItem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  /** Managed NE ids whose canvas dots should track poll results. */
  const [trackingIds, setTrackingIds] = useState<string[]>([]);

  const finishTracking = useCallback(() => {
    setTrackingIds([]);
    if (mapId) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.topologyGraph(mapId) });
    }
    void queryClient.invalidateQueries({ queryKey: queryKeys.managedNeAll });
  }, [mapId, queryClient]);

  // Poll tracked connect tests → update dots (works even when graph apply is dirty-locked).
  useEffect(() => {
    if (!trackingIds.length) return;
    let cancelled = false;
    const ids = trackingIds;

    const tick = async () => {
      try {
        const rows = await Promise.all(ids.map((id) => fetchManagedNeById(id)));
        if (cancelled) return;
        patchNodesConnectStatus(
          setNodes,
          rows.map((row) => ({ id: row.id, status: row.connect_status || "unknown" })),
        );
        setDetailRow((prev) => {
          if (!prev) {
            return ids.length === 1 ? rows[0] : prev;
          }
          const match = rows.find((r) => r.id === prev.id);
          return match || prev;
        });
        if (rows.every((r) => r.connect_status !== "testing")) {
          finishTracking();
        }
      } catch {
        /* ignore transient poll errors */
      }
    };

    void tick();
    const timer = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [trackingIds, setNodes, finishTracking]);

  const startTracking = useCallback(
    (ids: string[], openDetail: boolean) => {
      const unique = [...new Set(ids.map((x) => String(x || "").trim()).filter(Boolean))];
      if (!unique.length) return;
      patchNodesConnectStatus(
        setNodes,
        unique.map((id) => ({ id, status: "testing" })),
      );
      setTrackingIds(unique);
      if (openDetail && unique.length === 1) {
        void fetchManagedNeById(unique[0])
          .then((row) => setDetailRow(row))
          .catch(() => {
            /* ignore */
          });
      }
    },
    [setNodes],
  );

  const runForNode = useCallback(
    async (node: Node<NeNodeData> | null) => {
      closeCtxMenu();
      const managedId = String(node?.data.managed_ne_id || "").trim();
      if (!managedId) {
        showError(t("topology.connectTestNeedManaged"));
        return;
      }
      setSubmitting(true);
      try {
        const res = await connectTestManagedNe([managedId]);
        showOk(t("managedNe.connect.submitted", { n: res.submitted }));
        startTracking([managedId], true);
      } catch (err) {
        showError(String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [closeCtxMenu, showError, showOk, startTracking, t],
  );

  const runForNodes = useCallback(
    async (nodes: Node<NeNodeData>[]) => {
      closeCtxMenu();
      const ids = [
        ...new Set(
          nodes
            .map((n) => String(n.data.managed_ne_id || "").trim())
            .filter(Boolean),
        ),
      ];
      if (!ids.length) {
        showError(t("topology.connectTestNeedManaged"));
        return;
      }
      const skipped = Math.max(0, nodes.length - ids.length);
      setSubmitting(true);
      try {
        const res = await connectTestManagedNe(ids);
        const msg = t("managedNe.connect.submitted", { n: res.submitted });
        showOk(
          skipped > 0
            ? `${msg} · ${t("topology.connectTestSkipped").replace("{{count}}", String(skipped))}`
            : msg,
        );
        startTracking(ids, ids.length === 1);
      } catch (err) {
        showError(String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [closeCtxMenu, showError, showOk, startTracking, t],
  );

  const onRetestSubmitted = useCallback(
    (rowId: string) => {
      showOk(t("managedNe.connect.submitted", { n: 1 }));
      startTracking([rowId], true);
    },
    [showOk, startTracking, t],
  );

  return {
    connectDetailRow: detailRow,
    connectTestSubmitting: submitting,
    closeConnectDetail: () => setDetailRow(null),
    runConnectTestForNode: runForNode,
    runConnectTestForNodes: runForNodes,
    onConnectRetestSubmitted: onRetestSubmitted,
  };
}
