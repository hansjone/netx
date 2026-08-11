import { useCallback, useRef, useState } from "react";
import type { Edge, Node } from "@xyflow/react";
import { UNDO_MAX } from "../constants";
import type { HistorySnap } from "../pageTypes";
import type { NeNodeData } from "../TopologyReactFlowView";

export function useTopologyHistory(
  nodes: Node<NeNodeData>[],
  edges: Edge[],
  setNodes: React.Dispatch<React.SetStateAction<Node<NeNodeData>[]>>,
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>,
  t: (key: string) => string,
) {
  const [dirty, setDirty] = useState(false);
  const [historyTick, setHistoryTick] = useState(0);
  const dirtyRef = useRef(false);
  const historyRef = useRef<HistorySnap[]>([]);
  const redoRef = useRef<HistorySnap[]>([]);
  const historyLockRef = useRef(false);
  const pendingEdgeDeletesRef = useRef<Set<string>>(new Set());
  const pendingEdgeCreatesRef = useRef<Set<string>>(new Set());

  const canUndo = historyTick >= 0 && historyRef.current.length > 0;
  const canRedo = historyTick >= 0 && redoRef.current.length > 0;

  const markDirty = useCallback(() => {
    dirtyRef.current = true;
    setDirty(true);
  }, []);

  const clearDirty = useCallback(() => {
    dirtyRef.current = false;
    setDirty(false);
  }, []);

  const bumpHistory = useCallback(() => {
    setHistoryTick((n) => n + 1);
  }, []);

  const confirmDiscardIfDirty = useCallback(() => {
    if (!dirtyRef.current) return true;
    return window.confirm(t("topology.unsavedConfirm"));
  }, [t]);

  const snapshot = useCallback(
    (): HistorySnap => ({
      nodes: nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
      edges: edges.map((e) => ({ ...e })),
      pendingEdgeDeletes: [...pendingEdgeDeletesRef.current],
      pendingEdgeCreates: [...pendingEdgeCreatesRef.current],
    }),
    [nodes, edges],
  );

  const pushHistory = useCallback(() => {
    if (historyLockRef.current) return;
    historyRef.current = [...historyRef.current.slice(-(UNDO_MAX - 1)), snapshot()];
    redoRef.current = [];
    bumpHistory();
  }, [snapshot, bumpHistory]);

  const undo = useCallback(() => {
    const prev = historyRef.current.pop();
    if (!prev) return;
    redoRef.current.push(snapshot());
    historyLockRef.current = true;
    setNodes(prev.nodes);
    setEdges(prev.edges);
    pendingEdgeDeletesRef.current = new Set(prev.pendingEdgeDeletes);
    pendingEdgeCreatesRef.current = new Set(prev.pendingEdgeCreates || []);
    markDirty();
    bumpHistory();
    historyLockRef.current = false;
  }, [snapshot, setNodes, setEdges, markDirty, bumpHistory]);

  const redo = useCallback(() => {
    const next = redoRef.current.pop();
    if (!next) return;
    historyRef.current.push(snapshot());
    historyLockRef.current = true;
    setNodes(next.nodes);
    setEdges(next.edges);
    pendingEdgeDeletesRef.current = new Set(next.pendingEdgeDeletes);
    pendingEdgeCreatesRef.current = new Set(next.pendingEdgeCreates || []);
    markDirty();
    bumpHistory();
    historyLockRef.current = false;
  }, [snapshot, setNodes, setEdges, markDirty, bumpHistory]);

  return {
    dirty,
    canUndo,
    canRedo,
    dirtyRef,
    historyRef,
    redoRef,
    historyLockRef,
    pendingEdgeDeletesRef,
    pendingEdgeCreatesRef,
    markDirty,
    clearDirty,
    bumpHistory,
    confirmDiscardIfDirty,
    pushHistory,
    undo,
    redo,
  };
}
