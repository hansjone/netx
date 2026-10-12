import { useCallback } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Edge, Node } from "@xyflow/react";
import { queryKeys } from "../../../constants/queryKeys";
import { formatErr } from "../../../services/api";
import { commitTopologyView } from "../../../services/api/topology";
import type { TopologyViewGraph } from "../../../types";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import { applyViewGraph, flowToPositions } from "../lib/graphFlow";
import type { EdgeDefaults } from "../lib/edgeStyle";
import type { HistorySnap } from "../lib/pageTypes";
import { buildViewCommitBody } from "../lib/saveDiff";

type PortEdit = { a_port: string; b_port: string };
type StyleEdit = { stroke_color: string; stroke_width: number; line_style: string };

type Args = {
  mapId: string;
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  graphQueryData?: TopologyViewGraph;
  edgeDefaults: EdgeDefaults;
  setNodes: React.Dispatch<React.SetStateAction<Node<NeNodeData>[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  historyRef: React.MutableRefObject<HistorySnap[]>;
  redoRef: React.MutableRefObject<HistorySnap[]>;
  historyLockRef: React.MutableRefObject<boolean>;
  pendingEdgeCreatesRef: React.MutableRefObject<Set<string>>;
  pendingEdgeDeletesRef: React.MutableRefObject<Set<string>>;
  pendingEdgePortEditsRef: React.MutableRefObject<Map<string, PortEdit>>;
  pendingEdgeStyleRef: React.MutableRefObject<Map<string, StyleEdit>>;
  clearDirty: () => void;
  showOk: (msg: string) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
};

export function useTopologySave({
  mapId,
  nodes,
  edges,
  graphQueryData,
  edgeDefaults,
  setNodes,
  setEdges,
  historyRef,
  redoRef,
  historyLockRef,
  pendingEdgeCreatesRef,
  pendingEdgeDeletesRef,
  pendingEdgePortEditsRef,
  pendingEdgeStyleRef,
  clearDirty,
  showOk,
  showError,
  t,
}: Args) {
  const queryClient = useQueryClient();

  const resetLocalFromServer = useCallback(
    (graph: TopologyViewGraph) => {
      historyLockRef.current = true;
      applyViewGraph(graph, edgeDefaults, setNodes, setEdges);
      historyLockRef.current = false;
      pendingEdgeCreatesRef.current = new Set();
      pendingEdgeDeletesRef.current = new Set();
      pendingEdgePortEditsRef.current = new Map();
      pendingEdgeStyleRef.current = new Map();
      historyRef.current = [];
      redoRef.current = [];
      clearDirty();
    },
    [
      edgeDefaults,
      setNodes,
      setEdges,
      historyLockRef,
      pendingEdgeCreatesRef,
      pendingEdgeDeletesRef,
      pendingEdgePortEditsRef,
      pendingEdgeStyleRef,
      historyRef,
      redoRef,
      clearDirty,
    ],
  );

  const commitCanvas = useCallback(async () => {
    if (!mapId) throw new Error(t("topology.selectMap"));
    const body = buildViewCommitBody({
      nodes,
      edges,
      serverNodeIds: (graphQueryData?.nodes || []).map((n) => n.fabric_node_id),
      positions: flowToPositions(nodes),
      pendingCreates: pendingEdgeCreatesRef.current,
      pendingDeletes: pendingEdgeDeletesRef.current,
      pendingPortEdits: pendingEdgePortEditsRef.current,
      pendingStyles: pendingEdgeStyleRef.current,
    });
    const result = await commitTopologyView(mapId, body);
    if (result.graph) {
      queryClient.setQueryData(queryKeys.topologyGraph(mapId), result.graph);
      resetLocalFromServer(result.graph);
    } else {
      pendingEdgeCreatesRef.current = new Set();
      pendingEdgeDeletesRef.current = new Set();
      pendingEdgePortEditsRef.current = new Map();
      pendingEdgeStyleRef.current = new Map();
      historyRef.current = [];
      redoRef.current = [];
      clearDirty();
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyGraph(mapId) });
    }
    await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
    return result;
  }, [
    mapId,
    nodes,
    edges,
    graphQueryData,
    pendingEdgeCreatesRef,
    pendingEdgeDeletesRef,
    historyRef,
    redoRef,
    clearDirty,
    queryClient,
    resetLocalFromServer,
    t,
  ]);

  const saveMut = useMutation({
    mutationFn: commitCanvas,
    onSuccess: (result) => {
      const skipped = Number(result.skipped_locked || 0);
      if (skipped > 0) {
        showOk(t("topology.savedLockedSkipped").replace("{{count}}", String(skipped)));
      } else {
        showOk(t("topology.saved"));
      }
    },
    onError: async (err) => {
      showError(t("topology.saveFailed").replace("{{detail}}", formatErr(err)));
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyGraph(mapId) });
    },
  });

  return {
    saveMut,
    commitCanvas,
    resetLocalFromServer,
  };
}
