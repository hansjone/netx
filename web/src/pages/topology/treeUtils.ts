import type { CSSProperties } from "react";
import type { TopologyTreeFolderItem, TopologyTreeViewItem, TopologyWorldTransform } from "../../types";
import { WORLD_MAP_ENABLED } from "./constants";

export { WORLD_MAP_ENABLED };

export function findViewInRegion(
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

export function findFolderInTree(
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

export function indexFoldersById(
  folders: TopologyTreeFolderItem[],
  acc: Map<string, TopologyTreeFolderItem> = new Map(),
): Map<string, TopologyTreeFolderItem> {
  for (const folder of folders) {
    acc.set(folder.id, folder);
    indexFoldersById(folder.children || [], acc);
  }
  return acc;
}

export function folderPathIds(folders: TopologyTreeFolderItem[], folderId: string): string[] {
  const byId = indexFoldersById(folders);
  const path: string[] = [];
  let cur: string | undefined = folderId;
  const seen = new Set<string>();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    path.push(cur);
    const folder = byId.get(cur);
    const parent = String(folder?.parent_id || "").trim();
    if (!folder || !parent || !byId.has(parent)) break;
    cur = parent;
  }
  return path;
}

export function folderPathFolders(
  folders: TopologyTreeFolderItem[],
  folderId: string,
): TopologyTreeFolderItem[] {
  if (!folderId) return [];
  const byId = indexFoldersById(folders);
  return folderPathIds(folders, folderId)
    .map((id) => byId.get(id))
    .filter((f): f is TopologyTreeFolderItem => Boolean(f))
    .reverse();
}

export function isUmeWorldContainer(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  const ext = String(folder.external_ref || "").trim();
  return ext === "ume:world" || folder.name === "UME World";
}

export function isUmeWorldNavFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  const ext = String(folder.external_ref || "").trim();
  if (isUmeWorldContainer(folder) || ext === "ume:world:drill") return true;
  return Boolean(ext);
}

export function isWorldDrillFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  const ext = String(folder.external_ref || "").trim();
  return ext === "ume:world:drill" || (folder.name === "World" && Boolean(folder.is_system));
}

export function isWorldFlatViewName(name: string | undefined | null): boolean {
  const n = String(name || "").trim();
  return n === "世界地图" || n === "完整世界地图" || n === "World map";
}

export function isManualRootMapName(name: string | undefined | null): boolean {
  const n = String(name || "").trim();
  return n === "根图" || n === "Root map";
}

export function displayViewName(name: string | undefined | null, t: (key: string) => string): string {
  if (isWorldFlatViewName(name)) return t("topology.worldMapName");
  if (isManualRootMapName(name)) return t("topology.rootMapName");
  return String(name || "").trim();
}

export function isUmeStructuralFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder) return false;
  if (isUmeWorldContainer(folder) || isWorldDrillFolder(folder)) return true;
  const ext = String(folder.external_ref || "").trim();
  return ext === "ume:world" || ext === "ume:world:drill";
}

export function isManualRootMapFolder(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder || isUmeStructuralFolder(folder)) return false;
  return Boolean(folder.is_system) && !String(folder.external_ref || "").trim();
}

export function isUmeSyncedSubRegion(folder: TopologyTreeFolderItem | null | undefined): boolean {
  if (!folder || isUmeStructuralFolder(folder)) return false;
  return Boolean(String(folder.external_ref || "").trim());
}

export function worldVisualLodFromZoom(zoom: number): "dot" | "pin" | "full" {
  if (zoom < 0.04) return "dot";
  if (zoom < 0.12) return "pin";
  return "full";
}

export function worldDisplayBounds(wt: TopologyWorldTransform | null | undefined): {
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

export function isRegionCanvasFolder(
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

export function regionDisplayName(
  region: TopologyTreeFolderItem | null | undefined,
  t?: (key: string) => string,
): string {
  if (!region) return "";
  const raw = String(region.name || "").trim();
  if (t && isManualRootMapName(raw)) return t("topology.rootMapName");
  return raw;
}

export function folderNeCount(folder: TopologyTreeFolderItem | null | undefined): number {
  if (!folder) return 0;
  return Math.max(0, Math.floor(Number(folder.ne_count) || 0));
}

export function formatNeCount(count: number): string {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  return `${n}N`;
}

export function canvasBgLuminance(bg: string): number {
  const hex = String(bg || "").replace("#", "").trim();
  if (hex.length !== 6) return 0.1;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  if (![r, g, b].every((n) => Number.isFinite(n))) return 0.1;
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

export function canvasFloatChromeVars(bg: string): CSSProperties {
  const light = canvasBgLuminance(bg) > 0.55;
  if (light) {
    return {
      "--topo-float-bg": "rgba(255, 255, 255, 0.92)",
      "--topo-float-border": "rgba(15, 23, 42, 0.08)",
      "--topo-float-shadow": "0 8px 24px rgba(15, 23, 42, 0.12)",
      "--topo-float-fg": "#0f172a",
      "--topo-float-muted": "#64748b",
      "--topo-float-btn-bg": "transparent",
      "--topo-float-btn-border": "transparent",
      "--topo-float-btn-hover": "rgba(15, 23, 42, 0.06)",
      "--topo-float-active-bg": "rgba(37, 99, 235, 0.16)",
      "--topo-float-active-fg": "#1d4ed8",
      "--topo-float-active-border": "#3b82f6",
      "--topo-float-control-bg": "transparent",
      "--topo-float-control-hover": "rgba(15, 23, 42, 0.06)",
      "--topo-float-control-fill": "#334155",
      "--topo-float-input-bg": "#ffffff",
      "--topo-float-input-fg": "#0f172a",
      "--topo-float-input-border": "rgba(15, 23, 42, 0.16)",
    } as CSSProperties;
  }
  return {
    "--topo-float-bg": "rgba(15, 23, 42, 0.92)",
    "--topo-float-border": "rgba(148, 163, 184, 0.18)",
    "--topo-float-shadow": "0 8px 24px rgba(0, 0, 0, 0.35)",
    "--topo-float-fg": "#e2e8f0",
    "--topo-float-muted": "#94a3b8",
    "--topo-float-btn-bg": "transparent",
    "--topo-float-btn-border": "transparent",
    "--topo-float-btn-hover": "rgba(30, 41, 59, 0.9)",
    "--topo-float-active-bg": "rgba(37, 99, 235, 0.28)",
    "--topo-float-active-fg": "#93c5fd",
    "--topo-float-active-border": "#3b82f6",
    "--topo-float-control-bg": "transparent",
    "--topo-float-control-hover": "rgba(30, 41, 59, 0.9)",
    "--topo-float-control-fill": "#cbd5e1",
    "--topo-float-input-bg": "#0b1220",
    "--topo-float-input-fg": "#e2e8f0",
    "--topo-float-input-border": "rgba(148, 163, 184, 0.28)",
  } as CSSProperties;
}
