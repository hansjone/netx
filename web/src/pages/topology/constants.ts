/** Flat world-map canvas (LOD / scatter). Temporarily offline in the UI. */
export const WORLD_MAP_ENABLED = false;

export const LAST_LEAF_KEY = "netx.topology.lastLeafViewId";
export const TREE_EXPAND_KEY = "netx.topology.treeExpanded";
export const SIDEBAR_WIDTH_KEY = "netx.topology.sidebarWidth";
export const SIDEBAR_WIDTH_DEFAULT = 260;
export const SIDEBAR_WIDTH_MIN = 180;
export const SIDEBAR_WIDTH_MAX = 560;

export const UNDO_MAX = 40;
export const PALETTE_DND = "application/x-netx-topo-palette";
export const SEP = " / ";

export const FIT_VIEW_OPTS = { padding: 0.2, includeHiddenNodes: true } as const;
export const WORLD_LOCATE_ZOOM = 0.22;
export const WORLD_LOCATE_HALF = 12000;
export const WORLD_FLAT_ACCUM_CAP = 8000;

export const EDGE_DEFAULTS_KEY = "netx.topology.edgeDefaults";
export const AUTO_LAYOUT_DISCOVER_KEY = "netx.topology.autoLayoutAfterDiscover";
export const DISCOVER_AUTO_ADD_KEY = "netx.topology.discoverAutoAddUnmatched.v2";
export const DISCOVER_PROJECT_NEIGHBORS_KEY = "netx.topology.discoverProjectNeighbors.v2";
export const SCALE_BUNDLE_WIDTH_KEY = "netx.topology.scaleBundleWidth";
export const SHOW_PLACEHOLDER_BADGE_KEY = "netx.topology.showPlaceholderBadge";
export const CANVAS_BG_KEY = "netx.topology.canvasBg";
export const DEFAULT_CANVAS_BG = "#0f172a";
export const LEGACY_CANVAS_BG = "#dbeafe";
export const LABEL_COLORS_KEY = "netx.topology.labelColors";
export const VENDOR_COLORS_KEY = "netx.topology.vendorColors";
