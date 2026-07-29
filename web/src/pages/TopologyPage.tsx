import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useEdgesState,
  useNodesState,
  Handle,
  Position,
  MarkerType,
  ConnectionMode,
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
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { openNewModuleWindow, openOrFocusModule } from "../utils/moduleWindows";
import type {
  ManagedNeItem,
  TopologyDiscoverNeResult,
  TopologyDiscoverOut,
  TopologyEdgeItem,
  TopologyNodeItem,
  UmeNeItem,
} from "../types";

type NeNodeData = {
  label: string;
  managed_ne_id: string;
  ume_ne_id: string;
  ne_ip: string;
  vendor: string;
  connect_status: string;
};

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
};

const TopoDisplayContext = createContext<TopoDisplayOpts>({
  hideIp: true,
  hideVendor: true,
  hidePorts: true,
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

/** Classic Visio/Packet Tracer style router: disc + four outbound arrows. */
function RouterIcon() {
  return (
    <svg className="topo-node__icon" viewBox="0 0 64 64" aria-hidden="true">
      <ellipse cx="32" cy="39" rx="19" ry="8.5" className="topo-node__icon-shadow" />
      <path
        d="M13 31 A19 9.5 0 0 0 51 31 L51 36.5 A19 9.5 0 0 1 13 36.5 Z"
        className="topo-node__icon-rim"
      />
      <ellipse cx="32" cy="31" rx="19" ry="9.5" className="topo-node__icon-body" />
      <ellipse cx="32" cy="30" rx="15.5" ry="7" className="topo-node__icon-face" />
      <g className="topo-node__icon-arrows">
        {/* NW / NE / SW / SE */}
        <path d="M31 29 L19 18 L24.5 18.8 L23.2 23.8 Z" />
        <path d="M33 29 L45 18 L39.5 18.8 L40.8 23.8 Z" />
        <path d="M31 31 L19 42 L24.5 41.2 L23.2 36.2 Z" />
        <path d="M33 31 L45 42 L39.5 41.2 L40.8 36.2 Z" />
      </g>
    </svg>
  );
}

function NeNode({ data, selected }: NodeProps<Node<NeNodeData>>) {
  const { hideIp, hideVendor } = useContext(TopoDisplayContext);
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
    <div className={`topo-node topo-node--${tone}${selected ? " is-selected" : ""}`}>
      <div className="topo-node__glyph">
        {/* Centered on the disc so edges meet the circular icon, not the caption. */}
        <Handle type="target" position={Position.Left} className="topo-node__handle topo-node__handle--center" />
        <Handle type="source" position={Position.Right} className="topo-node__handle topo-node__handle--center" />
        <RouterIcon />
      </div>
      <div className="topo-node__caption">{bits.join(" · ")}</div>
    </div>
  );
}

const nodeTypes = { neNode: NeNode };

function edgeStyle(source: string): { stroke: string; strokeDasharray?: string; strokeWidth?: number } {
  const src = (source || "manual").toLowerCase();
  if (src === "stale") {
    return { stroke: "#dc2626", strokeDasharray: "4 4", strokeWidth: 2 };
  }
  if (src === "lldp" || src === "cdp") {
    return { stroke: "#0ea5e9", strokeDasharray: "6 4" };
  }
  return { stroke: "#64748b" };
}

function graphToFlow(nodes: TopologyNodeItem[], edges: TopologyEdgeItem[]) {
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
    const label = [e.source_port, e.target_port].filter(Boolean).join(" ↔ ");
    return {
      id: e.id,
      source: e.source_node_id,
      target: e.target_node_id,
      type: "straight",
      label: label || undefined,
      animated: false,
      style: edgeStyle(src),
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      data: {
        source: src,
        source_port: e.source_port || "",
        target_port: e.target_port || "",
      },
    };
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
    edges: edges.map((e) => ({
      id: e.id,
      source_node_id: e.source,
      target_node_id: e.target,
      source_port: String((e.data as { source_port?: string } | undefined)?.source_port || ""),
      target_port: String((e.data as { target_port?: string } | undefined)?.target_port || ""),
      source: String((e.data as { source?: string } | undefined)?.source || "manual"),
    })),
  };
}

