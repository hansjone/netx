import { useEffect, useRef } from "react";
import type { Edge, Node } from "@xyflow/react";
import type { UseMutationResult } from "@tanstack/react-query";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import { toolModeFromKey, type ToolMode } from "../lib/toolMode";

type Args = {
  canWriteTopology: boolean;
  mapId: string;
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  dirtyRef: React.MutableRefObject<boolean>;
  connectClickRef: React.MutableRefObject<string | null>;
  displayMenuRef: React.MutableRefObject<HTMLDetailsElement | null>;
  saveMut: Pick<UseMutationResult<unknown, unknown, void, unknown>, "isPending" | "mutate">;
  closeCtxMenu: () => void;
  clearSelection: () => void;
  setToolMode: React.Dispatch<React.SetStateAction<ToolMode>>;
  setFindOpen: React.Dispatch<React.SetStateAction<boolean>>;
  undo: () => void;
  redo: () => void;
  selectAllNodes: () => void;
  removeSelected: () => void;
};

/**
 * Global topology shortcuts. Handlers are read via refs so the window
 * listener is not rebound on every nodes/edges change.
 */
export function useTopologyKeyboard({
  canWriteTopology,
  mapId,
  nodes,
  edges,
  dirtyRef,
  connectClickRef,
  displayMenuRef,
  saveMut,
  closeCtxMenu,
  clearSelection,
  setToolMode,
  setFindOpen,
  undo,
  redo,
  selectAllNodes,
  removeSelected,
}: Args) {
  const latestRef = useRef({
    canWriteTopology,
    mapId,
    nodes,
    edges,
    saveMut,
    closeCtxMenu,
    clearSelection,
    setToolMode,
    setFindOpen,
    undo,
    redo,
    selectAllNodes,
    removeSelected,
  });
  latestRef.current = {
    canWriteTopology,
    mapId,
    nodes,
    edges,
    saveMut,
    closeCtxMenu,
    clearSelection,
    setToolMode,
    setFindOpen,
    undo,
    redo,
    selectAllNodes,
    removeSelected,
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || (e.target as HTMLElement)?.isContentEditable) {
        return;
      }
      const s = latestRef.current;
      if (e.key === "Escape") {
        s.closeCtxMenu();
        s.clearSelection();
        connectClickRef.current = null;
        s.setToolMode((m) => (m === "connect" ? "select" : m));
        if (displayMenuRef.current?.open) displayMenuRef.current.open = false;
        s.setFindOpen(false);
        return;
      }
      const mode = toolModeFromKey(e.key);
      if (mode && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        s.setToolMode(mode);
        connectClickRef.current = null;
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (s.canWriteTopology && s.mapId && dirtyRef.current && !s.saveMut.isPending) s.saveMut.mutate();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        s.undo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) {
        e.preventDefault();
        s.redo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
        e.preventDefault();
        s.selectAllNodes();
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        if (!s.canWriteTopology) return;
        if (s.nodes.some((n) => n.selected) || s.edges.some((ed) => ed.selected)) {
          e.preventDefault();
          void s.removeSelected();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [connectClickRef, displayMenuRef, dirtyRef]);
}
