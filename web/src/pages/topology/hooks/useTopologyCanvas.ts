import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, type QueryClient } from "@tanstack/react-query";
import type { Edge, Node, ReactFlowInstance } from "@xyflow/react";
import { fetchTopologyGraph, formatErr } from "../../../services/api";
import { queryKeys } from "../../../constants/queryKeys";
import type { TopologyTreeViewItem, TopologyViewGraph, TopologyWorldTransform } from "../../../types";
import {
  FIT_VIEW_OPTS,
  WORLD_LOCATE_HALF,
  WORLD_LOCATE_ZOOM,
  WORLD_MAP_ENABLED,
} from "../constants";
import type { EdgeDefaults, EdgeStyleData } from "../edgeStyle";
import { withEdgeVisual } from "../edgeStyle";
import { graphFingerprint, graphToFlow, mergeOverlayIntoNodes, overlayFingerprint } from "../graphFlow";
import {
  buildLinkDisplayEdges,
  type LinkMember,
} from "../linkDisplay";
import type { NeNodeData } from "../TopologyReactFlowView";
import { TOPO_HANDLE_X, TOPO_HANDLE_Y } from "../topoGeometry";
import type { ToolMode } from "../toolMode";
import {
  displayViewName,
  isWorldFlatViewName,
  worldDisplayBounds,
  worldVisualLodFromZoom,
} from "../treeUtils";
import { mergeFlatWorldGraph } from "../worldGraph";
import type { HistorySnap } from "../pageTypes";

