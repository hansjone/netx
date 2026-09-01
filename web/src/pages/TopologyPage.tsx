import {
  DISCOVER_AUTO_ADD_KEY,
  DISCOVER_PROJECT_NEIGHBORS_KEY,
  PALETTE_DND,
  SCALE_BUNDLE_WIDTH_KEY,
  SEP,
  SHOW_PLACEHOLDER_BADGE_KEY,
  WORLD_MAP_ENABLED,
} from "./topology/constants";
import type { CtxMenu, PaletteItem, PaletteSource } from "./topology/pageTypes";
import { clampSidebarWidth, loadSidebarWidth, saveSidebarWidth } from "./topology/sidebarLayout";
import {
  canvasBgLuminance,
  canvasFloatChromeVars,
  findViewInRegion,
  isWorldFlatViewName,
  regionDisplayName,
} from "./topology/treeUtils";
import {
  loadAutoLayoutAfterDiscover,
  loadBoolFlag,
  loadCanvasBg,
  loadLabelColors,
  loadVendorColors,
  persistBoolFlag,
  type LabelColors,
  type VendorColors,
} from "./topology/displayPrefs";
import {
  BUILTIN_EDGE_DEFAULTS,
  type EdgeDefaultStyle,
  type EdgeDefaults,
  type EdgeSourceKind,
  type EdgeStyleData,
  loadEdgeDefaults,
  persistEdgeDefaults,
  resolveEdgeStyle,
  withEdgeVisual,
} from "./topology/edgeStyle";
import { applyViewGraph, flowToPositions } from "./topology/graphFlow";
import { isLocalPendingEdgeId, newLocalEdgeId } from "./topology/idUtils";
import { fuzzyIncludes, isPlaceholderSource, nodeMatchesQuery } from "./topology/searchUtils";
import { TopologyDiscoverPanel } from "./topology/TopologyDiscoverPanel";
import { TopologyHexBrowser } from "./topology/TopologyHexBrowser";
import { TopologyToolbar } from "./topology/TopologyToolbar";
import { TopologyViewTools } from "./topology/TopologyViewTools";
import { TopologySidebar } from "./topology/TopologySidebar";
import { TopologyModals } from "./topology/TopologyModals";
import { TopologyCtxMenu } from "./topology/TopologyCtxMenu";
import { useTopologyHistory } from "./topology/hooks/useTopologyHistory";
import { useTopologyDiscover } from "./topology/hooks/useTopologyDiscover";
import { useTopologyTreeNav } from "./topology/hooks/useTopologyTreeNav";
import { useTopologyCanvas } from "./topology/hooks/useTopologyCanvas";
import { useTopologyCreateNe } from "./topology/hooks/useTopologyCreateNe";
import { useTopologyConnectTest } from "./topology/hooks/useTopologyConnectTest";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  Connection,
  Edge,
  Node,
} from "@xyflow/react";
import {
  addTopologyViewNodes,
  createFabricManualEdge,
  deleteFabricEdges,
  purgePlaceholderFabricNodes,
  fetchLldpCollectDashboard,
  fetchManagedNe,
  fetchManagedNeById,
  fetchTopologyGraph,
  fetchUmeNe,
  patchTopologyEdgeStyle,
  patchTopologyPositions,
  removeTopologyViewNodes,
  applyUmeTopologyToFabric,
} from "../services/api";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { useAuth } from "../auth/AuthContext";
import { formatErr } from "../services/api";
import { openOrFocusModule } from "../utils/moduleWindows";
import type {
  FabricNodeSearchHit,
  TopologyViewItem,
} from "../types";
import { alignNodes } from "./topology/layoutGraph";
import {
  formatPortPairLabel,
  isAggregateEdgeId,
  physicalIdsForDisplayEdge,
} from "./topology/linkDisplay";
import { behaviorForMode, toolModeFromKey, type ToolMode } from "./topology/toolMode";
import type { NeNodeData } from "./topology/TopologyReactFlowView";
import {
  buildExportGraph,
  exportTopologySvg,
  exportTopologyXml,
} from "./topology/exportTopology";

/** xyflow + canvas nodes — only loaded when a map canvas is open. */
const TopologyReactFlowView = lazy(() => import("./topology/TopologyReactFlowView"));

