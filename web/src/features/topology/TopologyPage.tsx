import {
  DISCOVER_AUTO_ADD_KEY,
  DISCOVER_PROJECT_NEIGHBORS_KEY,
  SCALE_BUNDLE_WIDTH_KEY,
  SHOW_PLACEHOLDER_BADGE_KEY,
  SHOW_CONNECT_STATUS_KEY,
  SHOW_ALARM_OVERLAY_KEY,
  SHOW_LEVEL_COLORS_KEY,
} from "./lib/constants";
import type { CtxMenu, PaletteSource } from "./lib/pageTypes";
import { clampSidebarWidth, loadSidebarWidth, saveSidebarWidth } from "./lib/sidebarLayout";
import {
  loadAutoLayoutAfterDiscover,
  loadBoolFlag,
  loadCanvasBg,
  loadLabelColors,
  loadVendorColors,
  persistBoolFlag,
  type LabelColors,
  type VendorColors,
} from "./lib/displayPrefs";
import {
  BUILTIN_EDGE_DEFAULTS,
  type EdgeDefaultStyle,
  type EdgeDefaults,
  type EdgeSourceKind,
  type EdgeStyleData,
  loadEdgeDefaults,
  persistEdgeDefaults,
  resolveEdgeStyle,
} from "./lib/edgeStyle";
import { useTopologySave } from "./hooks/useTopologySave";
import { useTopologyPalette } from "./hooks/useTopologyPalette";
import { useTopologyEdgeEditing } from "./hooks/useTopologyEdgeEditing";
import { useTopologyKeyboard } from "./hooks/useTopologyKeyboard";
import { useTopologyFind } from "./hooks/useTopologyFind";
import { useTopologyExport } from "./hooks/useTopologyExport";
import { useTopologyNodeOps } from "./hooks/useTopologyNodeOps";
import {
  openWebcrtFor as openWebcrtForLink,
  openNeFor as openNeForLink,
  openAlarmsFor as openAlarmsForLink,
  openPortTrafficForEdge as openPortTrafficForEdgeLink,
} from "./lib/moduleLinks";
import { useTopologyHistory } from "./hooks/useTopologyHistory";
import { useTopologyDiscover } from "./hooks/useTopologyDiscover";
import { useTopologyTreeNav } from "./hooks/useTopologyTreeNav";
import { useTopologyCanvas } from "./hooks/useTopologyCanvas";
import { useTopologyCreateNe } from "./hooks/useTopologyCreateNe";
import { useTopologyConnectTest } from "./hooks/useTopologyConnectTest";
import { useTopologyAlarmOverlay } from "./hooks/useTopologyAlarmOverlay";
import { TopologyPageView } from "./components/TopologyPageView";
import {
  lazy,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type {
  Edge,
  Node,
} from "@xyflow/react";
import {
  fetchLldpCollectDashboard,
  applyUmeTopologyToFabric,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import { useAuth } from "../auth/AuthContext";
import { alignNodes } from "./lib/layoutGraph";
import { behaviorForMode, type ToolMode } from "./lib/toolMode";
import type { NeNodeData } from "./components/TopologyReactFlowView";

/** xyflow + canvas nodes — only loaded when a map canvas is open. */
const TopologyReactFlowView = lazy(() => import("./components/TopologyReactFlowView"));

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
  const [showConnectStatus, setShowConnectStatus] = useState(() =>
    loadBoolFlag(SHOW_CONNECT_STATUS_KEY, true),
  );
  const [showAlarmOverlay, setShowAlarmOverlay] = useState(() =>
    loadBoolFlag(SHOW_ALARM_OVERLAY_KEY, true),
  );
  const [showLevelColors, setShowLevelColors] = useState(() =>
    loadBoolFlag(SHOW_LEVEL_COLORS_KEY, true),
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
  const [paletteSource, setPaletteSource] = useState<PaletteSource>("managed");
  const [paletteSelectedKeys, setPaletteSelectedKeys] = useState<string[]>([]);
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
  const [viewToolsToolbarSlot, setViewToolsToolbarSlot] = useState<HTMLDivElement | null>(null);
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
    pendingEdgePortEditsRef,
    pendingEdgeStyleRef,
    markDirty,
    clearDirty,
    bumpHistory,
    confirmDiscardIfDirty,
    pushHistory,
    popHistory,
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
    pendingEdgePortEditsRef,
    pendingEdgeStyleRef,
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

  const { saveMut, commitCanvas, resetLocalFromServer } = useTopologySave({
    mapId,
    nodes,
    edges,
    graphQueryData: graphQuery.data,
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
  });

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
    commitCanvas,
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

  const selectedNodes = useMemo(() => nodes.filter((n) => n.selected), [nodes]);
  const selectedNode = selectedNodes[0] || null;
  const selectedNodeIds = useMemo(() => selectedNodes.map((n) => n.id), [selectedNodes]);

  const { alarmByNodeId } = useTopologyAlarmOverlay({
    enabled: canvasMode && showAlarmOverlay && !isWorldFlatCanvas,
    nodes,
  });
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

  const closeCtxMenu = useCallback(() => setCtxMenu(null), []);

  const { renameSelectedNode, removeNodeById, purgePlaceholderById } = useTopologyNodeOps({
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
  });


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

  const connectTest = useTopologyConnectTest({ closeCtxMenu, mapId, setNodes });
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


  const {
    isValidConnection,
    onConnect,
    patchSelectedEdgeStyle,
    removeSelected,
    removeEdgeById,
    staleEdgeIds,
    removeStaleEdges,
  } = useTopologyEdgeEditing({
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
  });

  useTopologyKeyboard({
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
  });

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

  const openWebcrtFor = (node: Node<NeNodeData> | null) =>
    openWebcrtForLink(node, { showOk, showError, t, closeCtxMenu });
  const openNeFor = (node: Node<NeNodeData> | null) =>
    openNeForLink(node, { showError, t, closeCtxMenu });
  const openAlarmsFor = (node: Node<NeNodeData> | null) =>
    openAlarmsForLink(node, { showError, t, closeCtxMenu });
  const openPortTrafficForEdge = (edge: Edge | null) =>
    openPortTrafficForEdgeLink(edge, nodes, { showError, t, closeCtxMenu });

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


  const { canvasHits, jumpToTreeSearchHit, findOnCanvas } = useTopologyFind({
    mapId,
    canvasMode,
    nodes,
    regions,
    pendingHighlightNe,
    graphQueryData: graphQuery.data,
    canvasQuery,
    setCanvasQuery,
    setSearchHitIds,
    findOpen,
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
  });

  const { exporting, runTopologyExport, titleText } = useTopologyExport({
    canvasMode,
    isWorldFlatCanvas,
    activeLeafName,
    activeRegion,
    activeView,
    graphQueryData: graphQuery.data,
    nodes,
    edges,
    displayEdges,
    labelColors,
    vendorColors,
    hideIp,
    hideVendor,
    showOk,
    showError,
    t,
  });

  const placeCtxMenu = (clientX: number, clientY: number, size?: { w?: number; h?: number }): { x: number; y: number } => {
    const pad = 8;
    const w = size?.w ?? 200;
    const h = size?.h ?? 240;
    const x = Math.min(clientX, window.innerWidth - w - pad);
    const y = Math.min(clientY, window.innerHeight - h - pad);
    return { x: Math.max(pad, x), y: Math.max(pad, y) };
  };

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

  const {
    paletteAdding,
    outsidePeersAdding,
    addPaletteItems,
    onPaletteDragStart,
    onCanvasDragOver,
    onCanvasDrop,
    addOutsidePeers,
    outsidePeerNameById,
    outsidePeersVisible,
    paletteVisible,
    paletteLoading,
  } = useTopologyPalette({
    canWriteTopology,
    mapId,
    activeViewName: activeView?.name,
    nodes,
    keyword,
    debouncedKeyword,
    addNeOpen,
    paletteSource,
    outsidePeers,
    outsidePeerQuery,
    dirtyRef,
    rfRef,
    commitCanvas,
    resetLocalFromServer,
    showOk,
    showError,
    t,
    setOutsidePeersOpen,
    setOutsidePeerSelectedIds,
  });

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

  const viewModel = {
    TopologyReactFlowView,
    activeRegion,
    activeView,
    addNeOpen,
    addOutsidePeers,
    addPaletteItems,
    alarmByNodeId,
    applyAlign,
    applyWorldMut,
    autoLayoutAfterDiscover,
    breadcrumbFolders,
    browseEntries,
    canRedo,
    canUndo,
    canWriteTopology,
    cancelDiscover,
    canvasBg,
    canvasGraphEmpty,
    canvasGraphError,
    canvasGraphErrorMsg,
    canvasGraphLoading,
    canvasGraphRefreshing,
    canvasHits,
    canvasMode,
    canvasQuery,
    canvasRef,
    canvasZoom,
    clearSelection,
    closeConnectDetail,
    closeCreateNeMode,
    closeCtxMenu,
    closeManagedForm,
    closePlaceholder,
    connectClickRef,
    connectDetailRow,
    connectTestSelectedFor,
    connectTestSubmitting,
    createNeModeOpen,
    createRegionMut,
    ctxMenu,
    ctxMenuRef,
    debouncedTreeNeQuery,
    deleteFolderMut,
    dirty,
    discoverAutoAddUnmatched,
    discoverError,
    discoverOneFor,
    discoverOpen,
    discoverPct,
    discoverProgress,
    discoverProjectNeighbors,
    discoverReport,
    discoverResults,
    discoverSelectedFor,
    discoverSummary,
    discovering,
    displayEdges,
    displayMenuRef,
    displayOpts,
    edgeDefaults,
    edgeFlow,
    edges,
    expandPhysicalLinks,
    expandedIds,
    exporting,
    findActiveIdx,
    findBoxRef,
    findOnCanvas,
    findOpen,
    fitCanvas,
    focusEdge,
    focusNode,
    fullscreen,
    goBackBrowse,
    goCanvas,
    goRegion,
    goRoot,
    goUmeWorldNav,
    graphQuery,
    graphTruncated,
    hexBrowseRegion,
    hideIp,
    hidePorts,
    hideVendor,
    hotBrowseKey,
    isValidConnection,
    isWorldFlatCanvas,
    jumpToTreeSearchHit,
    keyword,
    labelColors,
    liveSync,
    managedFormInitial,
    managedFormOpen,
    mapId,
    markDirty,
    newRootDialog,
    nodes,
    nodesRef,
    onCanvasDragOver,
    onCanvasDrop,
    onConnect,
    onConnectRetestSubmitted,
    onManagedFormSaved,
    onNodeClick,
    onNodeDoubleClick,
    onPaletteDragStart,
    onSidebarResizeDown,
    onTreeClearHotBrowseKey,
    onTreeDeleteFolder,
    onTreeDeleteMap,
    onTreeHotBrowseKey,
    onTreeToggleExpand,
    openAlarmsFor,
    openCreateNeAt,
    openNeFor,
    openPortTrafficForEdge,
    openWebcrtFor,
    outsidePeerNameById,
    outsidePeerQuery,
    outsidePeerSelectedIds,
    outsidePeers,
    outsidePeersAdding,
    outsidePeersOpen,
    outsidePeersVisible,
    paletteAdding,
    paletteLoading,
    paletteSelectedKeys,
    paletteSource,
    paletteVisible,
    patchSelectedEdgeStyle,
    pendingFitRef,
    pickCreateManaged,
    pickCreatePlaceholder,
    placeCtxMenu,
    placeholderBusy,
    placeholderDialog,
    popHistory,
    primaryViewOfFolder,
    promptNewRegion,
    promptNewSubRegion,
    promptRenameMap,
    promptRenameRegion,
    purgePlaceholderById,
    pushHistory,
    redo,
    regions,
    removeEdgeById,
    removeNodeById,
    removeSelected,
    removeStaleEdges,
    renameMapMut,
    renameRegionMut,
    renameSelectedNode,
    resetEdgeDefaults,
    rfRef,
    rootFolderId,
    runConnectTestForNode,
    runTopologyExport,
    saveMut,
    scaleBundleWidth,
    scheduleFlatViewportRefresh,
    searchHitIds,
    selectedConnectableCount,
    selectedDiscoverableCount,
    selectedEdge,
    selectedEdgeData,
    selectedEdgeId,
    selectedEdgeResolved,
    selectedEdgeSourceNode,
    selectedEdgeTargetNode,
    selectedFolderId,
    selectedNode,
    selectedNodeIds,
    selectedNodes,
    setAddNeOpen,
    setAutoLayoutAfterDiscover,
    setCanvasBg,
    setCanvasQuery,
    setCanvasZoom,
    setCtxMenu,
    setDiscoverAutoAddUnmatched,
    setDiscoverOpen,
    setDiscoverProjectNeighbors,
    setEdgeFlow,
    setEdges,
    setExpandPhysicalLinks,
    setFindActiveIdx,
    setFindOpen,
    setHideIp,
    setHidePorts,
    setHideVendor,
    setHotBrowseKey,
    setKeyword,
    setLabelColors,
    setLiveSync,
    setNewRootDialog,
    setNodes,
    setOutsidePeerQuery,
    setOutsidePeerSelectedIds,
    setOutsidePeersOpen,
    setPaletteSelectedKeys,
    setPaletteSource,
    setPlaceholderDialog,
    setScaleBundleWidth,
    setSelectedFolderId,
    setShowAlarmOverlay,
    setShowConnectStatus,
    setShowLevelColors,
    setShowPlaceholderBadge,
    setSidebarCollapsed,
    setSnapToGrid,
    setToolMode,
    setTreeNeQuery,
    setTreeSearchOpen,
    setVendorColors,
    setViewToolsToolbarSlot,
    setWorldFocusFolderId,
    showAlarmOverlay,
    showConnectStatus,
    showError,
    showLevelColors,
    showPlaceholderBadge,
    showWorldScatter,
    sidebarCollapsed,
    sidebarResizing,
    sidebarWidth,
    snapToGrid,
    staleEdgeIds,
    submitNewRoot,
    submitPlaceholder,
    t,
    titleText,
    toggleFullscreen,
    toolBehavior,
    toolMode,
    topoPageRef,
    treeFailed,
    treeLoading,
    treeNeQuery,
    treeNeSearchQuery,
    treeQuery,
    treeRoot,
    treeSearchOpen,
    treeSearchRef,
    truncateBannerText,
    truncateReason,
    umeWorldHexModules,
    undo,
    updateEdgeDefault,
    vendorColors,
    viewToolsToolbarSlot,
    worldDockMe,
    worldNeedsApply,
    worldScatter,
    worldTotal,
    worldTransformRef,
    worldViewId,
    worldVisualLod,
  };

  return <TopologyPageView v={viewModel} />;
}
