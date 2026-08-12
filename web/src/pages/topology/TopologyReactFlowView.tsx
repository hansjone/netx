import { createContext, memo, useContext, type Dispatch, type SetStateAction } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  ControlButton,
  MiniMap,
  Handle,
  Position,
  ConnectionLineType,
  ConnectionMode,
  SelectionMode,
  applyNodeChanges,
  applyEdgeChanges,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type EdgeChange,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { WorldScatterLayer, type WorldScatterPoint } from "./WorldScatterLayer";
import { ParallelEdge } from "./ParallelEdge";
import { isAggregateEdgeId } from "./linkDisplay";
import type { ToolMode, ToolModeBehavior } from "./toolMode";
import type { TopologyViewRole, TopologyWorldTransform } from "../../types";

export type NeNodeData = {
  label: string;
  managed_ne_id: string;
  ume_ne_id: string;
  ne_ip: string;
  vendor: string;
  connect_status: string;
  managed_source?: string;
  kind?: "ne" | "region" | "layer";
  folder_id?: string;
  view_id?: string;
  role?: TopologyViewRole | string;
  subtitle?: string;
  node_count?: number;
};

export type TopoDisplayOpts = {
  hideIp: boolean;
  hideVendor: boolean;
  hidePorts: boolean;
  connectMode: boolean;
  showPlaceholderBadge: boolean;
  worldVisualLod: "dot" | "pin" | "full";
};

const TopoDisplayContext = createContext<TopoDisplayOpts>({
  hideIp: true,
  hideVendor: true,
  hidePorts: true,
  connectMode: false,
  showPlaceholderBadge: false,
  worldVisualLod: "full",
});

const SEP = " / ";

import { isPlaceholderSource } from "./searchUtils";
import { nodeIconTone } from "./vendorTone";

export { isPlaceholderSource };

function RouterIcon() {
  return (
    <span className="topo-node__icon" aria-hidden="true">
      <img className="topo-node__icon-art" src="/topo/ne-router.png" alt="" draggable={false} />
      <span className="topo-node__icon-tint" />
    </span>
  );
}

function RegionCanvasIcon() {
  return (
    <span className="topo-node__icon topo-node__icon--region" aria-hidden="true">
      <img className="topo-node__icon-art" src="/topo/region-building.png" alt="" draggable={false} />
    </span>
  );
}

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

const NeNode = memo(function NeNode({ data, selected }: NodeProps<Node<NeNodeData>>) {
  const { hideIp, hideVendor, connectMode, showPlaceholderBadge, worldVisualLod } =
    useContext(TopoDisplayContext);
  const isRegion = data.kind === "region";
  const tone = isRegion ? "region" : nodeIconTone(data.vendor, data.managed_ne_id, data.ume_ne_id);
  const placeholder = !isRegion && isPlaceholderSource(data.managed_source, data.ne_ip);
  const showBadge = placeholder && showPlaceholderBadge;
  const name = data.label || (!hideIp ? data.ne_ip : "") || (isRegion ? "Region" : "NE");
  const secondary = isRegion
    ? []
    : [
        hideIp || !data.ne_ip || data.ne_ip === name ? "" : data.ne_ip,
        hideVendor || !data.vendor ? "" : data.vendor,
      ].filter(Boolean);

  if (!isRegion && worldVisualLod === "dot") {
    return (
      <div
        className={`topo-node topo-node--dot topo-node--${tone}${selected ? " is-selected" : ""}`}
        title={name}
      >
        <Handle type="target" position={Position.Left} className="topo-node__handle topo-node__handle--dot" />
        <Handle type="source" position={Position.Right} className="topo-node__handle topo-node__handle--dot" />
        <span className="topo-node__pixel" aria-hidden="true" />
      </div>
    );
  }
  if (!isRegion && worldVisualLod === "pin") {
    return (
      <div
        className={`topo-node topo-node--pin topo-node--${tone}${selected ? " is-selected" : ""}`}
        title={name}
      >
        <Handle type="target" position={Position.Left} className="topo-node__handle topo-node__handle--dot" />
        <Handle type="source" position={Position.Right} className="topo-node__handle topo-node__handle--dot" />
        <span className="topo-node__pin" aria-hidden="true" />
      </div>
    );
  }

  return (
    <div
      className={`topo-node topo-node--${tone}${selected ? " is-selected" : ""}${
        connectMode && !isRegion ? " is-connect-mode" : ""
      }${showBadge ? " is-placeholder" : ""}${isRegion ? " is-region" : ""}`}
      title={isRegion ? name : showBadge ? data.managed_source || "placeholder" : name}
    >
      <div className="topo-node__glyph">
        <Handle
          type="target"
          position={Position.Left}
          className="topo-node__handle topo-node__handle--center"
          isConnectable={connectMode && !isRegion}
        />
        <Handle
          type="source"
          position={Position.Right}
          className="topo-node__handle topo-node__handle--center"
          isConnectable={connectMode && !isRegion}
        />
        {!isRegion ? <RouterIcon /> : <RegionCanvasIcon />}
        {showBadge ? (
          <span className="topo-node__badge" aria-hidden>
            {String(data.managed_source || "ph").slice(0, 4)}
          </span>
        ) : null}
      </div>
      <div className="topo-node__caption">
        <span className="topo-node__caption-name">{name}</span>
        {secondary.length ? (
          <span className="topo-node__caption-meta">{secondary.join(SEP)}</span>
        ) : null}
      </div>
    </div>
  );
});

