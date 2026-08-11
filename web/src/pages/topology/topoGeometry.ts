/** Fixed box for onlyRenderVisibleElements (xyflow skips off-screen mount when sized + handles set). */
export const TOPO_NODE_W = 80;
export const TOPO_NODE_H = 25;
export const TOPO_ICON = 25;
/** region-building.png is 195×133 — keep aspect so icon center == handle. */
export const TOPO_REGION_ICON_H = Math.round((TOPO_ICON * 133) / 195);
export const TOPO_HANDLE_X = TOPO_NODE_W / 2;
export const TOPO_HANDLE_Y = TOPO_ICON / 2;
export const TOPO_REGION_HANDLE_X = TOPO_NODE_W / 2;
export const TOPO_REGION_HANDLE_Y = TOPO_REGION_ICON_H / 2;

/** API x/y is icon center; RF position is top-left of the node box. */
export function iconFlowPosition(
  apiX: number,
  apiY: number,
  handleX: number,
  handleY: number,
): { x: number; y: number } {
  return { x: apiX - handleX, y: apiY - handleY };
}

export function iconApiPosition(
  flowX: number,
  flowY: number,
  handleX: number,
  handleY: number,
): { x: number; y: number } {
  return { x: flowX + handleX, y: flowY + handleY };
}

export function regionFlowPosition(apiX: number, apiY: number): { x: number; y: number } {
  return iconFlowPosition(apiX, apiY, TOPO_REGION_HANDLE_X, TOPO_REGION_HANDLE_Y);
}

export function regionApiPosition(flowX: number, flowY: number): { x: number; y: number } {
  return iconApiPosition(flowX, flowY, TOPO_REGION_HANDLE_X, TOPO_REGION_HANDLE_Y);
}

export function neFlowPosition(apiX: number, apiY: number): { x: number; y: number } {
  return iconFlowPosition(apiX, apiY, TOPO_HANDLE_X, TOPO_HANDLE_Y);
}

export function neApiPosition(flowX: number, flowY: number): { x: number; y: number } {
  return iconApiPosition(flowX, flowY, TOPO_HANDLE_X, TOPO_HANDLE_Y);
}

export function isRegionFabricId(id: string): boolean {
  return String(id || "").startsWith("region:");
}
