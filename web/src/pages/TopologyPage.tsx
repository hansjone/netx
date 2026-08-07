import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
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
  ConnectionLineType,
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
  addTopologyViewNodes,
  createFabricManualEdge,
  createTopologyFolder,
  createTopologyPlaceholder,
  deleteFabricEdges,
  purgePlaceholderFabricNodes,
  deleteTopologyFolder,
  deleteTopologyMap,
  fetchLldpCollectDashboard,
  fetchLldpDiscoverJob,
  fetchManagedNe,
  fetchManagedNeById,
  fetchTopologyGraph,
  fetchTopologyTree,
  fetchTopologyWorld,
  fetchUmeNe,
  patchTopologyEdgeStyle,
  patchTopologyPositions,
  projectTopologyNeighbors,
  removeTopologyViewNodes,
  searchFabricNodes,
  startLldpDiscover,
  stopLldpCollectJob,
  updateLldpCollectPolicy,
  updateTopologyFolder,
  updateTopologyMap,
  applyUmeTopologyToFabric,
} from "../services/api";
import { queryKeys } from "../constants/queryKeys";
import { HelpHint } from "../components/HelpHint";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { openOrFocusModule } from "../utils/moduleWindows";
import { WorldScatterLayer } from "./topology/WorldScatterLayer";
import type {
  FabricNodeSearchHit,
  TopologyDiscoverJob,
  TopologyDiscoverNeResult,
  TopologyDiscoverOut,
  TopologyEdgeItem,
  TopologyNodeItem,
  TopologyTreeFolderItem,
  TopologyTreeViewItem,
  TopologyViewEdgeItem,
  TopologyViewGraph,
  TopologyViewNodeItem,
  TopologyViewRole,
  TopologyWorldTransform,
} from "../types";
import { alignNodes, layoutGraph, type LayoutKind } from "./topology/layoutGraph";
import { ParallelEdge } from "./topology/ParallelEdge";
import {
  buildLinkDisplayEdges,
  formatPortPairLabel,
  isAggregateEdgeId,
  physicalIdsForDisplayEdge,
  type LinkEdgeData,
  type LinkMember,
} from "./topology/linkDisplay";
import { behaviorForMode, toolModeFromKey, type ToolMode } from "./topology/toolMode";

const LAST_LEAF_KEY = "netx.topology.lastLeafViewId";
const TREE_EXPAND_KEY = "netx.topology.treeExpanded";

function findViewInRegion(
  regions: TopologyTreeFolderItem[],
  viewId: string,
): { region: TopologyTreeFolderItem; view: TopologyTreeViewItem } | null {
  for (const region of regions) {
    const view = (region.views || []).find((v) => v.id === viewId);
    if (view) return { region, view };
    const nested = findViewInRegion(region.children || [], viewId);
    if (nested) return nested;
  }
  return null;
}

function findFolderInTree(
  folders: TopologyTreeFolderItem[],
  folderId: string,
): TopologyTreeFolderItem | null {
  for (const folder of folders) {
    if (folder.id === folderId) return folder;
    const nested = findFolderInTree(folder.children || [], folderId);
    if (nested) return nested;
  }
  return null;
}

function indexFoldersById(
  folders: TopologyTreeFolderItem[],
  acc: Map<string, TopologyTreeFolderItem> = new Map(),
): Map<string, TopologyTreeFolderItem> {
  for (const folder of folders) {
    acc.set(folder.id, folder);
    indexFoldersById(folder.children || [], acc);
  }
  return acc;
}

/** Self + ancestors (rootward), for expanding the left tree path. */
function folderPathIds(
  folders: TopologyTreeFolderItem[],
  folderId: string,
): string[] {
  const byId = indexFoldersById(folders);
  const path: string[] = [];
  let cur: string | undefined = folderId;
  const seen = new Set<string>();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    path.push(cur);
    const folder = byId.get(cur);
    const parent = String(folder?.parent_id || "").trim();
    // Stop at topology root (kind=root) — regions list only contains region folders.
    if (!folder || !parent || !byId.has(parent)) break;
    cur = parent;
  }
  return path;
}

/** UME World container only — hex nav, not a canvas. */
function isUmeWorldContainer(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  const ext = String(folder.external_ref || "").trim();
  return ext === "ume:world" || folder.name === "UME World";
}

/** UME World / SBN nav folders share drill canvases; manual regions keep their own maps. */
function isUmeWorldNavFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  const ext = String(folder.external_ref || "").trim();
  if (isUmeWorldContainer(folder) || ext === "ume:world:drill") return true;
  return Boolean(ext);
}

function isWorldDrillFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  const ext = String(folder.external_ref || "").trim();
  return ext === "ume:world:drill" || (folder.name === "World" && Boolean(folder.is_system));
}

function isWorldFlatViewName(name: string | undefined | null): boolean {
  const n = String(name || "").trim();
  return n === "世界地图" || n === "完整世界地图" || n === "World map";
}

function displayViewName(name: string | undefined | null, t: (key: string) => string): string {
  if (isWorldFlatViewName(name)) return t("topology.worldMapName");
  return String(name || "").trim();
}

/** UME structural folders (not user-deletable): container + World drill. */
function isUmeStructuralFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  if (isUmeWorldContainer(folder) || isWorldDrillFolder(folder)) return true;
  const ext = String(folder.external_ref || "").trim();
  return ext === "ume:world" || ext === "ume:world:drill";
}

/** Auto「根图」under a manual 根 — rename OK, delete only with parent 根. */
function isManualRootMapFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder || isUmeStructuralFolder(folder)) return false;
  return Boolean(folder.is_system) && !String(folder.external_ref || "").trim();
}

/** UME-synced SBN under World — deletable like a manual sub-region. */
function isUmeSyncedSubRegion(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder || isUmeStructuralFolder(folder)) return false;
  return Boolean(String(folder.external_ref || "").trim());
}

/** Visual LOD for 1:1 world coords (~1e5 span). fitView overview is ~0.01. */
function worldVisualLodFromZoom(zoom: number): "dot" | "pin" | "full" {
  if (zoom < 0.04) return "dot";
  if (zoom < 0.12) return "pin";
  return "full";
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

/**
 * Region row IS the canvas for nested / UME SBN / World drill.
 * Top-level under Network root (and UME World container) are hex-nav only — same as UME World.
 */
function isRegionCanvasFolder(
  folder: TopologyTreeFolderItem | null | undefined,
  rootId?: string,
): boolean {
  if (!folder) return false;
  if (isUmeWorldContainer(folder)) return false;
  const rid = String(rootId || "").trim();
  const parent = String(folder.parent_id || "").trim();
  if (rid && parent === rid) return false;
  return true;
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="12"
      height="12"
      aria-hidden="true"
      className="topo-svg-icon"
      style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.12s ease" }}
    >
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9 6l6 6-6 6"
      />
    </svg>
  );
}

function regionDisplayName(region: TopologyTreeFolderItem | null | undefined): string {
  if (!region) return "";
  return region.name || "";
}

function formatUpdatedAt(value?: string | null): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

const SNAP_GRID: [number, number] = [16, 16];

/**
 * onlyRenderVisibleElements leaves off-screen nodes unmeasured; default fitView
 * skips those. includeHiddenNodes uses declared width/height so fit still works.
 */
const FIT_VIEW_OPTS = { padding: 0.2, includeHiddenNodes: true } as const;
/** Zoom after locate on flat world — neighborhood visible with full icons, not a lone pin. */
const WORLD_LOCATE_ZOOM = 0.22;
/** Half-extent (display/world units) fetched around the target on locate. */
const WORLD_LOCATE_HALF = 12000;
/** Soft cap for accumulated flat-world tiles (Google-Earth style keep-alive). */
const WORLD_FLAT_ACCUM_CAP = 8000;

/** Merge newly fetched flat LOD into the existing cache so visited areas stay. */
function mergeFlatWorldGraph(
  prev: TopologyViewGraph | undefined,
  next: TopologyViewGraph,
  opts?: { centerX?: number; centerY?: number },
): TopologyViewGraph {
  const scatter = next.scatter?.length ? next.scatter : prev?.scatter;
  if (!prev?.nodes?.length) {
    return scatter?.length && !next.scatter?.length ? { ...next, scatter } : next;
  }
  // Overview / zoom-out payloads have empty RF nodes — keep (or refresh) scatter,
  // drop heavy tiles when the server intentionally cleared them.
  if (!next?.nodes?.length) {
    return {
      ...prev,
      view: next.view || prev.view,
      nodes: [],
      edges: [],
      scatter: scatter || [],
      world_transform: next.world_transform ?? prev.world_transform,
      truncated: Boolean(next.truncated || prev.truncated),
      truncate_reason: next.truncate_reason || prev.truncate_reason || "",
      outside_peers: next.outside_peers?.length ? next.outside_peers : prev.outside_peers,
    };
  }

  const nodeMap = new Map<string, TopologyViewNodeItem>();
  for (const n of prev.nodes) nodeMap.set(String(n.fabric_node_id), n);
  for (const n of next.nodes) nodeMap.set(String(n.fabric_node_id), n);

  const edgeMap = new Map<string, TopologyViewEdgeItem>();
  for (const e of prev.edges || []) edgeMap.set(String(e.id), e);
  for (const e of next.edges || []) edgeMap.set(String(e.id), e);

  let nodes = [...nodeMap.values()];
  const cx = opts?.centerX;
  const cy = opts?.centerY;
  if (nodes.length > WORLD_FLAT_ACCUM_CAP && cx != null && cy != null) {
    const prefer = new Set(next.nodes.map((n) => String(n.fabric_node_id)));
    const kept = next.nodes.slice();
    const extras = nodes
      .filter((n) => !prefer.has(String(n.fabric_node_id)))
      .sort((a, b) => {
        const da = (Number(a.x) - cx) ** 2 + (Number(a.y) - cy) ** 2;
        const db = (Number(b.x) - cx) ** 2 + (Number(b.y) - cy) ** 2;
        return da - db;
      });
    const room = Math.max(0, WORLD_FLAT_ACCUM_CAP - kept.length);
    nodes = kept.concat(extras.slice(0, room));
  }

  const idSet = new Set(nodes.map((n) => String(n.fabric_node_id)));
  const edges = [...edgeMap.values()].filter(
    (e) => idSet.has(String(e.a_node_id)) && idSet.has(String(e.b_node_id)),
  );

  return {
    ...next,
    view: next.view || prev.view,
    nodes,
    edges,
    world_transform: next.world_transform ?? prev.world_transform,
    scatter: scatter || [],
    truncated: Boolean(prev.truncated || next.truncated),
    truncate_reason: next.truncate_reason || prev.truncate_reason || "",
    outside_peers: next.outside_peers?.length ? next.outside_peers : prev.outside_peers,
  };
}
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

/** ASCII-safe separators ? avoid Unicode middots that corrupt on some editors. */
const SEP = " / ";

/** Left-panel collapse / expand control (simple chevron). */
function SidebarFoldIcon({ expand }: { expand?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        d={expand ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"}
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        fill="currentColor"
        d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"
      />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        d="M12 5v14M5 12h14"
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" className="topo-svg-icon">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        d="M6 6l12 12M18 6L6 18"
      />
    </svg>
  );
}

function RegionGlyph({ size = 16 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
      <path
        d="M4 8.5L12 4l8 4.5v7L12 20l-8-4.5v-7z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M12 4v16M4 8.5l8 4.5 8-4.5" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.7" />
    </svg>
  );
}

function LayerGlyph({ role, size = 16 }: { role?: string; size?: number }) {
  const r = String(role || "core").toLowerCase();
  if (r === "aggregation") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
        <rect x="4" y="5" width="16" height="4" rx="1.5" fill="currentColor" opacity="0.9" />
        <rect x="6" y="11" width="12" height="3.5" rx="1.2" fill="currentColor" opacity="0.65" />
        <rect x="8" y="16.5" width="8" height="3" rx="1" fill="currentColor" opacity="0.45" />
      </svg>
    );
  }
  if (r === "access") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
        <circle cx="7" cy="12" r="2.4" fill="currentColor" />
        <circle cx="17" cy="7" r="2.2" fill="currentColor" opacity="0.8" />
        <circle cx="17" cy="17" r="2.2" fill="currentColor" opacity="0.8" />
        <path d="M9.4 12H14.5M14.5 12L16 8.4M14.5 12L16 15.6" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" className="topo-tree__glyph-svg">
      <rect x="7" y="3.5" width="10" height="17" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="12" cy="8" r="2.2" fill="currentColor" />
      <path d="M9 13.5h6M9 16.5h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
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
  managed_source?: string;
  /** Canvas navigation node (not a fabric NE). */
  kind?: "ne" | "region" | "layer";
  folder_id?: string;
  view_id?: string;
  role?: TopologyViewRole | string;
  subtitle?: string;
  node_count?: number;
};

type HistorySnap = {
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  pendingEdgeDeletes: string[];
  pendingEdgeCreates: string[];
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
  connectMode: boolean;
  /** Show TOPO/LLDP placeholder corner badge on nodes. */
  showPlaceholderBadge: boolean;
  /** Flat world map visual LOD (Google-Earth style). */
  worldVisualLod: "dot" | "pin" | "full";
};

type CtxMenu =
  | { kind: "node"; id: string; x: number; y: number }
  | { kind: "edge"; id: string; x: number; y: number }
  | { kind: "selection"; x: number; y: number }
  | { kind: "pane"; x: number; y: number; flowX: number; flowY: number };

const TopoDisplayContext = createContext<TopoDisplayOpts>({
  hideIp: true,
  hideVendor: true,
  hidePorts: true,
  connectMode: false,
  showPlaceholderBadge: false,
  worldVisualLod: "full",
});

function newId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
}

/** Client-only edge id until Save calls createFabricManualEdge. */
function newLocalEdgeId(): string {
  return `local:${newId()}`;
}

function isLocalPendingEdgeId(id: string): boolean {
  return String(id || "").startsWith("local:");
}

/**
 * Icon tone for /topo/ne-router.png (base = blue).
 * Unmanaged (no managed/ume) and unknown vendors → gray.
 * UME-linked NEs default to ZTE when vendor is empty/unknown.
 */