const nodeTypes = { neNode: NeNode };
const edgeTypes = { topoParallel: ParallelEdge };

const SNAP_GRID: [number, number] = [16, 16];
const FIT_VIEW_OPTS = { padding: 0.2, includeHiddenNodes: true } as const;

function canvasBgRgb(bg: string): { r: number; g: number; b: number } | null {
  const hex = String(bg || "").replace("#", "").trim();
  if (hex.length !== 6) return null;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  if (![r, g, b].every((n) => Number.isFinite(n))) return null;
  return { r, g, b };
}

function canvasDotColor(bg: string): string {
  const rgb = canvasBgRgb(bg);
  if (!rgb) return "#93c5fd";
  const lum = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
  return lum > 0.55 ? "#93c5fd" : "#64748b";
}

/** MiniMap colors follow the canvas bg so a light map doesn't get a dark inset. */
function minimapTheme(bg: string): {
  bgColor: string;
  nodeColor: string;
  nodeStrokeColor: string;
  maskColor: string;
  maskStrokeColor: string;
} {
  const rgb = canvasBgRgb(bg) || { r: 11, g: 18, b: 32 };
  const lum = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
  const light = lum > 0.55;
  return {
    bgColor: bg || (light ? "#e8eef6" : "#0b1220"),
    nodeColor: "#3b82f6",
    nodeStrokeColor: light ? "#93c5fd" : "#1e3a5f",
    maskColor: light ? "rgba(15, 23, 42, 0.14)" : "rgba(2, 8, 23, 0.62)",
    maskStrokeColor: light ? "rgba(59, 130, 246, 0.55)" : "rgba(148, 163, 184, 0.45)",
  };
}

function worldDisplayBounds(wt: TopologyWorldTransform | null | undefined): {
  x: number;
  y: number;
  width: number;
  height: number;
} | null {
  if (!wt) return null;
  const scale = Number(wt.scale) || 1;
  const width = Math.max(1, (Number(wt.full_max_x) - Number(wt.full_min_x)) * scale);
  const height = Math.max(1, (Number(wt.full_max_y) - Number(wt.full_min_y)) * scale);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  return { x: 0, y: 0, width, height };
}

type CtxMenuLike =
  | { kind: "node"; id: string; x: number; y: number }
  | { kind: "edge"; id: string; x: number; y: number }
  | { kind: "selection"; x: number; y: number }
  | { kind: "pane"; x: number; y: number; flowX: number; flowY: number };