export type UseTopologyCanvasOptions = {
  mapId: string;
  treeFlatMap: boolean;
  liveSync: boolean;
  canvasMode: boolean;
  activeView: TopologyTreeViewItem | null;
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  setNodes: React.Dispatch<React.SetStateAction<Node<NeNodeData>[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  edgeDefaults: EdgeDefaults;
  toolMode: ToolMode;
  hideIp: boolean;
  hideVendor: boolean;
  hidePorts: boolean;
  showPlaceholderBadge: boolean;
  showConnectStatus: boolean;
  showAlarmOverlay: boolean;
  showLevelColors: boolean;
  expandPhysicalLinks: boolean;
  scaleBundleWidth: boolean;
  edgeFlow: boolean;
  selectedEdgeId: string | null;
  setSelectedEdgeId: React.Dispatch<React.SetStateAction<string | null>>;
  setSearchHitIds: React.Dispatch<React.SetStateAction<string[]>>;
  searchHitTimerRef: React.MutableRefObject<number | null>;
  queryClient: QueryClient;
  dirtyRef: React.MutableRefObject<boolean>;
  historyLockRef: React.MutableRefObject<boolean>;
  historyRef: React.MutableRefObject<HistorySnap[]>;
  redoRef: React.MutableRefObject<HistorySnap[]>;
  pendingEdgeDeletesRef: React.MutableRefObject<Set<string>>;
  pendingEdgeCreatesRef: React.MutableRefObject<Set<string>>;
  clearDirty: () => void;
  bumpHistory: () => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
};

export function useTopologyCanvas(opts: UseTopologyCanvasOptions) {
  const {
    mapId,
    treeFlatMap,
    liveSync,
    canvasMode,
    activeView,
    nodes,
    edges,
    setNodes,
    setEdges,
    edgeDefaults,
    toolMode,
    hideIp,
    hideVendor,
    hidePorts,
    showPlaceholderBadge,
    showConnectStatus,
    showAlarmOverlay,
    showLevelColors,
    expandPhysicalLinks,
    scaleBundleWidth,
    edgeFlow,
    selectedEdgeId,
    setSelectedEdgeId,
    setSearchHitIds,
    searchHitTimerRef,
    queryClient,
    dirtyRef,
    historyLockRef,
    historyRef,
    redoRef,
    pendingEdgeDeletesRef,
    pendingEdgeCreatesRef,
    clearDirty,
    bumpHistory,
    showError,
    t,
  } = opts;

  const rfRef = useRef<ReactFlowInstance<Node<NeNodeData>, Edge> | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const appliedMapIdRef = useRef("");
  const appliedGraphFpRef = useRef("");
  const appliedOverlayFpRef = useRef("");
  const pendingFitRef = useRef(false);
  const nodesRef = useRef<Node<NeNodeData>[]>([]);
  const [canvasZoom, setCanvasZoom] = useState(1);
  const worldTransformRef = useRef<TopologyWorldTransform | null>(null);
  const flatLodFetchGenRef = useRef(0);
  const flatLodTimerRef = useRef<number | null>(null);
  const isWorldFlatCanvasRef = useRef(false);
  const mapIdRef = useRef(mapId);

  nodesRef.current = nodes;

  const graphQuery = useQuery({
    queryKey: queryKeys.topologyGraph(mapId),
    queryFn: () =>
      fetchTopologyGraph(
        mapId,
        WORLD_MAP_ENABLED && treeFlatMap ? { lod: "overview" } : undefined,
      ),
    enabled: Boolean(mapId) && (WORLD_MAP_ENABLED || !treeFlatMap),
    staleTime: liveSync ? 0 : 30_000,
    refetchOnWindowFocus: liveSync && !treeFlatMap,
    refetchInterval: liveSync && mapId && !treeFlatMap ? 3000 : false,
    refetchIntervalInBackground: false,
  });

  const isWorldFlatCanvas =
    WORLD_MAP_ENABLED &&
    (treeFlatMap ||
      isWorldFlatViewName(graphQuery.data?.view?.name) ||
      Boolean(graphQuery.data?.view?.filter?.world_flat));

  isWorldFlatCanvasRef.current = isWorldFlatCanvas;
  mapIdRef.current = mapId;

  const worldVisualLod = isWorldFlatCanvas ? worldVisualLodFromZoom(canvasZoom) : "full";

  const displayOpts = useMemo(
    () => ({
      hideIp,
      hideVendor,
      hidePorts,
      connectMode: toolMode === "connect",
      showPlaceholderBadge,
      showConnectStatus,
      showAlarmOverlay,
      showLevelColors,
      worldVisualLod,
    }),
    [
      hideIp,
      hideVendor,
      hidePorts,
      toolMode,
      showPlaceholderBadge,
      showConnectStatus,
      showAlarmOverlay,
      showLevelColors,
      worldVisualLod,
    ],
  );

  const displayEdges = useMemo(() => {
    if (isWorldFlatCanvas && worldVisualLod !== "full") return [];
    const built = buildLinkDisplayEdges(edges, expandPhysicalLinks, hidePorts, scaleBundleWidth).map((e) =>
      withEdgeVisual(e, edgeDefaults),
    );
    return built.map((e) => {
      const d = (e.data || {}) as EdgeStyleData;
      const selected =
        e.id === selectedEdgeId ||
        Boolean(d.members?.some((m: LinkMember) => m.id === selectedEdgeId));
      return {
        ...e,
        selected,
        label: e.label ? String(e.label) : "",
        animated: edgeFlow,
      };
    });
  }, [
    edges,
    expandPhysicalLinks,
    hidePorts,
    scaleBundleWidth,
    edgeFlow,
    edgeDefaults,
    selectedEdgeId,
    isWorldFlatCanvas,
    worldVisualLod,
  ]);

  useEffect(() => {
    if (graphQuery.data?.world_transform) {
      worldTransformRef.current = graphQuery.data.world_transform;
    }
  }, [graphQuery.data?.world_transform]);

  const refreshFlatViewport = useCallback(async () => {
    if (!WORLD_MAP_ENABLED || !mapId || !isWorldFlatCanvas || dirtyRef.current) return;
    const inst = rfRef.current;
    if (!inst) return;
    const zoom = inst.getZoom();
    setCanvasZoom(zoom);
    const gen = ++flatLodFetchGenRef.current;
    const commit = (g: TopologyViewGraph, centerX: number, centerY: number) => {
      if (g.world_transform) worldTransformRef.current = g.world_transform;
      queryClient.setQueryData(queryKeys.topologyGraph(mapId), (prev: TopologyViewGraph | undefined) =>
        mergeFlatWorldGraph(prev, g, { centerX, centerY }),
      );
    };
    try {
      if (zoom < 0.12) {
        const prev = queryClient.getQueryData<TopologyViewGraph>(queryKeys.topologyGraph(mapId));
        if (!prev?.scatter?.length) {
          const g = await fetchTopologyGraph(mapId, { lod: "overview" });
          if (gen !== flatLodFetchGenRef.current || dirtyRef.current) return;
          const bounds = worldDisplayBounds(g.world_transform);
          const cx = bounds ? bounds.x + bounds.width / 2 : 0;
          const cy = bounds ? bounds.y + bounds.height / 2 : 0;
          commit(g, cx, cy);
        } else if (prev.nodes.length > 0) {
          commit(
            {
              ...prev,
              nodes: [],
              edges: [],
              scatter: prev.scatter,
              world_transform: prev.world_transform
                ? { ...prev.world_transform, lod: "overview" }
                : prev.world_transform,
            },
            0,
            0,
          );
        }
        return;
      }
      const pane = canvasRef.current?.getBoundingClientRect();
      const w = pane?.width || 1200;
      const h = pane?.height || 800;
      const vp = inst.getViewport();
      const z = Math.max(vp.zoom || 0.01, 0.01);
      const min_x = -vp.x / z;
      const min_y = -vp.y / z;
      const max_x = (w - vp.x) / z;
      const max_y = (h - vp.y) / z;
      const g = await fetchTopologyGraph(mapId, {
        lod: "detail",
        min_x,
        max_x,
        min_y,
        max_y,
      });
      if (gen !== flatLodFetchGenRef.current || dirtyRef.current) return;
      commit(g, (min_x + max_x) / 2, (min_y + max_y) / 2);
    } catch {
      /* keep last good sample */
    }
  }, [mapId, isWorldFlatCanvas, queryClient, dirtyRef]);

  const scheduleFlatViewportRefresh = useCallback(() => {
    if (!WORLD_MAP_ENABLED || !isWorldFlatCanvas) return;
    if (flatLodTimerRef.current) window.clearTimeout(flatLodTimerRef.current);
    flatLodTimerRef.current = window.setTimeout(() => {
      flatLodTimerRef.current = null;
      void refreshFlatViewport();
    }, 200);
  }, [isWorldFlatCanvas, refreshFlatViewport]);

  const fitCanvas = useCallback(() => {
    const inst = rfRef.current;
    if (!inst) return;
    const bounds = isWorldFlatCanvas
      ? worldDisplayBounds(worldTransformRef.current || graphQuery.data?.world_transform)
      : null;
    if (bounds) inst.fitBounds(bounds, { padding: 0.12, duration: 0 });
    else inst.fitView({ ...FIT_VIEW_OPTS });
    setCanvasZoom(inst.getZoom());
  }, [isWorldFlatCanvas, graphQuery.data?.world_transform]);

  useEffect(() => {
    return () => {
      if (flatLodTimerRef.current) window.clearTimeout(flatLodTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!canvasMode) return;
    void import("../TopologyReactFlowView");
  }, [canvasMode]);

  useEffect(() => {
    appliedMapIdRef.current = "";
    appliedGraphFpRef.current = "";
    appliedOverlayFpRef.current = "";
  }, [mapId]);

  useEffect(() => {
    if (!canvasMode) return;
    if (!mapId || !graphQuery.data) return;
    const entering = appliedMapIdRef.current !== mapId;

    // Dirty geometry: still refresh connect/level/inventory overlays from live graph.
    if (!entering && dirtyRef.current) {
      const ofp = overlayFingerprint(graphQuery.data);
      if (ofp === appliedOverlayFpRef.current) return;
      const { nodes: merged, changed } = mergeOverlayIntoNodes(nodesRef.current, graphQuery.data);
      appliedOverlayFpRef.current = ofp;
      if (changed) {
        historyLockRef.current = true;
        setNodes(merged);
        historyLockRef.current = false;
      }
      return;
    }

    const fp = graphFingerprint(graphQuery.data);
    if (!entering && fp === appliedGraphFpRef.current) return;

    const selectedIds = entering
      ? new Set<string>()
      : new Set(nodesRef.current.filter((n) => n.selected).map((n) => n.id));
    const { rfNodes, rfEdges } = graphToFlow(graphQuery.data.nodes, graphQuery.data.edges, edgeDefaults);
    const nextNodes =
      selectedIds.size > 0
        ? rfNodes.map((n) => (selectedIds.has(n.id) ? { ...n, selected: true } : n))
        : rfNodes;

    historyLockRef.current = true;
    setNodes(nextNodes);
    setEdges(rfEdges);
    appliedGraphFpRef.current = fp;
    appliedOverlayFpRef.current = overlayFingerprint(graphQuery.data);
    if (entering) {
      appliedMapIdRef.current = mapId;
      historyRef.current = [];
      redoRef.current = [];
      pendingEdgeDeletesRef.current = new Set();
      pendingEdgeCreatesRef.current = new Set();
      clearDirty();
      bumpHistory();
      const hasScatter = (graphQuery.data.scatter?.length || 0) > 0;
      const hasWorld = Boolean(worldDisplayBounds(graphQuery.data.world_transform));
      pendingFitRef.current = nextNodes.length > 0 || hasScatter || hasWorld;
    }
    historyLockRef.current = false;
  }, [
    canvasMode,
    mapId,
    graphQuery.data,
    edgeDefaults,
    setNodes,
    setEdges,
    clearDirty,
    bumpHistory,
    dirtyRef,
    historyLockRef,
    historyRef,
    redoRef,
    pendingEdgeDeletesRef,
    pendingEdgeCreatesRef,
  ]);

  useEffect(() => {
    if (!pendingFitRef.current) return;
    const wt = graphQuery.data?.world_transform;
    const bounds = isWorldFlatCanvas ? worldDisplayBounds(wt) : null;
    const canFitNodes = nodes.length > 0;
    if (!bounds && !canFitNodes) return;
    let cancelled = false;
    let tries = 0;
    const run = () => {
      if (cancelled) return;
      const inst = rfRef.current;
      if (!inst) {
        if (tries++ < 40) window.setTimeout(run, 40);
        return;
      }
      pendingFitRef.current = false;
      if (bounds) {
        inst.fitBounds(bounds, { padding: 0.12, duration: 0 });
      } else {
        inst.fitView({ ...FIT_VIEW_OPTS, duration: 0 });
      }
      setCanvasZoom(inst.getZoom());
    };
    const timer = window.setTimeout(run, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [nodes, mapId, isWorldFlatCanvas, graphQuery.data?.world_transform]);

  useEffect(() => {
    setEdges((eds) => eds.map((e) => withEdgeVisual(e, edgeDefaults)));
  }, [edgeDefaults, setEdges]);

  const focusNode = useCallback(
    (nodeId: string, additive: boolean) => {
      setSelectedEdgeId(null);
      setNodes((ns) =>
        ns.map((n) => {
          if (additive) {
            if (n.id === nodeId) return { ...n, selected: !n.selected };
            return n;
          }
          return { ...n, selected: n.id === nodeId };
        }),
      );
      setEdges((es) => es.map((e) => ({ ...e, selected: false })));
    },
    [setNodes, setEdges, setSelectedEdgeId],
  );

  const focusWorldAround = useCallback(
    async (nodeId: string, displayX: number, displayY: number) => {
      const viewId = mapIdRef.current;
      if (!viewId || !isWorldFlatCanvasRef.current || dirtyRef.current) return;
      if (flatLodTimerRef.current) {
        window.clearTimeout(flatLodTimerRef.current);
        flatLodTimerRef.current = null;
      }
      const gen = ++flatLodFetchGenRef.current;
      const inst = rfRef.current;
      if (inst) {
        setCanvasZoom(WORLD_LOCATE_ZOOM);
        inst.setCenter(displayX, displayY, { zoom: WORLD_LOCATE_ZOOM, duration: 200 });
      }
      const half = WORLD_LOCATE_HALF;
      try {
        const g = await fetchTopologyGraph(viewId, {
          lod: "detail",
          min_x: displayX - half,
          max_x: displayX + half,
          min_y: displayY - half,
          max_y: displayY + half,
        });
        if (gen !== flatLodFetchGenRef.current || dirtyRef.current) return;
        if (g.world_transform) worldTransformRef.current = g.world_transform;
        queryClient.setQueryData(queryKeys.topologyGraph(viewId), (prev: TopologyViewGraph | undefined) =>
          mergeFlatWorldGraph(prev, g, { centerX: displayX, centerY: displayY }),
        );
      } catch {
        /* keep last sample */
      }
      if (gen !== flatLodFetchGenRef.current) return;
      window.requestAnimationFrame(() => {
        if (flatLodFetchGenRef.current !== gen) return;
        rfRef.current?.setCenter(displayX, displayY, { zoom: WORLD_LOCATE_ZOOM, duration: 0 });
        setCanvasZoom(WORLD_LOCATE_ZOOM);
        focusNode(nodeId, false);
        setSearchHitIds([nodeId]);
        if (searchHitTimerRef.current) window.clearTimeout(searchHitTimerRef.current);
        searchHitTimerRef.current = window.setTimeout(() => {
          setSearchHitIds((cur) => (cur.length === 1 && cur[0] === nodeId ? [] : cur));
          searchHitTimerRef.current = null;
        }, 8000);
      });
    },
    [queryClient, focusNode, dirtyRef, setSearchHitIds, searchHitTimerRef],
  );

  const locateNode = useCallback(
    (nodeId: string, opts?: { worldX?: number; worldY?: number }) => {
      const onCanvas = nodesRef.current.find((n) => n.id === nodeId);
      const wt = worldTransformRef.current;
      const originX = Number(wt?.origin_x) || 0;
      const originY = Number(wt?.origin_y) || 0;
      const scale = Math.max(Number(wt?.scale) || 1, 1e-9);

      let displayX: number;
      let displayY: number;
      if (onCanvas) {
        displayX = onCanvas.position.x + TOPO_HANDLE_X;
        displayY = onCanvas.position.y + TOPO_HANDLE_Y;
      } else if (opts?.worldX != null && opts?.worldY != null) {
        displayX = (Number(opts.worldX) - originX) * scale;
        displayY = (Number(opts.worldY) - originY) * scale;
      } else {
        showError(t("topology.locateNotOnCanvas"));
        return;
      }

      focusNode(nodeId, false);
      setSearchHitIds([nodeId]);
      if (searchHitTimerRef.current) window.clearTimeout(searchHitTimerRef.current);
      searchHitTimerRef.current = window.setTimeout(() => {
        setSearchHitIds((cur) => (cur.length === 1 && cur[0] === nodeId ? [] : cur));
        searchHitTimerRef.current = null;
      }, 8000);

      if (isWorldFlatCanvasRef.current) {
        void focusWorldAround(nodeId, displayX, displayY);
        return;
      }

      window.setTimeout(() => {
        rfRef.current?.fitView({
          nodes: [{ id: nodeId }],
          padding: 0.45,
          duration: 280,
          includeHiddenNodes: true,
        });
      }, 30);
    },
    [focusNode, focusWorldAround, showError, t, setSearchHitIds, searchHitTimerRef],
  );

  const graphTruncated = Boolean(graphQuery.data?.truncated);
  const truncateReason = String(graphQuery.data?.truncate_reason || "").trim();
  const worldScatter = graphQuery.data?.scatter || [];
  const worldTotal = Number(graphQuery.data?.world_transform?.total || 0);
  const worldDockMe = Number(graphQuery.data?.world_transform?.dock_me_count || 0);
  const showWorldScatter =
    isWorldFlatCanvas && worldVisualLod !== "full" && worldScatter.length > 0;
  const canvasGraphLoading = Boolean(mapId) && (graphQuery.isPending || !graphQuery.data);
  const canvasGraphError = Boolean(mapId) && graphQuery.isError;
  const canvasGraphErrorMsg = graphQuery.error ? formatErr(graphQuery.error) : "";
  const canvasGraphEmpty =
    Boolean(mapId) &&
    graphQuery.isSuccess &&
    !graphQuery.isFetching &&
    (graphQuery.data?.nodes?.length ?? 0) === 0 &&
    worldScatter.length === 0 &&
    nodes.length === 0 &&
    worldTotal === 0;
  const worldNeedsApply = isWorldFlatCanvas && canvasGraphEmpty && worldDockMe > 0;
  const canvasGraphRefreshing =
    Boolean(mapId) && liveSync && graphQuery.isFetching && Boolean(graphQuery.data) && !canvasGraphLoading;

  const truncateBannerText = useMemo(() => {
    if (!graphTruncated) return "";
    if (isWorldFlatViewName(activeView?.name) || Boolean(graphQuery.data?.view?.filter?.world_flat)) {
      return "";
    }
    if (truncateReason === "membership_cap") return t("topology.truncatedMembership");
    if (truncateReason === "membership_frozen") return t("topology.truncatedFrozen");
    if (truncateReason === "too_many_view_nodes") return t("topology.truncatedNodes");
    if (truncateReason === "too_many_edges") return t("topology.truncatedEdges");
    return t("topology.truncatedGeneric");
  }, [graphTruncated, truncateReason, activeView?.name, graphQuery.data?.view?.filter?.world_flat, t]);

  const activeLeafName = useMemo(() => {
    if (!mapId) return "";
    if (activeView?.name) return displayViewName(activeView.name, t);
    return displayViewName(graphQuery.data?.view?.name, t);
  }, [mapId, activeView, graphQuery.data?.view?.name, t]);

  return {
    rfRef,
    canvasRef,
    nodesRef,
    appliedMapIdRef,
    pendingFitRef,
    isWorldFlatCanvasRef,
    mapIdRef,
    worldTransformRef,
    graphQuery,
    isWorldFlatCanvas,
    canvasZoom,
    setCanvasZoom,
    worldVisualLod,
    displayOpts,
    displayEdges,
    refreshFlatViewport,
    scheduleFlatViewportRefresh,
    fitCanvas,
    focusNode,
    locateNode,
    graphTruncated,
    truncateReason,
    worldScatter,
    worldTotal,
    worldDockMe,
    showWorldScatter,
    canvasGraphLoading,
    canvasGraphError,
    canvasGraphErrorMsg,
    canvasGraphEmpty,
    worldNeedsApply,
    canvasGraphRefreshing,
    truncateBannerText,
    activeLeafName,
  };
}