export function TopologyPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [mapId, setMapId] = useState<string>("");
  const [keyword, setKeyword] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [hideIp, setHideIp] = useState(true);
  const [hideVendor, setHideVendor] = useState(true);
  const [hidePorts, setHidePorts] = useState(true);
  const [edgeFlow, setEdgeFlow] = useState(false);
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
  const discoverAbortRef = useRef<AbortController | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NeNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const rfRef = useRef<ReactFlowInstance<Node<NeNodeData>, Edge> | null>(null);
  const dirtyRef = useRef(false);
  const displayOpts = useMemo(
    () => ({ hideIp, hideVendor, hidePorts }),
    [hideIp, hideVendor, hidePorts],
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
    queryKey: [...queryKeys.managedNeAll, "topology-palette", keyword],
    queryFn: () =>
      fetchManagedNe({
        keyword: keyword.trim(),
        vendor: "",
        connectStatus: "",
        page: 1,
        pageSize: 100,
      }),
    enabled: paletteSource === "managed",
  });

  const umeQuery = useQuery({
    queryKey: ["umeInventoryNe", "topology-palette", keyword],
    queryFn: () =>
      fetchUmeNe({
        keyword: keyword.trim(),
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
    const { rfNodes, rfEdges } = graphToFlow(graphQuery.data.nodes, graphQuery.data.edges);
    setNodes(rfNodes);
    setEdges(rfEdges);
    dirtyRef.current = false;
    window.setTimeout(() => rfRef.current?.fitView({ padding: 0.2 }), 50);
  }, [graphQuery.data, setNodes, setEdges]);

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
      setMapId(row.id);
      showOk(t("topology.newMap"));
      // Prompt rename right after create — default name is a placeholder.
      window.setTimeout(() => promptRenameMap(row.id, row.name), 0);
    },
    onError: (err) => showError(String(err)),
  });

  const deleteMapMut = useMutation({
    mutationFn: (id: string) => deleteTopologyMap(id),
    onSuccess: async (_out, id) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      if (mapId === id) {
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
      dirtyRef.current = false;
      queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      showOk(t("topology.saved"));
    },
    onError: (err) => showError(String(err)),
  });

  const runDiscover = useCallback(async () => {
    if (!mapId || discovering) return;
    discoverAbortRef.current?.abort();
    const ac = new AbortController();
    discoverAbortRef.current = ac;
    setDiscoverOpen(true);
    setDiscovering(true);
    setDiscoverReport(null);
    setDiscoverLiveResults([]);
    setDiscoverError("");
    setDiscoverProgress({
      index: 0,
      total: nodes.filter((n) => Boolean(n.data.managed_ne_id)).length,
      neName: "",
      neIp: "",
      edgesAdded: 0,
      edgesUpdated: 0,
    });
    try {
      if (dirtyRef.current) {
        await putTopologyGraph(mapId, flowToGraphPayload(nodes, edges));
        dirtyRef.current = false;
      }
      const out = await discoverTopologyNeighborsStream(
        mapId,
        { protocol: "auto" },
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
        const { rfNodes, rfEdges } = graphToFlow(out.graph.nodes, out.graph.edges);
        setNodes(rfNodes);
        setEdges(rfEdges);
        dirtyRef.current = false;
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
  }, [mapId, discovering, nodes, edges, queryClient, setNodes, setEdges, showOk, showError, t]);

  const discoverResults = discoverReport?.results?.length
    ? discoverReport.results
    : discoverLiveResults;
  const discoverSummary = discoverReport
    ? {
        scanned: discoverReport.scanned,
        added: discoverReport.edges_added,
        updated: discoverReport.edges_updated,
        stale: discoverReport.edges_stale || 0,
        failed: discoverReport.results.filter((r) => !r.ok).length,
      }
    : {
        scanned: discoverLiveResults.length,
        added: discoverProgress.edgesAdded,
        updated: discoverProgress.edgesUpdated,
        stale: 0,
        failed: discoverLiveResults.filter((r) => !r.ok).length,
      };

  const onConnect = useCallback(
    (connection: Connection) => {
      dirtyRef.current = true;
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            id: newId(),
            type: "straight",
            markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
            style: { stroke: "#64748b" },
            data: { source: "manual", source_port: "", target_port: "" },
          },
          eds,
        ),
      );
    },
    [setEdges],
  );

  const addManagedNeToCanvas = (ne: ManagedNeItem) => {
    if (!mapId) {
      showError(t("topology.selectMap"));
      return;
    }
    if (nodes.some((n) => n.data.managed_ne_id === ne.id)) {
      return;
    }
    const offset = nodes.length * 24;
    dirtyRef.current = true;
    setNodes((prev) => [
      ...prev,
      {
        id: newId(),
        type: "neNode",
        position: { x: 80 + offset, y: 80 + offset },
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
  };

  const addUmeNeToCanvas = (ne: UmeNeItem) => {
    if (!mapId) {
      showError(t("topology.selectMap"));
      return;
    }
    if (nodes.some((n) => n.data.ume_ne_id === ne.ne_id)) {
      return;
    }
    const offset = nodes.length * 24;
    const name = (ne.host_name || ne.ne_name || ne.user_label || ne.ip_address || ne.ne_id).trim();
    dirtyRef.current = true;
    setNodes((prev) => [
      ...prev,
      {
        id: newId(),
        type: "neNode",
        position: { x: 80 + offset, y: 80 + offset },
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

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) || null,
    [nodes, selectedNodeId],
  );
  const selectedEdge = useMemo(
    () => edges.find((e) => e.id === selectedEdgeId) || null,
    [edges, selectedEdgeId],
  );
  const staleEdgeCount = useMemo(
    () =>
      edges.filter((e) => String((e.data as { source?: string } | undefined)?.source || "") === "stale")
        .length,
    [edges],
  );

  const removeSelected = () => {
    if (selectedEdgeId) {
      dirtyRef.current = true;
      setEdges((es) => es.filter((e) => e.id !== selectedEdgeId));
      setSelectedEdgeId(null);
      return;
    }
    if (!selectedNodeId) return;
    dirtyRef.current = true;
    setNodes((ns) => ns.filter((n) => n.id !== selectedNodeId));
    setEdges((es) => es.filter((e) => e.source !== selectedNodeId && e.target !== selectedNodeId));
    setSelectedNodeId(null);
  };

  const removeStaleEdges = () => {
    const n = staleEdgeCount;
    if (n <= 0) return;
    dirtyRef.current = true;
    setEdges((es) =>
      es.filter((e) => String((e.data as { source?: string } | undefined)?.source || "") !== "stale"),
    );
    setSelectedEdgeId(null);
    showOk(t("topology.staleRemoved").replace("{{count}}", String(n)));
  };

  const openWebcrt = () => {
    const managedId = selectedNode?.data.managed_ne_id;
    const umeId = selectedNode?.data.ume_ne_id;
    if (managedId) {
      openNewModuleWindow({
        moduleId: "webcrt",
        path: `/webcrt?ne_id=${encodeURIComponent(managedId)}`,
      });
      return;
    }
    if (umeId) {
      openNewModuleWindow({
        moduleId: "webcrt",
        path: `/webcrt?ne_id=${encodeURIComponent(umeId)}&source=ume`,
      });
      return;
    }
    showError(t("topology.noNeLink"));
  };

  const openNe = () => {
    const managedId = selectedNode?.data.managed_ne_id;
    const umeId = selectedNode?.data.ume_ne_id;
    if (managedId) {
      openOrFocusModule({
        moduleId: "managed-ne",
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
    if (!hideAddedNes) return palette;
    return palette.filter((item) =>
      item.source === "managed"
        ? !onCanvasManagedIds.has(item.managed_ne_id)
        : !onCanvasUmeIds.has(item.ume_ne_id),
    );
  }, [palette, hideAddedNes, onCanvasManagedIds, onCanvasUmeIds]);
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
              ›
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
                    onClick={() => createMapMut.mutate()}
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
                    ‹
                  </button>
                </div>
              </div>
              {maps.length === 0 ? (
                <p className="panel__hint">{t("topology.emptyMaps")}</p>
              ) : (
                <ul className="topo-map-list">
                  {maps.map((m) => (
                    <li key={m.id} className={mapId === m.id ? "is-active" : ""}>
                      <button
                        type="button"
                        className="topo-map-list__item"
                        onClick={() => setMapId(m.id)}
                        onDoubleClick={() => promptRenameMap(m.id, m.name)}
                        title={t("topology.renameHint")}
                      >
                        <span className="topo-map-list__name">{m.name}</span>
                        <span className="topo-map-list__meta">
                          {m.node_count}N / {m.edge_count}E
                        </span>
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        title={t("topology.rename")}
                        disabled={renameMapMut.isPending}
                        onClick={() => promptRenameMap(m.id, m.name)}
                      >
                        ✎
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        title={t("topology.deleteMap")}
                        onClick={() => {
                          const msg = t("topology.deleteMapConfirm").replace("{{name}}", m.name);
                          if (window.confirm(msg)) deleteMapMut.mutate(m.id);
                        }}
                      >
                        ×
                      </button>
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
                      <li key={item.key}>
                        <button
                          type="button"
                          className="topo-palette__item"
                          disabled={!mapId || onCanvas}
                          onClick={() => addPaletteItem(item)}
                          title={`${item.name}\n${item.ip}`}
                        >
                          <span className="topo-palette__name">{item.name}</span>
                          <span className="topo-palette__meta">
                            {item.meta}
                            {onCanvas ? " ✓" : ""}
                          </span>
                        </button>
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
          <div className="topo-toolbar__left">
            <div className="topo-toolbar__title">
              <strong>{maps.find((m) => m.id === mapId)?.name || t("topology.selectMap")}</strong>
              {mapId ? (
                <button
                  type="button"
                  className="btn btn--sm btn--ghost"
                  title={t("topology.rename")}
                  disabled={renameMapMut.isPending}
                  onClick={() => {
                    const current = maps.find((m) => m.id === mapId)?.name || "";
                    promptRenameMap(mapId, current);
                  }}
                >
                  {t("topology.rename")}
                </button>
              ) : null}
            </div>
            <span className="panel__hint">{t("topology.canvasHint")}</span>
          </div>
          <div className="topo-toolbar__actions">
            <div className="topo-display-toggles" role="group" aria-label={t("topology.display")}>
              <label className="topo-display-toggles__item">
                <input
                  type="checkbox"
                  checked={hideIp}
                  onChange={(e) => setHideIp(e.target.checked)}
                />
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
            </div>
            <button
              type="button"
              className="btn btn--sm"
              disabled={!mapId || saveMut.isPending}
              onClick={() => saveMut.mutate()}
            >
              {saveMut.isPending ? t("topology.saving") : t("topology.save")}
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

        {selectedNode ? (
          <div className="topo-selection">
            <span>
              {t("topology.selected")}: <strong>{selectedNode.data.label}</strong>
              {selectedNode.data.ne_ip ? ` (${selectedNode.data.ne_ip})` : ""}
            </span>
            <div className="topo-selection__actions">
              <button type="button" className="btn btn--sm" onClick={openWebcrt}>
                {t("topology.openWebcrt")}
              </button>
              <button type="button" className="btn btn--sm" onClick={openNe}>
                {t("topology.openNe")}
              </button>
              <button type="button" className="btn btn--sm btn--ghost" onClick={removeSelected}>
                {t("topology.removeNode")}
              </button>
            </div>
          </div>
        ) : selectedEdge ? (
          <div className="topo-selection">
            <span>
              {t("topology.selectedEdge")}:{" "}
              <strong>
                {String((selectedEdge.data as { source?: string } | undefined)?.source || "manual")}
              </strong>
              {selectedEdge.label ? ` · ${String(selectedEdge.label)}` : ""}
            </span>
            <div className="topo-selection__actions">
              <button type="button" className="btn btn--sm btn--ghost" onClick={removeSelected}>
                {t("topology.removeEdge")}
              </button>
            </div>
          </div>
        ) : null}

        {discoverOpen ? (
          <div className="topo-discover">
            <div className="topo-discover__head">
              <strong>{t("topology.discoverReport")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => setDiscoverOpen(false)}
                disabled={discovering}
              >
                {t("topology.discoverClose")}
              </button>
            </div>
            {discovering ? (
              <p className="panel__hint panel__hint--live">
                {discoverProgress.total <= 0
                  ? t("topology.discoverNoTargets")
                  : t("topology.discoverProgressLive")
                      .replace("{{index}}", String(discoverProgress.index))
                      .replace("{{total}}", String(discoverProgress.total))
                      .replace(
                        "{{name}}",
                        discoverProgress.neName || discoverProgress.neIp || "…",
                      )}
              </p>
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
                <ul className="topo-discover__list">
                  {discoverResults.map((r) => (
                    <li key={r.ne_id} className={r.ok ? "is-ok" : "is-fail"}>
                      <div className="topo-discover__item-title">
                        <span>{r.ne_name || r.ne_ip || r.ne_id}</span>
                        <span className="topo-discover__badge">{r.ok ? "OK" : "FAIL"}</span>
                      </div>
                      <div className="topo-discover__item-meta">
                        {r.ne_ip ? `${r.ne_ip} · ` : ""}
                        {r.ok
                          ? t("topology.discoverNeOk")
                              .replace("{{neighbors}}", String(r.neighbors))
                              .replace("{{added}}", String(r.edges_added))
                              .replace("{{updated}}", String(r.edges_updated))
                          : r.error || t("topology.discoverNeFail")}
                      </div>
                      {r.command ? (
                        <div className="topo-discover__item-cmd">{r.command}</div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}

        <div className="topo-canvas">
          {mapId ? (
            <TopoDisplayContext.Provider value={displayOpts}>
              <ReactFlow
                nodes={nodes}
                edges={displayEdges}
                nodeTypes={nodeTypes}
                connectionMode={ConnectionMode.Loose}
                defaultEdgeOptions={{ type: "straight" }}
                onNodesChange={(changes) => {
                  dirtyRef.current = true;
                  onNodesChange(changes);
                }}
                onEdgesChange={(changes) => {
                  dirtyRef.current = true;
                  onEdgesChange(changes);
                }}
                onConnect={onConnect}
                onNodeClick={(_e, node) => {
                  setSelectedEdgeId(null);
                  setSelectedNodeId(node.id);
                }}
                onEdgeClick={(_e, edge) => {
                  setSelectedNodeId(null);
                  setSelectedEdgeId(edge.id);
                }}
                onPaneClick={() => {
                  setSelectedNodeId(null);
                  setSelectedEdgeId(null);
                }}
                onInit={(inst) => {
                  rfRef.current = inst;
                }}
                fitView
                deleteKeyCode={["Backspace", "Delete"]}
                edgesFocusable
                elementsSelectable
              >
                <Background gap={18} size={1} />
                <Controls />
                <MiniMap pannable zoomable />
              </ReactFlow>
            </TopoDisplayContext.Provider>
          ) : (
            <div className="topo-canvas__empty">{t("topology.selectMap")}</div>
          )}
        </div>
      </main>
    </div>
  );
}