export type TopologyReactFlowViewProps = {
  displayOpts: TopoDisplayOpts;
  nodes: Node<NeNodeData>[];
  setNodes: Dispatch<SetStateAction<Node<NeNodeData>[]>>;
  edges: Edge[];
  setEdges: Dispatch<SetStateAction<Edge[]>>;
  displayEdges: Edge[];
  searchHitIds: string[];
  isWorldFlatCanvas: boolean;
  worldVisualLod: "dot" | "pin" | "full";
  toolBehavior: ToolModeBehavior;
  toolMode: ToolMode;
  snapToGrid: boolean;
  canvasBg: string;
  worldScatter: WorldScatterPoint[];
  showWorldScatter: boolean;
  fullscreen: boolean;
  connectHint: string;
  fullscreenLabel: string;
  exitFullscreenLabel: string;
  worldTransform: TopologyWorldTransform | null | undefined;
  nodesRef: { current: Node<NeNodeData>[] };
  rfRef: { current: ReactFlowInstance<Node<NeNodeData>, Edge> | null };
  pendingFitRef: { current: boolean };
  markDirty: () => void;
  pushHistory: () => void;
  onConnect: (connection: Connection) => void;
  isValidConnection: (connection: Connection | Edge) => boolean;
  onNodeClick: (e: React.MouseEvent, node: Node<NeNodeData>) => void;
  onNodeDoubleClick: (e: React.MouseEvent, node: Node<NeNodeData>) => void;
  focusEdge: (id: string) => void;
  focusNode: (id: string, additive: boolean) => void;
  clearSelection: () => void;
  setCtxMenu: (menu: CtxMenuLike | null) => void;
  placeCtxMenu: (
    x: number,
    y: number,
    size?: { w?: number; h?: number },
  ) => { x: number; y: number };
  selectedNodeIds: string[];
  closeCtxMenu: () => void;
  setCanvasZoom: (z: number) => void;
  scheduleFlatViewportRefresh: () => void;
  toggleFullscreen: () => void | Promise<void>;
  readOnly?: boolean;
};

