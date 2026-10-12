import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Edge, Node, ReactFlowInstance } from "@xyflow/react";
import type { FabricNodeSearchHit, TopologyTreeFolderItem } from "../../../types";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import { WORLD_MAP_ENABLED } from "../lib/constants";
import { nodeMatchesQuery } from "../lib/searchUtils";
import { findViewInRegion, isWorldFlatViewName } from "../lib/treeUtils";

type Args = {
  mapId: string;
  canvasMode: boolean;
  nodes: Node<NeNodeData>[];
  regions: TopologyTreeFolderItem[];
  pendingHighlightNe: string;
  graphQueryData: unknown;
  canvasQuery: string;
  setCanvasQuery: React.Dispatch<React.SetStateAction<string>>;
  setSearchHitIds: React.Dispatch<React.SetStateAction<string[]>>;
  findOpen: boolean;
  setFindOpen: React.Dispatch<React.SetStateAction<boolean>>;
  findActiveIdx: number;
  setFindActiveIdx: React.Dispatch<React.SetStateAction<number>>;
  findBoxRef: React.MutableRefObject<HTMLDivElement | null>;
  displayMenuRef: React.MutableRefObject<HTMLDetailsElement | null>;
  isWorldFlatCanvasRef: React.MutableRefObject<boolean>;
  rfRef: React.MutableRefObject<ReactFlowInstance<Node<NeNodeData>, Edge> | null>;
  locateNode: (id: string, world?: { worldX?: number; worldY?: number }) => void;
  confirmDiscardIfDirty: () => boolean;
  setMapId: (id: string) => void;
  setSelectedFolderId: (id: string) => void;
  setExpandedIds: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  setPendingHighlightNe: (id: string) => void;
  setTreeNeQuery: (q: string) => void;
  setDebouncedTreeNeQuery: (q: string) => void;
  setTreeSearchOpen: (open: boolean) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
};