function nodeIconTone(vendor: string, managedNeId: string, umeNeId = ""): string {
  const hasManaged = Boolean(String(managedNeId || "").trim());
  const hasUme = Boolean(String(umeNeId || "").trim());
  if (!hasManaged && !hasUme) return "gray";
  const v = String(vendor || "").trim().toLowerCase();
  if (!v || v === "other" || v === "unknown" || v === "generic") {
    return hasUme ? "zte" : "gray";
  }
  if (v.includes("cisco")) return "cisco";
  if (v.includes("huawei")) return "huawei";
  if (v.includes("zte")) return "zte";
  if (v.includes("juniper")) return "juniper";
  if (v.includes("nokia") || v.includes("alcatel")) return "nokia";
  if (v.includes("ericsson")) return "ericsson";
  if (v.includes("h3c") || v.includes("comware")) return "h3c";
  if (v.includes("ruijie") || v.includes("锐捷")) return "ruijie";
  if (v.includes("mikrotik")) return "mikrotik";
  return hasUme ? "zte" : "gray";
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

/**
 * Keep the PNG’s white texture/highlights; tint brand color via mix-blend-mode.
 * Color comes from --topo-icon-color (vendor palette in Display settings).
 */
function RouterIcon() {
  return (
    <span className="topo-node__icon" aria-hidden="true">
      <img className="topo-node__icon-art" src="/topo/ne-router.png" alt="" draggable={false} />
      <span className="topo-node__icon-tint" />
    </span>
  );
}

/** Canvas sub-region building art (UME-style); no vendor tint. */
function RegionCanvasIcon() {
  return (
    <span className="topo-node__icon topo-node__icon--region" aria-hidden="true">
      <img
        className="topo-node__icon-art"
        src="/topo/region-building.png"
        alt=""
        draggable={false}
      />
    </span>
  );
}

/** Fixed box for onlyRenderVisibleElements (xyflow skips off-screen mount when sized + handles set).
 * Caption is absolutely positioned under the glyph so the RF box is icon-only. */
const TOPO_NODE_W = 80;
const TOPO_NODE_H = 25;
/** Must match `.topo-node__glyph` (25×25, top-centered) + `--center` handles. */
const TOPO_ICON = 25;
/** region-building.png is 195×133 — keep aspect so icon center == handle. */
const TOPO_REGION_ICON_H = Math.round((TOPO_ICON * 133) / 195);
const TOPO_HANDLE_X = TOPO_NODE_W / 2;
const TOPO_HANDLE_Y = TOPO_ICON / 2;
const TOPO_REGION_HANDLE_X = TOPO_NODE_W / 2;
const TOPO_REGION_HANDLE_Y = TOPO_REGION_ICON_H / 2;

/** API x/y is icon center; RF position is top-left of the node box. */
function iconFlowPosition(
  apiX: number,
  apiY: number,
  handleX: number,
  handleY: number,
): { x: number; y: number } {
  return { x: apiX - handleX, y: apiY - handleY };
}

function iconApiPosition(
  flowX: number,
  flowY: number,
  handleX: number,
  handleY: number,
): { x: number; y: number } {
  return { x: flowX + handleX, y: flowY + handleY };
}

function regionFlowPosition(apiX: number, apiY: number): { x: number; y: number } {
  return iconFlowPosition(apiX, apiY, TOPO_REGION_HANDLE_X, TOPO_REGION_HANDLE_Y);
}

function regionApiPosition(flowX: number, flowY: number): { x: number; y: number } {
  return iconApiPosition(flowX, flowY, TOPO_REGION_HANDLE_X, TOPO_REGION_HANDLE_Y);
}

function neFlowPosition(apiX: number, apiY: number): { x: number; y: number } {
  return iconFlowPosition(apiX, apiY, TOPO_HANDLE_X, TOPO_HANDLE_Y);
}

function neApiPosition(flowX: number, flowY: number): { x: number; y: number } {
  return iconApiPosition(flowX, flowY, TOPO_HANDLE_X, TOPO_HANDLE_Y);
}

function isPlaceholderSource(source: string | undefined, neIp: string): boolean {
  const src = String(source || "").trim().toLowerCase();
  if (src === "lldp" || src === "topology") return true;
  return !String(neIp || "").trim() && Boolean(src);
}

const NeNode = memo(function NeNode({ data, selected }: NodeProps<Node<NeNodeData>>) {
  const { hideIp, hideVendor, connectMode, showPlaceholderBadge, worldVisualLod } =
    useContext(TopoDisplayContext);
  const isRegion = data.kind === "region";
  const tone = isRegion ? "region" : nodeIconTone(data.vendor, data.managed_ne_id, data.ume_ne_id);
  const placeholder = !isRegion && isPlaceholderSource(data.managed_source, data.ne_ip);
  const showBadge = placeholder && showPlaceholderBadge;
  const name = data.label || (!hideIp ? data.ne_ip : "") || (isRegion ? "Region" : "NE");
  // Region count stays in the title "(N)"; do not repeat under the icon.
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
      title={
        isRegion
          ? "Open region canvas"
          : showBadge
            ? data.managed_source || "placeholder"
            : name
      }
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

type EdgeStyleData = LinkEdgeData;

type EdgeLineStyle = "solid" | "dashed" | "dotted";
type EdgeSourceKind = "manual" | "discovered" | "stale";

type EdgeDefaultStyle = {
  stroke_color: string;
  stroke_width: number;
  line_style: EdgeLineStyle;
};

type EdgeDefaults = Record<EdgeSourceKind, EdgeDefaultStyle>;

const EDGE_DEFAULTS_KEY = "netx.topology.edgeDefaults";
const AUTO_LAYOUT_DISCOVER_KEY = "netx.topology.autoLayoutAfterDiscover";
const DISCOVER_AUTO_ADD_KEY = "netx.topology.discoverAutoAddUnmatched.v2";
const DISCOVER_PROJECT_NEIGHBORS_KEY = "netx.topology.discoverProjectNeighbors.v2";
const SCALE_BUNDLE_WIDTH_KEY = "netx.topology.scaleBundleWidth";
const SHOW_PLACEHOLDER_BADGE_KEY = "netx.topology.showPlaceholderBadge";
const CANVAS_BG_KEY = "netx.topology.canvasBg";
const DEFAULT_CANVAS_BG = "#0f172a";
/** Previous light default — migrate so existing sessions pick up dark canvas. */
const LEGACY_CANVAS_BG = "#dbeafe";
const LABEL_COLORS_KEY = "netx.topology.labelColors";
const VENDOR_COLORS_KEY = "netx.topology.vendorColors";

const VENDOR_TONE_KEYS = [
  "cisco",
  "huawei",
  "zte",
  "juniper",
  "nokia",
  "ericsson",
  "h3c",
  "ruijie",
  "mikrotik",
  "gray",
] as const;
type VendorToneKey = (typeof VENDOR_TONE_KEYS)[number];
type VendorColors = Record<VendorToneKey, string>;
type LabelColors = { name: string; edgeLabel: string };

const DEFAULT_LABEL_COLORS: LabelColors = {
  name: "#f1f5f9",
  edgeLabel: "#e2e8f0",
};

const DEFAULT_VENDOR_COLORS: VendorColors = {
  cisco: "#049fd9",
  huawei: "#cf0a2c",
  zte: "#0091da",
  juniper: "#84b135",
  nokia: "#124191",
  ericsson: "#1e3a5f",
  h3c: "#7ac143",
  ruijie: "#7c3aed",
  mikrotik: "#ea580c",
  gray: "#94a3b8",
};

function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}

function loadCanvasBg(): string {
  try {
    const raw = String(localStorage.getItem(CANVAS_BG_KEY) || "").trim().toLowerCase();
    if (raw === LEGACY_CANVAS_BG) {
      persistCanvasBg(DEFAULT_CANVAS_BG);
      return DEFAULT_CANVAS_BG;
    }
    if (isHexColor(raw)) return raw;
  } catch {
    /* ignore */
  }
  return DEFAULT_CANVAS_BG;
}

function persistCanvasBg(value: string) {
  try {
    localStorage.setItem(CANVAS_BG_KEY, value);
  } catch {
    /* ignore */
  }
}

function loadLabelColors(): LabelColors {
  try {
    const raw = localStorage.getItem(LABEL_COLORS_KEY);
    if (!raw) return { ...DEFAULT_LABEL_COLORS };
    const parsed = JSON.parse(raw) as Partial<LabelColors>;
    return {
      name: isHexColor(String(parsed.name || "")) ? String(parsed.name).toLowerCase() : DEFAULT_LABEL_COLORS.name,
      edgeLabel: isHexColor(String(parsed.edgeLabel || ""))
        ? String(parsed.edgeLabel).toLowerCase()
        : DEFAULT_LABEL_COLORS.edgeLabel,
    };
  } catch {
    return { ...DEFAULT_LABEL_COLORS };
  }
}

function persistLabelColors(value: LabelColors) {
  try {
    localStorage.setItem(LABEL_COLORS_KEY, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

function loadVendorColors(): VendorColors {
  try {
    const raw = localStorage.getItem(VENDOR_COLORS_KEY);
    if (!raw) return { ...DEFAULT_VENDOR_COLORS };
    const parsed = JSON.parse(raw) as Partial<VendorColors>;
    const out = { ...DEFAULT_VENDOR_COLORS };
    for (const key of VENDOR_TONE_KEYS) {
      const c = String(parsed?.[key] || "").trim().toLowerCase();
      if (isHexColor(c)) out[key] = c;
    }
    return out;
  } catch {
    return { ...DEFAULT_VENDOR_COLORS };
  }
}

function persistVendorColors(value: VendorColors) {
  try {
    localStorage.setItem(VENDOR_COLORS_KEY, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

function canvasDotColor(bg: string): string {
  const hex = String(bg || "").replace("#", "");
  if (hex.length !== 6) return "#93c5fd";
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.55 ? "#93c5fd" : "#64748b";
}

function loadBoolFlag(key: string, defaultValue: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return defaultValue;
    return raw === "1" || raw === "true";
  } catch {
    return defaultValue;
  }
}

function persistBoolFlag(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function loadAutoLayoutAfterDiscover(): boolean {
  return loadBoolFlag(AUTO_LAYOUT_DISCOVER_KEY, false);
}

function persistAutoLayoutAfterDiscover(value: boolean) {
  persistBoolFlag(AUTO_LAYOUT_DISCOVER_KEY, value);
}

const BUILTIN_EDGE_DEFAULTS: EdgeDefaults = {
  manual: { stroke_color: "#64748b", stroke_width: 2, line_style: "solid" },
  discovered: { stroke_color: "#0ea5e9", stroke_width: 2, line_style: "dashed" },
  stale: { stroke_color: "#dc2626", stroke_width: 2, line_style: "dashed" },
};

function sourceKind(source: string): EdgeSourceKind {
  const src = (source || "manual").toLowerCase();
  if (src === "stale" || src === "missing") return "stale";
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

function withEdgeVisual(edge: Edge, defaults: EdgeDefaults): Edge {
  const data = (edge.data || {}) as EdgeStyleData;
  const style = resolveEdgeStyle(data, defaults);
  // RF EdgeText defaults labelShowBg=true with a white rect — keep digits only.
  return { ...edge, style, markerEnd: undefined, labelShowBg: false };
}

function graphToFlow(
  nodes: TopologyViewNodeItem[],
  edges: TopologyViewEdgeItem[],
  defaults: EdgeDefaults,
) {
  const rfNodes: Node<NeNodeData>[] = nodes.map((n) => {
    const isRegion =
      n.kind === "region" || n.device_type === "region" || String(n.fabric_node_id || "").startsWith("region:");
    const apiX = Number(n.x) || 0;
    const apiY = Number(n.y) || 0;
    // API coords are icon-center (UME + region); RF position is top-left of the icon box.
    const position = isRegion
      ? regionFlowPosition(apiX, apiY)
      : neFlowPosition(apiX, apiY);
    const hx = isRegion ? TOPO_REGION_HANDLE_X : TOPO_HANDLE_X;
    const hy = isRegion ? TOPO_REGION_HANDLE_Y : TOPO_HANDLE_Y;
    return {
    id: n.fabric_node_id,
    type: "neNode",
    position,
    width: TOPO_NODE_W,
    height: isRegion ? TOPO_REGION_ICON_H : TOPO_NODE_H,
    // Predetermined handles must match DOM anchors (icon center).
    handles: [
      { type: "target", position: Position.Left, x: hx, y: hy },
      { type: "source", position: Position.Right, x: hx, y: hy },
    ],
    data: {
      label: n.label || n.name || n.ip || n.fabric_node_id,
      managed_ne_id: n.managed_ne_id || "",
      ume_ne_id: n.ume_ne_id || "",
      ne_ip: n.ip || "",
      vendor: n.vendor || "",
      connect_status: n.connect_status || "",
      managed_source: n.managed_source || "",
      kind: (n.kind as NeNodeData["kind"]) || (isRegion ? "region" : "ne"),
      folder_id: n.folder_id || "",
      view_id: n.view_id || "",
      node_count: n.node_count || 0,
    },
  };
  });
  const rfEdges: Edge[] = edges.map((e) => {
    const src =
      e.status === "stale" || e.status === "missing" ? "stale" : e.source || "manual";
    const label = formatPortPairLabel(e.a_port || "", e.b_port || "");
    const data: EdgeStyleData = {
      source: src,
      source_port: e.a_port || "",
      target_port: e.b_port || "",
      stroke_color: e.stroke_color || "",
      stroke_width: Number(e.stroke_width || 0),
      line_style: e.line_style || "",
      discovered_at: e.discovered_at ?? null,
    };
    return withEdgeVisual(
      {
        id: e.id,
        source: e.a_node_id,
        target: e.b_node_id,
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

function flowToPositions(nodes: Node<NeNodeData>[]) {
  return nodes.map((n) => {
    const isRegion = n.data.kind === "region" || String(n.id || "").startsWith("region:");
    const pos = isRegion
      ? regionApiPosition(n.position.x, n.position.y)
      : neApiPosition(n.position.x, n.position.y);
    return {
      fabric_node_id: n.id,
      x: pos.x,
      y: pos.y,
      label: n.data.label || "",
    };
  });
}

/** Stable signature so live poll only re-applies when the server graph actually changed. */
function graphFingerprint(graph: TopologyViewGraph): string {
  const nodes = [...graph.nodes]
    .map(
      (n) =>
        `${n.fabric_node_id}:${Math.round(Number(n.x) || 0)}:${Math.round(Number(n.y) || 0)}:${n.label || n.name || ""}`,
    )
    .sort()
    .join("|");
  const edges = [...graph.edges]
    .map((e) => `${e.id}:${e.a_node_id}:${e.b_node_id}:${e.a_port || ""}:${e.b_port || ""}:${e.status || ""}`)
    .sort()
    .join("|");
  return `${nodes}#${edges}#${graph.outside_peers?.length || 0}#${graph.world_transform?.lod || ""}#${graph.scatter?.length || 0}#${graph.world_transform?.total || 0}`;
}

function applyViewGraph(
  graph: TopologyViewGraph,
  defaults: EdgeDefaults,
  setNodes: (ns: Node<NeNodeData>[]) => void,
  setEdges: (es: Edge[]) => void,
  localPositions?: Map<string, { x: number; y: number }>,
) {
  const { rfNodes, rfEdges } = graphToFlow(graph.nodes, graph.edges, defaults);
  const merged = localPositions?.size
    ? rfNodes.map((n) => {
        const p = localPositions.get(n.id);
        return p ? { ...n, position: { ...p } } : n;
      })
    : rfNodes;
  setNodes(merged);
  setEdges(rfEdges);
  return { rfNodes: merged, rfEdges };
}

export function TopologyPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  // mapId set → fabric canvas; otherwise browse directory (region or view folder).
  const [mapId, setMapId] = useState<string>("");
  /** When mapId is the World canvas, optional SBN region folder to zoom/filter. */
  const [worldFocusFolderId, setWorldFocusFolderId] = useState<string>("");
  const [worldViewId, setWorldViewId] = useState<string>("");
  const [selectedFolderId, setSelectedFolderId] = useState<string>("");
  /** Hover sync key between left tree and right hex browser: `region:<id>` | `view:<id>`. */
  const [hotBrowseKey, setHotBrowseKey] = useState("");
  const [pendingHighlightNe, setPendingHighlightNe] = useState("");
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [treeNeQuery, setTreeNeQuery] = useState("");
  const [debouncedTreeNeQuery, setDebouncedTreeNeQuery] = useState("");
  const [treeSearchOpen, setTreeSearchOpen] = useState(false);
  const treeSearchRef = useRef<HTMLDivElement | null>(null);
  const [dirty, setDirty] = useState(false);
  const [historyTick, setHistoryTick] = useState(0);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
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
  const [toolMode, setToolMode] = useState<ToolMode>("select");
  const [snapToGrid, setSnapToGrid] = useState(true);
  const [autoLayoutAfterDiscover, setAutoLayoutAfterDiscover] = useState(loadAutoLayoutAfterDiscover);
  const [discoverAutoAddUnmatched, setDiscoverAutoAddUnmatched] = useState(() =>
    loadBoolFlag(DISCOVER_AUTO_ADD_KEY, true),
  );
  const [discoverProjectNeighbors, setDiscoverProjectNeighbors] = useState(() =>
    loadBoolFlag(DISCOVER_PROJECT_NEIGHBORS_KEY, true),
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [addNeOpen, setAddNeOpen] = useState(false);
  const [paletteSource, setPaletteSource] = useState<PaletteSource>("managed");
  const [paletteSelectedKeys, setPaletteSelectedKeys] = useState<string[]>([]);
  const [paletteAdding, setPaletteAdding] = useState(false);
  const [createNeDialog, setCreateNeDialog] = useState<{
    flowX: number;
    flowY: number;
    name: string;
    ip_address: string;
  } | null>(null);
  const [createNeBusy, setCreateNeBusy] = useState(false);
  const [newRootDialog, setNewRootDialog] = useState<{ name: string } | null>(null);
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
  const [discoverJobId, setDiscoverJobId] = useState("");
  const discoverAbortRef = useRef(false);
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
  const findJustLocatedRef = useRef(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NeNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const rfRef = useRef<ReactFlowInstance<Node<NeNodeData>, Edge> | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dirtyRef = useRef(false);
  const appliedMapIdRef = useRef("");
  const appliedGraphFpRef = useRef("");
  /** Fit once after graph hydrate — world coords are far outside the default viewport. */
  const pendingFitRef = useRef(false);
  const nodesRef = useRef<Node<NeNodeData>[]>([]);
  const historyRef = useRef<HistorySnap[]>([]);
  const redoRef = useRef<HistorySnap[]>([]);
  const historyLockRef = useRef(false);
  /** Fabric edge ids removed locally; flushed on Save (not when only removing nodes from view). */
  const pendingEdgeDeletesRef = useRef<Set<string>>(new Set());
  /** Local `local:*` edge ids added on canvas; flushed on Save via createFabricManualEdge. */
  const pendingEdgeCreatesRef = useRef<Set<string>>(new Set());
  const connectClickRef = useRef<string | null>(null);
  const canUndo = historyTick >= 0 && historyRef.current.length > 0;
  const canRedo = historyTick >= 0 && redoRef.current.length > 0;

  nodesRef.current = nodes;

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
  const [canvasZoom, setCanvasZoom] = useState(1);
  const worldTransformRef = useRef<TopologyWorldTransform | null>(null);
  const flatLodFetchGenRef = useRef(0);
  const flatLodTimerRef = useRef<number | null>(null);
  const isWorldFlatCanvasRef = useRef(false);
  const mapIdRef = useRef(mapId);

  const treeQuery = useQuery({
    queryKey: queryKeys.topologyTree,
    queryFn: fetchTopologyTree,
    staleTime: liveSync ? 0 : 30_000,
    refetchOnWindowFocus: liveSync,
    refetchInterval: liveSync ? 5000 : false,
    refetchIntervalInBackground: false,
  });

  const treeFlatMap = useMemo(() => {
    if (!mapId) return false;
    const kids = treeQuery.data?.root?.children || [];
    return isWorldFlatViewName(findViewInRegion(kids, mapId)?.view?.name);
  }, [mapId, treeQuery.data?.root?.children]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedTreeNeQuery(treeNeQuery.trim()), 200);
    return () => window.clearTimeout(timer);
  }, [treeNeQuery]);

  const treeNeSearchQuery = useQuery({
    queryKey: queryKeys.fabricNodeSearch(debouncedTreeNeQuery, 1),
    queryFn: () => searchFabricNodes({ q: debouncedTreeNeQuery, page: 1, pageSize: 30 }),
    enabled: debouncedTreeNeQuery.length >= 1,
  });

  const graphQuery = useQuery({
    queryKey: queryKeys.topologyGraph(mapId),
    queryFn: () =>
      fetchTopologyGraph(mapId, treeFlatMap ? { lod: "overview" } : undefined),
    enabled: Boolean(mapId),
    staleTime: liveSync ? 0 : 30_000,
    refetchOnWindowFocus: liveSync && !treeFlatMap,
    refetchInterval: liveSync && mapId && !treeFlatMap ? 3000 : false,
    refetchIntervalInBackground: false,
  });

  const isWorldFlatCanvas =
    treeFlatMap ||
    isWorldFlatViewName(graphQuery.data?.view?.name) ||
    Boolean(graphQuery.data?.view?.filter?.world_flat);

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
      worldVisualLod,
    }),
    [hideIp, hideVendor, hidePorts, toolMode, showPlaceholderBadge, worldVisualLod],
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
        // Empty string clears RF's previous edge-text; undefined can leave a stale label.
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
    if (!mapId || !isWorldFlatCanvas || dirtyRef.current) return;
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
      // Far / mid zoom: starfield scatter only (screen-space canvas). Drop heavy RF tiles.
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
          // Always shed RF tiles when leaving close-up so scatter is the sole layer.
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
  }, [mapId, isWorldFlatCanvas, queryClient]);

  const scheduleFlatViewportRefresh = useCallback(() => {
    if (!isWorldFlatCanvas) return;
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
    try {
      localStorage.setItem(TREE_EXPAND_KEY, JSON.stringify(expandedIds));
    } catch {
      /* ignore */
    }
  }, [expandedIds]);

  useEffect(() => {
    try {
      if (mapId) localStorage.setItem(LAST_LEAF_KEY, mapId);
      else localStorage.removeItem(LAST_LEAF_KEY);
    } catch {
      /* ignore */
    }
  }, [mapId]);

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

  const treeRoot = treeQuery.data?.root || null;
  const regions = useMemo(() => treeRoot?.children || [], [treeRoot]);
  const treeLoading = treeQuery.isPending && !treeQuery.data;
  const treeFailed = treeQuery.isError && !treeQuery.data;

  useEffect(() => {
    if (!treeRoot || !regions.length) return;
    if (mapId) {
      const hit = findViewInRegion(regions, mapId);
      if (hit && selectedFolderId !== hit.region.id) {
        setSelectedFolderId(hit.region.id);
        setExpandedIds((prev) => ({ ...prev, [hit.region.id]: true }));
      }
      return;
    }
    // Keep root (all regions) as the default landing view — do not auto-pick regions[0].
    if (selectedFolderId && !regions.some((r) => r.id === selectedFolderId)) {
      setSelectedFolderId("");
    }
  }, [mapId, selectedFolderId, treeRoot, regions]);

  const rootFolderId = String(treeRoot?.id || "").trim();

  const canvasMode = Boolean(mapId);

  const activeRegion = useMemo(() => {
    if (!selectedFolderId) return null;
    return findFolderInTree(regions, selectedFolderId);
  }, [regions, selectedFolderId]);

  const activeView = useMemo(() => {
    if (!mapId) return null;
    return findViewInRegion(regions, mapId)?.view || null;
  }, [mapId, regions]);

  const browseEntries = useMemo(
    (): TopologyTreeViewItem[] => {
      // Region === canvas: no map subdirectory listing.
      // UME World / top-level containers: hex shows modules (handled separately).
      if (
        !activeRegion ||
        isRegionCanvasFolder(activeRegion, rootFolderId) ||
        isUmeWorldContainer(activeRegion)
      ) {
        return [];
      }
      return activeRegion.views || [];
    },
    [activeRegion, rootFolderId],
  );

  const hexBrowseRegion = useMemo(() => {
    // Root hex, or nav container hex (UME World / top-level manual).
    if (!activeRegion) return null;
    if (isUmeWorldContainer(activeRegion)) return activeRegion;
    if (isRegionCanvasFolder(activeRegion, rootFolderId)) return null;
    return activeRegion;
  }, [activeRegion, rootFolderId]);

  const umeWorldHexModules = useMemo(() => {
    if (!activeRegion || !isUmeWorldContainer(activeRegion)) return null;
    const drill =
      (activeRegion.children || []).find((c) => isWorldDrillFolder(c)) || null;
    const flatView =
      (activeRegion.views || []).find((v) => isWorldFlatViewName(v.name)) || null;
    return { drill, flatView };
  }, [activeRegion]);

  // Resolve World drill view id for shortcuts; do not auto-open canvas.
  useEffect(() => {
    let cancelled = false;
    fetchTopologyWorld()
      .then((w) => {
        if (cancelled) return;
        setWorldViewId(w.view_id);
      })
      .catch(() => {
        /* world not seeded yet — keep classic browse */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const expandFolderPath = useCallback(
    (folderId: string) => {
      const path = folderPathIds(regions, folderId);
      if (!path.length) return;
      setExpandedIds((p) => {
        const next = { ...p };
        for (const id of path) next[id] = true;
        return next;
      });
    },
    [regions],
  );

  const goUmeWorldNav = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    const container = regions.find(
      (r) => r.external_ref === "ume:world" || r.name === "UME World",
    );
    if (!container) return;
    setWorldFocusFolderId("");
    setSelectedFolderId(container.id);
    setExpandedIds((p) => ({ ...p, [container.id]: true }));
    setMapId("");
    clearDirty();
  }, [confirmDiscardIfDirty, clearDirty, regions]);

  const goWorld = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    if (!worldViewId) return;
    setWorldFocusFolderId("");
    const container = regions.find(
      (r) => r.external_ref === "ume:world" || r.name === "UME World",
    );
    let drillFolder =
      (container?.children || []).find((c) => isWorldDrillFolder(c)) || null;
    if (!drillFolder) {
      for (const r of regions) {
        if (isWorldDrillFolder(r)) {
          drillFolder = r;
          break;
        }
        const nested = (r.children || []).find((c) => isWorldDrillFolder(c));
        if (nested) {
          drillFolder = nested;
          break;
        }
      }
    }
    if (drillFolder) {
      setSelectedFolderId(drillFolder.id);
    } else if (container) {
      setSelectedFolderId(container.id);
    }
    setMapId(worldViewId);
    clearDirty();
  }, [confirmDiscardIfDirty, clearDirty, worldViewId, regions]);

  const primaryViewOfFolder = useCallback((folder: TopologyTreeFolderItem | null | undefined) => {
    if (!folder) return null;
    const views = folder.views || [];
    if (isWorldDrillFolder(folder)) {
      return views.find((v) => v.name === "World") || views[0] || null;
    }
    // UME World container: World canvas lives on the drill child, not here.
    if (folder.external_ref === "ume:world" || folder.name === "UME World") {
      const drill = (folder.children || []).find((c) => isWorldDrillFolder(c));
      if (drill) {
        const dv = drill.views || [];
        return dv.find((v) => v.name === "World") || dv[0] || null;
      }
      return views.find((v) => !isWorldFlatViewName(v.name)) || null;
    }
    return views[0] || null;
  }, []);

  const goRegion = useCallback(
    (folderId: string) => {
      if (!confirmDiscardIfDirty()) return;
      setSelectedFolderId(folderId);
      expandFolderPath(folderId);
      const folder = findFolderInTree(regions, folderId);
      // Region === canvas: open its primary map (no map subdirectory / hex list).
      if (isRegionCanvasFolder(folder, String(treeRoot?.id || ""))) {
        setWorldFocusFolderId("");
        const view = primaryViewOfFolder(folder);
        if (view) {
          setMapId(view.id);
          clearDirty();
          return;
        }
      }
      // UME World container: fall through to empty selection (use goWorld from tree).
      setMapId("");
      setWorldFocusFolderId("");
      clearDirty();
      setNodes([]);
      setEdges([]);
    },
    [
      confirmDiscardIfDirty,
      clearDirty,
      setNodes,
      setEdges,
      regions,
      primaryViewOfFolder,
      expandFolderPath,
      treeRoot?.id,
    ],
  );

  const goRoot = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    setSelectedFolderId("");
    setWorldFocusFolderId("");
    setMapId("");
    clearDirty();
    setNodes([]);
    setEdges([]);
  }, [confirmDiscardIfDirty, clearDirty, setNodes, setEdges]);

  const goCanvas = useCallback(
    (viewId: string, folderId?: string) => {
      if (!confirmDiscardIfDirty()) return;
      const hit = findViewInRegion(regions, viewId);
      const regionId = folderId || hit?.region.id || selectedFolderId;
      if (regionId) {
        setSelectedFolderId(regionId);
        expandFolderPath(regionId);
      }
      setWorldFocusFolderId("");
      setMapId(viewId);
    },
    [confirmDiscardIfDirty, regions, selectedFolderId, expandFolderPath],
  );

  const goBackBrowse = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    const folder = findFolderInTree(regions, selectedFolderId);
    // Leaving a region canvas under UME World → container hex nav.
    if (folder) {
      const path = folderPathIds(regions, folder.id);
      const container = regions.find((r) => isUmeWorldContainer(r));
      if (container && (folder.id === container.id || path.includes(container.id))) {
        setSelectedFolderId(container.id);
        setWorldFocusFolderId("");
        setMapId("");
        clearDirty();
        setNodes([]);
        setEdges([]);
        return;
      }
    }
    // Leaving other region canvases → parent nav container, else root hex.
    if (isRegionCanvasFolder(folder, String(treeRoot?.id || "")) || isUmeWorldNavFolder(folder)) {
      const parentId = String(folder?.parent_id || "").trim();
      const parent = parentId ? findFolderInTree(regions, parentId) : null;
      if (parent && !isRegionCanvasFolder(parent, String(treeRoot?.id || ""))) {
        setSelectedFolderId(parent.id);
      } else {
        setSelectedFolderId("");
      }
    }
    setWorldFocusFolderId("");
    setMapId("");
    clearDirty();
    setNodes([]);
    setEdges([]);
  }, [confirmDiscardIfDirty, clearDirty, setNodes, setEdges, regions, selectedFolderId, treeRoot?.id]);

  // Deep link from classify search: /topology?view=&ne=
  useEffect(() => {
    const viewId = String(searchParams.get("view") || "").trim();
    const neId = String(searchParams.get("ne") || "").trim();
    if (!viewId || !regions.length) return;
    const hit = findViewInRegion(regions, viewId);
    if (!hit) return;
    setSelectedFolderId(hit.region.id);
    setExpandedIds((p) => ({ ...p, [hit.region.id]: true }));
    setMapId(viewId);
    if (neId) setPendingHighlightNe(neId);
    setSearchParams({}, { replace: true });
  }, [regions, searchParams, setSearchParams]);

  useEffect(() => {
    appliedMapIdRef.current = "";
    appliedGraphFpRef.current = "";
  }, [mapId]);

  // Keep the left tree scrolled to the active view/folder after drill-down.
  useEffect(() => {
    if (!mapId && !selectedFolderId) return;
    const root = document.querySelector(".topo-region-list");
    if (!root) return;
    const hit =
      root.querySelector("li.is-active") ||
      root.querySelector(".topo-region-list__block.is-branch-active");
    if (hit && "scrollIntoView" in hit) {
      (hit as HTMLElement).scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [mapId, selectedFolderId, expandedIds]);

  useEffect(() => {
    if (!canvasMode) return;
    if (!mapId || !graphQuery.data) return;
    const entering = appliedMapIdRef.current !== mapId;
    // Live poll while on the same map: apply server graph only if no unsaved local edits.
    if (!entering && dirtyRef.current) return;

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
    if (entering) {
      appliedMapIdRef.current = mapId;
      historyRef.current = [];
      redoRef.current = [];
      pendingEdgeDeletesRef.current = new Set();
      pendingEdgeCreatesRef.current = new Set();
      clearDirty();
      bumpHistory();
      // World / large canvases use absolute coords far outside the default RF
      // viewport — without an initial fit the canvas looks empty after load.
      const hasScatter = (graphQuery.data.scatter?.length || 0) > 0;
      const hasWorld = Boolean(worldDisplayBounds(graphQuery.data.world_transform));
      pendingFitRef.current = nextNodes.length > 0 || hasScatter || hasWorld;
    }
    historyLockRef.current = false;
  }, [canvasMode, mapId, graphQuery.data, edgeDefaults, setNodes, setEdges, clearDirty, bumpHistory]);

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
        // duration 0 — animated fit with 400+ nodes often never paints the first frame.
        inst.fitView({ ...FIT_VIEW_OPTS, duration: 0 });
      }
      setCanvasZoom(inst.getZoom());
    };
    const t = window.setTimeout(run, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [nodes, mapId, isWorldFlatCanvas, graphQuery.data?.world_transform]);

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
        pendingEdgeDeletes: [...pendingEdgeDeletesRef.current],
        pendingEdgeCreates: [...pendingEdgeCreatesRef.current],
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
      pendingEdgeDeletes: [...pendingEdgeDeletesRef.current],
      pendingEdgeCreates: [...pendingEdgeCreatesRef.current],
    });
    historyLockRef.current = true;
    setNodes(prev.nodes);
    setEdges(prev.edges);
    pendingEdgeDeletesRef.current = new Set(prev.pendingEdgeDeletes);
    pendingEdgeCreatesRef.current = new Set(prev.pendingEdgeCreates || []);
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
      pendingEdgeDeletes: [...pendingEdgeDeletesRef.current],
      pendingEdgeCreates: [...pendingEdgeCreatesRef.current],
    });
    historyLockRef.current = true;
    setNodes(next.nodes);
    setEdges(next.edges);
    pendingEdgeDeletesRef.current = new Set(next.pendingEdgeDeletes);
    pendingEdgeCreatesRef.current = new Set(next.pendingEdgeCreates || []);
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
      if (opts?.persist && mapId) {
        try {
          const graph = await patchTopologyPositions(mapId, flowToPositions(next));
          if (
            pendingEdgeDeletesRef.current.size === 0 &&
            pendingEdgeCreatesRef.current.size === 0
          ) {
            clearDirty();
          }
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
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
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

  const createRegionMut = useMutation({
    mutationFn: (input: { name: string; parent_id?: string }) => {
      const parentId = String(input.parent_id || "").trim();
      // Top-level root: omit parent_id so API bootstraps system root even if tree query failed.
      return createTopologyFolder(
        parentId
          ? { name: input.name, kind: "region", parent_id: parentId }
          : { name: input.name, kind: "region" },
      );
    },
    onSuccess: async (folder, input) => {
      setNewRootDialog(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      const tree = await fetchTopologyTree();
      const regionsList = tree.root?.children || [];
      const hit = findFolderInTree(regionsList, folder.id);
      const parent = String(input.parent_id || folder.parent_id || hit?.parent_id || "").trim();
      const rootId = String(tree.root?.id || "").trim();
      const isNested = Boolean(parent && parent !== rootId);
      setWorldFocusFolderId("");
      setExpandedIds((prev) => {
        const next = { ...prev, [folder.id]: true };
        if (parent) next[parent] = true;
        return next;
      });
      if (isNested) {
        // Stay on parent canvas so the new region building icon is visible.
        setSelectedFolderId(parent);
        expandFolderPath(parent);
        const parentHit = findFolderInTree(regionsList, parent);
        const parentView =
          primaryViewOfFolder(parentHit) ||
          (parentHit?.views || []).find((v) => String(v.kind) === "physical") ||
          (parentHit?.views || [])[0];
        const stayViewId = mapId || parentView?.id || "";
        if (stayViewId) {
          setMapId(stayViewId);
          await queryClient.invalidateQueries({
            queryKey: queryKeys.topologyGraph(stayViewId),
          });
        }
      } else {
        // Top-level = nav container (UME World pattern): stay on hex browse, do not open canvas.
        setSelectedFolderId(folder.id);
        setMapId("");
      }
      showOk(t("topology.regionCreated"));
    },
    onError: (err) => showError(String(err)),
  });

  const renameRegionMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      updateTopologyFolder(id, { name }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      showOk(t("topology.regionRenamed"));
    },
    onError: (err) => showError(String(err)),
  });

  const promptRenameRegion = useCallback(
    (id: string, currentName: string) => {
      const next = window.prompt(t("topology.renameRegionPrompt"), currentName);
      if (next == null) return;
      const name = next.trim();
      if (!name || name === currentName) return;
      renameRegionMut.mutate({ id, name });
    },
    [renameRegionMut, t],
  );

  const deleteMapMut = useMutation({
    mutationFn: (id: string) => deleteTopologyMap(id),
    onSuccess: async (_out, id) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
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

  const deleteFolderMut = useMutation({
    mutationFn: (id: string) => deleteTopologyFolder(id, false),
    onSuccess: async (_out, id) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      if (selectedFolderId === id) {
        setSelectedFolderId("");
        setMapId("");
      }
      showOk(t("topology.regionDeleted"));
    },
    onError: (err) => showError(String(err)),
  });

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

  const promptNewRegion = useCallback(() => {
    setNewRootDialog({ name: t("topology.newRegionName") });
  }, [t]);

  const submitNewRoot = useCallback(() => {
    const name = String(newRootDialog?.name || "").trim();
    if (!name) {
      showError(t("topology.newRegionPrompt"));
      return;
    }
    createRegionMut.mutate({ name });
  }, [newRootDialog, createRegionMut, showError, t]);

  const promptNewSubRegion = useCallback(() => {
    if (isWorldFlatViewName(activeView?.name)) {
      showError(t("topology.subRegionNotOnFlat"));
      return;
    }
    let parentId = selectedFolderId;
    if (!parentId && worldViewId && mapId === worldViewId) {
      const container = regions.find(
        (r) => r.external_ref === "ume:world" || r.name === "UME World",
      );
      const drill = (container?.children || []).find((c) => isWorldDrillFolder(c));
      parentId = drill?.id || container?.id || "";
    }
    if (!parentId) {
      showError(t("topology.subRegionNeedParent"));
      return;
    }
    const name = window.prompt(t("topology.newSubRegionPrompt"), t("topology.newSubRegionName"));
    if (!name?.trim()) return;
    createRegionMut.mutate({ name: name.trim(), parent_id: parentId });
    setCtxMenu(null);
  }, [activeView?.name, selectedFolderId, worldViewId, mapId, regions, createRegionMut, t, showError]);

  const renderWorldNavFolder = useCallback(
    (folder: TopologyTreeFolderItem, depth: number): ReactNode => {
      const kids = folder.children || [];
      const containerFolder =
        folder.external_ref === "ume:world" || folder.name === "UME World";
      const drillFolder = isWorldDrillFolder(folder);
      const umeNav = isUmeWorldNavFolder(folder);
      // Region === canvas: hide physical/custom view rows; only child regions.
      // UME World container: only「世界地图」as a sibling view.
      const canvasRegion = isRegionCanvasFolder(folder, String(treeRoot?.id || ""));
      const visibleViews = canvasRegion
        ? []
        : containerFolder
          ? (folder.views || []).filter((v) => isWorldFlatViewName(v.name))
          : folder.views || [];
      const hasKids = kids.length > 0 || visibleViews.length > 0;
      const open = Boolean(expandedIds[folder.id]);
      const viewActiveUnder =
        (folder.views || []).some((v) => v.id === mapId) ||
        (drillFolder && Boolean(worldViewId) && mapId === worldViewId);
      const isSelected = selectedFolderId === folder.id;
      const focused = viewActiveUnder || (isSelected && Boolean(mapId));
      const regionActive = isSelected && !mapId;
      const hot = hotBrowseKey === `region:${folder.id}`;
      const childCount = kids.length;

      const renderViewRow = (v: TopologyTreeViewItem) => {
        const isPhysical = String(v.kind) === "physical";
        const viewHot = hotBrowseKey === `view:${v.id}`;
        const viewActive = mapId === v.id;
        return (
          <li
            key={v.id}
            className={`topo-region-list__view${viewActive ? " is-active" : ""}${
              viewHot ? " is-hot" : ""
            }`}
          >
            <div
              className="topo-map-list__row"
              onMouseEnter={() => setHotBrowseKey(`view:${v.id}`)}
              onMouseLeave={() =>
                setHotBrowseKey((k) => (k === `view:${v.id}` ? "" : k))
              }
            >
              {/* Same width as folder chevron so leaf canvas aligns under parent title. */}
              <span className="topo-region-list__chevron-spacer" aria-hidden="true" />
              <button
                type="button"
                className="topo-map-list__item"
                onClick={() => {
                  setWorldFocusFolderId("");
                  goCanvas(v.id, folder.id);
                }}
                onDoubleClick={() =>
                  !umeNav ? promptRenameMap(v.id, v.name) : undefined
                }
                title={!umeNav ? t("topology.renameHint") : undefined}
              >
                <span className="topo-map-list__name">
                  <span
                    className={`topo-region-list__glyph topo-dir__icon--${
                      isPhysical ? "core" : "aggregation"
                    }`}
                    aria-hidden="true"
                  >
                    <LayerGlyph
                      role={isPhysical ? "core" : "aggregation"}
                      size={13}
                    />
                  </span>
                  <span className="topo-map-list__title">
                    {displayViewName(v.name, t)}
                    {viewActive && dirty ? " *" : ""}
                  </span>
                  {!umeNav ? (
                    <span className="topo-map-list__count">{v.node_count || 0}N</span>
                  ) : null}
                </span>
              </button>
              {!umeNav ? (
                <div className="topo-map-list__actions">
                  <button
                    type="button"
                    className="topo-map-list__icon"
                    title={t("topology.rename")}
                    aria-label={t("topology.rename")}
                    disabled={renameMapMut.isPending}
                    onClick={() => promptRenameMap(v.id, v.name)}
                  >
                    <PencilIcon />
                  </button>
                  <button
                    type="button"
                    className="topo-map-list__icon"
                    title={t("topology.deleteMap")}
                    aria-label={t("topology.deleteMap")}
                    disabled={isPhysical}
                    onClick={() => {
                      const msg = t("topology.deleteMapConfirm").replace(
                        "{{name}}",
                        v.name,
                      );
                      if (window.confirm(msg)) deleteMapMut.mutate(v.id);
                    }}
                  >
                    <CloseIcon />
                  </button>
                </div>
              ) : null}
            </div>
          </li>
        );
      };

      return (
        <li
          key={folder.id}
          className={`topo-region-list__block${
            focused ? " is-branch-active" : ""
          }${regionActive ? " is-active" : ""}${hot ? " is-hot" : ""}`}
        >
          <div
            className="topo-map-list__row topo-region-list__row"
            onMouseEnter={() => setHotBrowseKey(`region:${folder.id}`)}
            onMouseLeave={() =>
              setHotBrowseKey((k) => (k === `region:${folder.id}` ? "" : k))
            }
          >
            <button
              type="button"
              className="topo-map-list__icon topo-region-list__chevron"
              title={open ? t("topology.collapseRegion") : t("topology.expandRegion")}
              aria-label={open ? t("topology.collapseRegion") : t("topology.expandRegion")}
              aria-expanded={open}
              disabled={!hasKids}
              onClick={(e) => {
                e.stopPropagation();
                if (!hasKids) return;
                setExpandedIds((p) => ({ ...p, [folder.id]: !open }));
              }}
            >
              <ChevronIcon open={open} />
            </button>
            <button
              type="button"
              className="topo-map-list__item"
              onClick={() => {
                if (containerFolder) {
                  // UME World: hex nav for L2 + world map (not auto-open canvas).
                  goUmeWorldNav();
                } else {
                  goRegion(folder.id);
                }
              }}
              title={t("topology.openRegion")}
            >
              <span className="topo-map-list__name">
                <span className="topo-region-list__glyph" aria-hidden="true">
                  <RegionGlyph size={14} />
                </span>
                <span className="topo-map-list__title">{regionDisplayName(folder)}</span>
                <span className="topo-map-list__count">
                  {(() => {
                    if (containerFolder) {
                      return t("topology.regionNodeHint").replace(
                        "{{count}}",
                        String(kids.length + visibleViews.length),
                      );
                    }
                    const neCount = (folder.views || []).reduce(
                      (sum, v) => sum + (Number(v.node_count) || 0),
                      0,
                    );
                    // Prefer canvas NE count; fall back to child-region count.
                    if (neCount > 0) return `${neCount}N`;
                    if (childCount > 0) return String(childCount);
                    return "";
                  })()}
                </span>
              </span>
            </button>
            {(() => {
              const canRenameFolder =
                !isUmeStructuralFolder(folder) &&
                (!folder.is_system || isManualRootMapFolder(folder));
              const canDeleteFolder =
                !isUmeStructuralFolder(folder) &&
                !isManualRootMapFolder(folder) &&
                (!folder.is_system || isUmeSyncedSubRegion(folder));
              if (!canRenameFolder && !canDeleteFolder) return null;
              return (
              <div className="topo-map-list__actions">
                {canRenameFolder ? (
                  <button
                    type="button"
                    className="topo-map-list__icon"
                    title={t("topology.renameRegion")}
                    aria-label={t("topology.renameRegion")}
                    disabled={renameRegionMut.isPending}
                    onClick={() => promptRenameRegion(folder.id, folder.name)}
                  >
                    <PencilIcon />
                  </button>
                ) : null}
                {canDeleteFolder ? (
                <button
                  type="button"
                  className="topo-map-list__icon"
                  title={t("topology.deleteRegion")}
                  aria-label={t("topology.deleteRegion")}
                  disabled={deleteFolderMut.isPending}
                  onClick={() => {
                    const msg = t("topology.deleteRegionConfirm").replace(
                      "{{name}}",
                      folder.name,
                    );
                    if (window.confirm(msg)) {
                      deleteFolderMut.mutate(folder.id);
                    }
                  }}
                >
                  <CloseIcon />
                </button>
                ) : null}
              </div>
              );
            })()}
          </div>
          {open ? (
            <ul className="topo-map-list topo-region-list__maps">
              {containerFolder ? (
                <>
                  {kids.map((child) => renderWorldNavFolder(child, depth + 1))}
                  {visibleViews.map(renderViewRow)}
                </>
              ) : (
                <>
                  {visibleViews.map(renderViewRow)}
                  {kids.map((child) => renderWorldNavFolder(child, depth + 1))}
                </>
              )}
            </ul>
          ) : null}
        </li>
      );
    },
    [
      expandedIds,
      mapId,
      worldViewId,
      selectedFolderId,
      hotBrowseKey,
      dirty,
      goUmeWorldNav,
      goWorld,
      goRegion,
      goCanvas,
      promptRenameRegion,
      promptRenameMap,
      renameRegionMut.isPending,
      renameMapMut.isPending,
      deleteFolderMut,
      deleteMapMut,
      t,
      treeRoot?.id,
    ],
  );

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
    onError: (err) => showError(String(err)),
  });

  const runDiscover = useCallback(
    async (neIds?: string[]) => {
      if (!mapId || discovering) return;
      // Topology canvas: single-NE (or explicit id list) only — bulk collect lives under Network → LLDP.
      const filterIds = (neIds || []).map((x) => String(x || "").trim()).filter(Boolean);
      if (!filterIds.length) {
        showError(t("topology.discoverOneNeedNe"));
        return;
      }
      setDiscoverOpen(true);
      setDiscovering(true);
      setDiscoverReport(null);
      setDiscoverLiveResults([]);
      setDiscoverError("");
      setDiscoverJobId("");
      discoverAbortRef.current = false;
      const scannable = nodes.filter((n) => Boolean(n.data.managed_ne_id || n.data.ume_ne_id));
      const scoped = scannable.filter(
        (n) =>
          filterIds.includes(String(n.data.managed_ne_id || "")) ||
          filterIds.includes(String(n.data.ume_ne_id || "")),
      );
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
          const localIds = nodes.map((n) => n.id);
          const localSet = new Set(localIds);
          const serverSet = new Set(serverIds);
          const toAdd = localIds.filter((id) => !serverSet.has(id));
          const toRemove = serverIds.filter((id) => !localSet.has(id));
          if (toAdd.length) {
            await addTopologyViewNodes(mapId, { fabric_node_ids: toAdd });
          }
          if (toRemove.length) {
            await removeTopologyViewNodes(mapId, toRemove);
          }
          await patchTopologyPositions(mapId, flowToPositions(nodes));
          clearDirty();
        }
        const ne_ids = filterIds;
        if (!scoped.length) {
          throw new Error(t("topology.discoverOneNeedNe"));
        }
        const jobStart = await startLldpDiscover({
          scope: "ne_ids",
          ne_ids,
          concurrency: 4,
          auto_add_unmatched: discoverAutoAddUnmatched,
          trigger_mode: "topology",
        });
        setDiscoverJobId(jobStart.id);
        let job: TopologyDiscoverJob = jobStart;
        let cancelled = false;
        for (let i = 0; i < 600; i++) {
          if (discoverAbortRef.current) {
            cancelled = true;
            break;
          }
          await new Promise((r) => window.setTimeout(r, 500));
          if (discoverAbortRef.current) {
            cancelled = true;
            break;
          }
          job = await fetchLldpDiscoverJob(jobStart.id, { page: 1, pageSize: 5 });
          setDiscoverProgress((p) => ({
            ...p,
            index: job.done,
            total: job.total || p.total,
            edgesAdded: job.edges_added,
            edgesUpdated: job.edges_updated,
            neName: job.items?.[job.items.length - 1]?.ne_name || p.neName,
            neIp: job.items?.[job.items.length - 1]?.ne_ip || p.neIp,
          }));
          setDiscoverLiveResults((job.items || []) as TopologyDiscoverNeResult[]);
          if (
            job.status === "done" ||
            job.status === "failed" ||
            job.status === "cancelled" ||
            job.status === "stopped"
          ) {
            if (job.status === "cancelled" || job.status === "stopped") cancelled = true;
            break;
          }
        }
        if (cancelled || discoverAbortRef.current) {
          setDiscoverError(t("topology.discoverCancelled"));
          setDiscoverReport({
            map_id: mapId,
            protocol: "lldp",
            job_id: job.id,
            scanned: job.done,
            edges_added: job.edges_added,
            edges_updated: job.edges_updated,
            edges_stale: job.edges_missing ?? job.edges_stale,
            results: (job.items || []) as TopologyDiscoverNeResult[],
            graph: null,
          });
          return;
        }
        if (job.status === "failed") {
          throw new Error(job.error || "discover_failed");
        }
        // Final page: pull a fuller item slice for the summary panel.
        try {
          job = await fetchLldpDiscoverJob(jobStart.id, { page: 1, pageSize: 100 });
        } catch {
          /* keep last polled job */
        }
        // Refresh base graph from server (LLDP may have new edges), then dry-run project.
        const baseGraph = await fetchTopologyGraph(mapId);
        const projected = discoverProjectNeighbors
          ? await projectTopologyNeighbors(mapId, {
              seed_fabric_node_ids: scoped.map((n) => n.id),
              dry_run: true,
            })
          : baseGraph;
        // Keep server query as committed graph (dry_run must not poison cache as persisted).
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), baseGraph);
        if (discoverProjectNeighbors && projected.truncate_reason === "membership_frozen") {
          showError(t("topology.truncatedFrozen"));
        }
        appliedMapIdRef.current = mapId;
        let { rfNodes, rfEdges } = graphToFlow(projected.nodes, projected.edges, edgeDefaults);
        // Keep existing node positions when we did not auto-layout.
        const localPos = new Map(nodes.map((n) => [n.id, n.position]));
        const serverNodeIds = new Set((baseGraph.nodes || []).map((n) => n.fabric_node_id));
        rfNodes = rfNodes.map((n) => {
          const p = localPos.get(n.id);
          return p ? { ...n, position: { ...p } } : n;
        });
        let didAutoLayout = false;
        if (autoLayoutAfterDiscover && rfNodes.length > 1) {
          rfNodes = layoutGraph(rfNodes, rfEdges, "hierarchical-tb");
          didAutoLayout = true;
        }
        historyLockRef.current = true;
        setNodes(rfNodes);
        setEdges(rfEdges);
        historyLockRef.current = false;
        const localOnly = rfNodes.some((n) => !serverNodeIds.has(n.id));
        if (localOnly || didAutoLayout) {
          markDirty();
        } else {
          clearDirty();
        }
        const out: TopologyDiscoverOut = {
          map_id: mapId,
          protocol: "lldp",
          job_id: job.id,
          scanned: job.done,
          edges_added: job.edges_added,
          edges_updated: job.edges_updated,
          edges_stale: job.edges_missing ?? job.edges_stale,
          results: (job.items || []) as TopologyDiscoverNeResult[],
          graph: projected,
        };
        setDiscoverReport(out);
        await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
        await queryClient.invalidateQueries({ queryKey: queryKeys.fabricSummary });
        showOk(
          t("topology.discovered")
            .replace("{{added}}", String(out.edges_added))
            .replace("{{updated}}", String(out.edges_updated))
            .replace("{{stale}}", String(out.edges_stale || 0)),
        );
      } catch (err) {
        if (discoverAbortRef.current) {
          setDiscoverError(t("topology.discoverCancelled"));
        } else {
          setDiscoverError(String(err));
          showError(t("topology.discoverFail").replace("{{detail}}", String(err)));
        }
      } finally {
        setDiscovering(false);
      }
    },
    [mapId, discovering, nodes, queryClient, setNodes, setEdges, showOk, showError, t, autoLayoutAfterDiscover, discoverAutoAddUnmatched, discoverProjectNeighbors, edgeDefaults, clearDirty, markDirty, graphQuery.data],
  );

  const cancelDiscover = useCallback(async () => {
    discoverAbortRef.current = true;
    const jobId = discoverJobId;
    if (jobId) {
      try {
        await stopLldpCollectJob(jobId);
      } catch {
        /* best-effort stop; local poll also exits */
      }
    }
  }, [discoverJobId]);

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
        missing: discoverReport.edges_stale || 0,
        failed: discoverCounts.fail,
      }
    : {
        scanned: discoverLiveResults.length,
        added: discoverProgress.edgesAdded,
        updated: discoverProgress.edgesUpdated,
        missing: 0,
        failed: discoverCounts.fail,
      };
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
      if (!mapId || !isValidConnection(connection)) return;
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
    [mapId, isValidConnection, pushHistory, edgeDefaults, setEdges, markDirty],
  );

  const addPaletteItems = useCallback(
    async (items: PaletteItem[], origin?: { x: number; y: number }): Promise<boolean> => {
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
          const portLabel = formatPortPairLabel(data.source_port || "", data.target_port || "");
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

  const projectOutsidePeers = useCallback(async () => {
    if (!mapId) return;
    try {
      const before = nodes.length;
      const projected = await projectTopologyNeighbors(mapId, { dry_run: true });
      if (projected.truncate_reason === "membership_frozen") {
        showError(t("topology.truncatedFrozen"));
        return;
      }
      const localPos = new Map(nodes.map((n) => [n.id, n.position]));
      historyLockRef.current = true;
      applyViewGraph(projected, edgeDefaults, setNodes, setEdges, localPos);
      historyLockRef.current = false;
      const added = Math.max(0, projected.nodes.length - before);
      if (added > 0) {
        markDirty();
      }
      showOk(t("topology.projectedNeighbors").replace("{{count}}", String(added)));
    } catch (err) {
      showError(String(err));
    }
  }, [
    mapId,
    nodes,
    edgeDefaults,
    setNodes,
    setEdges,
    markDirty,
    showOk,
    showError,
    t,
  ]);

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
          void removeSelected();
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
    const id = String(node?.data.managed_ne_id || node?.data.ume_ne_id || "").trim();
    if (!id) {
      showError(t("topology.discoverOneNeedNe"));
      return;
    }
    void runDiscover([id]);
  };

  const openCreateNeAt = (flowX: number, flowY: number) => {
    if (isWorldFlatViewName(activeView?.name)) {
      showError(t("topology.worldMapNoDirectNes"));
      return;
    }
    closeCtxMenu();
    setCreateNeDialog({ flowX, flowY, name: "", ip_address: "" });
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

  const submitCreateNe = useCallback(async () => {
    if (!mapId || !createNeDialog) return;
    const name = createNeDialog.name.trim();
    if (!name) {
      showError(t("topology.createNeNameRequired"));
      return;
    }
    setCreateNeBusy(true);
    try {
      const graph = await createTopologyPlaceholder(mapId, {
        name,
        ip_address: createNeDialog.ip_address.trim(),
        x: createNeDialog.flowX,
        y: createNeDialog.flowY,
      });
      queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
      historyLockRef.current = true;
      applyViewGraph(graph, edgeDefaults, setNodes, setEdges);
      historyLockRef.current = false;
      clearDirty();
      const created = graph.nodes.find(
        (n) =>
          String(n.name || "").trim() === name &&
          Math.abs(Number(n.x) - createNeDialog.flowX) < 0.5 &&
          Math.abs(Number(n.y) - createNeDialog.flowY) < 0.5,
      );
      if (created?.fabric_node_id) {
        focusNode(created.fabric_node_id, false);
      }
      setCreateNeDialog(null);
      showOk(t("topology.createNeDone").replace("{{name}}", name));
      void queryClient.invalidateQueries({ queryKey: ["managedNe"] });
      void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
    } catch (err) {
      showError(String(err));
    } finally {
      setCreateNeBusy(false);
    }
  }, [
    mapId,
    createNeDialog,
    queryClient,
    edgeDefaults,
    setNodes,
    setEdges,
    clearDirty,
    showError,
    showOk,
    t,
    focusNode,
  ]);

  const focusWorldAround = useCallback(
    async (nodeId: string, displayX: number, displayY: number) => {
      const viewId = mapIdRef.current;
      if (!viewId || !isWorldFlatCanvasRef.current || dirtyRef.current) return;
      if (flatLodTimerRef.current) {
        window.clearTimeout(flatLodTimerRef.current);
        flatLodTimerRef.current = null;
      }
      const gen = ++flatLodFetchGenRef.current;
      // Zoom immediately so the user isn't stuck on overview while detail loads.
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
      // Re-center after graph replace (fit/apply can nudge viewport) and re-select.
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
    [queryClient, focusNode],
  );

  const locateNode = useCallback(
    (nodeId: string, opts?: { worldX?: number; worldY?: number }) => {
      const onCanvas = nodesRef.current.find((n) => n.id === nodeId);
      const wt = worldTransformRef.current;
      const originX = Number(wt?.origin_x) || 0;
      const originY = Number(wt?.origin_y) || 0;
      const scale = Math.max(Number(wt?.scale) || 1, 1e-9);

      let displayX: number | undefined;
      let displayY: number | undefined;
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
    [focusNode, focusWorldAround, showError, t],
  );

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
        views.find((v) => isWorldFlatViewName(v.view_name)) ||
        views.find((v) => String(v.kind) === "physical") ||
        views[0] ||
        null;
      if (!target) {
        showError(t("topology.treeSearchNotOnMap"));
        return;
      }
      if (!confirmDiscardIfDirty()) return;
      const regionId = target.folder_id || findViewInRegion(regions, target.view_id)?.region.id;
      if (regionId) {
        setSelectedFolderId(regionId);
        setExpandedIds((p) => ({ ...p, [regionId]: true }));
      }
      if (isWorldFlatViewName(target.view_name) && worldCoords) {
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
    ],
  );

  useEffect(() => {
    if (!treeSearchOpen) return;
    const onDoc = (e: MouseEvent) => {
      const el = treeSearchRef.current;
      if (el && e.target instanceof Element && !el.contains(e.target)) {
        setTreeSearchOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [treeSearchOpen]);

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
  const graphTruncated = Boolean(graphQuery.data?.truncated);
  const truncateReason = String(graphQuery.data?.truncate_reason || "").trim();
  const worldScatter = graphQuery.data?.scatter || [];
  const worldTotal = Number(graphQuery.data?.world_transform?.total || 0);
  const worldDockMe = Number(graphQuery.data?.world_transform?.dock_me_count || 0);
  const showWorldScatter =
    isWorldFlatCanvas && worldVisualLod !== "full" && worldScatter.length > 0;
  const canvasGraphLoading = Boolean(mapId) && (graphQuery.isPending || !graphQuery.data);
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
    // World map soft-caps viewport nodes; the banner is noise there.
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

  const titleText = useMemo(() => {
    if (canvasMode) return activeLeafName || t("topology.selectMap");
    if (activeRegion) return regionDisplayName(activeRegion);
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

  return (
    <div className={`topo-page${sidebarCollapsed ? " is-sidebar-collapsed" : ""}`}>
      <aside className="topo-sidebar" aria-label={t("topology.maps")}>
        {sidebarCollapsed ? (
          <div className="topo-sidebar__rail-wrap">
            <button
              type="button"
              className="topo-sidebar__rail"
              title={t("topology.expandSidebar")}
              aria-label={t("topology.expandSidebar")}
              onClick={() => setSidebarCollapsed(false)}
            >
              <span className="topo-sidebar__rail-icon" aria-hidden="true">
                <SidebarFoldIcon expand />
              </span>
            </button>
            <button
              type="button"
              className={`topo-sidebar__rail topo-sidebar__rail--live${liveSync ? " is-on" : ""}`}
              aria-pressed={liveSync}
              title={t("topology.liveSyncHint")}
              aria-label={liveSync ? t("topology.liveSyncOn") : t("topology.liveSync")}
              onClick={() => setLiveSync((v) => !v)}
            >
              <span className="topo-sidebar__rail-live-dot" aria-hidden="true" />
            </button>
          </div>
        ) : (
          <>
            <div className="topo-sidebar__section">
              <div className="topo-tree-search" ref={treeSearchRef}>
                <div className="topo-tree-search__bar">
                  <input
                    className="input"
                    type="search"
                    value={treeNeQuery}
                    placeholder={t("topology.treeSearchPh")}
                    aria-label={t("topology.treeSearch")}
                    onChange={(e) => {
                      setTreeNeQuery(e.target.value);
                      setTreeSearchOpen(true);
                    }}
                    onFocus={() => setTreeSearchOpen(true)}
                  />
                  <button
                    type="button"
                    className="topo-sidebar__icon-btn"
                    onClick={promptNewRegion}
                    disabled={createRegionMut.isPending}
                    title={t("topology.newRegion")}
                    aria-label={t("topology.newRegion")}
                  >
                    <PlusIcon />
                  </button>
                  <button
                    type="button"
                    className="topo-sidebar__icon-btn"
                    title={t("topology.collapseSidebar")}
                    aria-label={t("topology.collapseSidebar")}
                    onClick={() => setSidebarCollapsed(true)}
                  >
                    <SidebarFoldIcon />
                  </button>
                </div>
                <div className="topo-sidebar__live">
                  <button
                    type="button"
                    className={`btn btn--sm${liveSync ? "" : " btn--ghost"}`}
                    aria-pressed={liveSync}
                    title={t("topology.liveSyncHint")}
                    onClick={() => setLiveSync((v) => !v)}
                  >
                    {liveSync ? t("topology.liveSyncOn") : t("topology.liveSync")}
                  </button>
                </div>
                {treeSearchOpen && treeNeQuery.trim() ? (
                  <div className="topo-tree-search__panel" role="listbox">
                    {debouncedTreeNeQuery.length < 1 || treeNeSearchQuery.isFetching ? (
                      <p className="topo-tree-search__hint muted">…</p>
                    ) : !(treeNeSearchQuery.data?.items || []).length ? (
                      <p className="topo-tree-search__hint muted">{t("topology.treeSearchEmpty")}</p>
                    ) : (
                      (treeNeSearchQuery.data?.items || []).map((hit) => {
                        const views = hit.views || [];
                        const title = hit.name || hit.ip || hit.id.slice(0, 8);
                        return (
                          <div key={hit.id} className="topo-tree-search__item">
                            <button
                              type="button"
                              className="topo-tree-search__ne"
                              onClick={() => jumpToTreeSearchHit(hit)}
                              title={
                                views.length
                                  ? t("topology.locateOnCanvas")
                                  : t("topology.treeSearchNotOnMap")
                              }
                            >
                              <span className="topo-tree-search__name">{title}</span>
                              {hit.ip ? <span className="topo-tree-search__ip muted">{hit.ip}</span> : null}
                              {!views.length ? (
                                <span className="topo-tree-search__meta muted">
                                  {t("topology.treeSearchNoViews")}
                                </span>
                              ) : (
                                <span className="topo-tree-search__meta muted">
                                  {t("topology.treeSearchViewCount").replace(
                                    "{{count}}",
                                    String(views.length),
                                  )}
                                </span>
                              )}
                            </button>
                            {views.length > 0 ? (
                              <div className="topo-tree-search__views">
                                {views.map((v) => (
                                  <button
                                    key={`${hit.id}-${v.view_id}`}
                                    type="button"
                                    className="topo-tree-search__view"
                                    onClick={() => jumpToTreeSearchHit(hit, v)}
                                    title={`${v.folder_name ? `${v.folder_name} / ` : ""}${v.view_name}`}
                                  >
                                    {v.folder_name
                                      ? `${v.folder_name} / ${v.view_name || v.view_id.slice(0, 8)}`
                                      : v.view_name || v.view_id.slice(0, 8)}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        );
                      })
                    )}
                    {(treeNeSearchQuery.data?.total || 0) > 30 ? (
                      <p className="topo-tree-search__hint muted">
                        {t("topology.treeSearchTruncated").replace(
                          "{{total}}",
                          String(treeNeSearchQuery.data?.total || 0),
                        )}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </div>
              {!treeRoot ? (
                <p className="panel__hint topo-tree-status" role="status">
                  {treeLoading ? (
                    <>
                      <span className="topo-loading-spinner" aria-hidden="true" />
                      {t("topology.treeLoading")}
                    </>
                  ) : treeFailed ? (
                    <>
                      {t("topology.treeLoadFailed")}
                      {treeQuery.error ? (
                        <span className="muted"> ({String(treeQuery.error)})</span>
                      ) : null}{" "}
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        onClick={() => void treeQuery.refetch()}
                      >
                        {t("topology.treeRetry")}
                      </button>
                    </>
                  ) : (
                    t("topology.emptyMaps")
                  )}
                </p>
              ) : regions.length === 0 ? (
                <p className="panel__hint">{t("topology.emptyMaps")}</p>
              ) : (
                <div className="topo-region-list-scroll">
                  <ul className="topo-map-list topo-region-list">
                    {regions.map((region) => renderWorldNavFolder(region, 0))}
                  </ul>
                </div>
              )}
              {mapId && outsidePeers.length > 0 && (
                <div className="topo-outside-peers">
                  <p className="panel__hint">
                    {t("topology.outsidePeers").replace("{{count}}", String(outsidePeers.length))}
                  </p>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => void projectOutsidePeers()}
                  >
                    {t("topology.projectNeighbors")}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </aside>

      <main className="topo-main">
        {canvasMode ? (
        <div className="topo-toolbar">
          <div className="topo-toolbar__row">
            <div className="topo-toolbar__title">
              <div className="topo-breadcrumb">
                <button
                  type="button"
                  className="topo-breadcrumb__link"
                  onClick={() => goRoot()}
                >
                  {t("topology.rootName")}
                </button>
                {activeRegion ? (
                  <>
                    <span className="topo-breadcrumb__sep">/</span>
                    {activeView &&
                    isRegionCanvasFolder(activeRegion, rootFolderId) &&
                    primaryViewOfFolder(activeRegion)?.id === activeView.id ? (
                      <span className="topo-breadcrumb__current">
                        {regionDisplayName(activeRegion)}
                        {dirty ? " *" : ""}
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="topo-breadcrumb__link"
                        onClick={() => goRegion(activeRegion.id)}
                      >
                        {regionDisplayName(activeRegion)}
                      </button>
                    )}
                  </>
                ) : null}
                {activeView &&
                !(
                  activeRegion &&
                  isRegionCanvasFolder(activeRegion, rootFolderId) &&
                  primaryViewOfFolder(activeRegion)?.id === activeView.id
                ) ? (
                  <>
                    <span className="topo-breadcrumb__sep">/</span>
                    <span className="topo-breadcrumb__current">
                      {activeView.name}
                      {dirty ? " *" : ""}
                    </span>
                  </>
                ) : null}
              </div>
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
                className="btn btn--sm"
                disabled={isWorldFlatCanvas}
                title={
                  isWorldFlatCanvas ? t("topology.worldMapNoDirectNes") : undefined
                }
                onClick={() => {
                  if (isWorldFlatCanvas) {
                    showError(t("topology.worldMapNoDirectNes"));
                    return;
                  }
                  setKeyword("");
                  setPaletteSelectedKeys([]);
                  setAddNeOpen(true);
                }}
              >
                {t("topology.addNe")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={isWorldFlatCanvas}
                title={
                  isWorldFlatCanvas ? t("topology.worldMapNoDirectNes") : undefined
                }
                onClick={() => {
                  if (isWorldFlatCanvas) {
                    showError(t("topology.worldMapNoDirectNes"));
                    return;
                  }
                  let flowX = 80 + nodes.length * 24;
                  let flowY = 80 + nodes.length * 24;
                  if (rfRef.current) {
                    const pane = document.querySelector(".react-flow__pane");
                    const rect = pane?.getBoundingClientRect();
                    if (rect && rect.width > 0 && rect.height > 0) {
                      const center = rfRef.current.screenToFlowPosition({
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                      });
                      flowX = center.x;
                      flowY = center.y;
                    }
                  }
                  openCreateNeAt(flowX, flowY);
                }}
              >
                {t("topology.createNe")}
              </button>
              <button type="button" className="btn btn--sm btn--ghost" onClick={() => goBackBrowse()}>
                {t("topology.backUp")}
              </button>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!canUndo}
                onClick={undo}
                title="Ctrl+Z"
              >
                {t("topology.undo")}
              </button>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!canRedo}
                onClick={redo}
                title="Ctrl+Y"
              >
                {t("topology.redo")}
              </button>
              <button
                type="button"
                className={`btn btn--sm${dirty ? "" : " btn--ghost"}`}
                disabled={saveMut.isPending || !dirty}
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
                className="btn btn--sm btn--ghost"
                disabled={!isWorldFlatCanvas && nodes.length === 0}
                onClick={() => fitCanvas()}
              >
                {t("topology.fit")}
              </button>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!staleEdgeIds.length}
                title={t("topology.removeStaleHint")}
                onClick={() => void removeStaleEdges()}
              >
                {t("topology.removeStale").replace("{{count}}", String(staleEdgeIds.length))}
              </button>
            </div>
          </div>
          <div className="topo-toolbar__row topo-toolbar__row--tools">
            <div className="topo-toolbar__panel" aria-label={t("topology.toolModes")}>
              <div className="topo-tools" role="toolbar" aria-label={t("topology.toolModes")}>
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
                    className={`topo-tools__btn${toolMode === mode ? " is-active" : ""}`}
                    title={`${label} (${key})`}
                    aria-pressed={toolMode === mode}
                    onClick={() => {
                      setToolMode(mode);
                      connectClickRef.current = null;
                    }}
                  >
                    <span className="topo-tools__label">{label}</span>
                    <kbd className="topo-tools__key">{key}</kbd>
                  </button>
                ))}
              </div>
            </div>
            {!fullscreen ? (
              <div
                className="topo-toolbar__panel topo-toolbar__panel--view"
                role="toolbar"
                aria-label={t("topology.display")}
                ref={setViewToolsToolbarSlot}
              />
            ) : null}
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
                onClick={() => {
                  if (discovering) {
                    void cancelDiscover();
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
                    .replace("{{stale}}", String(discoverSummary.missing))
                    .replace("{{failed}}", String(discoverSummary.failed))}
                </p>
                <p className="panel__hint">
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    onClick={() => {
                      const jobId = String(discoverReport?.job_id || "").trim();
                      const qs = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
                      openOrFocusModule({
                        moduleId: "network",
                        path: `/network/topology/lldp${qs}`,
                      });
                    }}
                  >
                    {t("topology.discoverGoLldp")}
                  </button>
                </p>
              </>
            ) : null}
          </div>
        ) : null}

        {!canvasMode ? (
          <div className="topo-browser" aria-label={t("topology.browserTitle")}>
            <div className="topo-browser__head">
              <div>
                {hexBrowseRegion ? (
                  <div className="topo-breadcrumb">
                    <button
                      type="button"
                      className="topo-breadcrumb__link"
                      onClick={() => goRoot()}
                    >
                      {t("topology.rootName")}
                    </button>
                    <span className="topo-breadcrumb__sep">/</span>
                    <span className="topo-breadcrumb__current">{regionDisplayName(hexBrowseRegion)}</span>
                  </div>
                ) : (
                  <strong>{titleText}</strong>
                )}
                <p className="topo-browser__sub">
                  {hexBrowseRegion
                    ? isUmeWorldContainer(hexBrowseRegion) && umeWorldHexModules
                      ? t("topology.browserRegionSub").replace(
                          "{{count}}",
                          String(
                            (umeWorldHexModules.drill ? 1 : 0) +
                              (umeWorldHexModules.flatView ? 1 : 0),
                          ),
                        )
                      : t("topology.browserRegionSub").replace(
                          "{{count}}",
                          String(browseEntries.length),
                        )
                    : t("topology.browserRegionsSub").replace(
                        "{{count}}",
                        String(regions.length),
                      )}
                </p>
              </div>
              <button
                type="button"
                className={`btn btn--sm${liveSync ? "" : " btn--ghost"}`}
                aria-pressed={liveSync}
                title={t("topology.liveSyncHint")}
                onClick={() => setLiveSync((v) => !v)}
              >
                {liveSync ? t("topology.liveSyncOn") : t("topology.liveSync")}
              </button>
            </div>
            {!hexBrowseRegion ? (
              treeLoading ? (
                <div className="topo-browser__empty topo-browser__empty--loading" role="status">
                  <span className="topo-loading-spinner" aria-hidden="true" />
                  <p>{t("topology.treeLoading")}</p>
                </div>
              ) : treeFailed ? (
                <div className="topo-browser__empty">
                  <p>{t("topology.treeLoadFailed")}</p>
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
              ) : regions.length === 0 ? (
                <div className="topo-browser__empty">
                  <span className="topo-browser__empty-icon" aria-hidden="true">
                    <RegionGlyph size={36} />
                  </span>
                  <p>{t("topology.emptyMaps")}</p>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={promptNewRegion}
                    disabled={createRegionMut.isPending}
                  >
                    {t("topology.newRegion")}
                  </button>
                </div>
              ) : (
                <div className="topo-browser__grid topo-browser__grid--regions">
                  {regions.map((region, idx) => (
                    <button
                      key={region.id}
                      type="button"
                      className={`topo-region-hex topo-region-hex--tone-${idx % 5}${
                        selectedFolderId === region.id ? " is-selected" : ""
                      }${hotBrowseKey === `region:${region.id}` ? " is-hot" : ""}`}
                      onMouseEnter={() => setHotBrowseKey(`region:${region.id}`)}
                      onMouseLeave={() =>
                        setHotBrowseKey((k) => (k === `region:${region.id}` ? "" : k))
                      }
                      onClick={() =>
                        isUmeWorldContainer(region) ? goUmeWorldNav() : goRegion(region.id)
                      }
                      title={t("topology.openRegion")}
                    >
                      <span className="topo-region-hex__icon" aria-hidden="true">
                        <RegionGlyph size={22} />
                      </span>
                      <span className="topo-region-hex__title">
                        <span className="topo-region-hex__name">{regionDisplayName(region)}</span>
                        <span className="topo-region-hex__meta">
                          {t("topology.regionNodeHint").replace(
                            "{{count}}",
                            String(
                              isUmeWorldContainer(region)
                                ? (region.children?.length || 0) + (region.views?.length || 0)
                                : region.views?.length || 0,
                            ),
                          )}
                        </span>
                      </span>
                    </button>
                  ))}
                  <button
                    type="button"
                    className="topo-region-hex topo-region-hex--add"
                    onClick={promptNewRegion}
                    disabled={createRegionMut.isPending}
                    title={t("topology.newRegion")}
                  >
                    <span className="topo-region-hex__plus" aria-hidden="true">
                      +
                    </span>
                    <span className="topo-region-hex__name">{t("topology.newRegion")}</span>
                  </button>
                </div>
              )
            ) : isUmeWorldContainer(hexBrowseRegion) && umeWorldHexModules ? (
              <div className="topo-browser__grid topo-browser__grid--regions">
                {umeWorldHexModules.drill ? (
                  <button
                    key={umeWorldHexModules.drill.id}
                    type="button"
                    className={`topo-region-hex topo-region-hex--tone-0${
                      hotBrowseKey === `region:${umeWorldHexModules.drill.id}` ? " is-hot" : ""
                    }`}
                    onMouseEnter={() =>
                      setHotBrowseKey(`region:${umeWorldHexModules.drill!.id}`)
                    }
                    onMouseLeave={() =>
                      setHotBrowseKey((k) =>
                        k === `region:${umeWorldHexModules.drill!.id}` ? "" : k,
                      )
                    }
                    onClick={() => goRegion(umeWorldHexModules.drill!.id)}
                    title={t("topology.openRegion")}
                  >
                    <span className="topo-region-hex__icon" aria-hidden="true">
                      <RegionGlyph size={22} />
                    </span>
                    <span className="topo-region-hex__title">
                      <span className="topo-region-hex__name">
                        {regionDisplayName(umeWorldHexModules.drill)}
                      </span>
                      <span className="topo-region-hex__meta">
                        {t("topology.regionNodeHint").replace(
                          "{{count}}",
                          String(umeWorldHexModules.drill.children?.length || 0),
                        )}
                      </span>
                    </span>
                  </button>
                ) : null}
                {umeWorldHexModules.flatView ? (
                  <button
                    key={umeWorldHexModules.flatView.id}
                    type="button"
                    className={`topo-region-hex topo-region-hex--tone-1${
                      hotBrowseKey === `view:${umeWorldHexModules.flatView.id}` ? " is-hot" : ""
                    }`}
                    onMouseEnter={() =>
                      setHotBrowseKey(`view:${umeWorldHexModules.flatView!.id}`)
                    }
                    onMouseLeave={() =>
                      setHotBrowseKey((k) =>
                        k === `view:${umeWorldHexModules.flatView!.id}` ? "" : k,
                      )
                    }
                    onClick={() =>
                      goCanvas(umeWorldHexModules.flatView!.id, hexBrowseRegion.id)
                    }
                    title={t("topology.openMap")}
                  >
                    <span className="topo-region-hex__icon" aria-hidden="true">
                      <RegionGlyph size={22} />
                    </span>
                    <span className="topo-region-hex__title">
                      <span className="topo-region-hex__name">
                        {umeWorldHexModules.flatView.name}
                      </span>
                      <span className="topo-region-hex__meta">
                        {t("topology.regionNodeHint").replace(
                          "{{count}}",
                          String(umeWorldHexModules.flatView.node_count || 0),
                        )}
                      </span>
                    </span>
                  </button>
                ) : null}
              </div>
            ) : (
              <div className="topo-browser__grid topo-browser__grid--regions">
                {(hexBrowseRegion.children || []).map((region, idx) => (
                  <button
                    key={region.id}
                    type="button"
                    className={`topo-region-hex topo-region-hex--tone-${idx % 5}${
                      selectedFolderId === region.id ? " is-selected" : ""
                    }${hotBrowseKey === `region:${region.id}` ? " is-hot" : ""}`}
                    onMouseEnter={() => setHotBrowseKey(`region:${region.id}`)}
                    onMouseLeave={() =>
                      setHotBrowseKey((k) => (k === `region:${region.id}` ? "" : k))
                    }
                    onClick={() => goRegion(region.id)}
                    title={t("topology.openRegion")}
                  >
                    <span className="topo-region-hex__icon" aria-hidden="true">
                      <RegionGlyph size={22} />
                    </span>
                    <span className="topo-region-hex__title">
                      <span className="topo-region-hex__name">{regionDisplayName(region)}</span>
                      <span className="topo-region-hex__meta">
                        {t("topology.regionNodeHint").replace(
                          "{{count}}",
                          String(region.views?.length || region.children?.length || 0),
                        )}
                      </span>
                    </span>
                  </button>
                ))}
                {browseEntries.map((v, idx) => {
                  const isPhysical = String(v.kind) === "physical";
                  const tone = isPhysical ? "physical" : String((idx + 1) % 5);
                  return (
                    <button
                      key={v.id}
                      type="button"
                      className={`topo-region-hex topo-region-hex--tone-${tone}${
                        mapId === v.id ? " is-selected" : ""
                      }${hotBrowseKey === `view:${v.id}` ? " is-hot" : ""}`}
                      onMouseEnter={() => setHotBrowseKey(`view:${v.id}`)}
                      onMouseLeave={() =>
                        setHotBrowseKey((k) => (k === `view:${v.id}` ? "" : k))
                      }
                      onClick={() => goCanvas(v.id, hexBrowseRegion.id)}
                      title={t("topology.openMap")}
                    >
                      <span className="topo-region-hex__icon" aria-hidden="true">
                        <LayerGlyph role={isPhysical ? "core" : "aggregation"} size={22} />
                      </span>
                      <span className="topo-region-hex__title">
                        <span className="topo-region-hex__name">{displayViewName(v.name, t)}</span>
                        <span className="topo-region-hex__meta">{v.node_count || 0}N</span>
                      </span>
                    </button>
                  );
                })}
                {/* Top-level「根」is nav-only with unique「根图」— no extra L2 / custom map. */}
                {String(hexBrowseRegion.parent_id || "") !== rootFolderId ? (
                  <button
                    type="button"
                    className="topo-region-hex topo-region-hex--add"
                    onClick={() => {
                      setSelectedFolderId(hexBrowseRegion.id);
                      promptNewSubRegion();
                    }}
                    title={t("topology.newSubRegion")}
                  >
                    <span className="topo-region-hex__plus" aria-hidden="true">
                      +
                    </span>
                    <span className="topo-region-hex__name">{t("topology.newSubRegion")}</span>
                  </button>
                ) : null}
              </div>
            )}
          </div>
        ) : (
          <div
            className={`topo-canvas${fullscreen ? " is-fullscreen" : ""}${toolMode === "pan" ? " is-pan-mode" : ""}`}
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
            {(() => {
              const viewToolsBody = (
                <>
              <details className="topo-toolbar__display" ref={displayMenuRef}>
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
                      checked={showPlaceholderBadge}
                      onChange={(e) => {
                        const next = e.target.checked;
                        setShowPlaceholderBadge(next);
                        persistBoolFlag(SHOW_PLACEHOLDER_BADGE_KEY, next);
                      }}
                    />
                    {t("topology.showPlaceholderBadge")}
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
                      checked={expandPhysicalLinks}
                      onChange={(e) => setExpandPhysicalLinks(e.target.checked)}
                    />
                    {t("topology.expandPhysicalLinks")}
                  </label>
                  <label className="topo-display-toggles__item">
                    <input
                      type="checkbox"
                      checked={scaleBundleWidth}
                      disabled={expandPhysicalLinks}
                      onChange={(e) => {
                        const next = e.target.checked;
                        setScaleBundleWidth(next);
                        persistBoolFlag(SCALE_BUNDLE_WIDTH_KEY, next);
                      }}
                    />
                    {t("topology.scaleBundleWidth")}
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
                      onChange={(e) => {
                        const next = e.target.checked;
                        setAutoLayoutAfterDiscover(next);
                        persistAutoLayoutAfterDiscover(next);
                      }}
                    />
                    {t("topology.autoLayoutDiscover")}
                  </label>
                  <label className="topo-display-toggles__item">
                    <input
                      type="checkbox"
                      checked={discoverAutoAddUnmatched}
                      onChange={(e) => {
                        const next = e.target.checked;
                        setDiscoverAutoAddUnmatched(next);
                        persistBoolFlag(DISCOVER_AUTO_ADD_KEY, next);
                        void updateLldpCollectPolicy({ auto_add_unmatched: next })
                          .then(() => {
                            void queryClient.invalidateQueries({
                              queryKey: queryKeys.lldpCollectDashboard,
                            });
                          })
                          .catch(() => {
                            /* local toggle still applies to this canvas discover */
                          });
                      }}
                    />
                    {t("topology.discoverAutoAddUnmatched")}
                  </label>
                  <label className="topo-display-toggles__item">
                    <input
                      type="checkbox"
                      checked={discoverProjectNeighbors}
                      onChange={(e) => {
                        const next = e.target.checked;
                        setDiscoverProjectNeighbors(next);
                        persistBoolFlag(DISCOVER_PROJECT_NEIGHBORS_KEY, next);
                      }}
                    />
                    {t("topology.discoverProjectNeighbors")}
                  </label>
                  <div className="topo-display-defaults topo-display-defaults--canvas-bg topo-display-defaults--colors">
                    <div className="topo-display-defaults__head">
                      <strong>{t("topology.canvasBg")}</strong>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        onClick={() => {
                          setCanvasBg(DEFAULT_CANVAS_BG);
                          persistCanvasBg(DEFAULT_CANVAS_BG);
                        }}
                      >
                        {t("topology.canvasBgReset")}
                      </button>
                    </div>
                    <div className="topo-display-defaults__row">
                      <span className="topo-display-defaults__name">{t("topology.canvasBgColor")}</span>
                      <input
                        type="color"
                        value={canvasBg}
                        title={t("topology.canvasBgColor")}
                        onChange={(e) => {
                          const next = e.target.value;
                          setCanvasBg(next);
                          persistCanvasBg(next);
                        }}
                      />
                      <input
                        className="topo-toolbar__select topo-canvas-bg-hex"
                        value={canvasBg}
                        spellCheck={false}
                        aria-label={t("topology.canvasBgColor")}
                        onChange={(e) => {
                          const raw = e.target.value.trim();
                          if (!/^#[0-9a-fA-F]{0,6}$/.test(raw)) return;
                          setCanvasBg(raw);
                          if (isHexColor(raw)) persistCanvasBg(raw.toLowerCase());
                        }}
                        onBlur={() => {
                          if (!isHexColor(canvasBg)) {
                            setCanvasBg(loadCanvasBg());
                          }
                        }}
                      />
                    </div>
                  </div>
                  <div className="topo-display-defaults topo-display-defaults--canvas-bg topo-display-defaults--colors">
                    <div className="topo-display-defaults__head">
                      <strong>{t("topology.textColors")}</strong>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        onClick={() => {
                          setLabelColors({ ...DEFAULT_LABEL_COLORS });
                          persistLabelColors({ ...DEFAULT_LABEL_COLORS });
                        }}
                      >
                        {t("topology.textColorsReset")}
                      </button>
                    </div>
                    {(
                      [
                        ["name", t("topology.labelColor")],
                        ["edgeLabel", t("topology.edgeLabelColor")],
                      ] as const
                    ).map(([key, label]) => (
                      <div key={key} className="topo-display-defaults__row">
                        <span className="topo-display-defaults__name">{label}</span>
                        <input
                          type="color"
                          value={labelColors[key]}
                          title={label}
                          onChange={(e) => {
                            const next = { ...labelColors, [key]: e.target.value.toLowerCase() };
                            setLabelColors(next);
                            persistLabelColors(next);
                          }}
                        />
                        <input
                          className="topo-toolbar__select topo-canvas-bg-hex"
                          value={labelColors[key]}
                          spellCheck={false}
                          aria-label={label}
                          onChange={(e) => {
                            const raw = e.target.value.trim();
                            if (!/^#[0-9a-fA-F]{0,6}$/.test(raw)) return;
                            const next = { ...labelColors, [key]: raw };
                            setLabelColors(next);
                            if (isHexColor(raw)) persistLabelColors({ ...next, [key]: raw.toLowerCase() });
                          }}
                          onBlur={() => {
                            if (!isHexColor(labelColors[key])) {
                              setLabelColors(loadLabelColors());
                            }
                          }}
                        />
                      </div>
                    ))}
                  </div>
                  <div className="topo-display-defaults topo-display-defaults--canvas-bg topo-display-defaults--colors">
                    <div className="topo-display-defaults__head">
                      <strong>{t("topology.vendorColors")}</strong>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        onClick={() => {
                          setVendorColors({ ...DEFAULT_VENDOR_COLORS });
                          persistVendorColors({ ...DEFAULT_VENDOR_COLORS });
                        }}
                      >
                        {t("topology.vendorColorsReset")}
                      </button>
                    </div>
                    {VENDOR_TONE_KEYS.map((key) => {
                      const label = t(`topology.vendorTone.${key}`);
                      return (
                        <div key={key} className="topo-display-defaults__row">
                          <span className="topo-display-defaults__name">{label}</span>
                          <input
                            type="color"
                            value={vendorColors[key]}
                            title={label}
                            onChange={(e) => {
                              const next = { ...vendorColors, [key]: e.target.value.toLowerCase() };
                              setVendorColors(next);
                              persistVendorColors(next);
                            }}
                          />
                          <input
                            className="topo-toolbar__select topo-canvas-bg-hex"
                            value={vendorColors[key]}
                            spellCheck={false}
                            aria-label={label}
                            onChange={(e) => {
                              const raw = e.target.value.trim();
                              if (!/^#[0-9a-fA-F]{0,6}$/.test(raw)) return;
                              const next = { ...vendorColors, [key]: raw };
                              setVendorColors(next);
                              if (isHexColor(raw)) {
                                persistVendorColors({ ...next, [key]: raw.toLowerCase() });
                              }
                            }}
                            onBlur={() => {
                              if (!isHexColor(vendorColors[key])) {
                                setVendorColors(loadVendorColors());
                              }
                            }}
                          />
                        </div>
                      );
                    })}
                  </div>
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
                            {[n.data.ne_ip, n.data.vendor].filter(Boolean).join(SEP)}
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
                </>
              );
              if (!fullscreen) {
                if (!viewToolsToolbarSlot) return null;
                return createPortal(viewToolsBody, viewToolsToolbarSlot);
              }
              return (
                <div className="topo-view-tools" role="toolbar" aria-label={t("topology.display")}>
                  {viewToolsBody}
                </div>
              );
            })()}
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
              <TopoDisplayContext.Provider value={displayOpts}>
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
                      ? {
                          ...sized,
                          selected: true,
                          className: "is-search-hit",
                        }
                      : { ...sized, className: undefined };
                  })
                  }
                  edges={isWorldFlatCanvas && worldVisualLod !== "full" ? [] : displayEdges}
                  nodeTypes={nodeTypes}
                  edgeTypes={edgeTypes}
                  onlyRenderVisibleElements={
                    isWorldFlatCanvas ? worldVisualLod === "dot" : true
                  }
                  connectionMode={ConnectionMode.Loose}
                  connectionLineType={ConnectionLineType.Straight}
                  connectionLineStyle={{ stroke: "#38bdf8", strokeWidth: 2 }}
                  defaultEdgeOptions={{ type: "straight", labelShowBg: false }}
                  proOptions={{ hideAttribution: true }}
                  minZoom={isWorldFlatCanvas ? 0.002 : 0.05}
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
                  onNodesChange={(changes) => {
                    if (
                      changes.some(
                        (c) => c.type === "position" || c.type === "remove" || c.type === "add",
                      )
                    ) {
                      markDirty();
                    }
                    onNodesChange(changes);
                  }}
                  onEdgesChange={(changes) => {
                    const physical = changes.filter((c) => {
                      if (c.type === "select") return false;
                      if ("id" in c && isAggregateEdgeId(String(c.id))) return false;
                      return true;
                    });
                    if (physical.some((c) => c.type !== "select")) {
                      markDirty();
                    }
                    if (physical.length) onEdgesChange(physical);
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
                    setCtxMenu(
                      multi ? { kind: "selection", ...pos } : { kind: "node", id: node.id, ...pos },
                    );
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
                        const bounds = isWorldFlatCanvas
                          ? worldDisplayBounds(worldTransformRef.current || graphQuery.data?.world_transform)
                          : null;
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
                      {t("topology.connectHint")}
                    </div>
                  ) : null}
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
                  {!isWorldFlatCanvas ? <MiniMap pannable zoomable /> : null}
                </ReactFlow>
              </TopoDisplayContext.Provider>
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

      {newRootDialog ? (
        <div
          className="topo-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="topo-new-root-title"
        >
          <div className="topo-modal__backdrop" onClick={() => setNewRootDialog(null)} />
          <div className="topo-modal__panel" style={{ maxWidth: 420 }}>
            <div className="topo-modal__head">
              <strong id="topo-new-root-title">{t("topology.newRegion")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => setNewRootDialog(null)}
              >
                {t("topology.discoverClose")}
              </button>
            </div>
            <p className="panel__hint topo-modal__hint">{t("topology.folderHint")}</p>
            <div className="form-grid" style={{ padding: "0 16px 8px" }}>
              <label>
                <span className="form-label">{t("topology.newRegionPrompt")}</span>
                <input
                  autoFocus
                  value={newRootDialog.name}
                  disabled={createRegionMut.isPending}
                  onChange={(e) => setNewRootDialog({ name: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitNewRoot();
                  }}
                />
              </label>
            </div>
            <div className="topo-modal__foot">
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={createRegionMut.isPending}
                onClick={() => setNewRootDialog(null)}
              >
                {t("topology.discoverClose")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={createRegionMut.isPending || !String(newRootDialog.name || "").trim()}
                onClick={submitNewRoot}
              >
                {createRegionMut.isPending ? "…" : t("topology.newRegion")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {createNeDialog && canvasMode ? (
        <div
          className="topo-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="topo-create-ne-title"
        >
          <div
            className="topo-modal__backdrop"
            onClick={() => {
              if (createNeBusy) return;
              setCreateNeDialog(null);
            }}
          />
          <div className="topo-modal__panel" style={{ maxWidth: 420 }}>
            <div className="topo-modal__head">
              <strong id="topo-create-ne-title">{t("topology.createNeTitle")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={createNeBusy}
                onClick={() => setCreateNeDialog(null)}
              >
                {t("topology.discoverClose")}
              </button>
            </div>
            <p className="panel__hint topo-modal__hint">{t("topology.createNeHint")}</p>
            <div className="form-grid" style={{ padding: "0 16px 8px" }}>
              <label>
                <span className="form-label">
                  {t("topology.createNeName")}
                  <span className="form-label__required" aria-hidden="true">
                    {" "}
                    *
                  </span>
                </span>
                <input
                  autoFocus
                  value={createNeDialog.name}
                  placeholder={t("topology.createNeNamePh")}
                  disabled={createNeBusy}
                  onChange={(e) =>
                    setCreateNeDialog((prev) => (prev ? { ...prev, name: e.target.value } : prev))
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitCreateNe();
                  }}
                />
              </label>
              <label>
                <span className="form-label">{t("topology.createNeIp")}</span>
                <input
                  value={createNeDialog.ip_address}
                  placeholder={t("topology.createNeIpPh")}
                  disabled={createNeBusy}
                  onChange={(e) =>
                    setCreateNeDialog((prev) =>
                      prev ? { ...prev, ip_address: e.target.value } : prev,
                    )
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitCreateNe();
                  }}
                />
              </label>
            </div>
            <div className="topo-modal__foot">
              <button
                type="button"
                className="btn btn--ghost"
                disabled={createNeBusy}
                onClick={() => setCreateNeDialog(null)}
              >
                {t("topology.discoverClose")}
              </button>
              <button
                type="button"
                className="btn btn--primary"
                disabled={createNeBusy}
                onClick={() => void submitCreateNe()}
              >
                {createNeBusy ? t("topology.createNeBusy") : t("topology.createNe")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {addNeOpen && canvasMode ? (
        <div className="topo-modal" role="dialog" aria-modal="true" aria-label={t("topology.addNe")}>
          <div
            className="topo-modal__backdrop"
            onClick={() => {
              if (paletteAdding) return;
              setAddNeOpen(false);
            }}
          />
          <div className="topo-modal__panel">
            <div className="topo-modal__head">
              <strong>{t("topology.addNe")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={paletteAdding}
                onClick={() => setAddNeOpen(false)}
              >
                {t("topology.discoverClose")}
              </button>
            </div>
            <p className="panel__hint topo-modal__hint">{t("topology.paletteHint")}</p>
            <div className="topo-palette-source" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={paletteSource === "managed"}
                className={`topo-palette-source__btn${paletteSource === "managed" ? " is-active" : ""}`}
                disabled={paletteAdding}
                onClick={() => {
                  setPaletteSource("managed");
                  setPaletteSelectedKeys([]);
                }}
              >
                {t("topology.paletteManaged")}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={paletteSource === "ume"}
                className={`topo-palette-source__btn${paletteSource === "ume" ? " is-active" : ""}`}
                disabled={paletteAdding}
                onClick={() => {
                  setPaletteSource("ume");
                  setPaletteSelectedKeys([]);
                }}
              >
                {t("topology.paletteUme")}
              </button>
            </div>
            <input
              className="input"
              value={keyword}
              onChange={(e) => {
                setKeyword(e.target.value);
                setPaletteSelectedKeys([]);
              }}
              placeholder={t("topology.filterPh")}
              disabled={paletteAdding}
              autoFocus
            />
            {paletteVisible.length > 0 ? (
              <div className="topo-modal__selectbar">
                <label className="topo-modal__selectall">
                  <input
                    type="checkbox"
                    checked={
                      paletteVisible.length > 0 &&
                      paletteVisible.every((item) => paletteSelectedKeys.includes(item.key))
                    }
                    disabled={paletteAdding}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setPaletteSelectedKeys(paletteVisible.map((item) => item.key));
                        return;
                      }
                      setPaletteSelectedKeys([]);
                    }}
                    aria-label={t("topology.selectAllVisible")}
                  />
                  <span>{t("topology.selectAllVisible")}</span>
                </label>
                <span className="panel__hint">
                  {t("topology.selectedCount").replace("{{count}}", String(paletteSelectedKeys.length))}
                </span>
              </div>
            ) : null}
            <ul className="topo-palette topo-modal__list">
              {paletteLoading ? (
                <li className="topo-palette__empty">
                  <span className="panel__hint">{t("topology.paletteLoading")}</span>
                </li>
              ) : paletteVisible.length === 0 ? (
                <li className="topo-palette__empty">
                  <span className="panel__hint">{t("topology.paletteEmpty")}</span>
                </li>
              ) : (
                paletteVisible.map((item) => {
                  const checked = paletteSelectedKeys.includes(item.key);
                  return (
                    <li key={item.key}>
                      <div className={`topo-palette__row${checked ? " is-selected" : ""}`}>
                        <label className="topo-palette__check">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={paletteAdding}
                            onChange={(e) => {
                              setPaletteSelectedKeys((prev) =>
                                e.target.checked
                                  ? [...new Set([...prev, item.key])]
                                  : prev.filter((key) => key !== item.key),
                              );
                            }}
                            aria-label={item.name}
                          />
                        </label>
                        <button
                          type="button"
                          className="topo-palette__item"
                          draggable={!paletteAdding}
                          disabled={paletteAdding}
                          onClick={() => {
                            setPaletteSelectedKeys((prev) =>
                              prev.includes(item.key)
                                ? prev.filter((key) => key !== item.key)
                                : [...prev, item.key],
                            );
                          }}
                          onDragStart={(e) => onPaletteDragStart(e, item)}
                          title={t("topology.paletteDragHint")}
                        >
                          <span className="topo-palette__name">{item.name}</span>
                          <span className="topo-palette__meta">{item.meta}</span>
                        </button>
                      </div>
                    </li>
                  );
                })
              )}
            </ul>
            <div className="topo-modal__foot">
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={paletteAdding}
                onClick={() => setAddNeOpen(false)}
              >
                {t("topology.discoverClose")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={paletteSelectedKeys.length === 0 || paletteAdding}
                onClick={async () => {
                  const selected = paletteVisible.filter((item) => paletteSelectedKeys.includes(item.key));
                  if (selected.length === 0) return;
                  const ok = await addPaletteItems(selected);
                  if (!ok) return;
                  setPaletteSelectedKeys([]);
                  setAddNeOpen(false);
                }}
              >
                {paletteAdding
                  ? t("topology.addingNe")
                  : t("topology.addSelected").replace("{{count}}", String(paletteSelectedKeys.length))}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {ctxMenu
        ? createPortal(
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
                  onClick={() => {
                    closeCtxMenu();
                    void toggleFullscreen();
                  }}
                >
                  {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
                </button>
              </li>
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
          ) : ctxMenu.kind === "pane" ? (
            <>
              <li className="topo-ctx__head" role="presentation">
                {t("topology.paneMenu")}
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  disabled={isWorldFlatViewName(activeView?.name)}
                  onClick={() => openCreateNeAt(ctxMenu.flowX, ctxMenu.flowY)}
                >
                  {t("topology.createNe")}
                </button>
              </li>
              {!isWorldFlatViewName(activeView?.name) ? (
                <li role="none">
                  <button
                    type="button"
                    className="topo-ctx__item"
                    role="menuitem"
                    disabled={createRegionMut.isPending}
                    onClick={() => promptNewSubRegion()}
                  >
                    {t("topology.newSubRegion")}
                  </button>
                </li>
              ) : null}
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  onClick={() => {
                    closeCtxMenu();
                    void toggleFullscreen();
                  }}
                >
                  {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
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
                  onClick={() => {
                    closeCtxMenu();
                    void toggleFullscreen();
                  }}
                >
                  {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
                </button>
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
              {selectedNode &&
              isPlaceholderSource(selectedNode.data.managed_source, selectedNode.data.ne_ip) ? (
                <li role="none">
                  <button
                    type="button"
                    className="topo-ctx__item topo-ctx__item--danger"
                    role="menuitem"
                    onClick={() => void purgePlaceholderById(ctxMenu.id)}
                  >
                    {t("topology.deletePlaceholder")}
                  </button>
                </li>
              ) : null}
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
              <li className="topo-ctx__head" role="presentation">
                {t("topology.edgeMenu")}
              </li>
              <li role="none">
                <button
                  type="button"
                  className="topo-ctx__item"
                  role="menuitem"
                  onClick={() => {
                    closeCtxMenu();
                    void toggleFullscreen();
                  }}
                >
                  {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
                </button>
              </li>
              <li className="topo-ctx__section" role="none">
                <div className="topo-ctx__style" onMouseDown={(e) => e.stopPropagation()}>
                  <div className="topo-ctx__style-title">{t("topology.edgeStyle")}</div>
                  {selectedEdgeResolved ? (
                    <div className="topo-ctx__style-grid">
                      {Number(selectedEdgeData.member_count || 1) > 1 ? (
                        <div className="topo-ctx__style-members">
                          <div className="topo-ctx__style-title">
                            {t("topology.linkMembers").replace(
                              "{{count}}",
                              String(selectedEdgeData.member_count || 0),
                            )}
                          </div>
                          <ul className="topo-ctx__member-list">
                            {(selectedEdgeData.members || []).map((m) => (
                              <li key={m.id}>
                                {formatPortPairLabel(m.a_port, m.b_port) || m.id.slice(0, 8)}
                              </li>
                            ))}
                          </ul>
                          {!expandPhysicalLinks ? (
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => {
                                setExpandPhysicalLinks(true);
                                closeCtxMenu();
                              }}
                            >
                              {t("topology.expandPhysicalLinks")}
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                      <label className="topo-ctx__style-row">
                        <span>
                          {(selectedEdgeSourceNode?.data.label ||
                            selectedEdgeSourceNode?.data.ne_ip ||
                            t("topology.endpointA")) +
                            " · " +
                            t("topology.port")}
                        </span>
                        <input
                          type="text"
                          className="topo-ctx__style-input"
                          value={selectedEdgeData.source_port || ""}
                          placeholder="?"
                          disabled={Number(selectedEdgeData.member_count || 1) > 1 && !expandPhysicalLinks}
                          onFocus={() => pushHistory()}
                          onChange={(e) =>
                            patchSelectedEdgeStyle({ source_port: e.target.value }, { skipHistory: true })
                          }
                        />
                      </label>
                      <label className="topo-ctx__style-row">
                        <span>
                          {(selectedEdgeTargetNode?.data.label ||
                            selectedEdgeTargetNode?.data.ne_ip ||
                            t("topology.endpointB")) +
                            " · " +
                            t("topology.port")}
                        </span>
                        <input
                          type="text"
                          className="topo-ctx__style-input"
                          value={selectedEdgeData.target_port || ""}
                          placeholder="?"
                          disabled={Number(selectedEdgeData.member_count || 1) > 1 && !expandPhysicalLinks}
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
            </ul>,
            canvasRef.current || document.body,
          )
        : null}
    </div>
  );
}
