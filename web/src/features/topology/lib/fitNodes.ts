import type { Node, ReactFlowInstance } from "@xyflow/react";
import { TOPO_NODE_H, TOPO_NODE_W } from "./topoGeometry";

type FitOpts = {
  padding: number;
  duration?: number;
  /** Smallest box to fit (flow units); keeps a single-node locate from zooming to max. */
  minWidth?: number;
  minHeight?: number;
};

/**
 * Fit the viewport to nodes using their known geometry.
 *
 * xyflow's fitView waits until the targeted nodes are measured. With
 * onlyRenderVisibleElements, nodes outside the initial viewport are never rendered,
 * so on a large canvas far from the origin fitView stays queued forever and the
 * user sees an empty canvas. Our nodes carry explicit width/height, so we compute
 * the bounds ourselves and use fitBounds, which needs no measurement.
 */
export function fitToNodes(
  inst: Pick<ReactFlowInstance, "fitBounds">,
  nodes: Array<Pick<Node, "position" | "width" | "height">>,
  opts: FitOpts,
): boolean {
  if (!nodes.length) return false;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    const w = Number(n.width) || TOPO_NODE_W;
    const h = Number(n.height) || TOPO_NODE_H;
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    maxX = Math.max(maxX, n.position.x + w);
    maxY = Math.max(maxY, n.position.y + h);
  }
  if (!Number.isFinite(minX)) return false;
  const width = Math.max(1, opts.minWidth ?? 0, maxX - minX);
  const height = Math.max(1, opts.minHeight ?? 0, maxY - minY);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  void inst.fitBounds(
    { x: cx - width / 2, y: cy - height / 2, width, height },
    { padding: opts.padding, duration: opts.duration ?? 0 },
  );
  return true;
}