export default function TopologyReactFlowView(props: TopologyReactFlowViewProps) {
  const {
    displayOpts,
    nodes,
    setNodes,
    setEdges,
    displayEdges,
    searchHitIds,
    isWorldFlatCanvas,
    worldVisualLod,
    toolBehavior,
    toolMode,
    snapToGrid,
    canvasBg,
    worldScatter,
    showWorldScatter,
    fullscreen,
    connectHint,
    fullscreenLabel,
    exitFullscreenLabel,
    worldTransform,
    nodesRef,
    rfRef,
    pendingFitRef,
    markDirty,
    pushHistory,
    onConnect,
    isValidConnection,
    onNodeClick,
    onNodeDoubleClick,
    focusEdge,
    focusNode,
    clearSelection,
    setCtxMenu,
    placeCtxMenu,
    selectedNodeIds,
    closeCtxMenu,
    setCanvasZoom,
    scheduleFlatViewportRefresh,
    toggleFullscreen,
  } = props;

  return (
    <TopoDisplayContext.Provider value={displayOpts}>
      <ReactFlow
        nodes={
          isWorldFlatCanvas && worldVisualLod !== "full"
            ? []
            : nodes.map((n) => {
                const isRegion = n.data.kind === "region";
                const lod = isWorldFlatCanvas ? worldVisualLod : "full";
                const sized =
                  !isRegion && lod === "dot"
                    ? { ...n, width: 6, height: 6 }
                    : !isRegion && lod === "pin"
                      ? { ...n, width: 14, height: 14 }
                      : n;
                return searchHitIds.includes(n.id)
                  ? { ...sized, selected: true, className: "is-search-hit" }
                  : { ...sized, className: undefined };
              })
        }
        edges={isWorldFlatCanvas && worldVisualLod !== "full" ? [] : displayEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onlyRenderVisibleElements={isWorldFlatCanvas ? worldVisualLod === "dot" : true}
        connectionMode={ConnectionMode.Loose}
        connectionLineType={ConnectionLineType.Straight}
        connectionLineStyle={{ stroke: "#38bdf8", strokeWidth: 2 }}
        defaultEdgeOptions={{ type: "straight", labelShowBg: false }}
        proOptions={{ hideAttribution: true }}
        // Composed metro canvases can span 40k–70k; default 0.05 clips fitView.
        minZoom={isWorldFlatCanvas || nodes.length >= 400 ? 0.002 : 0.05}
        maxZoom={isWorldFlatCanvas ? 12 : 4}
        nodesDraggable={
          isWorldFlatCanvas
            ? worldVisualLod === "full" && toolBehavior.nodesDraggable
            : toolBehavior.nodesDraggable
        }
        nodesConnectable={toolBehavior.nodesConnectable}
        elementsSelectable={toolBehavior.elementsSelectable}
        panOnDrag={toolBehavior.panOnDrag}
        selectionOnDrag={toolBehavior.selectionOnDrag}
        panOnScroll={toolBehavior.panOnScroll}
        selectionMode={SelectionMode.Partial}
        multiSelectionKeyCode="Shift"
        snapToGrid={snapToGrid}
        snapGrid={SNAP_GRID}
        onNodeDragStart={() => {
          pushHistory();
        }}
        onNodesChange={(changes: NodeChange<Node<NeNodeData>>[]) => {
          if (changes.some((c) => c.type === "position" || c.type === "remove" || c.type === "add")) {
            markDirty();
          }
          setNodes((nds) => applyNodeChanges(changes, nds));
        }}
        onEdgesChange={(changes: EdgeChange<Edge>[]) => {
          const physical = changes.filter((c) => {
            if (c.type === "select") return false;
            if ("id" in c && isAggregateEdgeId(String(c.id))) return false;
            return true;
          });
          if (physical.some((c) => c.type !== "select")) {
            markDirty();
          }
          if (physical.length) setEdges((eds) => applyEdgeChanges(physical, eds));
        }}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
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
          // Modest estimate; useLayoutEffect measures real height and clamps into state.
          const pos = placeCtxMenu(e.clientX, e.clientY, { w: 280, h: 320 });
          setCtxMenu({ kind: "edge", id: edge.id, ...pos });
        }}
        onSelectionContextMenu={(e) => {
          e.preventDefault();
          const pos = placeCtxMenu(e.clientX, e.clientY);
          setCtxMenu({ kind: "selection", ...pos });
        }}
        onPaneContextMenu={(e) => {
          e.preventDefault();
          if (!rfRef.current) return;
          const flow = rfRef.current.screenToFlowPosition({
            x: e.clientX,
            y: e.clientY,
          });
          const pos = placeCtxMenu(e.clientX, e.clientY, { w: 180, h: 100 });
          setCtxMenu({
            kind: "pane",
            ...pos,
            flowX: flow.x,
            flowY: flow.y,
          });
        }}
        onMoveStart={closeCtxMenu}
        onMove={(_e, vp) => {
          if (isWorldFlatCanvas) setCanvasZoom(vp.zoom);
        }}
        onMoveEnd={(_e, vp) => {
          if (!isWorldFlatCanvas) return;
          setCanvasZoom(vp.zoom);
          scheduleFlatViewportRefresh();
        }}
        onInit={(inst) => {
          rfRef.current = inst as ReactFlowInstance<Node<NeNodeData>, Edge>;
          if (pendingFitRef.current) {
            pendingFitRef.current = false;
            window.requestAnimationFrame(() => {
              const bounds = isWorldFlatCanvas ? worldDisplayBounds(worldTransform) : null;
              if (bounds) {
                inst.fitBounds(bounds, { padding: 0.12, duration: 0 });
              } else if (nodesRef.current.length > 0) {
                inst.fitView({ ...FIT_VIEW_OPTS, duration: 0 });
              }
              setCanvasZoom(inst.getZoom());
            });
          }
        }}
        fitView={false}
        deleteKeyCode={null}
        edgesFocusable
      >
        <WorldScatterLayer
          points={worldScatter}
          mode={worldVisualLod === "pin" ? "pin" : "dot"}
          visible={showWorldScatter}
        />
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color={canvasDotColor(canvasBg)}
          bgColor={canvasBg}
        />
        {toolMode === "connect" ? (
          <div className="topo-mode-hint" role="status">
            {connectHint}
          </div>
        ) : null}
        <Controls showInteractive>
          <ControlButton
            className="topo-fs-control"
            onClick={() => void toggleFullscreen()}
            title={fullscreen ? exitFullscreenLabel : fullscreenLabel}
            aria-label={fullscreen ? exitFullscreenLabel : fullscreenLabel}
          >
            <FullscreenIcon exit={fullscreen} />
          </ControlButton>
        </Controls>
        {!isWorldFlatCanvas ? (
          <MiniMap
            pannable
            zoomable
            {...minimapTheme(canvasBg)}
            nodeStrokeWidth={1}
            maskStrokeWidth={1}
          />
        ) : null}
      </ReactFlow>
    </TopoDisplayContext.Provider>
  );
}
