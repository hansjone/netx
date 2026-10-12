import { useCallback, useMemo } from "react";
import type { Connection, Edge, Node } from "@xyflow/react";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import {
  type EdgeDefaults,
  type EdgeStyleData,
  withEdgeVisual,
} from "../lib/edgeStyle";
import { isLocalPendingEdgeId, newLocalEdgeId } from "../lib/idUtils";
import {
  formatPortPairLabel,
  isAggregateEdgeId,
  physicalIdsForDisplayEdge,
} from "../lib/linkDisplay";

type PortEdit = { a_port: string; b_port: string };
type StyleEdit = { stroke_color: string; stroke_width: number; line_style: string };

type Args = {
  canWriteTopology: boolean;
  mapId: string;
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  displayEdges: Edge[];
  selectedEdgeId: string | null;
  selectedEdge: Edge | null;
  edgeDefaults: EdgeDefaults;
  setNodes: React.Dispatch<React.SetStateAction<Node<NeNodeData>[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  setSelectedEdgeId: React.Dispatch<React.SetStateAction<string | null>>;
  connectClickRef: React.MutableRefObject<string | null>;
  pendingEdgeCreatesRef: React.MutableRefObject<Set<string>>;
  pendingEdgeDeletesRef: React.MutableRefObject<Set<string>>;
  pendingEdgePortEditsRef: React.MutableRefObject<Map<string, PortEdit>>;
  pendingEdgeStyleRef: React.MutableRefObject<Map<string, StyleEdit>>;
  pushHistory: () => void;
  markDirty: () => void;
  closeCtxMenu: () => void;
  showOk: (msg: string) => void;
  t: (key: string) => string;
};

export function useTopologyEdgeEditing({
  canWriteTopology,
  mapId,
  nodes,
  edges,
  displayEdges,
  selectedEdgeId,
  selectedEdge,
  edgeDefaults,
  setNodes,
  setEdges,
  setSelectedEdgeId,
  connectClickRef,
  pendingEdgeCreatesRef,
  pendingEdgeDeletesRef,
  pendingEdgePortEditsRef,
  pendingEdgeStyleRef,
  pushHistory,
  markDirty,
  closeCtxMenu,
  showOk,
  t,
}: Args) {
  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const source = String(connection.source || "");
      const target = String(connection.target || "");
      if (!source || !target || source === target) return false;
      return !edges.some(
        (e) =>
          (e.source === source && e.target === target) ||
          (e.source === target && e.target === source),
      );
    },
    [edges],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!canWriteTopology || !mapId || !isValidConnection(connection)) return;
      const source = String(connection.source || "");
      const target = String(connection.target || "");
      if (!source || !target) return;
      pushHistory();
      connectClickRef.current = null;
      const id = newLocalEdgeId();
      const data: EdgeStyleData = {
        source: "manual",
        source_port: "",
        target_port: "",
        stroke_color: "",
        stroke_width: 0,
        line_style: "",
        discovered_at: null,
      };
      const edge = withEdgeVisual(
        {
          id,
          source,
          target,
          // Center anchors only — omit handle ids so attach matches discovered edges after Save.
          sourceHandle: null,
          targetHandle: null,
          type: "straight",
          animated: false,
          data,
        },
        edgeDefaults,
      );
      pendingEdgeCreatesRef.current.add(id);
      setEdges((es) => [...es, edge]);
      markDirty();
    },
    [
      mapId,
      isValidConnection,
      pushHistory,
      edgeDefaults,
      setEdges,
      markDirty,
      canWriteTopology,
      connectClickRef,
      pendingEdgeCreatesRef,
    ],
  );

  const patchSelectedEdgeStyle = useCallback(
    (
      patch: Partial<
        Pick<EdgeStyleData, "stroke_color" | "stroke_width" | "line_style" | "source_port" | "target_port">
      >,
      opts?: { skipHistory?: boolean },
    ) => {
      if (!selectedEdgeId || !mapId) return;
      const display = displayEdges.find((e) => e.id === selectedEdgeId) || selectedEdge;
      const targetIds = physicalIdsForDisplayEdge(display, edges);
      if (!targetIds.length) return;
      // Port edits only make sense on a single physical manual link.
      if (
        (isAggregateEdgeId(selectedEdgeId) || targetIds.length > 1) &&
        (patch.source_port !== undefined || patch.target_port !== undefined)
      ) {
        return;
      }
      if (!opts?.skipHistory) pushHistory();
      setEdges((eds) =>
        eds.map((e) => {
          if (!targetIds.includes(e.id)) return e;
          const prev = (e.data || {}) as EdgeStyleData;
          const data: EdgeStyleData = {
            ...prev,
            stroke_color: patch.stroke_color !== undefined ? patch.stroke_color : prev.stroke_color || "",
            stroke_width:
              patch.stroke_width !== undefined ? Number(patch.stroke_width || 0) : Number(prev.stroke_width || 0),
            line_style: patch.line_style !== undefined ? patch.line_style : prev.line_style || "",
            source_port: patch.source_port !== undefined ? patch.source_port : prev.source_port || "",
            target_port: patch.target_port !== undefined ? patch.target_port : prev.target_port || "",
          };
          const portLabel = formatPortPairLabel(
            data.source_port || "",
            data.target_port || "",
            data.display_label,
          );
          return withEdgeVisual({ ...e, data, label: portLabel || undefined }, edgeDefaults);
        }),
      );
      markDirty();
      for (const fabricEdgeId of targetIds) {
        const edge = edges.find((e) => e.id === fabricEdgeId);
        const prev = (edge?.data || {}) as EdgeStyleData;
        const nextStyle = {
          stroke_color: patch.stroke_color !== undefined ? patch.stroke_color : prev.stroke_color || "",
          stroke_width:
            patch.stroke_width !== undefined ? Number(patch.stroke_width || 0) : Number(prev.stroke_width || 0),
          line_style: patch.line_style !== undefined ? patch.line_style : prev.line_style || "",
        };
        if (
          patch.stroke_color !== undefined ||
          patch.stroke_width !== undefined ||
          patch.line_style !== undefined
        ) {
          pendingEdgeStyleRef.current.set(fabricEdgeId, nextStyle);
        }
        if (patch.source_port !== undefined || patch.target_port !== undefined) {
          const src = String(prev.source || "").toLowerCase();
          // New local manual edges are saved via edge_creates; only queue port edits for saved edges.
          if (src === "manual" && !pendingEdgeCreatesRef.current.has(fabricEdgeId) && !isLocalPendingEdgeId(fabricEdgeId)) {
            pendingEdgePortEditsRef.current.set(fabricEdgeId, {
              a_port: patch.source_port !== undefined ? patch.source_port : prev.source_port || "",
              b_port: patch.target_port !== undefined ? patch.target_port : prev.target_port || "",
            });
          }
        }
      }
    },
    [
      selectedEdgeId,
      selectedEdge,
      mapId,
      edges,
      displayEdges,
      setEdges,
      pushHistory,
      edgeDefaults,
      markDirty,
      pendingEdgeStyleRef,
      pendingEdgePortEditsRef,
      pendingEdgeCreatesRef,
    ],
  );

  const queueDeleteEdges = useCallback(
    (
      edgeIds: string[],
      opts?: { confirmKey?: string; okKey?: string; skipConfirm?: boolean },
    ) => {
      if (!edgeIds.length) return false;
      if (!opts?.skipConfirm) {
        const confirmMsg = t(opts?.confirmKey || "topology.deleteEdgeConfirm");
        if (!window.confirm(confirmMsg)) return false;
      }
      pushHistory();
      for (const id of edgeIds) {
        if (pendingEdgeCreatesRef.current.has(id) || isLocalPendingEdgeId(id)) {
          pendingEdgeCreatesRef.current.delete(id);
        } else {
          pendingEdgeDeletesRef.current.add(id);
        }
      }
      const idSet = new Set(edgeIds);
      setEdges((es) => es.filter((e) => !idSet.has(e.id)));
      setSelectedEdgeId((cur) => (cur && idSet.has(cur) ? null : cur));
      markDirty();
      showOk(
        t(opts?.okKey || "topology.edgeDeleted").replace("{{count}}", String(edgeIds.length)),
      );
      return true;
    },
    [pushHistory, setEdges, setSelectedEdgeId, markDirty, showOk, t, pendingEdgeCreatesRef, pendingEdgeDeletesRef],
  );

  const removeSelected = useCallback(() => {
    if (!mapId) return;
    const nodeIds = nodes.filter((n) => n.selected).map((n) => n.id);
    const selectedDisplay = displayEdges.filter((e) => e.selected);
    const edgeIds = new Set<string>();
    for (const de of selectedDisplay) {
      for (const id of physicalIdsForDisplayEdge(de, edges)) edgeIds.add(id);
    }
    for (const e of edges) {
      if (e.selected) edgeIds.add(e.id);
    }
    if (!nodeIds.length && !edgeIds.size) return;

    if (nodeIds.length && edgeIds.size) {
      const msg = t("topology.deleteSelectionConfirm")
        .replace("{{nodes}}", String(nodeIds.length))
        .replace("{{edges}}", String(edgeIds.size));
      if (!window.confirm(msg)) return;
      pushHistory();
      const nodeSet = new Set(nodeIds);
      for (const id of edgeIds) {
        if (pendingEdgeCreatesRef.current.has(id) || isLocalPendingEdgeId(id)) {
          pendingEdgeCreatesRef.current.delete(id);
        } else {
          pendingEdgeDeletesRef.current.add(id);
        }
      }
      setNodes((ns) => ns.filter((n) => !nodeSet.has(n.id)));
      setEdges((es) =>
        es.filter(
          (e) =>
            !edgeIds.has(e.id) && !nodeSet.has(e.source) && !nodeSet.has(e.target),
        ),
      );
      setSelectedEdgeId(null);
      markDirty();
      showOk(
        t("topology.selectionDeleted")
          .replace("{{nodes}}", String(nodeIds.length))
          .replace("{{edges}}", String(edgeIds.size)),
      );
      return;
    }

    if (!nodeIds.length && edgeIds.size) {
      queueDeleteEdges([...edgeIds]);
      return;
    }

    pushHistory();
    const nodeSet = new Set(nodeIds);
    setNodes((ns) => ns.filter((n) => !nodeSet.has(n.id)));
    // Drop incident edges from the canvas only — do not queue Fabric deletes.
    setEdges((es) => {
      const next = es.filter((e) => !nodeSet.has(e.source) && !nodeSet.has(e.target));
      for (const e of es) {
        if (next.some((x) => x.id === e.id)) continue;
        pendingEdgeCreatesRef.current.delete(e.id);
      }
      return next;
    });
    setSelectedEdgeId(null);
    markDirty();
  }, [
    mapId,
    nodes,
    edges,
    displayEdges,
    setNodes,
    setEdges,
    setSelectedEdgeId,
    pushHistory,
    markDirty,
    showOk,
    queueDeleteEdges,
    t,
    pendingEdgeCreatesRef,
    pendingEdgeDeletesRef,
  ]);

  const removeEdgeById = useCallback(
    (edgeId: string) => {
      const display = displayEdges.find((e) => e.id === edgeId);
      const ids = physicalIdsForDisplayEdge(display, edges);
      const list = ids.length ? ids : [edgeId];
      queueDeleteEdges(list);
      closeCtxMenu();
    },
    [displayEdges, edges, queueDeleteEdges, closeCtxMenu],
  );

  const staleEdgeIds = useMemo(() => {
    return edges
      .filter((e) => {
        const src = String((e.data as EdgeStyleData | undefined)?.source || "");
        return src === "stale";
      })
      .map((e) => e.id);
  }, [edges]);

  const removeStaleEdges = useCallback(() => {
    if (!staleEdgeIds.length) return;
    queueDeleteEdges(staleEdgeIds, {
      confirmKey: "topology.removeStaleHint",
      okKey: "topology.staleRemoved",
    });
  }, [staleEdgeIds, queueDeleteEdges]);

  return {
    isValidConnection,
    onConnect,
    patchSelectedEdgeStyle,
    queueDeleteEdges,
    removeSelected,
    removeEdgeById,
    staleEdgeIds,
    removeStaleEdges,
  };
}
