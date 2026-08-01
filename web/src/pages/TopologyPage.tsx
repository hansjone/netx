import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  ControlButton,
  MiniMap,
  addEdge,
  useEdgesState,
  useNodesState,
  Handle,
  Position,
  MarkerType,
  ConnectionMode,
  SelectionMode,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  createTopologyMap,
  deleteTopologyMap,
  discoverTopologyNeighborsStream,
  fetchManagedNe,
  fetchTopologyGraph,
  fetchTopologyMaps,
  fetchUmeNe,
  putTopologyGraph,
  updateTopologyMap,
} from "../services/api";
import { queryKeys } from "../constants/queryKeys";
import { HelpHint } from "../components/HelpHint";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { openOrFocusModule } from "../utils/moduleWindows";
import type {
  ManagedNeItem,
  TopologyDiscoverNeResult,
  TopologyDiscoverOut,
  TopologyEdgeItem,
  TopologyNodeItem,
  UmeNeItem,
} from "../types";
import { alignNodes, layoutGraph, type LayoutKind } from "./topology/layoutGraph";
import { behaviorForMode, toolModeFromKey, type ToolMode } from "./topology/toolMode";

const SNAP_GRID: [number, number] = [16, 16];
const UNDO_MAX = 40;
const PALETTE_DND = "application/x-netx-topo-palette";

function FullscreenIcon({ exit }: { exit?: boolean }) {
  if (exit) {
    return (
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <path
          fill="currentColor"
          d="M7 14H5v5h5v-2H7v-3zm12 0h-2v3h-3v2h5v-5zM7 5h3V3H5v5h2V5zm10 0v3h2V3h-5v2h3z"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M7 14H5v5h5v-2H7v-3zm0-9h3V3H5v5h2V5zm12 9h-2v3h-3v2h5v-5zm-2-9V3h-3v2h3v3h2V5h-2z"
      />
    </svg>
  );
}

type NeNodeData = {
  label: string;
  managed_ne_id: string;
  ume_ne_id: string;
  ne_ip: string;
  vendor: string;
  connect_status: string;
};

type HistorySnap = { nodes: Node<NeNodeData>[]; edges: Edge[] };

type PaletteSource = "managed" | "ume";

type PaletteItem = {
  key: string;
  source: PaletteSource;
  managed_ne_id: string;
  ume_ne_id: string;
  name: string;
  ip: string;
  vendor: string;
  meta: string;
  connect_status: string;
};

type TopoDisplayOpts = {
  hideIp: boolean;
  hideVendor: boolean;
  hidePorts: boolean;
  connectMode: boolean;
};

type CtxMenu =
  | { kind: "node"; id: string; x: number; y: number }
  | { kind: "edge"; id: string; x: number; y: number }
  | { kind: "selection"; x: number; y: number };

const TopoDisplayContext = createContext<TopoDisplayOpts>({
  hideIp: true,
  hideVendor: true,
  hidePorts: true,
  connectMode: false,
});

function newId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
}

function vendorTone(vendor: string): string {
  const v = String(vendor || "").trim().toLowerCase();
  if (v.includes("cisco")) return "cisco";
  if (v.includes("huawei")) return "huawei";
  if (v.includes("zte")) return "zte";
  if (v.includes("juniper")) return "juniper";
  if (v.includes("nokia") || v.includes("alcatel")) return "nokia";
  if (v.includes("ericsson")) return "ericsson";
  if (v.includes("h3c") || v.includes("comware")) return "h3c";
  return "other";
}

function discoverResultKind(r: TopologyDiscoverNeResult): "ok" | "warn" | "fail" {
  if (!r.ok) return "fail";
  const unmatchedCount = r.unmatched_count ?? (r.unmatched?.length || 0);
  if (r.parser_stub || unmatchedCount > 0) return "warn";
  return "ok";
}

function normalizeSearchText(s: string): string {
  return String(s || "")
    .toLowerCase()
    .replace(/[\s_\-./]+/g, "");
}

/** Case-insensitive substring + loose subsequence (e.g. r1 ? router-1). */
function fuzzyIncludes(haystack: string, needle: string): boolean {
  const h = normalizeSearchText(haystack);
  const n = normalizeSearchText(needle);
  if (!n) return true;
  if (!h) return false;
  if (h.includes(n)) return true;
  let i = 0;
  for (const ch of h) {
    if (ch === n[i]) i += 1;
    if (i >= n.length) return true;
  }
  return false;
}

function nodeMatchesQuery(n: Node<NeNodeData>, query: string): boolean {
  const tokens = String(query || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (!tokens.length) return false;
  const bits = [n.data.label, n.data.ne_ip, n.data.vendor, n.data.managed_ne_id, n.data.ume_ne_id];
  return tokens.every((tok) => bits.some((b) => fuzzyIncludes(String(b || ""), tok)));
}

/** Asset: /topo/ne-router.png (default blue = ZTE); other vendors tint via CSS. */
function RouterIcon() {
  return (
    <img
      className="topo-node__icon"
      src="/topo/ne-router.png"
      alt=""
      draggable={false}
      aria-hidden="true"
    />
  );
}

function NeNode({ data, selected }: NodeProps<Node<NeNodeData>>) {
  const { hideIp, hideVendor, connectMode } = useContext(TopoDisplayContext);
  const tone = vendorTone(data.vendor);
  const name = data.label || (!hideIp ? data.ne_ip : "") || "NE";
  const bits = [
    name,
    hideIp ? "" : data.ne_ip,
    hideVendor ? "" : data.vendor,
  ].filter((x, i, arr) => {
    const s = String(x || "").trim();
    if (!s) return false;
    return arr.findIndex((y) => String(y || "").trim() === s) === i;
  });
  return (
    <div
      className={`topo-node topo-node--${tone}${selected ? " is-selected" : ""}${
        connectMode ? " is-connect-mode" : ""
      }`}
    >
      <div className="topo-node__glyph">
        <Handle
          type="target"
          position={Position.Left}
          className="topo-node__handle topo-node__handle--center"
          isConnectable={connectMode}
        />
        <Handle
          type="source"
          position={Position.Right}
          className="topo-node__handle topo-node__handle--center"
          isConnectable={connectMode}
        />
        <RouterIcon />
      </div>
      <div className="topo-node__caption">{bits.join(" ? ")}</div>
    </div>
  );
}

const nodeTypes = { neNode: NeNode };

type EdgeStyleData = {
  source?: string;
  source_port?: string;
  target_port?: string;
  stroke_color?: string;
  stroke_width?: number;
  line_style?: string;
  discovered_at?: string | null;
};

type EdgeLineStyle = "solid" | "dashed" | "dotted";
type EdgeSourceKind = "manual" | "discovered" | "stale";

type EdgeDefaultStyle = {
  stroke_color: string;
  stroke_width: number;
  line_style: EdgeLineStyle;
};

type EdgeDefaults = Record<EdgeSourceKind, EdgeDefaultStyle>;

const EDGE_DEFAULTS_KEY = "netx.topology.edgeDefaults";

const BUILTIN_EDGE_DEFAULTS: EdgeDefaults = {
  manual: { stroke_color: "#64748b", stroke_width: 2, line_style: "solid" },
  discovered: { stroke_color: "#0ea5e9", stroke_width: 2, line_style: "dashed" },
  stale: { stroke_color: "#dc2626", stroke_width: 2, line_style: "dashed" },
};

function sourceKind(source: string): EdgeSourceKind {
  const src = (source || "manual").toLowerCase();
  if (src === "stale") return "stale";
  if (src === "lldp" || src === "cdp") return "discovered";
  return "manual";
}

function loadEdgeDefaults(): EdgeDefaults {
  try {
    const raw = localStorage.getItem(EDGE_DEFAULTS_KEY);
    if (!raw) return { ...BUILTIN_EDGE_DEFAULTS, manual: { ...BUILTIN_EDGE_DEFAULTS.manual }, discovered: { ...BUILTIN_EDGE_DEFAULTS.discovered }, stale: { ...BUILTIN_EDGE_DEFAULTS.stale } };
    const parsed = JSON.parse(raw) as Partial<EdgeDefaults>;
    const pick = (kind: EdgeSourceKind): EdgeDefaultStyle => {
      const base = BUILTIN_EDGE_DEFAULTS[kind];
      const cur = parsed?.[kind];
      const color = String(cur?.stroke_color || base.stroke_color).trim() || base.stroke_color;
      const width = Math.max(1, Math.min(12, Number(cur?.stroke_width || base.stroke_width) || base.stroke_width));
      const line = String(cur?.line_style || base.line_style).toLowerCase();
      const line_style: EdgeLineStyle =
        line === "dashed" || line === "dotted" || line === "solid" ? line : base.line_style;
      return { stroke_color: color, stroke_width: width, line_style };
    };
    return { manual: pick("manual"), discovered: pick("discovered"), stale: pick("stale") };
  } catch {
    return {
      manual: { ...BUILTIN_EDGE_DEFAULTS.manual },
      discovered: { ...BUILTIN_EDGE_DEFAULTS.discovered },
      stale: { ...BUILTIN_EDGE_DEFAULTS.stale },
    };
  }
}

function persistEdgeDefaults(next: EdgeDefaults) {
  try {
    localStorage.setItem(EDGE_DEFAULTS_KEY, JSON.stringify(next));
  } catch {
    /* ignore quota */
  }
}

function dashForLineStyle(lineStyle: string): string | undefined {
  const s = (lineStyle || "").trim().toLowerCase();
  if (s === "dashed") return "6 4";
  if (s === "dotted") return "2 2";
  return undefined;
}

function edgeStyleBySource(
  source: string,
  defaults: EdgeDefaults,
): { stroke: string; strokeDasharray?: string; strokeWidth: number } {
  const d = defaults[sourceKind(source)];
  return {
    stroke: d.stroke_color,
    strokeWidth: d.stroke_width,
    strokeDasharray: dashForLineStyle(d.line_style),
  };
}

function resolveEdgeStyle(
  data: EdgeStyleData | undefined,
  defaults: EdgeDefaults,
): { stroke: string; strokeDasharray?: string; strokeWidth: number } {
  const base = edgeStyleBySource(data?.source || "manual", defaults);
  const color = String(data?.stroke_color || "").trim();
  const width = Number(data?.stroke_width || 0);
  const line = String(data?.line_style || "").trim().toLowerCase();
  return {
    stroke: color || base.stroke,
    strokeWidth: width > 0 ? width : base.strokeWidth,
    strokeDasharray: line ? dashForLineStyle(line) : base.strokeDasharray,
  };
}

function edgeMarker(stroke: string) {
  return { type: MarkerType.ArrowClosed, width: 16, height: 16, color: stroke };
}

function withEdgeVisual(edge: Edge, defaults: EdgeDefaults): Edge {
  const data = (edge.data || {}) as EdgeStyleData;
  const style = resolveEdgeStyle(data, defaults);
  return { ...edge, style, markerEnd: edgeMarker(style.stroke) };
}

function graphToFlow(nodes: TopologyNodeItem[], edges: TopologyEdgeItem[], defaults: EdgeDefaults) {
  const rfNodes: Node<NeNodeData>[] = nodes.map((n) => ({
    id: n.id,
    type: "neNode",
    position: { x: n.x || 0, y: n.y || 0 },
    data: {
      label: n.label || n.ne_name || n.ne_ip || n.id,
      managed_ne_id: n.managed_ne_id || "",
      ume_ne_id: n.ume_ne_id || "",
      ne_ip: n.ne_ip || "",
      vendor: n.vendor || "",
      connect_status: n.connect_status || "",
    },
  }));
  const rfEdges: Edge[] = edges.map((e) => {
    const src = e.source || "manual";
    const label = [e.source_port, e.target_port].filter(Boolean).join(" ? ");
    const data: EdgeStyleData = {
      source: src,
      source_port: e.source_port || "",
      target_port: e.target_port || "",
      stroke_color: e.stroke_color || "",
      stroke_width: Number(e.stroke_width || 0),
      line_style: e.line_style || "",
      discovered_at: e.discovered_at ?? null,
    };
    return withEdgeVisual(
      {
        id: e.id,
        source: e.source_node_id,
        target: e.target_node_id,
        type: "straight",
        label: label || undefined,
        animated: false,
        data,
      },
      defaults,
    );
  });
  return { rfNodes, rfEdges };
}

function flowToGraphPayload(nodes: Node<NeNodeData>[], edges: Edge[]) {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      managed_ne_id: n.data.managed_ne_id || "",
      ume_ne_id: n.data.ume_ne_id || "",
      label: n.data.label || "",
      x: n.position.x,
      y: n.position.y,
    })),
    edges: edges.map((e) => {
      const data = (e.data || {}) as EdgeStyleData;
      return {
        id: e.id,
        source_node_id: e.source,
        target_node_id: e.target,
        source_port: String(data.source_port || ""),
        target_port: String(data.target_port || ""),
        source: String(data.source || "manual"),
        stroke_color: String(data.stroke_color || ""),
        stroke_width: Number(data.stroke_width || 0),
        line_style: String(data.line_style || ""),
        discovered_at: data.discovered_at ?? null,
      };
    }),
  };
}

