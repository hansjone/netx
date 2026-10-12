import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Edge, Node } from "@xyflow/react";
import { queryKeys } from "../../../constants/queryKeys";
import { fetchTopologyGraph, purgePlaceholderFabricNodes } from "../../../services/api";
import type { TopologyViewGraph } from "../../../types";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import { isPlaceholderSource } from "../lib/searchUtils";

type Args = {
  mapId: string;
  nodes: Node<NeNodeData>[];
  selectedNode: Node<NeNodeData> | null;
  setNodes: React.Dispatch<React.SetStateAction<Node<NeNodeData>[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  pendingEdgeCreatesRef: React.MutableRefObject<Set<string>>;
  dirtyRef: React.MutableRefObject<boolean>;
  appliedMapIdRef: React.MutableRefObject<string>;
  pushHistory: () => void;
  markDirty: () => void;
  bumpHistory: () => void;
  closeCtxMenu: () => void;
  commitCanvas: () => Promise<unknown>;
  resetLocalFromServer: (graph: TopologyViewGraph) => void;
  showOk: (msg: string) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
};

export function useTopologyNodeOps({
  mapId,
  nodes,
  selectedNode,
  setNodes,
  setEdges,
  pendingEdgeCreatesRef,
  dirtyRef,
  appliedMapIdRef,
  pushHistory,
  markDirty,
  bumpHistory,
  closeCtxMenu,
  commitCanvas,
  resetLocalFromServer,
  showOk,
  showError,
  t,
}: Args) {
  const queryClient = useQueryClient();

  const renameSelectedNode = useCallback(() => {
    if (!selectedNode) return;
    const currentOverride = String(selectedNode.data.label_override ?? "");
    const next = window.prompt(
      `${t("topology.renameNodePrompt")}\n${t("topology.renameNodeClearHint")}`,
      currentOverride || selectedNode.data.label || "",
    );
    if (next == null) return;
    const override = next.trim();
    const prevOverride = currentOverride;
    const display =
      override ||
      selectedNode.data.name ||
      selectedNode.data.ne_ip ||
      selectedNode.id;
    if (override === prevOverride && display === (selectedNode.data.label || "")) {
      closeCtxMenu();
      return;
    }
    pushHistory();
    markDirty();
    setNodes((ns) =>
      ns.map((n) =>
        n.id === selectedNode.id
          ? {
              ...n,
              data: {
                ...n.data,
                label: display,
                label_override: override,
              },
            }
          : n,
      ),
    );
    closeCtxMenu();
  }, [selectedNode, t, pushHistory, markDirty, setNodes, closeCtxMenu]);

  const removeNodeById = useCallback(
    (nodeId: string) => {
      if (!mapId) return;
      pushHistory();
      closeCtxMenu();
      setNodes((ns) => ns.filter((n) => n.id !== nodeId));
      setEdges((es) => {
        const next = es.filter((e) => e.source !== nodeId && e.target !== nodeId);
        for (const e of es) {
          if (next.some((x) => x.id === e.id)) continue;
          pendingEdgeCreatesRef.current.delete(e.id);
        }
        return next;
      });
      markDirty();
    },
    [mapId, pushHistory, closeCtxMenu, setNodes, setEdges, pendingEdgeCreatesRef, markDirty],
  );

  const purgePlaceholderById = useCallback(
    async (nodeId: string) => {
      if (!mapId) return;
      const node = nodes.find((n) => n.id === nodeId);
      if (!node || !isPlaceholderSource(node.data.managed_source, node.data.ne_ip)) {
        return;
      }
      if (!window.confirm(t("topology.deletePlaceholderConfirm"))) return;
      closeCtxMenu();
      try {
        if (dirtyRef.current) {
          await commitCanvas();
        }
        await purgePlaceholderFabricNodes([nodeId]);
        const graph = await fetchTopologyGraph(mapId);
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
        appliedMapIdRef.current = mapId;
        resetLocalFromServer(graph);
        bumpHistory();
        showOk(t("topology.deletePlaceholderDone"));
      } catch (err) {
        showError(String(err));
      }
    },
    [
      mapId,
      nodes,
      closeCtxMenu,
      commitCanvas,
      resetLocalFromServer,
      queryClient,
      bumpHistory,
      showOk,
      showError,
      t,
      dirtyRef,
      appliedMapIdRef,
    ],
  );

  return { renameSelectedNode, removeNodeById, purgePlaceholderById };
}