export function useTopologyFind({
  mapId,
  canvasMode,
  nodes,
  regions,
  pendingHighlightNe,
  graphQueryData,
  canvasQuery,
  setCanvasQuery,
  setSearchHitIds,
  setFindOpen,
  findActiveIdx,
  setFindActiveIdx,
  findBoxRef,
  displayMenuRef,
  isWorldFlatCanvasRef,
  rfRef,
  locateNode,
  confirmDiscardIfDirty,
  setMapId,
  setSelectedFolderId,
  setExpandedIds,
  setPendingHighlightNe,
  setTreeNeQuery,
  setDebouncedTreeNeQuery,
  setTreeSearchOpen,
  showError,
  t,
}: Args) {
  const findJustLocatedRef = useRef(false);
  const pendingLocateWorldRef = useRef<{
    id: string;
    worldX?: number;
    worldY?: number;
  } | null>(null);

  const jumpToTreeSearchHit = useCallback(
    (
      hit: FabricNodeSearchHit,
      view?: NonNullable<FabricNodeSearchHit["views"]>[number],
    ) => {
      const views = hit.views || [];
      const worldCoords =
        hit.world_x != null && hit.world_y != null
          ? { worldX: Number(hit.world_x), worldY: Number(hit.world_y) }
          : undefined;
      const onCurrent =
        Boolean(mapId) &&
        (views.some((v) => v.view_id === mapId) ||
          nodes.some((n) => n.id === hit.id) ||
          (isWorldFlatCanvasRef.current && worldCoords != null));
      if (onCurrent && mapId && (!view || view.view_id === mapId)) {
        if (isWorldFlatCanvasRef.current) {
          locateNode(hit.id, worldCoords);
        } else if (nodes.some((n) => n.id === hit.id)) {
          locateNode(hit.id);
        } else {
          setPendingHighlightNe(hit.id);
        }
        setTreeNeQuery("");
        setDebouncedTreeNeQuery("");
        setTreeSearchOpen(false);
        return;
      }
      const target =
        view ||
        (WORLD_MAP_ENABLED
          ? views.find((v) => isWorldFlatViewName(v.view_name))
          : undefined) ||
        views.find((v) => !isWorldFlatViewName(v.view_name) && String(v.kind) === "physical") ||
        views.find((v) => !isWorldFlatViewName(v.view_name)) ||
        views[0] ||
        null;
      if (!target) {
        showError(t("topology.treeSearchNotOnMap"));
        return;
      }
      if (!WORLD_MAP_ENABLED && isWorldFlatViewName(target.view_name)) {
        showError(t("topology.worldMapOffline"));
        return;
      }
      if (!confirmDiscardIfDirty()) return;
      const regionId = target.folder_id || findViewInRegion(regions, target.view_id)?.region.id;
      if (regionId) {
        setSelectedFolderId(regionId);
        setExpandedIds((p) => ({ ...p, [regionId]: true }));
      }
      if (WORLD_MAP_ENABLED && isWorldFlatViewName(target.view_name) && worldCoords) {
        pendingLocateWorldRef.current = { id: hit.id, ...worldCoords };
      } else {
        pendingLocateWorldRef.current = null;
      }
      setMapId(target.view_id);
      setPendingHighlightNe(hit.id);
      setTreeNeQuery("");
      setDebouncedTreeNeQuery("");
      setTreeSearchOpen(false);
    },
    [
      mapId,
      nodes,
      locateNode,
      showError,
      t,
      confirmDiscardIfDirty,
      regions,
      setSelectedFolderId,
      setMapId,
      setPendingHighlightNe,
      setTreeNeQuery,
      setDebouncedTreeNeQuery,
      setTreeSearchOpen,
      setExpandedIds,
      isWorldFlatCanvasRef,
    ],
  );

  useEffect(() => {
    if (!pendingHighlightNe || !canvasMode) return;
    const nodeId = pendingHighlightNe;
    const pendingWorld = pendingLocateWorldRef.current;
    if (
      pendingWorld &&
      pendingWorld.id === nodeId &&
      isWorldFlatCanvasRef.current &&
      rfRef.current
    ) {
      pendingLocateWorldRef.current = null;
      setPendingHighlightNe("");
      window.setTimeout(
        () =>
          locateNode(nodeId, {
            worldX: pendingWorld.worldX,
            worldY: pendingWorld.worldY,
          }),
        80,
      );
      return;
    }
    if (!nodes.length || !nodes.some((n) => n.id === nodeId)) return;
    pendingLocateWorldRef.current = null;
    setPendingHighlightNe("");
    window.setTimeout(() => locateNode(nodeId), 80);
  }, [
    pendingHighlightNe,
    canvasMode,
    nodes,
    locateNode,
    mapId,
    graphQueryData,
    isWorldFlatCanvasRef,
    rfRef,
    setPendingHighlightNe,
  ]);

  const canvasHits = useMemo(() => {
    const q = canvasQuery.trim();
    if (!q) return [];
    return nodes.filter((n) => nodeMatchesQuery(n, q));
  }, [canvasQuery, nodes]);

  useEffect(() => {
    const q = canvasQuery.trim();
    if (!q) {
      setFindActiveIdx(0);
      setFindOpen(false);
      if (findJustLocatedRef.current) {
        findJustLocatedRef.current = false;
        return;
      }
      setSearchHitIds([]);
      return;
    }
    setFindOpen(true);
    setSearchHitIds(canvasHits.map((n) => n.id));
    setFindActiveIdx((idx) => (canvasHits.length ? Math.min(idx, canvasHits.length - 1) : 0));
  }, [canvasQuery, canvasHits, setFindActiveIdx, setFindOpen, setSearchHitIds]);

  const findOnCanvas = useCallback(
    (nodeId?: string) => {
      const q = canvasQuery.trim();
      if (!q) return;
      if (!canvasHits.length) {
        showError(t("topology.findNoMatch"));
        return;
      }
      const id =
        nodeId ||
        canvasHits[Math.max(0, Math.min(findActiveIdx, canvasHits.length - 1))]?.id ||
        canvasHits[0].id;
      findJustLocatedRef.current = true;
      setFindOpen(false);
      setCanvasQuery("");
      setFindActiveIdx(0);
      locateNode(id);
    },
    [
      canvasQuery,
      canvasHits,
      findActiveIdx,
      locateNode,
      showError,
      t,
      setFindOpen,
      setCanvasQuery,
      setFindActiveIdx,
    ],
  );

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (findBoxRef.current && target && !findBoxRef.current.contains(target)) {
        setFindOpen(false);
      }
      const details = displayMenuRef.current;
      if (details?.open && target && !details.contains(target)) {
        details.open = false;
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [displayMenuRef, findBoxRef, setFindOpen]);

  return {
    canvasHits,
    jumpToTreeSearchHit,
    findOnCanvas,
  };
}