export function TopologyPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [mapId, setMapId] = useState<string>("");
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [dirty, setDirty] = useState(false);
  const [historyTick, setHistoryTick] = useState(0);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const [hideIp, setHideIp] = useState(true);
  const [hideVendor, setHideVendor] = useState(true);
  const [hidePorts, setHidePorts] = useState(true);
  const [edgeFlow, setEdgeFlow] = useState(false);
  const [edgeDefaults, setEdgeDefaults] = useState<EdgeDefaults>(() => loadEdgeDefaults());
  const [toolMode, setToolMode] = useState<ToolMode>("select");
  const [snapToGrid, setSnapToGrid] = useState(true);
  const [autoLayoutAfterDiscover, setAutoLayoutAfterDiscover] = useState(true);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [hideAddedNes, setHideAddedNes] = useState(true);
  const [paletteSource, setPaletteSource] = useState<PaletteSource>("managed");
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoverReport, setDiscoverReport] = useState<TopologyDiscoverOut | null>(null);
  const [discoverLiveResults, setDiscoverLiveResults] = useState<TopologyDiscoverNeResult[]>([]);
  const [discoverProgress, setDiscoverProgress] = useState({
    index: 0,
    total: 0,
    neName: "",
    neIp: "",
    edgesAdded: 0,
    edgesUpdated: 0,
  });
  const [discoverError, setDiscoverError] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const [canvasQuery, setCanvasQuery] = useState("");
  const [searchHitIds, setSearchHitIds] = useState<string[]>([]);
  const [findOpen, setFindOpen] = useState(false);
  const [findActiveIdx, setFindActiveIdx] = useState(0);
  const [discoverFilter, setDiscoverFilter] = useState<"issues" | "fail" | "warn" | "ok" | "all">(
    "issues",
  );
  const [discoverDetail, setDiscoverDetail] = useState<TopologyDiscoverNeResult | null>(null);
  const [discoverListOpen, setDiscoverListOpen] = useState(false);
  const discoverAbortRef = useRef<AbortController | null>(null);
  const searchHitTimerRef = useRef<number | null>(null);
  const findBoxRef = useRef<HTMLDivElement | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NeNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const rfRef = useRef<ReactFlowInstance<Node<NeNodeData>, Edge> | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dirtyRef = useRef(false);
  const historyRef = useRef<HistorySnap[]>([]);
  const redoRef = useRef<HistorySnap[]>([]);
  const historyLockRef = useRef(false);
  const connectClickRef = useRef<string | null>(null);
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

  const selectMap = useCallback(
    (id: string) => {
      if (id === mapId) return;
      if (!confirmDiscardIfDirty()) return;
      setMapId(id);
    },
    [mapId, confirmDiscardIfDirty],
  );

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

  const toolBehavior = useMemo(() => behaviorForMode(toolMode), [toolMode]);
  const displayOpts = useMemo(
    () => ({ hideIp, hideVendor, hidePorts, connectMode: toolMode === "connect" }),
    [hideIp, hideVendor, hidePorts, toolMode],
  );
  const displayEdges = useMemo(
    () =>
      edges.map((e) => ({
        ...e,
        label: hidePorts ? undefined : e.label,
        animated: edgeFlow,
      })),
    [edges, hidePorts, edgeFlow],
  );

  const mapsQuery = useQuery({
    queryKey: queryKeys.topologyMaps,
    queryFn: fetchTopologyMaps,
  });

  const graphQuery = useQuery({
    queryKey: queryKeys.topologyGraph(mapId),
    queryFn: () => fetchTopologyGraph(mapId),
    enabled: Boolean(mapId),
  });

  const neQuery = useQuery({
    queryKey: [...queryKeys.managedNeAll, "topology-palette", debouncedKeyword],
    queryFn: () =>
      fetchManagedNe({
        keyword: debouncedKeyword,
        vendor: "",
        connectStatus: "",
        page: 1,
        pageSize: 100,
      }),
    enabled: paletteSource === "managed",
  });

  const umeQuery = useQuery({
    queryKey: ["umeInventoryNe", "topology-palette", debouncedKeyword],
    queryFn: () =>
      fetchUmeNe({
        keyword: debouncedKeyword,
        page: 1,
        pageSize: 100,
      }),
    enabled: paletteSource === "ume",
  });

  useEffect(() => {
    if (!mapId && mapsQuery.data?.items?.length) {
      setMapId(mapsQuery.data.items[0].id);
    }
  }, [mapId, mapsQuery.data]);

  useEffect(() => {
    if (!graphQuery.data) return;
    const { rfNodes, rfEdges } = graphToFlow(graphQuery.data.nodes, graphQuery.data.edges, edgeDefaults);
    historyLockRef.current = true;
    setNodes(rfNodes);
    setEdges(rfEdges);
    historyRef.current = [];
    redoRef.current = [];
    clearDirty();
    bumpHistory();
    historyLockRef.current = false;
    window.setTimeout(() => rfRef.current?.fitView({ padding: 0.2 }), 50);
    // edgeDefaults applied separately so changing defaults won't reload the whole graph.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only reload when graph payload changes
  }, [graphQuery.data, setNodes, setEdges]);

  useEffect(() => {
    setEdges((eds) => eds.map((e) => withEdgeVisual(e, edgeDefaults)));
  }, [edgeDefaults, setEdges]);

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
      manual: { ...BUILTIN_EDGE_DEFAULTS.manual },
      discovered: { ...BUILTIN_EDGE_DEFAULTS.discovered },
      stale: { ...BUILTIN_EDGE_DEFAULTS.stale },
    };
    persistEdgeDefaults(next);
    setEdgeDefaults(next);
  }, []);

  const pushHistory = useCallback(() => {
    if (historyLockRef.current) return;
    historyRef.current = [
      ...historyRef.current.slice(-(UNDO_MAX - 1)),
      {
        nodes: nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
        edges: edges.map((e) => ({ ...e })),
      },
    ];
    redoRef.current = [];
    bumpHistory();
  }, [nodes, edges, bumpHistory]);

  const undo = useCallback(() => {
    const prev = historyRef.current.pop();
    if (!prev) return;
    redoRef.current.push({
      nodes: nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
      edges: edges.map((e) => ({ ...e })),
    });
    historyLockRef.current = true;
    setNodes(prev.nodes);
    setEdges(prev.edges);
    markDirty();
    bumpHistory();
    historyLockRef.current = false;
  }, [nodes, edges, setNodes, setEdges, markDirty, bumpHistory]);

  const redo = useCallback(() => {
    const next = redoRef.current.pop();
    if (!next) return;
    historyRef.current.push({
      nodes: nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
      edges: edges.map((e) => ({ ...e })),
    });
    historyLockRef.current = true;
    setNodes(next.nodes);
    setEdges(next.edges);
    markDirty();
    bumpHistory();
    historyLockRef.current = false;
  }, [nodes, edges, setNodes, setEdges, markDirty, bumpHistory]);

  const applyLayout = useCallback(
    async (kind: LayoutKind, opts?: { onlySelected?: boolean; persist?: boolean }) => {
      const onlyIds =
        opts?.onlySelected
          ? new Set(nodes.filter((n) => n.selected).map((n) => n.id))
          : undefined;
      if (onlyIds && onlyIds.size === 0) {
        showError(t("topology.layoutNeedSelection"));
        return;
      }
      pushHistory();
      const next = layoutGraph(nodes, edges, kind, { onlyIds });
      setNodes(next);
      markDirty();
      window.setTimeout(() => rfRef.current?.fitView({ padding: 0.2 }), 40);
      if (opts?.persist && mapId) {
        try {
          const graph = await putTopologyGraph(mapId, flowToGraphPayload(next, edges));
          clearDirty();
          queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
        } catch (err) {
          showError(String(err));
        }
      }
    },
    [nodes, edges, setNodes, pushHistory, mapId, queryClient, showError, t, markDirty, clearDirty],
  );

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
  const renameMapMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      updateTopologyMap(id, { name }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      showOk(t("topology.renamed"));
    },
    onError: (err) => showError(String(err)),
  });

  const promptRenameMap = useCallback(
    (id: string, currentName: string) => {
      const next = window.prompt(t("topology.renamePrompt"), currentName);
      if (next == null) return;
      const name = next.trim();
      if (!name || name === currentName) return;
      renameMapMut.mutate({ id, name });
    },
    [renameMapMut, t],
  );

  const createMapMut = useMutation({
    mutationFn: () => createTopologyMap({ name: t("topology.newMapName") }),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      clearDirty();
      setMapId(row.id);
      showOk(t("topology.newMap"));
      // Prompt rename right after create ? default name is a placeholder.
      window.setTimeout(() => promptRenameMap(row.id, row.name), 0);
    },
    onError: (err) => showError(String(err)),
  });

  const deleteMapMut = useMutation({
    mutationFn: (id: string) => deleteTopologyMap(id),
    onSuccess: async (_out, id) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      if (mapId === id) {
        clearDirty();
        setMapId("");
        setNodes([]);
        setEdges([]);
      }
    },
    onError: (err) => showError(String(err)),
  });

  const saveMut = useMutation({
    mutationFn: () => putTopologyGraph(mapId, flowToGraphPayload(nodes, edges)),
    onSuccess: async (graph) => {
      clearDirty();
      queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      showOk(t("topology.saved"));
    },
    onError: (err) => showError(String(err)),
  });

  const runDiscover = useCallback(
    async (neIds?: string[]) => {
      if (!mapId || discovering) return;
      const filterIds = (neIds || []).map((x) => String(x || "").trim()).filter(Boolean);
      discoverAbortRef.current?.abort();
      const ac = new AbortController();
      discoverAbortRef.current = ac;
      setDiscoverOpen(true);
      setDiscovering(true);
      setDiscoverReport(null);
      setDiscoverLiveResults([]);
      setDiscoverError("");
      setDiscoverFilter("issues");
      setDiscoverDetail(null);
      setDiscoverListOpen(false);
      const scannable = nodes.filter((n) => Boolean(n.data.managed_ne_id || n.data.ume_ne_id));
      const scoped = filterIds.length
        ? scannable.filter(
            (n) =>
              filterIds.includes(String(n.data.managed_ne_id || "")) ||
              filterIds.includes(String(n.data.ume_ne_id || "")),
          )
        : scannable;
      setDiscoverProgress({
        index: 0,
        total: scoped.length,
        neName: "",
        neIp: "",
        edgesAdded: 0,
        edgesUpdated: 0,
      });
      try {
        if (dirtyRef.current) {
          await putTopologyGraph(mapId, flowToGraphPayload(nodes, edges));
          clearDirty();
        }
        const out = await discoverTopologyNeighborsStream(
          mapId,
          {
            protocol: "auto",
            ...(filterIds.length ? { ne_ids: filterIds } : {}),
          },
          {
            onStart: (ev) => {
              setDiscoverProgress((p) => ({ ...p, total: ev.total, index: 0 }));
            },
            onNeStart: (ev) => {
              setDiscoverProgress((p) => ({
                ...p,
                index: ev.index,
                total: ev.total,
                neName: ev.ne_name || ev.ne_ip || ev.ne_id,
                neIp: ev.ne_ip,
              }));
            },
            onNeResult: (ev) => {
              if (ev.result) {
                setDiscoverLiveResults((prev) => [...prev, ev.result]);
              }
              setDiscoverProgress((p) => ({
                ...p,
                index: ev.index,
                total: ev.total,
                edgesAdded: ev.edges_added,
                edgesUpdated: ev.edges_updated,
              }));
            },
          },
          ac.signal,
        );
        if (out.graph) {
          queryClient.setQueryData(queryKeys.topologyGraph(mapId), out.graph);
          let { rfNodes, rfEdges } = graphToFlow(out.graph.nodes, out.graph.edges, edgeDefaults);
          if (autoLayoutAfterDiscover && rfNodes.length > 1) {
            rfNodes = layoutGraph(rfNodes, rfEdges, "hierarchical-tb");
            try {
              const graph = await putTopologyGraph(mapId, flowToGraphPayload(rfNodes, rfEdges));
              queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
              clearDirty();
            } catch {
              markDirty();
            }
          } else {
            clearDirty();
          }
          historyLockRef.current = true;
          setNodes(rfNodes);
          setEdges(rfEdges);
          historyLockRef.current = false;
          window.setTimeout(() => rfRef.current?.fitView({ padding: 0.2 }), 50);
        }
        setDiscoverReport(out);
        await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
        showOk(
          t("topology.discovered")
            .replace("{{added}}", String(out.edges_added))
            .replace("{{updated}}", String(out.edges_updated))
            .replace("{{stale}}", String(out.edges_stale || 0)),
        );
      } catch (err) {
        if (ac.signal.aborted) return;
        setDiscoverError(String(err));
        showError(t("topology.discoverFail").replace("{{detail}}", String(err)));
      } finally {
        if (discoverAbortRef.current === ac) discoverAbortRef.current = null;
        setDiscovering(false);
      }
    },
    [mapId, discovering, nodes, edges, queryClient, setNodes, setEdges, showOk, showError, t, autoLayoutAfterDiscover, edgeDefaults],
  );

  const discoverResults = discoverReport?.results?.length
    ? discoverReport.results
    : discoverLiveResults;
  const discoverCounts = useMemo(() => {
    let ok = 0;
    let warn = 0;
    let fail = 0;
    for (const r of discoverResults) {
      const k = discoverResultKind(r);
      if (k === "fail") fail += 1;
      else if (k === "warn") warn += 1;
      else ok += 1;
    }
    return { ok, warn, fail, issues: warn + fail };
  }, [discoverResults]);
  const discoverSummary = discoverReport
    ? {
        scanned: discoverReport.scanned,
        added: discoverReport.edges_added,
        updated: discoverReport.edges_updated,
        stale: discoverReport.edges_stale || 0,
        failed: discoverCounts.fail,
      }
    : {
        scanned: discoverLiveResults.length,
        added: discoverProgress.edgesAdded,
        updated: discoverProgress.edgesUpdated,
        stale: 0,
        failed: discoverCounts.fail,
      };
  const discoverFiltered = useMemo(() => {
    return discoverResults.filter((r) => {
      const k = discoverResultKind(r);
      if (discoverFilter === "all") return true;
      if (discoverFilter === "issues") return k === "fail" || k === "warn";
      return k === discoverFilter;
    });
  }, [discoverResults, discoverFilter]);
  const DISCOVER_LIST_CAP = 200;
  const discoverVisible = discoverFiltered.slice(0, DISCOVER_LIST_CAP);
  const discoverPct =
    discovering && discoverProgress.total > 0
      ? Math.min(100, Math.round((discoverProgress.index / discoverProgress.total) * 100))
      : discoverReport
        ? 100
        : 0;

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const source = String(connection.source || "");
      const target = String(connection.target || "");
      if (!source || !target || source === target) return false;
      return !edges.some((e) => e.source === source && e.target === target);
    },
    [edges],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!isValidConnection(connection)) return;
      pushHistory();
      markDirty();
      connectClickRef.current = null;
      const data: EdgeStyleData = {
        source: "manual",
        source_port: "",
        target_port: "",
        stroke_color: "",
        stroke_width: 0,
        line_style: "",
        discovered_at: null,
      };
      setEdges((eds) =>
        addEdge(
          withEdgeVisual(
            {
              ...connection,
              id: newId(),
              type: "straight",
              data,
            },
            edgeDefaults,
          ),
          eds,
        ),
      );
    },
    [setEdges, pushHistory, edgeDefaults, markDirty, isValidConnection],
  );

  const addNodeAt = useCallback(
    (item: PaletteItem, position: { x: number; y: number }) => {
      if (!mapId) {
        showError(t("topology.selectMap"));
        return;
      }
      if (item.source === "managed") {
        if (nodes.some((n) => n.data.managed_ne_id === item.managed_ne_id)) return;
        const ne = (neQuery.data?.items || []).find((x) => x.id === item.managed_ne_id);
        if (!ne) return;
        pushHistory();
        markDirty();
        setNodes((prev) => [
          ...prev,
          {
            id: newId(),
            type: "neNode",
            position,
            data: {
              label: ne.name || ne.ip_address,
              managed_ne_id: ne.id,
              ume_ne_id: "",
              ne_ip: ne.ip_address,
              vendor: ne.vendor,
              connect_status: ne.connect_status,
            },
          },
        ]);
        return;
      }
      if (nodes.some((n) => n.data.ume_ne_id === item.ume_ne_id)) return;
      const ne = (umeQuery.data?.items || []).find((x) => x.ne_id === item.ume_ne_id);
      if (!ne) return;
      const name = (ne.host_name || ne.ne_name || ne.user_label || ne.ip_address || ne.ne_id).trim();
      pushHistory();
      markDirty();
      setNodes((prev) => [
        ...prev,
        {
          id: newId(),
          type: "neNode",
          position,
          data: {
            label: name,
            managed_ne_id: "",
            ume_ne_id: ne.ne_id,
            ne_ip: ne.ip_address || "",
            vendor: "ZTE",
            connect_status: ne.connection_status || "",
          },
        },
      ]);
    },
    [mapId, nodes, neQuery.data, umeQuery.data, setNodes, pushHistory, showError, t],
  );

  const addManagedNeToCanvas = (ne: ManagedNeItem) => {
    addNodeAt(
      {
        key: `managed:${ne.id}`,
        source: "managed",
        managed_ne_id: ne.id,
        ume_ne_id: "",
        name: ne.name || ne.ip_address,
        ip: ne.ip_address,
        vendor: ne.vendor,
        meta: "",
        connect_status: ne.connect_status,
      },
      { x: 80 + nodes.length * 24, y: 80 + nodes.length * 24 },
    );
  };

  const addUmeNeToCanvas = (ne: UmeNeItem) => {
    const name = (ne.host_name || ne.ne_name || ne.user_label || ne.ip_address || ne.ne_id).trim();
    addNodeAt(
      {
        key: `ume:${ne.ne_id}`,
        source: "ume",
        managed_ne_id: "",
        ume_ne_id: ne.ne_id,
        name,
        ip: ne.ip_address || "",
        vendor: "ZTE",
        meta: "",
        connect_status: ne.connection_status || "",
      },
      { x: 80 + nodes.length * 24, y: 80 + nodes.length * 24 },
    );
  };

  const addPaletteItem = (item: PaletteItem) => {
    if (item.source === "managed") {
      const ne = (neQuery.data?.items || []).find((x) => x.id === item.managed_ne_id);
      if (ne) addManagedNeToCanvas(ne);
      return;
    }
    const ne = (umeQuery.data?.items || []).find((x) => x.ne_id === item.ume_ne_id);
    if (ne) addUmeNeToCanvas(ne);
  };

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
  const selectedEdge = useMemo(
    () => (selectedEdgeId ? edges.find((e) => e.id === selectedEdgeId) || null : null),
    [edges, selectedEdgeId],
  );
  const selectedEdgeData = (selectedEdge?.data || {}) as EdgeStyleData;
  const selectedEdgeResolved = selectedEdge ? resolveEdgeStyle(selectedEdgeData, edgeDefaults) : null;

  const patchSelectedEdgeStyle = useCallback(
    (
      patch: Partial<
        Pick<EdgeStyleData, "stroke_color" | "stroke_width" | "line_style" | "source_port" | "target_port">
      >,
      opts?: { skipHistory?: boolean },
    ) => {
      if (!selectedEdgeId) return;
      if (!opts?.skipHistory) pushHistory();
      markDirty();
      setEdges((eds) =>
        eds.map((e) => {
          if (e.id !== selectedEdgeId) return e;
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
          const portLabel = [data.source_port, data.target_port].filter(Boolean).join(" ? ");
          return withEdgeVisual({ ...e, data, label: portLabel || undefined }, edgeDefaults);
        }),
      );
    },
    [selectedEdgeId, setEdges, pushHistory, edgeDefaults, markDirty],
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

  const staleEdgeCount = useMemo(
    () =>
      edges.filter((e) => String((e.data as { source?: string } | undefined)?.source || "") === "stale")
        .length,
    [edges],
  );

  const closeCtxMenu = useCallback(() => setCtxMenu(null), []);

  useEffect(() => {
    const syncFs = () => {
      const el = canvasRef.current;
      const active = Boolean(el && document.fullscreenElement === el);
      setFullscreen(active);
      if (active) {
        window.setTimeout(() => rfRef.current?.fitView({ padding: 0.15 }), 80);
      }
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

  const removeSelected = useCallback(() => {
    const nodeIds = new Set(nodes.filter((n) => n.selected).map((n) => n.id));
    const edgeIds = new Set(edges.filter((e) => e.selected).map((e) => e.id));
    if (nodeIds.size === 0 && edgeIds.size === 0) return;
    pushHistory();
    markDirty();
    setNodes((ns) => ns.filter((n) => !nodeIds.has(n.id)));
    setEdges((es) =>
      es.filter((e) => !edgeIds.has(e.id) && !nodeIds.has(e.source) && !nodeIds.has(e.target)),
    );
    setSelectedEdgeId(null);
    closeCtxMenu();
  }, [nodes, edges, setNodes, setEdges, pushHistory, closeCtxMenu]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || (e.target as HTMLElement)?.isContentEditable) {
        return;
      }
      if (e.key === "Escape") {
        closeCtxMenu();
        clearSelection();
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
        if (mapId && dirtyRef.current && !saveMut.isPending) saveMut.mutate();
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
        if (nodes.some((n) => n.selected) || edges.some((ed) => ed.selected)) {
          e.preventDefault();
          removeSelected();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeCtxMenu, clearSelection, undo, redo, selectAllNodes, removeSelected, nodes, edges, mapId, saveMut]);

  useEffect(() => {
    if (!ctxMenu) return;
    const onScroll = () => closeCtxMenu();
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

  const removeNodeById = (nodeId: string) => {
    pushHistory();
    markDirty();
    setNodes((ns) => ns.filter((n) => n.id !== nodeId));
    setEdges((es) => es.filter((e) => e.source !== nodeId && e.target !== nodeId));
    closeCtxMenu();
  };

  const removeEdgeById = (edgeId: string) => {
    pushHistory();
    markDirty();
    setEdges((es) => es.filter((e) => e.id !== edgeId));
    setSelectedEdgeId((cur) => (cur === edgeId ? null : cur));
    closeCtxMenu();
  };

  const removeStaleEdges = () => {
    const n = staleEdgeCount;
    if (n <= 0) return;
    pushHistory();
    markDirty();
    setEdges((es) =>
      es.filter((e) => String((e.data as { source?: string } | undefined)?.source || "") !== "stale"),
    );
    setSelectedEdgeId(null);
    showOk(t("topology.staleRemoved").replace("{{count}}", String(n)));
  };

  const openWebcrtFor = (node: Node<NeNodeData> | null) => {
    closeCtxMenu();
    const managedId = node?.data.managed_ne_id;
    const umeId = node?.data.ume_ne_id;
    if (managedId) {
      openOrFocusModule({
        moduleId: "webcrt",
        path: `/webcrt?ne_id=${encodeURIComponent(managedId)}`,
      });
      return;
    }
    if (umeId) {
      openOrFocusModule({
        moduleId: "webcrt",
        path: `/webcrt?ne_id=${encodeURIComponent(umeId)}&source=ume`,
      });
      return;
    }
    showError(t("topology.noNeLink"));
  };

  const openNeFor = (node: Node<NeNodeData> | null) => {
    closeCtxMenu();
    const managedId = node?.data.managed_ne_id;
    const umeId = node?.data.ume_ne_id;
    if (managedId) {
      openOrFocusModule({
        moduleId: "ne",
        path: "/ne",
      });
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
    const id = String(node?.data.managed_ne_id || node?.data.ume_ne_id || "").trim();
    if (!id) {
      showError(t("topology.discoverOneNeedNe"));
      return;
    }
    void runDiscover([id]);
  };

  const placeCtxMenu = (clientX: number, clientY: number, size?: { w?: number; h?: number }): { x: number; y: number } => {
    const pad = 8;
    const w = size?.w ?? 200;
    const h = size?.h ?? 240;
    const x = Math.min(clientX, window.innerWidth - w - pad);
    const y = Math.min(clientY, window.innerHeight - h - pad);
    return { x: Math.max(pad, x), y: Math.max(pad, y) };
  };

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
    [setNodes, setEdges],
  );

  const locateNode = useCallback(
    (nodeId: string) => {
      focusNode(nodeId, false);
      setSearchHitIds([nodeId]);
      if (searchHitTimerRef.current) window.clearTimeout(searchHitTimerRef.current);
      window.setTimeout(() => {
        rfRef.current?.fitView({ nodes: [{ id: nodeId }], padding: 0.45, duration: 280 });
      }, 30);
      searchHitTimerRef.current = window.setTimeout(() => {
        setSearchHitIds((cur) => (cur.length === 1 && cur[0] === nodeId ? [] : cur));
        searchHitTimerRef.current = null;
      }, 2200);
    },
    [focusNode],
  );

  const canvasHits = useMemo(() => {
    const q = canvasQuery.trim();
    if (!q) return [];
    return nodes.filter((n) => nodeMatchesQuery(n, q));
  }, [canvasQuery, nodes]);

  useEffect(() => {
    const q = canvasQuery.trim();
    if (!q) {
      setSearchHitIds([]);
      setFindActiveIdx(0);
      setFindOpen(false);
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
      setFindOpen(false);
      locateNode(id);
    },
    [canvasQuery, canvasHits, findActiveIdx, locateNode, showError, t],
  );

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!findBoxRef.current) return;
      if (!findBoxRef.current.contains(e.target as HTMLElement)) setFindOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  const locatePaletteItem = useCallback(
    (item: PaletteItem) => {
      const node = nodes.find((n) =>
        item.source === "managed"
          ? n.data.managed_ne_id === item.managed_ne_id
          : n.data.ume_ne_id === item.ume_ne_id,
      );
      if (!node) {
        showError(t("topology.findNoMatch"));
        return;
      }
      locateNode(node.id);
    },
    [nodes, locateNode, showError, t],
  );

  const focusEdge = useCallback(
    (edgeId: string) => {
      setSelectedEdgeId(edgeId);
      setNodes((ns) => ns.map((n) => ({ ...n, selected: false })));
      setEdges((es) => es.map((e) => ({ ...e, selected: e.id === edgeId })));
    },
    [setNodes, setEdges],
  );

  const onNodeClick = useCallback(
    (e: React.MouseEvent, node: Node<NeNodeData>) => {
      setCtxMenu(null);
      if (toolMode === "connect") {
        const prev = connectClickRef.current;
        if (!prev) {
          connectClickRef.current = node.id;
          focusNode(node.id, false);
          return;
        }
        if (prev !== node.id) {
          onConnect({ source: prev, target: node.id, sourceHandle: null, targetHandle: null });
        }
        connectClickRef.current = null;
        return;
      }
      focusNode(node.id, e.shiftKey || e.metaKey || e.ctrlKey);
    },
    [toolMode, focusNode, onConnect],
  );

  const maps = mapsQuery.data?.items || [];
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
          meta: `${ne.ip_address || "-"} · ${ne.ne_type || "UME"}`,
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
      meta: `${ne.ip_address} · ${ne.vendor}`,
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
    if (!hideAddedNes) return list;
    return list.filter((item) =>
      item.source === "managed"
        ? !onCanvasManagedIds.has(item.managed_ne_id)
        : !onCanvasUmeIds.has(item.ume_ne_id),
    );
  }, [palette, hideAddedNes, onCanvasManagedIds, onCanvasUmeIds, keyword]);
  const paletteLoading =
    (paletteSource === "managed" && neQuery.isLoading) ||
    (paletteSource === "ume" && umeQuery.isLoading);

  return (
    <div className={`topo-page${sidebarCollapsed ? " is-sidebar-collapsed" : ""}`}>
      <aside className="topo-sidebar" aria-label={t("topology.maps")}>
        {sidebarCollapsed ? (
          <button
            type="button"
            className="topo-sidebar__rail"
            title={t("topology.expandSidebar")}
            aria-label={t("topology.expandSidebar")}
            onClick={() => setSidebarCollapsed(false)}
          >
            <span className="topo-sidebar__rail-icon" aria-hidden="true">
              ?
            </span>
            <span className="topo-sidebar__rail-label">{t("topology.maps")}</span>
          </button>
        ) : (
          <>
            <div className="topo-sidebar__section">
              <div className="topo-sidebar__head">
                <strong>{t("topology.maps")}</strong>
                <div className="topo-sidebar__head-actions">
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => {
                      if (!confirmDiscardIfDirty()) return;
                      createMapMut.mutate();
                    }}
                    disabled={createMapMut.isPending}
                  >
                    {t("topology.newMap")}
                  </button>
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    title={t("topology.collapseSidebar")}
                    aria-label={t("topology.collapseSidebar")}
                    onClick={() => setSidebarCollapsed(true)}
                  >
                    ?
                  </button>
                </div>
              </div>
              {maps.length === 0 ? (
                <p className="panel__hint">{t("topology.emptyMaps")}</p>
              ) : (
                <ul className="topo-map-list">
                  {maps.map((m) => (
                    <li key={m.id} className={mapId === m.id ? "is-active" : ""}>
                      <div className="topo-map-list__row">
                        <button
                          type="button"
                          className="topo-map-list__item"
                          onClick={() => selectMap(m.id)}
                          onDoubleClick={() => promptRenameMap(m.id, m.name)}
                          title={t("topology.renameHint")}
                        >
                          <span className="topo-map-list__name">
                            {m.name}
                            {mapId === m.id && dirty ? " *" : ""}
                          </span>
                          <span className="topo-map-list__meta">
                            {m.node_count}N / {m.edge_count}E
                          </span>
                        </button>
                        <div className="topo-map-list__actions">
                          <button
                            type="button"
                            className="topo-map-list__icon"
                            title={t("topology.rename")}
                            disabled={renameMapMut.isPending}
                            onClick={() => promptRenameMap(m.id, m.name)}
                          >
                            ?
                          </button>
                          <button
                            type="button"
                            className="topo-map-list__icon"
                            title={t("topology.deleteMap")}
                            onClick={() => {
                              const msg = t("topology.deleteMapConfirm").replace("{{name}}", m.name);
                              if (window.confirm(msg)) deleteMapMut.mutate(m.id);
                            }}
                          >
                            ?
                          </button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="topo-sidebar__section topo-sidebar__section--grow">
              <div className="topo-sidebar__head">
                <strong>{t("topology.palette")}</strong>
                <label className="topo-display-toggles__item">
                  <input
                    type="checkbox"
                    checked={hideAddedNes}
                    onChange={(e) => setHideAddedNes(e.target.checked)}
                  />
                  {t("topology.hideAdded")}
                </label>
              </div>
              <div className="topo-palette-source" role="tablist" aria-label={t("topology.paletteSource")}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={paletteSource === "managed"}
                  className={`topo-palette-source__btn${paletteSource === "managed" ? " is-active" : ""}`}
                  onClick={() => setPaletteSource("managed")}
                >
                  {t("topology.paletteManaged")}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={paletteSource === "ume"}
                  className={`topo-palette-source__btn${paletteSource === "ume" ? " is-active" : ""}`}
                  onClick={() => setPaletteSource("ume")}
                >
                  {t("topology.paletteUme")}
                </button>
              </div>
                  <p className="panel__hint">{t("topology.paletteHint")}</p>
              <input
                className="input"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder={t("topology.filterPh")}
              />
              <ul className="topo-palette">
                {paletteLoading ? (
                  <li className="topo-palette__empty">
                    <span className="panel__hint">{t("topology.paletteLoading")}</span>
                  </li>
                ) : paletteVisible.length === 0 ? (
                  <li className="topo-palette__empty">
                    <span className="panel__hint">
                      {palette.length === 0
                        ? t("topology.paletteEmpty")
                        : t("topology.paletteHiddenAll")}
                    </span>
                  </li>
                ) : (
                  paletteVisible.map((item) => {
                    const onCanvas =
                      item.source === "managed"
                        ? onCanvasManagedIds.has(item.managed_ne_id)
                        : onCanvasUmeIds.has(item.ume_ne_id);
                    return (
                      <li key={item.key} className={onCanvas ? "is-on-canvas" : ""}>
                        <div className="topo-palette__row">
                          <button
                            type="button"
                            className="topo-palette__item"
                            disabled={!mapId}
                            draggable={Boolean(mapId) && !onCanvas}
                            onDragStart={(e) => {
                              if (onCanvas) {
                                e.preventDefault();
                                return;
                              }
                              onPaletteDragStart(e, item);
                            }}
                            onClick={() => {
                              if (onCanvas) locatePaletteItem(item);
                              else addPaletteItem(item);
                            }}
                            title={`${item.name}\n${item.ip}${onCanvas ? `\n${t("topology.locateOnCanvas")}` : ""}`}
                          >
                            <span className="topo-palette__name">{item.name}</span>
                            <span className="topo-palette__meta">
                              {item.meta}
                              {onCanvas ? " ?" : ""}
                            </span>
                          </button>
                        </div>
                      </li>
                    );
                  })
                )}
              </ul>
            </div>
          </>
        )}
      </aside>

      <main className="topo-main">
        <div className="topo-toolbar">
          <div className="topo-toolbar__row">
            <div className="topo-toolbar__title">
              <strong>{maps.find((m) => m.id === mapId)?.name || t("topology.selectMap")}</strong>
              {selectedNodes.length > 0 || selectedEdgeId ? (
                <span className="topo-toolbar__meta">
                  {selectedNodes.length > 0
                    ? t("topology.selectedCount").replace("{{count}}", String(selectedNodes.length))
                    : t("topology.selectedEdge")}
                </span>
              ) : null}
              <HelpHint text={t("topology.canvasHint")} ariaLabel={t("common.help")} />
            </div>
            <div className="topo-toolbar__actions">
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!mapId || !canUndo}
                onClick={undo}
                title="Ctrl+Z"
              >
                {t("topology.undo")}
              </button>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!mapId || !canRedo}
                onClick={redo}
                title="Ctrl+Y"
              >
                {t("topology.redo")}
              </button>
              <button
                type="button"
                className={`btn btn--sm${dirty ? "" : " btn--ghost"}`}
                disabled={!mapId || saveMut.isPending || !dirty}
                onClick={() => saveMut.mutate()}
                title="Ctrl+S"
              >
                {saveMut.isPending
                  ? t("topology.saving")
                  : dirty
                    ? t("topology.saveDirty")
                    : t("topology.save")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={!mapId || discovering}
                onClick={() => void runDiscover()}
              >
                {discovering ? t("topology.discovering") : t("topology.discover")}
              </button>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!mapId || discovering || staleEdgeCount <= 0}
                onClick={removeStaleEdges}
                title={t("topology.removeStaleHint")}
              >
                {t("topology.removeStale").replace("{{count}}", String(staleEdgeCount))}
              </button>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!mapId}
                onClick={() => rfRef.current?.fitView({ padding: 0.2 })}
              >
                {t("topology.fit")}
              </button>
            </div>
          </div>
          <div className="topo-toolbar__row topo-toolbar__row--tools">
            <div className="topo-tools" role="toolbar" aria-label={t("topology.toolModes")}>
              {(
                [
                  ["select", t("topology.toolSelect"), "V"],
                  ["pan", t("topology.toolPan"), "H"],
                  ["drag", t("topology.toolDrag"), "A"],
                  ["connect", t("topology.toolConnect"), "C"],
                ] as const
              ).map(([mode, label, key]) => (
                <button
                  key={mode}
                  type="button"
                  className={`topo-tools__btn${toolMode === mode ? " is-active" : ""}`}
                  title={`${label} (${key})`}
                  aria-pressed={toolMode === mode}
                  onClick={() => {
                    setToolMode(mode);
                    connectClickRef.current = null;
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div
              className="topo-toolbar__group topo-toolbar__group--find"
              aria-label={t("topology.findNode")}
              ref={findBoxRef}
            >
              <input
                className="topo-toolbar__find"
                value={canvasQuery}
                onChange={(e) => setCanvasQuery(e.target.value)}
                onFocus={() => {
                  if (canvasQuery.trim()) setFindOpen(true);
                }}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    if (!canvasHits.length) return;
                    setFindOpen(true);
                    setFindActiveIdx((i) => (i + 1) % canvasHits.length);
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    if (!canvasHits.length) return;
                    setFindOpen(true);
                    setFindActiveIdx((i) => (i - 1 + canvasHits.length) % canvasHits.length);
                    return;
                  }
                  if (e.key === "Escape") {
                    setFindOpen(false);
                    return;
                  }
                  if (e.key === "Enter") {
                    e.preventDefault();
                    findOnCanvas();
                  }
                }}
                placeholder={t("topology.findNodePh")}
                disabled={!mapId || nodes.length === 0}
                aria-label={t("topology.findNode")}
                aria-autocomplete="list"
                aria-expanded={findOpen}
              />
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!mapId || nodes.length === 0 || !canvasQuery.trim()}
                onClick={() => findOnCanvas()}
              >
                {t("topology.findNode")}
              </button>
              {findOpen && canvasQuery.trim() ? (
                <div className="topo-find-suggest" role="listbox">
                  {canvasHits.length === 0 ? (
                    <div className="topo-find-suggest__empty">{t("topology.findNoMatch")}</div>
                  ) : (
                    canvasHits.slice(0, 12).map((n, idx) => (
                      <button
                        key={n.id}
                        type="button"
                        role="option"
                        aria-selected={idx === findActiveIdx}
                        className={`topo-find-suggest__item${idx === findActiveIdx ? " is-active" : ""}`}
                        onMouseEnter={() => setFindActiveIdx(idx)}
                        onClick={() => findOnCanvas(n.id)}
                      >
                        <span className="topo-find-suggest__name">{n.data.label || n.id}</span>
                        <span className="topo-find-suggest__meta">
                          {[n.data.ne_ip, n.data.vendor].filter(Boolean).join(" ? ")}
                        </span>
                      </button>
                    ))
                  )}
                  {canvasHits.length > 12 ? (
                    <div className="topo-find-suggest__more">
                      {t("topology.findMore").replace("{{count}}", String(canvasHits.length - 12))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className="topo-toolbar__group" aria-label={t("topology.layout")}>
              <select
                className="topo-toolbar__select"
                aria-label={t("topology.layout")}
                disabled={!mapId || nodes.length === 0}
                defaultValue=""
                onChange={(e) => {
                  const v = e.target.value as LayoutKind | "selected-tb" | "";
                  e.target.value = "";
                  if (!v) return;
                  if (v === "selected-tb") void applyLayout("hierarchical-tb", { onlySelected: true });
                  else void applyLayout(v);
                }}
              >
                <option value="" disabled>
                  {t("topology.layout")}
                </option>
                <option value="hierarchical-tb">{t("topology.layoutHierarchicalTb")}</option>
                <option value="hierarchical-lr">{t("topology.layoutHierarchicalLr")}</option>
                <option value="force">{t("topology.layoutForce")}</option>
                <option value="grid">{t("topology.layoutGrid")}</option>
                <option value="radial">{t("topology.layoutRadial")}</option>
                <option value="selected-tb">{t("topology.layoutSelected")}</option>
              </select>
              <select
                className="topo-toolbar__select"
                aria-label={t("topology.align")}
                disabled={selectedNodes.length < 2}
                defaultValue=""
                onChange={(e) => {
                  const v = e.target.value as Parameters<typeof alignNodes>[2] | "";
                  e.target.value = "";
                  if (v) applyAlign(v);
                }}
              >
                <option value="" disabled>
                  {t("topology.align")}
                </option>
                <option value="left">{t("topology.alignLeft")}</option>
                <option value="right">{t("topology.alignRight")}</option>
                <option value="top">{t("topology.alignTop")}</option>
                <option value="bottom">{t("topology.alignBottom")}</option>
                <option value="h-center">{t("topology.alignHCenter")}</option>
                <option value="v-center">{t("topology.alignVCenter")}</option>
                <option value="h-distribute">{t("topology.alignHDistribute")}</option>
                <option value="v-distribute">{t("topology.alignVDistribute")}</option>
              </select>
            </div>
            <details className="topo-toolbar__display">
              <summary>{t("topology.display")}</summary>
              <div className="topo-display-toggles" role="group" aria-label={t("topology.display")}>
                <label className="topo-display-toggles__item">
                  <input type="checkbox" checked={hideIp} onChange={(e) => setHideIp(e.target.checked)} />
                  {t("topology.hideIp")}
                </label>
                <label className="topo-display-toggles__item">
                  <input
                    type="checkbox"
                    checked={hideVendor}
                    onChange={(e) => setHideVendor(e.target.checked)}
                  />
                  {t("topology.hideVendor")}
                </label>
                <label className="topo-display-toggles__item">
                  <input
                    type="checkbox"
                    checked={hidePorts}
                    onChange={(e) => setHidePorts(e.target.checked)}
                  />
                  {t("topology.hidePorts")}
                </label>
                <label className="topo-display-toggles__item">
                  <input
                    type="checkbox"
                    checked={edgeFlow}
                    onChange={(e) => setEdgeFlow(e.target.checked)}
                  />
                  {t("topology.edgeFlow")}
                </label>
                <label className="topo-display-toggles__item">
                  <input
                    type="checkbox"
                    checked={snapToGrid}
                    onChange={(e) => setSnapToGrid(e.target.checked)}
                  />
                  {t("topology.snapGrid")}
                </label>
                <label className="topo-display-toggles__item">
                  <input
                    type="checkbox"
                    checked={autoLayoutAfterDiscover}
                    onChange={(e) => setAutoLayoutAfterDiscover(e.target.checked)}
                  />
                  {t("topology.autoLayoutDiscover")}
                </label>
                <div className="topo-display-defaults">
                  <div className="topo-display-defaults__head">
                    <strong>{t("topology.edgeDefaults")}</strong>
                    <button type="button" className="btn btn--sm btn--ghost" onClick={resetEdgeDefaults}>
                      {t("topology.edgeDefaultsReset")}
                    </button>
                  </div>
                  {(
                    [
                      ["manual", t("topology.edgeManual")],
                      ["discovered", t("topology.edgeDiscovered")],
                      ["stale", t("topology.edgeStale")],
                    ] as const
                  ).map(([kind, label]) => {
                    const d = edgeDefaults[kind];
                    return (
                      <div key={kind} className="topo-display-defaults__row">
                        <span className="topo-display-defaults__name">{label}</span>
                        <input
                          type="color"
                          value={d.stroke_color}
                          title={t("topology.edgeColor")}
                          onChange={(e) => updateEdgeDefault(kind, { stroke_color: e.target.value })}
                        />
                        <select
                          className="topo-toolbar__select"
                          aria-label={t("topology.edgeLineStyle")}
                          value={d.line_style}
                          onChange={(e) =>
                            updateEdgeDefault(kind, {
                              line_style: e.target.value as EdgeLineStyle,
                            })
                          }
                        >
                          <option value="solid">{t("topology.edgeLineSolid")}</option>
                          <option value="dashed">{t("topology.edgeLineDashed")}</option>
                          <option value="dotted">{t("topology.edgeLineDotted")}</option>
                        </select>
                        <select
                          className="topo-toolbar__select"
                          aria-label={t("topology.edgeWidth")}
                          value={String(d.stroke_width)}
                          onChange={(e) =>
                            updateEdgeDefault(kind, { stroke_width: Number(e.target.value) || 2 })
                          }
                        >
                          {[1, 2, 3, 4, 5, 6, 8].map((w) => (
                            <option key={w} value={w}>
                              {w}px
                            </option>
                          ))}
                        </select>
                      </div>
                    );
                  })}
                </div>
              </div>
            </details>
          </div>
        </div>

        {discoverOpen ? (
          <div className="topo-discover">
            <div className="topo-discover__head">
              <strong>{t("topology.discoverReport")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => {
                  if (discovering) {
                    discoverAbortRef.current?.abort();
                    return;
                  }
                  setDiscoverOpen(false);
                }}
              >
                {discovering ? t("topology.discoverCancel") : t("topology.discoverClose")}
              </button>
            </div>
            {discovering && discoverProgress.total > 0 ? (
              <div className="topo-discover__progress" aria-live="polite">
                <div className="topo-discover__bar">
                  <div className="topo-discover__bar-fill" style={{ width: `${discoverPct}%` }} />
                </div>
                <div className="topo-discover__progress-meta">
                  <span>
                    {t("topology.discoverProgressLive")
                      .replace("{{index}}", String(discoverProgress.index))
                      .replace("{{total}}", String(discoverProgress.total))
                      .replace(
                        "{{name}}",
                        discoverProgress.neName || discoverProgress.neIp || "?",
                      )}
                  </span>
                  <span>{discoverPct}%</span>
                </div>
              </div>
            ) : null}
            {discovering && discoverProgress.total <= 0 ? (
              <p className="panel__hint panel__hint--live">{t("topology.discoverNoTargets")}</p>
            ) : null}
            {!discovering && discoverReport && discoverReport.scanned === 0 ? (
              <p className="panel__hint">{t("topology.discoverNoTargets")}</p>
            ) : null}
            {discoverError ? <p className="topo-discover__error">{discoverError}</p> : null}
            {discoverResults.length > 0 || discoverReport ? (
              <>
                <p className="topo-discover__summary">
                  {t("topology.discoverSummary")
                    .replace("{{scanned}}", String(discoverSummary.scanned))
                    .replace("{{added}}", String(discoverSummary.added))
                    .replace("{{updated}}", String(discoverSummary.updated))
                    .replace("{{stale}}", String(discoverSummary.stale))
                    .replace("{{failed}}", String(discoverSummary.failed))}
                </p>
                <div className="topo-discover__filters" role="group" aria-label={t("topology.discoverFilter")}>
                  {(
                    [
                      ["issues", t("topology.discoverFilterIssues"), discoverCounts.issues],
                      ["fail", t("topology.discoverFilterFail"), discoverCounts.fail],
                      ["warn", t("topology.discoverFilterWarn"), discoverCounts.warn],
                      ["ok", t("topology.discoverFilterOk"), discoverCounts.ok],
                    ] as const
                  ).map(([key, label, count]) => (
                    <button
                      key={key}
                      type="button"
                      className="topo-discover__filter"
                      onClick={() => {
                        setDiscoverFilter(key);
                        setDiscoverListOpen(true);
                      }}
                    >
                      {label}
                      <span className="topo-discover__filter-count">{count}</span>
                    </button>
                  ))}
                  <button
                    type="button"
                    className="topo-discover__filter topo-discover__filter--primary"
                    onClick={() => {
                      setDiscoverFilter("all");
                      setDiscoverListOpen(true);
                    }}
                  >
                    {t("topology.discoverFilterDetail")}
                    <span className="topo-discover__filter-count">{discoverResults.length}</span>
                  </button>
                </div>
              </>
            ) : null}
          </div>
        ) : null}

        <div
          className={`topo-canvas${fullscreen ? " is-fullscreen" : ""}${toolMode === "pan" ? " is-pan-mode" : ""}`}
          ref={canvasRef}
          onDragOver={onCanvasDragOver}
          onDrop={onCanvasDrop}
        >
          {mapId ? (
            <TopoDisplayContext.Provider value={displayOpts}>
              <ReactFlow
                nodes={nodes.map((n) =>
                  searchHitIds.includes(n.id)
                    ? { ...n, className: "is-search-hit" }
                    : { ...n, className: undefined },
                )}
                edges={displayEdges}
                nodeTypes={nodeTypes}
                connectionMode={ConnectionMode.Loose}
                defaultEdgeOptions={{ type: "straight" }}
                nodesDraggable={toolBehavior.nodesDraggable}
                nodesConnectable={toolBehavior.nodesConnectable}
                elementsSelectable={toolBehavior.elementsSelectable}
                panOnDrag={toolBehavior.panOnDrag}
                selectionOnDrag={toolBehavior.selectionOnDrag}
                panOnScroll={toolBehavior.panOnScroll}
                selectionMode={SelectionMode.Partial}
                multiSelectionKeyCode="Shift"
                snapToGrid={snapToGrid}
                snapGrid={SNAP_GRID}
                onNodeDragStart={() => pushHistory()}
                onNodesChange={(changes) => {
                  if (changes.some((c) => c.type === "position" || c.type === "remove" || c.type === "add")) {
                    markDirty();
                  }
                  onNodesChange(changes);
                }}
                onEdgesChange={(changes) => {
                  if (changes.some((c) => c.type !== "select")) {
                    markDirty();
                  }
                  onEdgesChange(changes);
                }}
                onConnect={onConnect}
                isValidConnection={isValidConnection}
                onNodeClick={onNodeClick}
                onEdgeClick={(_e, edge) => {
                  setCtxMenu(null);
                  focusEdge(edge.id);
                }}
                onPaneClick={() => {
                  setCtxMenu(null);
                  clearSelection();
                }}
                onNodeContextMenu={(e, node) => {
                  e.preventDefault();
                  const multi = selectedNodeIds.length > 1 && selectedNodeIds.includes(node.id);
                  if (!multi) focusNode(node.id, false);
                  const pos = placeCtxMenu(e.clientX, e.clientY);
                  setCtxMenu(multi ? { kind: "selection", ...pos } : { kind: "node", id: node.id, ...pos });
                }}
                onEdgeContextMenu={(e, edge) => {
                  e.preventDefault();
                  focusEdge(edge.id);
                  const pos = placeCtxMenu(e.clientX, e.clientY, { w: 260, h: 280 });
                  setCtxMenu({ kind: "edge", id: edge.id, ...pos });
                }}
                onSelectionContextMenu={(e) => {
                  e.preventDefault();
                  const pos = placeCtxMenu(e.clientX, e.clientY);
                  setCtxMenu({ kind: "selection", ...pos });
                }}
                onMoveStart={closeCtxMenu}
                onInit={(inst) => {
                  rfRef.current = inst as ReactFlowInstance<Node<NeNodeData>, Edge>;
                }}
                fitView
                deleteKeyCode={null}
                edgesFocusable
              >
                <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#cbd5e1" />
                <Controls showInteractive>
                  <ControlButton
                    className="topo-fs-control"
                    onClick={() => void toggleFullscreen()}
                    title={fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
                    aria-label={fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
                  >
                    <FullscreenIcon exit={fullscreen} />
                  </ControlButton>
                </Controls>
                <MiniMap pannable zoomable />
                <div className="topo-legend" aria-hidden="true">
                  {(
                    [
                      ["manual", t("topology.edgeManual")],
                      ["discovered", t("topology.edgeDiscovered")],
                      ["stale", t("topology.edgeStale")],
                    ] as const
                  ).map(([kind, label]) => {
                    const d = edgeDefaults[kind];
                    return (
                      <span key={kind} className="topo-legend__item">
                        <span
                          className="topo-legend__swatch"
                          style={{
                            borderTopColor: d.stroke_color,
                            borderTopWidth: Math.max(1, Math.min(4, d.stroke_width)),
                            borderTopStyle: d.line_style === "solid" ? "solid" : "dashed",
                          }}
                        />
                        {label}
                      </span>
                    );
                  })}
                </div>
              </ReactFlow>
            </TopoDisplayContext.Provider>
          ) : (
            <div className="topo-canvas__empty">{t("topology.selectMap")}</div>
          )}
        </div>
      </main>

      {ctxMenu ? (
        <ul
          className={`topo-ctx${ctxMenu.kind === "edge" ? " topo-ctx--edge" : ""}`}
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          role="menu"
          onContextMenu={(e) => e.preventDefault()}
        >
          {ctxMenu.kind === "selection" ? (
            <>
              <li className="topo-ctx__head" role="presentation">
                {t("topology.selectionMenu")}
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  disabled={discovering}
                  onClick={() => {
                    const ids = selectedNodes
                      .map((n) => String(n.data.managed_ne_id || n.data.ume_ne_id || "").trim())
                      .filter(Boolean);
                    closeCtxMenu();
                    if (!ids.length) {
                      showError(t("topology.discoverOneNeedNe"));
                      return;
                    }
                    void runDiscover(ids);
                  }}
                >
                  {t("topology.discoverSelected").replace("{{count}}", String(selectedNodes.length))}
                </button>
              </li>
              <li className="topo-ctx__sep" aria-hidden />
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item topo-ctx__item--danger"
                  role="menuitem"
                  onClick={() => removeSelected()}
                >
                  {t("topology.removeSelected").replace("{{count}}", String(selectedNodes.length))}
                </button>
              </li>
            </>
          ) : ctxMenu.kind === "node" ? (
            <>
              <li className="topo-ctx__head" role="presentation">
                {t("topology.nodeMenu")}
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  onClick={renameSelectedNode}
                >
                  {t("topology.renameNode")}
                </button>
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  disabled={discovering}
                  onClick={() => discoverOneFor(selectedNode)}
                >
                  {t("topology.discoverOne")}
                </button>
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  onClick={() => openWebcrtFor(selectedNode)}
                >
                  {t("topology.openWebcrt")}
                </button>
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  onClick={() => openNeFor(selectedNode)}
                >
                  {t("topology.openNe")}
                </button>
              </li>
              <li className="topo-ctx__sep" aria-hidden />
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item topo-ctx__item--danger"
                  role="menuitem"
                  onClick={() => removeNodeById(ctxMenu.id)}
                >
                  {t("topology.removeNode")}
                </button>
              </li>
            </>
          ) : (
            <>
              <li className="topo-ctx__section" role="none">
                <div className="topo-ctx__style" onMouseDown={(e) => e.stopPropagation()}>
                  <div className="topo-ctx__style-title">{t("topology.edgeStyle")}</div>
                  {selectedEdgeResolved ? (
                    <div className="topo-ctx__style-grid">
                      <label className="topo-ctx__style-row">
                        <span>{t("topology.sourcePort")}</span>
                        <input
                          type="text"
                          className="topo-ctx__style-input"
                          value={selectedEdgeData.source_port || ""}
                          placeholder="?"
                          onFocus={() => pushHistory()}
                          onChange={(e) =>
                            patchSelectedEdgeStyle({ source_port: e.target.value }, { skipHistory: true })
                          }
                        />
                      </label>
                      <label className="topo-ctx__style-row">
                        <span>{t("topology.targetPort")}</span>
                        <input
                          type="text"
                          className="topo-ctx__style-input"
                          value={selectedEdgeData.target_port || ""}
                          placeholder="?"
                          onFocus={() => pushHistory()}
                          onChange={(e) =>
                            patchSelectedEdgeStyle({ target_port: e.target.value }, { skipHistory: true })
                          }
                        />
                      </label>
                      <label className="topo-ctx__style-row">
                        <span>{t("topology.edgeColor")}</span>
                        <input
                          type="color"
                          value={selectedEdgeResolved.stroke}
                          onFocus={() => pushHistory()}
                          onChange={(e) =>
                            patchSelectedEdgeStyle({ stroke_color: e.target.value }, { skipHistory: true })
                          }
                        />
                      </label>
                      <label className="topo-ctx__style-row">
                        <span>{t("topology.edgeLineStyle")}</span>
                        <select
                          value={
                            selectedEdgeData.line_style ||
                            (selectedEdgeResolved.strokeDasharray ? "dashed" : "solid")
                          }
                          onChange={(e) => patchSelectedEdgeStyle({ line_style: e.target.value })}
                        >
                          <option value="solid">{t("topology.edgeLineSolid")}</option>
                          <option value="dashed">{t("topology.edgeLineDashed")}</option>
                          <option value="dotted">{t("topology.edgeLineDotted")}</option>
                        </select>
                      </label>
                      <label className="topo-ctx__style-row">
                        <span>{t("topology.edgeWidth")}</span>
                        <select
                          value={String(
                            Number(selectedEdgeData.stroke_width || 0) > 0
                              ? Number(selectedEdgeData.stroke_width)
                              : Math.round(Number(selectedEdgeResolved.strokeWidth || 2)),
                          )}
                          onChange={(e) =>
                            patchSelectedEdgeStyle({ stroke_width: Number(e.target.value) || 0 })
                          }
                        >
                          {[1, 2, 3, 4, 5, 6, 8].map((w) => (
                            <option key={w} value={w}>
                              {w}px
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  ) : null}
                </div>
              </li>
              <li className="topo-ctx__sep" aria-hidden />
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  onClick={() => openPortTrafficForEdge(selectedEdge)}
                >
                  {t("topology.openPortTraffic")}
                </button>
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  onClick={() =>
                    patchSelectedEdgeStyle({ stroke_color: "", stroke_width: 0, line_style: "" })
                  }
                >
                  {t("topology.edgeStyleReset")}
                </button>
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item topo-ctx__item--danger"
                  role="menuitem"
                  onClick={() => removeEdgeById(ctxMenu.id)}
                >
                  {t("topology.removeEdge")}
                </button>
              </li>
            </>
          )}
        </ul>
      ) : null}


      {discoverListOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setDiscoverListOpen(false)}
        >
          <div
            className="modal modal--wide topo-discover-modal topo-discover-list-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="topo-discover-list-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="topo-discover-modal__head">
              <div>
                <h3 id="topo-discover-list-title">{t("topology.discoverListTitle")}</h3>
                <p className="topo-discover-modal__sub">
                  {t("topology.discoverSummary")
                    .replace("{{scanned}}", String(discoverSummary.scanned))
                    .replace("{{added}}", String(discoverSummary.added))
                    .replace("{{updated}}", String(discoverSummary.updated))
                    .replace("{{stale}}", String(discoverSummary.stale))
                    .replace("{{failed}}", String(discoverSummary.failed))}
                </p>
              </div>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => setDiscoverListOpen(false)}
              >
                {t("topology.discoverClose")}
              </button>
            </div>

            <div className="topo-discover__filters" role="tablist" aria-label={t("topology.discoverFilter")}>
              {(
                [
                  ["issues", t("topology.discoverFilterIssues"), discoverCounts.issues],
                  ["fail", t("topology.discoverFilterFail"), discoverCounts.fail],
                  ["warn", t("topology.discoverFilterWarn"), discoverCounts.warn],
                  ["ok", t("topology.discoverFilterOk"), discoverCounts.ok],
                  ["all", t("topology.discoverFilterAll"), discoverResults.length],
                ] as const
              ).map(([key, label, count]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={discoverFilter === key}
                  className={`topo-discover__filter${discoverFilter === key ? " is-active" : ""}`}
                  onClick={() => setDiscoverFilter(key)}
                >
                  {label}
                  <span className="topo-discover__filter-count">{count}</span>
                </button>
              ))}
            </div>

            {discoverVisible.length === 0 ? (
              <p className="panel__hint">{t("topology.discoverListEmpty")}</p>
            ) : (
              <ul className="topo-discover__list topo-discover__list--modal">
                {discoverVisible.map((r) => {
                  const unmatchedCount = r.unmatched_count ?? (r.unmatched?.length || 0);
                  const kind = discoverResultKind(r);
                  const title = r.ne_name || r.ne_ip || r.ne_id;
                  const linkCount = r.links?.length || 0;
                  return (
                    <li key={r.ne_id} className={`is-${kind}`}>
                      <button
                        type="button"
                        className="topo-discover__row"
                        onClick={() => setDiscoverDetail(r)}
                      >
                        <span className="topo-discover__row-main">
                          <span className="topo-discover__row-name">{title}</span>
                          {r.ne_ip ? <span className="topo-discover__row-ip">{r.ne_ip}</span> : null}
                          <span className="topo-discover__row-meta">
                            {r.ok
                              ? t("topology.discoverNeOk")
                                  .replace("{{neighbors}}", String(r.neighbors))
                                  .replace("{{added}}", String(r.edges_added))
                                  .replace("{{updated}}", String(r.edges_updated))
                              : r.error || t("topology.discoverNeFail")}
                            {linkCount > 0
                              ? ` ? ${t("topology.discoverLinkCount").replace("{{count}}", String(linkCount))}`
                              : ""}
                            {r.ok && unmatchedCount > 0
                              ? ` ? ${t("topology.discoverUnmatched").replace("{{count}}", String(unmatchedCount))}`
                              : ""}
                          </span>
                        </span>
                        <span className="topo-discover__row-actions">
                          <span className="topo-discover__badge">
                            {kind === "fail"
                              ? "FAIL"
                              : kind === "warn"
                                ? r.parser_stub
                                  ? "STUB"
                                  : "WARN"
                                : "OK"}
                          </span>
                          <span className="topo-discover__detail-btn">{t("topology.discoverViewDetail")}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            {discoverFiltered.length > DISCOVER_LIST_CAP ? (
              <p className="topo-discover__cap">
                {t("topology.discoverShowLimited")
                  .replace("{{shown}}", String(DISCOVER_LIST_CAP))
                  .replace("{{total}}", String(discoverFiltered.length))}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}


      {discoverDetail ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setDiscoverDetail(null)}
        >
          <div
            className="modal modal--wide topo-discover-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="topo-discover-detail-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="topo-discover-modal__head">
              <div>
                <h3 id="topo-discover-detail-title">
                  {discoverDetail.ne_name || discoverDetail.ne_ip || discoverDetail.ne_id}
                </h3>
                <p className="topo-discover-modal__sub">
                  {[discoverDetail.ne_ip, discoverDetail.command].filter(Boolean).join(" ? ")}
                </p>
              </div>
              <span
                className={`topo-discover__badge topo-discover__badge--lg is-${discoverResultKind(discoverDetail)}`}
              >
                {discoverResultKind(discoverDetail) === "fail"
                  ? "FAIL"
                  : discoverResultKind(discoverDetail) === "warn"
                    ? discoverDetail.parser_stub
                      ? "STUB"
                      : "WARN"
                    : "OK"}
              </span>
            </div>

            {!discoverDetail.ok ? (
              <p className="topo-discover__error">
                {discoverDetail.error || t("topology.discoverNeFail")}
              </p>
            ) : null}
            {discoverDetail.parser_stub ? (
              <p className="topo-discover__item-warn">
                {t("topology.discoverParserStub").replace(
                  "{{parser}}",
                  discoverDetail.parser_key || "unknown",
                )}
              </p>
            ) : null}

            <div className="topo-discover-modal__stats">
              <span>
                {t("topology.discoverNeOk")
                  .replace("{{neighbors}}", String(discoverDetail.neighbors || 0))
                  .replace("{{added}}", String(discoverDetail.edges_added || 0))
                  .replace("{{updated}}", String(discoverDetail.edges_updated || 0))}
              </span>
            </div>

            <h4 className="topo-discover-modal__section">
              {t("topology.discoverLinksTitle").replace(
                "{{count}}",
                String(discoverDetail.links?.length || 0),
              )}
            </h4>
            {(discoverDetail.links || []).length === 0 ? (
              <p className="panel__hint">{t("topology.discoverLinksEmpty")}</p>
            ) : (
              <div className="topo-discover-modal__table-wrap">
                <table className="topo-discover-modal__table">
                  <thead>
                    <tr>
                      <th>{t("topology.discoverColPeer")}</th>
                      <th>{t("topology.discoverColLocalPort")}</th>
                      <th>{t("topology.discoverColRemotePort")}</th>
                      <th>{t("topology.discoverColProtocol")}</th>
                      <th>{t("topology.discoverColAction")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {(discoverDetail.links || []).map((link, idx) => (
                      <tr key={`${discoverDetail.ne_id}-l-${idx}`}>
                        <td>
                          <div className="topo-discover-modal__peer">
                            <strong>{link.peer_name || link.peer_ip || link.peer_ne_id || "?"}</strong>
                            {link.peer_ip ? <span>{link.peer_ip}</span> : null}
                          </div>
                        </td>
                        <td>{link.local_port || "?"}</td>
                        <td>{link.remote_port || "?"}</td>
                        <td>{(link.protocol || "?").toUpperCase()}</td>
                        <td>
                          <span className={`topo-discover-action is-${link.action || ""}`}>
                            {link.action === "added"
                              ? t("topology.discoverLinkAdded")
                              : link.action === "updated"
                                ? t("topology.discoverLinkUpdated")
                                : link.action === "kept_manual"
                                  ? t("topology.discoverLinkKeptManual")
                                  : link.action || "?"}
                          </span>
                        </td>
                        <td>
                          {link.peer_node_id ? (
                            <button
                              type="button"
                              className="btn btn--sm btn--ghost"
                              onClick={() => {
                                const nid = String(link.peer_node_id || "");
                                setDiscoverDetail(null);
                                locateNode(nid);
                              }}
                            >
                              {t("topology.discoverLocatePeer")}
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <h4 className="topo-discover-modal__section">
              {t("topology.discoverUnmatchedTitle").replace(
                "{{count}}",
                String(discoverDetail.unmatched_count ?? discoverDetail.unmatched?.length ?? 0),
              )}
            </h4>
            {(discoverDetail.unmatched || []).length === 0 ? (
              <p className="panel__hint">{t("topology.discoverUnmatchedEmpty")}</p>
            ) : (
              <div className="topo-discover-modal__table-wrap">
                <table className="topo-discover-modal__table">
                  <thead>
                    <tr>
                      <th>{t("topology.discoverColRemote")}</th>
                      <th>{t("topology.discoverColLocalPort")}</th>
                      <th>{t("topology.discoverColRemotePort")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(discoverDetail.unmatched || []).map((u, idx) => (
                      <tr key={`${discoverDetail.ne_id}-u-${idx}`}>
                        <td>
                          {(u.remote_name || u.remote_ip || "?").trim()}
                          {u.remote_ip && u.remote_name ? ` (${u.remote_ip})` : ""}
                        </td>
                        <td>{u.local_port || "?"}</td>
                        <td>{u.remote_port || "?"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {discoverDetail.raw_preview ? (
              <>
                <h4 className="topo-discover-modal__section">{t("topology.discoverRawPreview")}</h4>
                <pre className="topo-discover-modal__raw">{discoverDetail.raw_preview}</pre>
              </>
            ) : null}

            <div className="modal__actions">
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => {
                  const id = String(discoverDetail.ne_id || "");
                  const node = nodes.find(
                    (n) => n.data.managed_ne_id === id || n.data.ume_ne_id === id,
                  );
                  setDiscoverDetail(null);
                  if (node) locateNode(node.id);
                  else showError(t("topology.findNoMatch"));
                }}
              >
                {t("topology.discoverLocateNe")}
              </button>
              <button type="button" className="btn btn--sm" onClick={() => setDiscoverDetail(null)}>
                {t("topology.discoverClose")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

    </div>
  );
}
