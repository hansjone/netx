import { openNewModuleWindow } from "./moduleWindows";

export const PORT_TRAFFIC_WALL_MODULE_ID = "port-traffic-wall";

export function portTrafficBoardPath(boardId: string): string {
  return `/port-traffic/wall/${encodeURIComponent(boardId)}`;
}

/** Open a dedicated browser tab for one board (outside Network management). */
export function openPortTrafficBoardWindow(boardId: string): void {
  const id = String(boardId || "").trim();
  if (!id) return;
  openNewModuleWindow({
    moduleId: PORT_TRAFFIC_WALL_MODULE_ID,
    path: portTrafficBoardPath(id),
  });
}