export function TopologyPage() {
  const { t, locale } = useI18n();
  const { showOk, showError } = useToast();
  const { hasScope, isAdmin } = useAuth();
  const canWriteTopology = isAdmin || hasScope("ne:write");
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const ctxMenuRef = useRef<HTMLUListElement | null>(null);
  const [hideIp, setHideIp] = useState(true);
  const [hideVendor, setHideVendor] = useState(true);
  const [hidePorts, setHidePorts] = useState(true);
  /** Default: logical aggregate of parallel links; expand to see each physical edge. */
  const [expandPhysicalLinks, setExpandPhysicalLinks] = useState(false);
  /** When aggregating, optionally thicken the line by member count (off = single-line width). */
  const [scaleBundleWidth, setScaleBundleWidth] = useState(() =>
    loadBoolFlag(SCALE_BUNDLE_WIDTH_KEY, false),
  );
  /** TOPO/LLDP corner badge hidden by default; toggle in Display menu. */
  const [showPlaceholderBadge, setShowPlaceholderBadge] = useState(() =>
    loadBoolFlag(SHOW_PLACEHOLDER_BADGE_KEY, false),
  );
  const [edgeFlow, setEdgeFlow] = useState(false);
  const [edgeDefaults, setEdgeDefaults] = useState<EdgeDefaults>(() => loadEdgeDefaults());
  const [canvasBg, setCanvasBg] = useState(loadCanvasBg);
  const [labelColors, setLabelColors] = useState<LabelColors>(() => loadLabelColors());
  const [vendorColors, setVendorColors] = useState<VendorColors>(() => loadVendorColors());
  const [toolMode, setToolMode] = useState<ToolMode>("pan");
  const [snapToGrid, setSnapToGrid] = useState(true);
  const [autoLayoutAfterDiscover, setAutoLayoutAfterDiscover] = useState(loadAutoLayoutAfterDiscover);
  const [discoverAutoAddUnmatched, setDiscoverAutoAddUnmatched] = useState(() =>
    loadBoolFlag(DISCOVER_AUTO_ADD_KEY, true),
  );
  const [discoverProjectNeighbors, setDiscoverProjectNeighbors] = useState(() =>
    loadBoolFlag(DISCOVER_PROJECT_NEIGHBORS_KEY, true),
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => loadSidebarWidth());
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const sidebarDragRef = useRef<{ startX: number; startW: number } | null>(null);
  const topoPageRef = useRef<HTMLDivElement | null>(null);
  const [addNeOpen, setAddNeOpen] = useState(false);
  const [outsidePeersOpen, setOutsidePeersOpen] = useState(false);
  const [outsidePeerSelectedIds, setOutsidePeerSelectedIds] = useState<string[]>([]);
  const [outsidePeerQuery, setOutsidePeerQuery] = useState("");
  const [outsidePeersAdding, setOutsidePeersAdding] = useState(false);
  const [paletteSource, setPaletteSource] = useState<PaletteSource>("managed");
  const [paletteSelectedKeys, setPaletteSelectedKeys] = useState<string[]>([]);
  const [paletteAdding, setPaletteAdding] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  /** Opt-in poll so MCP / other clients painting the open map can be watched. Off by default. */
  const [liveSync, setLiveSync] = useState(false);
  const [canvasQuery, setCanvasQuery] = useState("");
  const [searchHitIds, setSearchHitIds] = useState<string[]>([]);
  const [findOpen, setFindOpen] = useState(false);
  const [findActiveIdx, setFindActiveIdx] = useState(0);
  const searchHitTimerRef = useRef<number | null>(null);
  const findBoxRef = useRef<HTMLDivElement | null>(null);
  const displayMenuRef = useRef<HTMLDetailsElement | null>(null);
  const exportMenuRef = useRef<HTMLDetailsElement | null>(null);
  const [exporting, setExporting] = useState(false);
  const [viewToolsToolbarSlot, setViewToolsToolbarSlot] = useState<HTMLDivElement | null>(null);
  const findJustLocatedRef = useRef(false);
  const [nodes, setNodes] = useState<Node<NeNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  const {
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
  } = useTopologyHistory(nodes, edges, setNodes, setEdges, t);

  const tree = useTopologyTreeNav({
    liveSync,
    searchParams,
    setSearchParams,
    locale,
    queryClient,
    confirmDiscardIfDirty,
    clearDirty,
    showOk,
    showError,
    t,
    setNodes,
    setEdges,
    setCtxMenu,
  });
  const {
    mapId,
    setMapId,
    setWorldFocusFolderId,
    worldViewId,
    selectedFolderId,
    setSelectedFolderId,
    hotBrowseKey,
    setHotBrowseKey,
    pendingHighlightNe,
    setPendingHighlightNe,
    expandedIds,
    setExpandedIds,
    treeNeQuery,
    setTreeNeQuery,
    debouncedTreeNeQuery,
    setDebouncedTreeNeQuery,
    treeSearchOpen,
    setTreeSearchOpen,
    treeSearchRef,
    newRootDialog,
    setNewRootDialog,
    treeQuery,
    treeFlatMap,
    treeNeSearchQuery,
    treeRoot,
    regions,
    treeLoading,
    treeFailed,
    rootFolderId,
    canvasMode,
    activeRegion,
    activeView,
    browseEntries,
    hexBrowseRegion,
    umeWorldHexModules,
    primaryViewOfFolder,
    goUmeWorldNav,
    goRegion,
    goRoot,
    goCanvas,
    goBackBrowse,
    renameMapMut,
    promptRenameMap,
    createRegionMut,
    renameRegionMut,
    promptRenameRegion,
    deleteFolderMut,
    promptNewRegion,
    submitNewRoot,
    promptNewSubRegion,
    onTreeToggleExpand,
    onTreeHotBrowseKey,
    onTreeClearHotBrowseKey,
    onTreeDeleteFolder,
    onTreeDeleteMap,
    breadcrumbFolders,
  } = tree;

  const connectClickRef = useRef<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedKeyword(keyword.trim()), 150);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  const toolBehavior = useMemo(() => {
    const base = behaviorForMode(toolMode);
    if (!canWriteTopology) {
      return { ...base, nodesDraggable: false, nodesConnectable: false };
    }
    return base;
  }, [toolMode, canWriteTopology]);

  const canvas = useTopologyCanvas({
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
  });
  const {
    rfRef,
    canvasRef,
    nodesRef,
    appliedMapIdRef,
    pendingFitRef,
    isWorldFlatCanvasRef,
    worldTransformRef,
    graphQuery,
    isWorldFlatCanvas,
    canvasZoom,
    setCanvasZoom,
    worldVisualLod,
    displayOpts,
    displayEdges,
    scheduleFlatViewportRefresh,
    fitCanvas,
    focusNode,
    locateNode,
    worldScatter,
    showWorldScatter,
    canvasGraphLoading,
    canvasGraphError,
    canvasGraphErrorMsg,
    canvasGraphEmpty,
    worldNeedsApply,
    canvasGraphRefreshing,
    truncateBannerText,
    activeLeafName,
    worldDockMe,
    worldTotal,
    graphTruncated,
    truncateReason,
  } = canvas;

  const discover = useTopologyDiscover({
    mapId,
    nodes,
    edges,
    graphNodes: graphQuery.data?.nodes,
    edgeDefaults,
    autoLayoutAfterDiscover,
    discoverAutoAddUnmatched,
    discoverProjectNeighbors,
    queryClient,
    setNodes,
    setEdges,
    dirtyRef,
    pendingEdgeDeletesRef,
    pendingEdgeCreatesRef,
    historyLockRef,
    appliedMapIdRef,
    markDirty,
    clearDirty,
    showOk,
    showError,
    t,
  });
  const {
    discoverOpen,
    setDiscoverOpen,
    discovering,
    discoverReport,
    discoverProgress,
    discoverError,
    discoverResults,
    discoverSummary,
    discoverPct,
    runDiscover,
    cancelDiscover,
  } = discover;

  // Keep canvas "auto placeholder" in sync with topology LLDP collect policy (shared strategy).
  useEffect(() => {
    let alive = true;
    void fetchLldpCollectDashboard()
      .then((dash) => {
        if (!alive || !dash?.policy) return;
        const next = Boolean(dash.policy.auto_add_unmatched);
        setDiscoverAutoAddUnmatched(next);
        persistBoolFlag(DISCOVER_AUTO_ADD_KEY, next);
      })
      .catch(() => {
        /* keep local default */
      });
    return () => {
      alive = false;
    };
  }, []);

  const neQuery = useQuery({
    queryKey: [...queryKeys.managedNeAll, "topology-add-ne", debouncedKeyword],
    queryFn: () =>
      fetchManagedNe({
        keyword: debouncedKeyword,
        vendor: "",
        connectStatus: "",
        page: 1,
        pageSize: 100,
      }),
    enabled: addNeOpen && paletteSource === "managed",
  });

  const umeQuery = useQuery({
    queryKey: ["umeInventoryNe", "topology-add-ne", debouncedKeyword],
    queryFn: () =>
      fetchUmeNe({
        keyword: debouncedKeyword,
        page: 1,
        pageSize: 100,
      }),
    enabled: addNeOpen && paletteSource === "ume",
  });

  const updateEdgeDefault = useCallback((kind: EdgeSourceKind, patch: Partial<EdgeDefaultStyle>) => {
    setEdgeDefaults((prev) => {
      const next: EdgeDefaults = {
        ...prev,
        [kind]: { ...prev[kind], ...patch },
      };
      persistEdgeDefaults(next);
      return next;
    });
  }, []);

  const resetEdgeDefaults = useCallback(() => {
    const next: EdgeDefaults = {
      ume: { ...BUILTIN_EDGE_DEFAULTS.ume },
      discovered: { ...BUILTIN_EDGE_DEFAULTS.discovered },
      stale: { ...BUILTIN_EDGE_DEFAULTS.stale },
      manual: { ...BUILTIN_EDGE_DEFAULTS.manual },
    };
    persistEdgeDefaults(next);
    setEdgeDefaults(next);
  }, []);

  const applyAlign = useCallback(
    (kind: Parameters<typeof alignNodes>[2]) => {
      const ids = nodes.filter((n) => n.selected).map((n) => n.id);
      if (ids.length < 2) {
        showError(t("topology.alignNeedSelection"));
        return;
      }
      pushHistory();
      setNodes(alignNodes(nodes, ids, kind));
      markDirty();
    },
    [nodes, setNodes, pushHistory, showError, t, markDirty],
  );

  const applyWorldMut = useMutation({
    mutationFn: () => applyUmeTopologyToFabric(),
    onSuccess: async () => {
      showOk(t("topology.worldApplyOk"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyGraph(mapId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      pendingFitRef.current = true;
    },
    onError: (err) => showError(String(err)),
  });

  const saveMut = useMutation({
    mutationFn: async () => {
      if (!mapId) throw new Error(t("topology.selectMap"));
      const createIds = [...pendingEdgeCreatesRef.current];
      for (const id of createIds) {
        const e = edges.find((x) => x.id === id);
        if (!e) continue;
        const d = (e.data || {}) as EdgeStyleData;
        await createFabricManualEdge({
          a_node_id: e.source,
          b_node_id: e.target,
          a_port: String(d.source_port || ""),
          b_port: String(d.target_port || ""),
        });
      }
      pendingEdgeCreatesRef.current.clear();
      const pendingEdges = [...pendingEdgeDeletesRef.current].filter((id) => !isLocalPendingEdgeId(id));
      if (pendingEdges.length) {
        await deleteFabricEdges(pendingEdges);
      }
      pendingEdgeDeletesRef.current.clear();
      const serverIds = (graphQuery.data?.nodes || [])
        .map((n) => n.fabric_node_id)
        .filter(Boolean);
      const localIds = nodes.map((n) => n.id);
      const localSet = new Set(localIds);
      const serverSet = new Set(serverIds);
      const isRegionNode = (id: string) => id.startsWith("region:");
      const toAdd = localIds.filter((id) => !serverSet.has(id) && !isRegionNode(id));
      const toRemove = serverIds.filter((id) => !localSet.has(id) && !isRegionNode(id));
      // Region icons are synthetic placements — persist via positions only.
      // Add first so a save that both adds and removes never flashes an empty view.
      if (toAdd.length) {
        await addTopologyViewNodes(mapId, { fabric_node_ids: toAdd });
      }
      if (toRemove.length) {
        await removeTopologyViewNodes(mapId, toRemove);
      }
      return patchTopologyPositions(mapId, flowToPositions(nodes));
    },
    onSuccess: async (graph) => {
      clearDirty();
      queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      showOk(t("topology.saved"));
    },
    onError: async (err) => {
      showError(t("topology.saveFailed").replace("{{detail}}", formatErr(err)));
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyGraph(mapId) });
    },
  });

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
    [mapId, isValidConnection, pushHistory, edgeDefaults, setEdges, markDirty, canWriteTopology],
  );

  const addPaletteItems = useCallback(
    async (items: PaletteItem[], origin?: { x: number; y: number }): Promise<boolean> => {
      if (!canWriteTopology) return false;
      if (!mapId) {
        showError(t("topology.selectMap"));
        return false;
      }
      if (isWorldFlatViewName(activeView?.name)) {
        showError(t("topology.worldMapNoDirectNes"));
        return false;
      }
      if (items.length === 0 || paletteAdding) return false;

      const onCanvasManaged = new Set(nodes.map((n) => n.data.managed_ne_id).filter(Boolean));
      const onCanvasUme = new Set(nodes.map((n) => n.data.ume_ne_id).filter(Boolean));
      const managedIds = [
        ...new Set(
          items
            .filter((item) => item.source === "managed")
            .map((item) => item.managed_ne_id)
            .filter((id) => id && !onCanvasManaged.has(id)),
        ),
      ];
      const umeIds = [
        ...new Set(
          items
            .filter((item) => item.source === "ume")
            .map((item) => item.ume_ne_id)
            .filter((id) => id && !onCanvasUme.has(id)),
        ),
      ];
      if (managedIds.length === 0 && umeIds.length === 0) return false;

      setPaletteAdding(true);
      try {
        const graph = await addTopologyViewNodes(mapId, {
          managed_ne_ids: managedIds,
          ume_ne_ids: umeIds,
          layout: "grid",
        });
        const managedSet = new Set(managedIds);
        const umeSet = new Set(umeIds);
        const added = graph.nodes.filter(
          (n) =>
            (n.managed_ne_id && managedSet.has(n.managed_ne_id)) ||
            (n.ume_ne_id && umeSet.has(n.ume_ne_id)),
        );
        if (added.length > 0) {
          const start = origin || { x: 80 + nodes.length * 24, y: 80 + nodes.length * 24 };
          const cols = Math.max(1, Math.ceil(Math.sqrt(added.length)));
          await patchTopologyPositions(
            mapId,
            added.map((n, i) => ({
              fabric_node_id: n.fabric_node_id,
              x: start.x + (i % cols) * 180,
              y: start.y + Math.floor(i / cols) * 120,
              label: n.label || n.name || "",
            })),
          );
        }
        const refreshed = await fetchTopologyGraph(mapId);
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), refreshed);
        historyLockRef.current = true;
        applyViewGraph(refreshed, edgeDefaults, setNodes, setEdges);
        historyLockRef.current = false;
        clearDirty();
        const count = added.length || managedIds.length + umeIds.length;
        showOk(t("topology.addSelectedDone").replace("{{count}}", String(count)));
        return count > 0;
      } catch (err) {
        showError(String(err));
        return false;
      } finally {
        setPaletteAdding(false);
      }
    },
    [
      mapId,
      nodes,
      paletteAdding,
      setNodes,
      setEdges,
      showError,
      showOk,
      t,
      queryClient,
      edgeDefaults,
      clearDirty,
      activeView?.name,
    ],
  );

  const addNodeAt = useCallback(
    async (item: PaletteItem, position: { x: number; y: number }) => {
      await addPaletteItems([item], position);
    },
    [addPaletteItems],
  );

  const onPaletteDragStart = (e: React.DragEvent, item: PaletteItem) => {
    e.dataTransfer.setData(PALETTE_DND, JSON.stringify(item));
    e.dataTransfer.effectAllowed = "copy";
  };

  const onCanvasDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes(PALETTE_DND)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };

  const onCanvasDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (!canWriteTopology) return;
    const raw = e.dataTransfer.getData(PALETTE_DND);
    if (!raw || !rfRef.current) return;
    try {
      const item = JSON.parse(raw) as PaletteItem;
      const pos = rfRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      addNodeAt(item, pos);
    } catch {
      /* ignore */
    }
  };
  const selectedNodes = useMemo(() => nodes.filter((n) => n.selected), [nodes]);
  const selectedNode = selectedNodes[0] || null;
  const selectedNodeIds = useMemo(() => selectedNodes.map((n) => n.id), [selectedNodes]);
  const selectedEdge = useMemo(() => {
    if (!selectedEdgeId) return null;
    return (
      displayEdges.find((e) => e.id === selectedEdgeId) ||
      edges.find((e) => e.id === selectedEdgeId) ||
      null
    );
  }, [selectedEdgeId, displayEdges, edges]);
  const selectedEdgeData = (selectedEdge?.data || {}) as EdgeStyleData;
  const selectedEdgeResolved = selectedEdge ? resolveEdgeStyle(selectedEdgeData, edgeDefaults) : null;
  const selectedEdgeSourceNode = selectedEdge ? nodes.find((n) => n.id === selectedEdge.source) : null;
  const selectedEdgeTargetNode = selectedEdge ? nodes.find((n) => n.id === selectedEdge.target) : null;

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
      // Port edits only make sense on a single physical link.
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
      const edge = edges.find((e) => e.id === targetIds[0]);
      const prev = (edge?.data || {}) as EdgeStyleData;
      for (const fabricEdgeId of targetIds) {
        void patchTopologyEdgeStyle(mapId, {
          fabric_edge_id: fabricEdgeId,
          stroke_color: patch.stroke_color !== undefined ? patch.stroke_color : prev.stroke_color || "",
          stroke_width:
            patch.stroke_width !== undefined ? Number(patch.stroke_width || 0) : Number(prev.stroke_width || 0),
          line_style: patch.line_style !== undefined ? patch.line_style : prev.line_style || "",
        }).catch((err) => showError(String(err)));
      }
    },
    [selectedEdgeId, selectedEdge, mapId, edges, displayEdges, setEdges, pushHistory, edgeDefaults, showError],
  );

  const renameSelectedNode = useCallback(() => {
    if (!selectedNode) return;
    const next = window.prompt(t("topology.renameNodePrompt"), selectedNode.data.label || "");
    if (next == null) return;
    const label = next.trim();
    if (!label || label === selectedNode.data.label) {
      setCtxMenu(null);
      return;
    }
    pushHistory();
    markDirty();
    setNodes((ns) =>
      ns.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, label } } : n)),
    );
    setCtxMenu(null);
  }, [selectedNode, t, pushHistory, markDirty, setNodes]);

  const closeCtxMenu = useCallback(() => setCtxMenu(null), []);

  const createNe = useTopologyCreateNe({
    mapId,
    activeViewName: activeView?.name,
    canWrite: canWriteTopology,
    edgeDefaults,
    setNodes,
    setEdges,
    clearDirty,
    historyLockRef,
    focusNode,
    closeCtxMenu,
  });
  const {
    modeOpen: createNeModeOpen,
    openCreateNeAt,
    closeMode: closeCreateNeMode,
    pickManaged: pickCreateManaged,
    pickPlaceholder: pickCreatePlaceholder,
    placeholderDialog,
    setPlaceholderDialog,
    placeholderBusy,
    closePlaceholder,
    submitPlaceholder,
    managedFormOpen,
    managedFormInitial,
    closeManagedForm,
    onManagedFormSaved,
  } = createNe;

  const connectTest = useTopologyConnectTest({ closeCtxMenu });
  const {
    connectDetailRow,
    connectTestSubmitting,
    closeConnectDetail,
    runConnectTestForNode,
    runConnectTestForNodes,
    onConnectRetestSubmitted,
  } = connectTest;

  useEffect(() => {
    const syncFs = () => {
      const el = canvasRef.current;
      const active = Boolean(el && document.fullscreenElement === el);
      setFullscreen(active);
    };
    document.addEventListener("fullscreenchange", syncFs);
    return () => document.removeEventListener("fullscreenchange", syncFs);
  }, []);

  const toggleFullscreen = useCallback(async () => {
    const el = canvasRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement === el) {
        await document.exitFullscreen();
      } else {
        await el.requestFullscreen();
      }
    } catch (err) {
      showError(String(err));
    }
  }, [showError]);

  const clearSelection = useCallback(() => {
    setSelectedEdgeId(null);
    setNodes((ns) => ns.map((n) => ({ ...n, selected: false })));
    setEdges((es) => es.map((e) => ({ ...e, selected: false })));
    connectClickRef.current = null;
  }, [setNodes, setEdges]);

  const selectAllNodes = useCallback(() => {
    setSelectedEdgeId(null);
    setNodes((ns) => ns.map((n) => ({ ...n, selected: true })));
    setEdges((es) => es.map((e) => ({ ...e, selected: false })));
  }, [setNodes, setEdges]);

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
    [pushHistory, setEdges, markDirty, showOk, t],
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
    pushHistory,
    markDirty,
    showOk,
    queueDeleteEdges,
    t,
  ]);

  const removeEdgeById = (edgeId: string) => {
    const display = displayEdges.find((e) => e.id === edgeId);
    const ids = physicalIdsForDisplayEdge(display, edges);
    const list = ids.length ? ids : [edgeId];
    queueDeleteEdges(list);
    closeCtxMenu();
  };

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

  const addOutsidePeers = useCallback(
    async (fabricNodeIds: string[]): Promise<boolean> => {
      if (!mapId) return false;
      const ids = [...new Set(fabricNodeIds.map((x) => String(x || "").trim()).filter(Boolean))];
      if (!ids.length || outsidePeersAdding) return false;
      setOutsidePeersAdding(true);
      try {
        const onCanvas = new Set(nodes.map((n) => n.id));
        const toAdd = ids.filter((id) => !onCanvas.has(id));
        if (!toAdd.length) {
          setOutsidePeersOpen(false);
          setOutsidePeerSelectedIds([]);
          return false;
        }
        const graph = await addTopologyViewNodes(mapId, {
          fabric_node_ids: toAdd,
          layout: "grid",
        });
        if (graph.truncate_reason === "membership_frozen") {
          showError(t("topology.truncatedFrozen"));
          return false;
        }
        const addedSet = new Set(toAdd);
        const added = (graph.nodes || []).filter((n) => addedSet.has(n.fabric_node_id));
        if (added.length > 0) {
          const peerList = graphQuery.data?.outside_peers || [];
          const viaAnchors = new Map(
            peerList
              .filter((p) => addedSet.has(p.fabric_node_id))
              .map((p) => [p.fabric_node_id, p.via_node_id] as const),
          );
          const cols = Math.max(1, Math.ceil(Math.sqrt(added.length)));
          await patchTopologyPositions(
            mapId,
            added.map((n, i) => {
              const viaId = viaAnchors.get(n.fabric_node_id);
              const viaNode = viaId ? nodes.find((x) => x.id === viaId) : undefined;
              const base = viaNode
                ? { x: viaNode.position.x + 200, y: viaNode.position.y }
                : { x: 80 + nodes.length * 24, y: 80 + nodes.length * 24 };
              return {
                fabric_node_id: n.fabric_node_id,
                x: base.x + (i % cols) * 180,
                y: base.y + Math.floor(i / cols) * 120,
                label: n.label || n.name || "",
              };
            }),
          );
        }
        const refreshed = await fetchTopologyGraph(mapId);
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), refreshed);
        historyLockRef.current = true;
        applyViewGraph(refreshed, edgeDefaults, setNodes, setEdges);
        historyLockRef.current = false;
        clearDirty();
        const count = added.length || toAdd.length;
        showOk(t("topology.projectedNeighbors").replace("{{count}}", String(count)));
        setOutsidePeerSelectedIds([]);
        setOutsidePeersOpen(false);
        return count > 0;
      } catch (err) {
        showError(String(err));
        return false;
      } finally {
        setOutsidePeersAdding(false);
      }
    },
    [
      mapId,
      nodes,
      graphQuery.data?.outside_peers,
      outsidePeersAdding,
      edgeDefaults,
      setNodes,
      setEdges,
      clearDirty,
      queryClient,
      showOk,
      showError,
      t,
    ],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || (e.target as HTMLElement)?.isContentEditable) {
        return;
      }
      if (e.key === "Escape") {
        closeCtxMenu();
        clearSelection();
        connectClickRef.current = null;
        setToolMode((m) => (m === "connect" ? "select" : m));
        if (displayMenuRef.current?.open) displayMenuRef.current.open = false;
        setFindOpen(false);
        return;
      }
      const mode = toolModeFromKey(e.key);
      if (mode && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setToolMode(mode);
        connectClickRef.current = null;
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (canWriteTopology && mapId && dirtyRef.current && !saveMut.isPending) saveMut.mutate();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) {
        e.preventDefault();
        redo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
        e.preventDefault();
        selectAllNodes();
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        if (!canWriteTopology) return;
        if (nodes.some((n) => n.selected) || edges.some((ed) => ed.selected)) {
          e.preventDefault();
          void removeSelected();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeCtxMenu, clearSelection, undo, redo, selectAllNodes, removeSelected, nodes, edges, mapId, saveMut, canWriteTopology]);

  useEffect(() => {
    if (!ctxMenu) return;
    const onScroll = (e: Event) => {
      const t = e.target;
      if (t instanceof Node && ctxMenuRef.current?.contains(t)) return;
      closeCtxMenu();
    };
    const onPointerDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest?.(".topo-ctx")) return;
      closeCtxMenu();
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("mousedown", onPointerDown, true);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("mousedown", onPointerDown, true);
    };
  }, [ctxMenu, closeCtxMenu]);

  useLayoutEffect(() => {
    if (!ctxMenu || !ctxMenuRef.current) return;
    const el = ctxMenuRef.current;
    const pad = 8;
    const rect = el.getBoundingClientRect();
    let x = ctxMenu.x;
    let y = ctxMenu.y;
    if (x + rect.width > window.innerWidth - pad) {
      x = Math.max(pad, window.innerWidth - rect.width - pad);
    }
    if (y + rect.height > window.innerHeight - pad) {
      y = Math.max(pad, window.innerHeight - rect.height - pad);
    }
    if (x !== ctxMenu.x || y !== ctxMenu.y) {
      setCtxMenu((prev) => (prev ? { ...prev, x, y } : null));
    }
  }, [ctxMenu]);

  const removeNodeById = (nodeId: string) => {
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
  };

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
          const createIds = [...pendingEdgeCreatesRef.current];
          for (const id of createIds) {
            const e = edges.find((x) => x.id === id);
            if (!e || e.source === nodeId || e.target === nodeId) continue;
            const d = (e.data || {}) as EdgeStyleData;
            await createFabricManualEdge({
              a_node_id: e.source,
              b_node_id: e.target,
              a_port: String(d.source_port || ""),
              b_port: String(d.target_port || ""),
            });
          }
          pendingEdgeCreatesRef.current.clear();
          const pendingEdges = [...pendingEdgeDeletesRef.current].filter(
            (id) => !isLocalPendingEdgeId(id),
          );
          if (pendingEdges.length) {
            await deleteFabricEdges(pendingEdges);
          }
          pendingEdgeDeletesRef.current.clear();
          const serverIds = (graphQuery.data?.nodes || [])
            .map((n) => n.fabric_node_id)
            .filter(Boolean);
          const localIds = new Set(nodes.map((n) => n.id));
          const toRemove = serverIds.filter((id) => !localIds.has(id) && id !== nodeId);
          if (toRemove.length) {
            await removeTopologyViewNodes(mapId, toRemove);
          }
          await patchTopologyPositions(
            mapId,
            flowToPositions(nodes.filter((n) => n.id !== nodeId)),
          );
        }
        await purgePlaceholderFabricNodes([nodeId]);
        const graph = await fetchTopologyGraph(mapId);
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
        appliedMapIdRef.current = mapId;
        const localPos = new Map(
          nodes.filter((n) => n.id !== nodeId).map((n) => [n.id, n.position]),
        );
        historyLockRef.current = true;
        applyViewGraph(graph, edgeDefaults, setNodes, setEdges, localPos);
        historyLockRef.current = false;
        pendingEdgeDeletesRef.current = new Set();
        pendingEdgeCreatesRef.current = new Set();
        historyRef.current = [];
        redoRef.current = [];
        bumpHistory();
        clearDirty();
        showOk(t("topology.deletePlaceholderDone"));
      } catch (err) {
        showError(String(err));
      }
    },
    [
      mapId,
      nodes,
      edges,
      graphQuery.data,
      closeCtxMenu,
      queryClient,
      edgeDefaults,
      setNodes,
      setEdges,
      clearDirty,
      bumpHistory,
      showOk,
      showError,
      t,
    ],
  );

  const openNeInventory = (opts: {
    neId?: string;
    create?: boolean;
    name?: string;
    ip_address?: string;
    vendor?: string;
  }) => {
    const q = new URLSearchParams();
    if (opts.neId) {
      q.set("ne_id", opts.neId);
    } else if (opts.create) {
      q.set("create", "1");
      if (opts.name) q.set("name", opts.name);
      if (opts.ip_address) q.set("ip_address", opts.ip_address);
      if (opts.vendor) q.set("vendor", opts.vendor);
    }
    const qs = q.toString();
    openOrFocusModule({
      moduleId: "ne",
      path: qs ? `/ne?${qs}` : "/ne",
    });
  };

  const openWebcrtFor = (node: Node<NeNodeData> | null) => {
    closeCtxMenu();
    if (!node) return;
    const managedId = String(node.data.managed_ne_id || "").trim();
    const umeId = String(node.data.ume_ne_id || "").trim();

    if (managedId) {
      void (async () => {
        try {
          const ne = await fetchManagedNeById(managedId);
          const src = String(ne.source || "").trim().toLowerCase();
          const hasIp = Boolean(String(ne.ip_address || "").trim());
          // Ready inventory hosts: reuse stored credentials (incl. hop/proxy).
          // LLDP / topology placeholders / incomplete rows → full NE edit form.
          const needsSetup = !hasIp || src === "lldp" || src === "topology";
          if (!needsSetup) {
            openOrFocusModule({
              moduleId: "webcrt",
              path: `/webcrt?ne_id=${encodeURIComponent(managedId)}`,
            });
            return;
          }
          showOk(t("topology.completeNeFirst"));
          openNeInventory({ neId: managedId });
        } catch (err) {
          showError(String(err));
        }
      })();
      return;
    }

    if (umeId) {
      openOrFocusModule({
        moduleId: "webcrt",
        path: `/webcrt?ne_id=${encodeURIComponent(umeId)}&source=ume`,
      });
      return;
    }

    // No inventory link — open NE create with topology hint fields.
    showOk(t("topology.completeNeFirst"));
    openNeInventory({
      create: true,
      name: String(node.data.label || "").trim(),
      ip_address: String(node.data.ne_ip || "").trim(),
      vendor: String(node.data.vendor || "").trim(),
    });
  };

  const openNeFor = (node: Node<NeNodeData> | null) => {
    closeCtxMenu();
    const managedId = String(node?.data.managed_ne_id || "").trim();
    const umeId = String(node?.data.ume_ne_id || "").trim();
    if (managedId) {
      openNeInventory({ neId: managedId });
      return;
    }
    if (umeId) {
      openOrFocusModule({
        moduleId: "ume",
        path: "/ume",
      });
      return;
    }
    showError(t("topology.noNeLink"));
  };

  const openPortTrafficForEdge = (edge: Edge | null) => {
    closeCtxMenu();
    if (!edge) {
      showError(t("topology.noNeLink"));
      return;
    }
    const srcNode = nodes.find((n) => n.id === edge.source);
    const data = (edge.data || {}) as EdgeStyleData;
    const ifname = String(data.source_port || "").trim();
    const managedId = String(srcNode?.data.managed_ne_id || "").trim();
    const umeId = String(srcNode?.data.ume_ne_id || "").trim();
    if (managedId) {
      const q = new URLSearchParams({ ne_id: managedId, source: "managed" });
      if (ifname) q.set("ifname", ifname);
      openOrFocusModule({
        moduleId: "network",
        path: `/network/tasks/port-traffic?${q.toString()}`,
      });
      return;
    }
    if (umeId) {
      const q = new URLSearchParams({ ne_id: umeId, source: "ume" });
      if (ifname) q.set("ifname", ifname);
      openOrFocusModule({
        moduleId: "network",
        path: `/network/tasks/port-traffic?${q.toString()}`,
      });
      return;
    }
    showError(t("topology.noNeLink"));
  };

  const discoverOneFor = (node: Node<NeNodeData> | null) => {
    closeCtxMenu();
    if (!canWriteTopology) {
      showError(t("topology.readOnlyHint"));
      return;
    }
    const id = String(node?.data.managed_ne_id || node?.data.ume_ne_id || "").trim();
    if (!id) {
      showError(t("topology.discoverOneNeedNe"));
      return;
    }
    void runDiscover([id]);
  };

  const selectedDiscoverableCount = useMemo(
    () =>
      selectedNodes.filter((n) =>
        Boolean(String(n.data.managed_ne_id || n.data.ume_ne_id || "").trim()),
      ).length,
    [selectedNodes],
  );

  const selectedConnectableCount = useMemo(
    () =>
      selectedNodes.filter((n) => Boolean(String(n.data.managed_ne_id || "").trim())).length,
    [selectedNodes],
  );

  const discoverSelectedFor = useCallback(() => {
    closeCtxMenu();
    if (!canWriteTopology) {
      showError(t("topology.readOnlyHint"));
      return;
    }
    const ids = [
      ...new Set(
        selectedNodes
          .map((n) => String(n.data.managed_ne_id || n.data.ume_ne_id || "").trim())
          .filter(Boolean),
      ),
    ];
    if (!ids.length) {
      showError(t("topology.discoverOneNeedNe"));
      return;
    }
    void runDiscover(ids);
  }, [closeCtxMenu, canWriteTopology, selectedNodes, runDiscover, showError, t]);

  const connectTestSelectedFor = useCallback(() => {
    void runConnectTestForNodes(selectedNodes);
  }, [runConnectTestForNodes, selectedNodes]);

  const placeCtxMenu = (clientX: number, clientY: number, size?: { w?: number; h?: number }): { x: number; y: number } => {
    const pad = 8;
    const w = size?.w ?? 200;
    const h = size?.h ?? 240;
    const x = Math.min(clientX, window.innerWidth - w - pad);
    const y = Math.min(clientY, window.innerHeight - h - pad);
    return { x: Math.max(pad, x), y: Math.max(pad, y) };
  };

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
  }, [pendingHighlightNe, canvasMode, nodes, locateNode, mapId, graphQuery.data]);

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
  }, [canvasQuery, canvasHits]);

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
    [canvasQuery, canvasHits, findActiveIdx, locateNode, showError, t],
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
  }, []);

  const focusEdge = useCallback(
    (edgeId: string) => {
      setSelectedEdgeId(edgeId);
      setNodes((ns) => ns.map((n) => ({ ...n, selected: false })));
      // Selection is applied in displayEdges; keep store flags clear.
      setEdges((es) => es.map((e) => ({ ...e, selected: false })));
    },
    [setNodes, setEdges],
  );

  const onNodeClick = useCallback(
    (e: React.MouseEvent, node: Node<NeNodeData>) => {
      setCtxMenu(null);
      if (node.data.kind === "region") {
        const viewId = String(node.data.view_id || "").trim();
        const folderId = String(node.data.folder_id || "").trim();
        if (viewId) {
          goCanvas(viewId, folderId || undefined);
          return;
        }
        if (folderId) {
          goRegion(folderId);
          return;
        }
      }
      if (toolMode === "connect") {
        focusNode(node.id, false);
        return;
      }
      focusNode(node.id, e.shiftKey || e.metaKey || e.ctrlKey);
    },
    [toolMode, focusNode, goCanvas, goRegion],
  );

  const onNodeDoubleClick = useCallback(
    (_e: React.MouseEvent, node: Node<NeNodeData>) => {
      // Regions already drill on single click; keep dblclick as no-op alias.
      if (node.data.kind === "region") {
        const viewId = String(node.data.view_id || "").trim();
        const folderId = String(node.data.folder_id || "").trim();
        if (viewId) goCanvas(viewId, folderId || undefined);
        else if (folderId) goRegion(folderId);
      }
    },
    [goCanvas, goRegion],
  );

  const outsidePeers = graphQuery.data?.outside_peers || [];
  const outsidePeerNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) {
      const label = String(n.data?.label || n.data?.subtitle || "").trim();
      m.set(n.id, label || n.id.slice(0, 8));
    }
    return m;
  }, [nodes]);
  const outsidePeersVisible = useMemo(() => {
    const q = outsidePeerQuery.trim().toLowerCase();
    if (!q) return outsidePeers;
    return outsidePeers.filter((p) => {
      const viaName = outsidePeerNameById.get(p.via_node_id) || p.via_node_id;
      const bits = [p.name, p.ip, viaName, p.fabric_node_id];
      return bits.some((b) => String(b || "").toLowerCase().includes(q));
    });
  }, [outsidePeers, outsidePeerQuery, outsidePeerNameById]);

  const runTopologyExport = useCallback(
    async (format: "svg" | "xml") => {
      const view: TopologyViewItem | undefined =
        graphQuery.data?.view ??
        (activeView
          ? {
              id: activeView.id,
              name: activeView.name,
              remark: "",
              node_count: activeView.node_count,
              kind: activeView.kind,
              role: activeView.role,
              sort_order: activeView.sort_order,
            }
          : undefined);
      if (!view) {
        showError(t("topology.selectMap"));
        return;
      }
      if (format !== "xml" && nodes.length === 0 && !isWorldFlatCanvas) {
        showError(t("topology.exportEmpty"));
        return;
      }
      setExporting(true);
      exportMenuRef.current?.removeAttribute("open");
      const filenameBase = activeLeafName || view.name || "topology";
      try {
        if (format === "svg") {
          await exportTopologySvg(
            nodes,
            displayEdges,
            filenameBase,
            {
              labelColor: labelColors.name,
              edgeLabelColor: labelColors.edgeLabel,
              vendorColors,
              hideIp,
              hideVendor,
            },
            filenameBase,
          );
          showOk(t("topology.exportedSvg"));
        } else {
          exportTopologyXml(buildExportGraph(view, nodes, edges, graphQuery.data), filenameBase);
          showOk(t("topology.exportedXml"));
        }
      } catch (err) {
        showError(t("topology.exportFailed").replace("{{detail}}", String(err)));
      } finally {
        setExporting(false);
      }
    },
    [
      activeView,
      graphQuery.data,
      nodes,
      edges,
      displayEdges,
      isWorldFlatCanvas,
      activeLeafName,
      labelColors,
      vendorColors,
      hideIp,
      hideVendor,
      showError,
      showOk,
      t,
    ],
  );

  const titleText = useMemo(() => {
    if (canvasMode) return activeLeafName || t("topology.selectMap");
    if (activeRegion) return regionDisplayName(activeRegion, t);
    return t("topology.rootName");
  }, [canvasMode, activeLeafName, activeRegion, t]);
  const onCanvasManagedIds = useMemo(
    () => new Set(nodes.map((n) => n.data.managed_ne_id).filter(Boolean)),
    [nodes],
  );
  const onCanvasUmeIds = useMemo(
    () => new Set(nodes.map((n) => n.data.ume_ne_id).filter(Boolean)),
    [nodes],
  );
  const palette = useMemo((): PaletteItem[] => {
    if (paletteSource === "ume") {
      return (umeQuery.data?.items || []).map((ne) => {
        const name = (ne.host_name || ne.ne_name || ne.user_label || ne.ip_address || ne.ne_id).trim();
        return {
          key: `ume:${ne.ne_id}`,
          source: "ume" as const,
          managed_ne_id: "",
          ume_ne_id: ne.ne_id,
          name,
          ip: ne.ip_address || "",
          vendor: "ZTE",
          meta: `${ne.ip_address || "-"}${SEP}${ne.ne_type || "UME"}`,
          connect_status: ne.connection_status || "",
        };
      });
    }
    return (neQuery.data?.items || []).map((ne) => ({
      key: `managed:${ne.id}`,
      source: "managed" as const,
      managed_ne_id: ne.id,
      ume_ne_id: "",
      name: ne.name || ne.ip_address,
      ip: ne.ip_address,
      vendor: ne.vendor,
      meta: `${ne.ip_address}${SEP}${ne.vendor}`,
      connect_status: ne.connect_status,
    }));
  }, [paletteSource, neQuery.data, umeQuery.data]);
  const paletteVisible = useMemo(() => {
    const tokens = keyword
      .trim()
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);
    let list = palette;
    if (tokens.length) {
      list = list.filter((item) => {
        const bits = [item.name, item.ip, item.vendor, item.managed_ne_id, item.ume_ne_id, item.meta];
        return tokens.every((tok) => bits.some((b) => fuzzyIncludes(String(b || ""), tok)));
      });
    }
    return list.filter((item) =>
      item.source === "managed"
        ? !onCanvasManagedIds.has(item.managed_ne_id)
        : !onCanvasUmeIds.has(item.ume_ne_id),
    );
  }, [palette, onCanvasManagedIds, onCanvasUmeIds, keyword]);
  const paletteLoading =
    (paletteSource === "managed" && neQuery.isLoading) ||
    (paletteSource === "ume" && umeQuery.isLoading);

  const onSidebarResizeDown = useCallback(
    (e: ReactMouseEvent<HTMLDivElement>) => {
      if (sidebarCollapsed) return;
      e.preventDefault();
      sidebarDragRef.current = { startX: e.clientX, startW: sidebarWidth };
      setSidebarResizing(true);
      const onMove = (ev: MouseEvent) => {
        const drag = sidebarDragRef.current;
        if (!drag) return;
        const pageW = topoPageRef.current?.clientWidth || window.innerWidth;
        const next = clampSidebarWidth(drag.startW + (ev.clientX - drag.startX), pageW);
        setSidebarWidth(next);
      };
      const onUp = () => {
        sidebarDragRef.current = null;
        setSidebarResizing(false);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        setSidebarWidth((w) => {
          const pageW = topoPageRef.current?.clientWidth || window.innerWidth;
          const next = clampSidebarWidth(w, pageW);
          saveSidebarWidth(next);
          return next;
        });
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [sidebarCollapsed, sidebarWidth],
  );

  return (
    <div
      ref={topoPageRef}
      className={`topo-page${sidebarCollapsed ? " is-sidebar-collapsed" : ""}${
        sidebarResizing ? " is-sidebar-resizing" : ""
      }`}
      style={
        sidebarCollapsed
          ? undefined
          : ({ "--topo-sidebar-w": `${sidebarWidth}px` } as CSSProperties)
      }
    >
      <TopologySidebar
        collapsed={sidebarCollapsed}
        resizing={sidebarResizing}
        treeSearchRef={treeSearchRef}
        treeNeQuery={treeNeQuery}
        onTreeNeQueryChange={setTreeNeQuery}
        treeSearchOpen={treeSearchOpen}
        onTreeSearchOpen={setTreeSearchOpen}
        debouncedTreeNeQuery={debouncedTreeNeQuery}
        treeSearchFetching={treeNeSearchQuery.isFetching}
        treeSearchItems={treeNeSearchQuery.data?.items || []}
        treeSearchTotal={treeNeSearchQuery.data?.total || 0}
        onJumpToSearchHit={jumpToTreeSearchHit}
        promptNewRegion={promptNewRegion}
        createRegionPending={createRegionMut.isPending}
        onCollapse={() => setSidebarCollapsed(true)}
        onExpand={() => setSidebarCollapsed(false)}
        treeRoot={treeRoot}
        treeLoading={treeLoading}
        treeFailed={treeFailed}
        treeError={treeQuery.error}
        onTreeRetry={() => void treeQuery.refetch()}
        regions={regions}
        treeNav={{
          treeRootId: String(treeRoot?.id || ""),
          expandedIds,
          mapId,
          worldViewId,
          selectedFolderId,
          hotBrowseKey,
          dirty,
          onToggleExpand: onTreeToggleExpand,
          onHotBrowseKey: onTreeHotBrowseKey,
          onClearHotBrowseKey: onTreeClearHotBrowseKey,
          onWorldFocusClear: () => setWorldFocusFolderId(""),
          goUmeWorldNav,
          goRegion,
          goCanvas,
          promptRenameRegion,
          promptRenameMap,
          renameRegionPending: renameRegionMut.isPending,
          renameMapPending: renameMapMut.isPending,
          onDeleteFolder: onTreeDeleteFolder,
          deleteFolderPending: deleteFolderMut.isPending,
          onDeleteMap: onTreeDeleteMap,
        }}
        onResizeDown={onSidebarResizeDown}
      />

      <main className="topo-main">
        {canvasMode ? (
          <TopologyToolbar
            readOnly={!canWriteTopology}
            breadcrumbFolders={breadcrumbFolders}
            activeView={activeView}
            activeRegion={activeRegion}
            rootFolderId={rootFolderId}
            dirty={dirty}
            selectedNodes={selectedNodes}
            selectedEdgeId={selectedEdgeId}
            liveSync={liveSync}
            onLiveSyncToggle={() => setLiveSync((v) => !v)}
            isWorldFlatCanvas={isWorldFlatCanvas}
            onAddNe={() => {
              if (isWorldFlatCanvas) {
                showError(t("topology.worldMapNoDirectNes"));
                return;
              }
              setKeyword("");
              setPaletteSelectedKeys([]);
              setAddNeOpen(true);
            }}
            onCreateNe={(flowX, flowY) => {
              if (isWorldFlatCanvas) {
                showError(t("topology.worldMapNoDirectNes"));
                return;
              }
              openCreateNeAt(flowX, flowY);
            }}
            nodes={nodes}
            rfRef={rfRef}
            onBackUp={goBackBrowse}
            canUndo={canUndo}
            onUndo={undo}
            canRedo={canRedo}
            onRedo={redo}
            savePending={saveMut.isPending}
            onSave={() => saveMut.mutate()}
            onFit={fitCanvas}
            exportMenuRef={exportMenuRef}
            exporting={exporting}
            onExport={runTopologyExport}
            mapId={mapId}
            staleEdgeCount={staleEdgeIds.length}
            onRemoveStale={removeStaleEdges}
            toolMode={toolMode}
            onToolModeChange={setToolMode}
            onConnectClickReset={() => {
              connectClickRef.current = null;
            }}
            fullscreen={fullscreen}
            viewToolsToolbarSlotRef={setViewToolsToolbarSlot}
            outsidePeerCount={outsidePeers.length}
            onOpenOutsidePeers={() => {
              setOutsidePeerQuery("");
              setOutsidePeerSelectedIds([]);
              setOutsidePeersOpen(true);
            }}
            goRoot={goRoot}
            goRegion={goRegion}
            primaryViewOfFolder={primaryViewOfFolder}
          />
        ) : null}

        {discoverOpen ? (
          <TopologyDiscoverPanel
            discovering={discovering}
            discoverProgress={discoverProgress}
            discoverPct={discoverPct}
            discoverReport={discoverReport}
            discoverError={discoverError}
            hasResults={discoverResults.length > 0 || Boolean(discoverReport)}
            discoverSummary={discoverSummary}
            onCancelDiscover={cancelDiscover}
            onClose={() => setDiscoverOpen(false)}
          />
        ) : null}

        {!canvasMode ? (
          <TopologyHexBrowser
            titleText={titleText}
            breadcrumbFolders={breadcrumbFolders}
            hexBrowseRegion={hexBrowseRegion}
            umeWorldHexModules={umeWorldHexModules}
            browseEntries={browseEntries}
            regions={regions}
            rootFolderId={rootFolderId}
            selectedFolderId={selectedFolderId}
            mapId={mapId}
            hotBrowseKey={hotBrowseKey}
            treeLoading={treeLoading}
            treeFailed={treeFailed}
            treeError={treeQuery.error}
            createRegionPending={createRegionMut.isPending}
            onTreeRetry={() => void treeQuery.refetch()}
            onHotBrowseKey={setHotBrowseKey}
            onClearHotBrowseKey={(key) => setHotBrowseKey((k) => (k === key ? "" : k))}
            goRoot={goRoot}
            goRegion={goRegion}
            goUmeWorldNav={goUmeWorldNav}
            goCanvas={goCanvas}
            promptNewRegion={promptNewRegion}
            promptNewSubRegion={promptNewSubRegion}
            onSelectFolder={setSelectedFolderId}
          />
        ) : (
          <div
            className={`topo-canvas${fullscreen ? " is-fullscreen" : ""}${
              toolMode === "pan" ? " is-pan-mode" : ""
            }${canvasBgLuminance(canvasBg) > 0.55 ? " is-canvas-light" : ""}`}
            ref={canvasRef}
            style={
              {
                "--topo-canvas-bg": canvasBg,
                "--topo-label-color": labelColors.name,
                "--topo-edge-label-color": labelColors.edgeLabel,
                "--topo-vendor-cisco": vendorColors.cisco,
                "--topo-vendor-huawei": vendorColors.huawei,
                "--topo-vendor-zte": vendorColors.zte,
                "--topo-vendor-juniper": vendorColors.juniper,
                "--topo-vendor-nokia": vendorColors.nokia,
                "--topo-vendor-ericsson": vendorColors.ericsson,
                "--topo-vendor-h3c": vendorColors.h3c,
                "--topo-vendor-ruijie": vendorColors.ruijie,
                "--topo-vendor-mikrotik": vendorColors.mikrotik,
                "--topo-vendor-gray": vendorColors.gray,
                ...canvasFloatChromeVars(canvasBg),
              } as CSSProperties
            }
            onDragOver={onCanvasDragOver}
            onDrop={onCanvasDrop}
          >
            {truncateBannerText ? (
              <div className="topo-truncate-banner" role="status">
                {truncateBannerText}
              </div>
            ) : null}
            {!canWriteTopology && mapId ? (
              <div className="topo-readonly-banner" role="status">
                {t("topology.readOnlyBanner")}
              </div>
            ) : null}
            <TopologyViewTools
              fullscreen={fullscreen}
              toolbarSlot={viewToolsToolbarSlot}
              displayMenuRef={displayMenuRef}
              findBoxRef={findBoxRef}
              hideIp={hideIp}
              onHideIpChange={setHideIp}
              hideVendor={hideVendor}
              onHideVendorChange={setHideVendor}
              showPlaceholderBadge={showPlaceholderBadge}
              onShowPlaceholderBadgeChange={setShowPlaceholderBadge}
              hidePorts={hidePorts}
              onHidePortsChange={setHidePorts}
              expandPhysicalLinks={expandPhysicalLinks}
              onExpandPhysicalLinksChange={setExpandPhysicalLinks}
              scaleBundleWidth={scaleBundleWidth}
              onScaleBundleWidthChange={setScaleBundleWidth}
              edgeFlow={edgeFlow}
              onEdgeFlowChange={setEdgeFlow}
              snapToGrid={snapToGrid}
              onSnapToGridChange={setSnapToGrid}
              autoLayoutAfterDiscover={autoLayoutAfterDiscover}
              onAutoLayoutAfterDiscoverChange={setAutoLayoutAfterDiscover}
              discoverAutoAddUnmatched={discoverAutoAddUnmatched}
              onDiscoverAutoAddUnmatchedChange={setDiscoverAutoAddUnmatched}
              discoverProjectNeighbors={discoverProjectNeighbors}
              onDiscoverProjectNeighborsChange={setDiscoverProjectNeighbors}
              canvasBg={canvasBg}
              onCanvasBgChange={setCanvasBg}
              labelColors={labelColors}
              onLabelColorsChange={setLabelColors}
              vendorColors={vendorColors}
              onVendorColorsChange={setVendorColors}
              edgeDefaults={edgeDefaults}
              onUpdateEdgeDefault={updateEdgeDefault}
              onResetEdgeDefaults={resetEdgeDefaults}
              selectedNodeCount={selectedNodes.length}
              onAlign={applyAlign}
              mapId={mapId}
              nodeCount={nodes.length}
              canvasQuery={canvasQuery}
              onCanvasQueryChange={setCanvasQuery}
              findOpen={findOpen}
              onFindOpenChange={setFindOpen}
              findActiveIdx={findActiveIdx}
              onFindActiveIdxChange={setFindActiveIdx}
              canvasHits={canvasHits}
              onFindOnCanvas={findOnCanvas}
            />
            {fullscreen ? (
              <div className="topo-fs-toolbar" role="toolbar" aria-label={t("topology.toolModes")}>
                {(
                  [
                    ["select", t("topology.toolSelect"), "V"],
                    ["pan", t("topology.toolPan"), "H"],
                    ["connect", t("topology.toolConnect"), "C"],
                  ] as const
                ).map(([mode, label, key]) => (
                  <button
                    key={mode}
                    type="button"
                    className={`topo-fs-toolbar__btn${toolMode === mode ? " is-active" : ""}`}
                    title={`${label} (${key})`}
                    aria-pressed={toolMode === mode}
                    onClick={() => {
                      setToolMode(mode);
                      connectClickRef.current = null;
                    }}
                  >
                    {label}
                    <kbd>{key}</kbd>
                  </button>
                ))}
                <button
                  type="button"
                  className="topo-fs-toolbar__btn"
                  title={t("topology.fit")}
                  onClick={() => fitCanvas()}
                >
                  {t("topology.fit")}
                </button>
                <button
                  type="button"
                  className="topo-fs-toolbar__btn"
                  title={t("topology.exitFullscreen")}
                  onClick={() => void toggleFullscreen()}
                >
                  {t("topology.exitFullscreen")}
                </button>
              </div>
            ) : null}
            {treeRoot ? (
              <>
                {isWorldFlatCanvas ? (
                  <div className="topo-world-hud" role="status">
                    <span>
                      {t("topology.worldHud")
                        .replace("{{total}}", String(worldTotal || worldScatter.length || 0))
                        .replace("{{cached}}", String(nodes.length))
                        .replace("{{zoom}}", canvasZoom.toFixed(3))
                        .replace("{{lod}}", worldVisualLod)}
                    </span>
                    {graphTruncated ? (
                      <span className="muted"> · {truncateReason || "truncated"}</span>
                    ) : null}
                  </div>
                ) : null}
                <Suspense
                  fallback={
                    <div className="topo-canvas__empty topo-canvas__status" role="status">
                      <span className="topo-loading-spinner" aria-hidden="true" />
                      <p>{t("topology.treeLoading")}</p>
                    </div>
                  }
                >
                  <TopologyReactFlowView
                    displayOpts={displayOpts}
                    nodes={nodes}
                    setNodes={setNodes}
                    edges={edges}
                    setEdges={setEdges}
                    displayEdges={displayEdges}
                    searchHitIds={searchHitIds}
                    isWorldFlatCanvas={isWorldFlatCanvas}
                    worldVisualLod={worldVisualLod}
                    toolBehavior={toolBehavior}
                    toolMode={toolMode}
                    snapToGrid={snapToGrid}
                    canvasBg={canvasBg}
                    worldScatter={worldScatter}
                    showWorldScatter={showWorldScatter}
                    fullscreen={fullscreen}
                    connectHint={t("topology.connectHint")}
                    fullscreenLabel={t("topology.fullscreen")}
                    exitFullscreenLabel={t("topology.exitFullscreen")}
                    worldTransform={worldTransformRef.current || graphQuery.data?.world_transform}
                    nodesRef={nodesRef}
                    rfRef={rfRef}
                    pendingFitRef={pendingFitRef}
                    markDirty={markDirty}
                    pushHistory={pushHistory}
                    onConnect={onConnect}
                    isValidConnection={isValidConnection}
                    onNodeClick={onNodeClick}
                    onNodeDoubleClick={onNodeDoubleClick}
                    focusEdge={focusEdge}
                    focusNode={focusNode}
                    clearSelection={clearSelection}
                    setCtxMenu={setCtxMenu}
                    placeCtxMenu={placeCtxMenu}
                    selectedNodeIds={selectedNodeIds}
                    closeCtxMenu={closeCtxMenu}
                    setCanvasZoom={setCanvasZoom}
                    scheduleFlatViewportRefresh={scheduleFlatViewportRefresh}
                    toggleFullscreen={toggleFullscreen}
                  />
                </Suspense>
              </>
            ) : treeLoading ? (
              <div className="topo-canvas__empty topo-canvas__status" role="status">
                <span className="topo-loading-spinner" aria-hidden="true" />
                <p>{t("topology.treeLoading")}</p>
              </div>
            ) : treeFailed ? (
              <div className="topo-canvas__empty">
                <p className="muted">{t("topology.treeLoadFailed")}</p>
                <div className="topo-browser__empty-actions">
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    onClick={() => void treeQuery.refetch()}
                  >
                    {t("topology.treeRetry")}
                  </button>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={promptNewRegion}
                    disabled={createRegionMut.isPending}
                  >
                    {t("topology.newRegion")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="topo-canvas__empty">
                <p className="muted">{t("topology.emptyMaps")}</p>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={promptNewRegion}
                  disabled={createRegionMut.isPending}
                >
                  {t("topology.newRegion")}
                </button>
              </div>
            )}
            {treeRoot && canvasGraphError ? (
              <div className="topo-canvas__overlay topo-canvas__overlay--error" role="alert">
                <p className="topo-canvas__overlay-title">{t("topology.graphLoadFailed")}</p>
                {canvasGraphErrorMsg ? (
                  <p className="topo-canvas__overlay-hint muted">{canvasGraphErrorMsg}</p>
                ) : null}
                <div className="topo-browser__empty-actions">
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    onClick={() => void graphQuery.refetch()}
                  >
                    {t("topology.treeRetry")}
                  </button>
                </div>
              </div>
            ) : null}
            {treeRoot && canvasGraphLoading ? (
              <div className="topo-canvas__overlay topo-canvas__overlay--loading" role="status">
                <span className="topo-loading-spinner topo-loading-spinner--lg" aria-hidden="true" />
                <p className="topo-canvas__overlay-title">{t("topology.graphLoading")}</p>
              </div>
            ) : null}
            {treeRoot && canvasGraphEmpty ? (
              <div
                className={`topo-canvas__overlay topo-canvas__overlay--empty${
                  worldNeedsApply ? " is-interactive" : ""
                }`}
                role="status"
              >
                <p className="topo-canvas__overlay-title">
                  {worldNeedsApply ? t("topology.worldEmptyCoords") : t("topology.canvasEmpty")}
                </p>
                <p className="topo-canvas__overlay-hint">
                  {worldNeedsApply
                    ? t("topology.worldEmptyCoordsHint").replace("{{dock}}", String(worldDockMe))
                    : t("topology.canvasEmptyHint")}
                </p>
                {worldNeedsApply ? (
                  <div className="btn-row" style={{ marginTop: 10, justifyContent: "center" }}>
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={applyWorldMut.isPending}
                      onClick={() => applyWorldMut.mutate()}
                    >
                      {applyWorldMut.isPending
                        ? t("topology.worldApplying")
                        : t("topology.worldApply")}
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            {treeRoot && canvasGraphRefreshing ? (
              <div className="topo-canvas__refresh" role="status" aria-live="polite">
                <span className="topo-loading-spinner" aria-hidden="true" />
                {t("topology.graphRefreshing")}
              </div>
            ) : null}
          </div>
        )}
      </main>

      <TopologyModals
        canvasMode={canvasMode}
        newRootDialog={newRootDialog}
        onNewRootNameChange={(name) => setNewRootDialog({ name })}
        onCloseNewRoot={() => setNewRootDialog(null)}
        onSubmitNewRoot={submitNewRoot}
        createRegionPending={createRegionMut.isPending}
        createNeModeOpen={createNeModeOpen}
        onCloseCreateNeMode={closeCreateNeMode}
        onPickCreateManaged={pickCreateManaged}
        onPickCreatePlaceholder={pickCreatePlaceholder}
        placeholderDialog={placeholderDialog}
        placeholderBusy={placeholderBusy}
        onPlaceholderChange={(patch) =>
          setPlaceholderDialog((prev) => (prev ? { ...prev, ...patch } : prev))
        }
        onClosePlaceholder={closePlaceholder}
        onSubmitPlaceholder={submitPlaceholder}
        managedFormOpen={managedFormOpen}
        managedFormInitial={managedFormInitial}
        onCloseManagedForm={closeManagedForm}
        onManagedFormSaved={(item) => void onManagedFormSaved(item)}
        connectDetailRow={connectDetailRow}
        onCloseConnectDetail={closeConnectDetail}
        onConnectRetestSubmitted={onConnectRetestSubmitted}
        outsidePeersOpen={outsidePeersOpen}
        outsidePeers={outsidePeers}
        outsidePeersVisible={outsidePeersVisible}
        outsidePeerQuery={outsidePeerQuery}
        onOutsidePeerQueryChange={setOutsidePeerQuery}
        outsidePeerSelectedIds={outsidePeerSelectedIds}
        onOutsidePeerSelectedIdsChange={setOutsidePeerSelectedIds}
        outsidePeerNameById={outsidePeerNameById}
        outsidePeersAdding={outsidePeersAdding}
        onCloseOutsidePeers={() => setOutsidePeersOpen(false)}
        onAddOutsidePeers={addOutsidePeers}
        addNeOpen={addNeOpen}
        paletteSource={paletteSource}
        onPaletteSourceChange={(source) => {
          setPaletteSource(source);
          setPaletteSelectedKeys([]);
        }}
        keyword={keyword}
        onKeywordChange={(value) => {
          setKeyword(value);
          setPaletteSelectedKeys([]);
        }}
        paletteVisible={paletteVisible}
        paletteSelectedKeys={paletteSelectedKeys}
        onPaletteSelectedKeysChange={setPaletteSelectedKeys}
        paletteLoading={paletteLoading}
        paletteAdding={paletteAdding}
        onCloseAddNe={() => setAddNeOpen(false)}
        onPaletteDragStart={onPaletteDragStart}
        onAddSelectedPalette={async () => {
          const selected = paletteVisible.filter((item) =>
            paletteSelectedKeys.includes(item.key),
          );
          if (selected.length === 0) return;
          const ok = await addPaletteItems(selected);
          if (!ok) return;
          setPaletteSelectedKeys([]);
          setAddNeOpen(false);
        }}
      />

      {ctxMenu ? (
        <TopologyCtxMenu
          ctxMenu={ctxMenu}
          menuRef={ctxMenuRef}
          portalTarget={canvasRef.current}
          fullscreen={fullscreen}
          selectedNodeCount={selectedNodes.length}
          selectedNode={selectedNode}
          selectedEdge={selectedEdge}
          selectedEdgeData={selectedEdgeData}
          selectedEdgeResolved={selectedEdgeResolved}
          selectedEdgeSourceNode={selectedEdgeSourceNode || null}
          selectedEdgeTargetNode={selectedEdgeTargetNode || null}
          expandPhysicalLinks={expandPhysicalLinks}
          discovering={discovering}
          isWorldFlatCanvas={isWorldFlatCanvas}
          createRegionPending={createRegionMut.isPending}
          onClose={closeCtxMenu}
          onToggleFullscreen={toggleFullscreen}
          onRemoveSelected={removeSelected}
          onDiscoverSelected={discoverSelectedFor}
          onConnectTestSelected={connectTestSelectedFor}
          selectedDiscoverableCount={selectedDiscoverableCount}
          selectedConnectableCount={selectedConnectableCount}
          onOpenCreateNe={openCreateNeAt}
          onPromptNewSubRegion={promptNewSubRegion}
          onRenameSelectedNode={renameSelectedNode}
          onDiscoverOne={discoverOneFor}
          onOpenWebcrt={openWebcrtFor}
          onOpenNe={openNeFor}
          onConnectTest={runConnectTestForNode}
          connectTestBusy={connectTestSubmitting}
          onPurgePlaceholder={purgePlaceholderById}
          onRemoveNode={removeNodeById}
          onExpandPhysicalLinks={() => setExpandPhysicalLinks(true)}
          onPushHistory={pushHistory}
          onPatchSelectedEdgeStyle={patchSelectedEdgeStyle}
          onOpenPortTraffic={openPortTrafficForEdge}
          onRemoveEdge={removeEdgeById}
        />
      ) : null}
    </div>
  );
}
