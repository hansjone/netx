import type { Edge, Node, Position } from "@xyflow/react";
import type { TopologyViewEdgeItem, TopologyViewGraph, TopologyViewNodeItem } from "../../types";
import type { NeNodeData } from "./TopologyReactFlowView";
import { formatPortPairLabel } from "./linkDisplay";
import type { EdgeDefaults, EdgeStyleData } from "./edgeStyle";
import { withEdgeVisual } from "./edgeStyle";
import {
  TOPO_NODE_H,
  TOPO_NODE_W,
  TOPO_REGION_HANDLE_X,
  TOPO_REGION_HANDLE_Y,
  TOPO_REGION_ICON_H,
  TOPO_HANDLE_X,
  TOPO_HANDLE_Y,
  neApiPosition,
  neFlowPosition,
  regionApiPosition,
  regionFlowPosition,
} from "./topoGeometry";

export function graphToFlow(
  nodes: TopologyViewNodeItem[],
  edges: TopologyViewEdgeItem[],
  defaults: EdgeDefaults,
) {
  const rfNodes: Node<NeNodeData>[] = nodes.map((n) => {
    const isRegion =
      n.kind === "region" || n.device_type === "region" || String(n.fabric_node_id || "").startsWith("region:");
    const apiX = Number(n.x) || 0;
    const apiY = Number(n.y) || 0;
    const position = isRegion ? regionFlowPosition(apiX, apiY) : neFlowPosition(apiX, apiY);
    const hx = isRegion ? TOPO_REGION_HANDLE_X : TOPO_HANDLE_X;
    const hy = isRegion ? TOPO_REGION_HANDLE_Y : TOPO_HANDLE_Y;
    return {
      id: n.fabric_node_id,
      type: "neNode",
      position,
      width: TOPO_NODE_W,
      height: isRegion ? TOPO_REGION_ICON_H : TOPO_NODE_H,
      handles: [
        { type: "target", position: "left" as Position, x: hx, y: hy },
        { type: "source", position: "right" as Position, x: hx, y: hy },
      ],
      data: {
        label: n.label || n.name || n.ip || n.fabric_node_id,
        managed_ne_id: n.managed_ne_id || "",
        ume_ne_id: n.ume_ne_id || "",
        ne_ip: n.ip || "",
        vendor: n.vendor || "",
        connect_status: n.connect_status || "",
        managed_source: n.managed_source || "",
        level: n.level ?? null,
        fabric_role: n.role || "",
        kind: (n.kind as NeNodeData["kind"]) || (isRegion ? "region" : "ne"),
        folder_id: n.folder_id || "",
        view_id: n.view_id || "",
        node_count: n.node_count || 0,
      },
    };
  });
  const rfEdges: Edge[] = edges.map((e) => {
    const src = e.status === "stale" || e.status === "missing" ? "stale" : e.source || "manual";
    const label = formatPortPairLabel(e.a_port || "", e.b_port || "", e.display_label || "");
    const data: EdgeStyleData = {
      source: src,
      source_port: e.a_port || "",
      target_port: e.b_port || "",
      stroke_color: e.stroke_color || "",
      stroke_width: Number(e.stroke_width || 0),
      line_style: e.line_style || "",
      discovered_at: e.discovered_at ?? null,
      display_label: e.display_label || "",
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

export function flowToPositions(nodes: Node<NeNodeData>[]) {
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

export function graphFingerprint(graph: TopologyViewGraph): string {
  const nodes = [...graph.nodes]
    .map(
      (n) =>
        `${n.fabric_node_id}:${Math.round(Number(n.x) || 0)}:${Math.round(Number(n.y) || 0)}:${n.label || n.name || ""}:${n.connect_status || ""}:${n.level ?? ""}:${n.role || ""}`,
    )
    .sort()
    .join("|");
  const edges = [...graph.edges]
    .map((e) => `${e.id}:${e.a_node_id}:${e.b_node_id}:${e.a_port || ""}:${e.b_port || ""}:${e.status || ""}`)
    .sort()
    .join("|");
  return `${nodes}#${edges}#${graph.outside_peers?.length || 0}#${graph.world_transform?.lod || ""}#${graph.scatter?.length || 0}#${graph.world_transform?.total || 0}`;
}

/** Overlay / inventory fields safe to refresh while the canvas has unsaved geometry edits. */
export function overlayFingerprint(graph: TopologyViewGraph): string {
  return [...graph.nodes]
    .map(
      (n) =>
        `${n.fabric_node_id}:${n.connect_status || ""}:${n.level ?? ""}:${n.role || ""}:${n.ume_ne_id || ""}:${n.managed_ne_id || ""}:${n.ip || ""}:${n.vendor || ""}:${n.managed_source || ""}`,
    )
    .sort()
    .join("|");
}

/**
 * Patch live overlay fields onto existing RF nodes without touching positions/selection.
 * Used when dirty lock blocks a full graph apply.
 */
export function mergeOverlayIntoNodes(
  current: Node<NeNodeData>[],
  graph: TopologyViewGraph,
): { nodes: Node<NeNodeData>[]; changed: boolean } {
  const byId = new Map(graph.nodes.map((n) => [n.fabric_node_id, n]));
  let changed = false;
  const nodes = current.map((node) => {
    const g = byId.get(node.id);
    if (!g || node.data.kind === "region") return node;
    const next = {
      connect_status: g.connect_status || "",
      level: g.level ?? null,
      fabric_role: g.role || "",
      ume_ne_id: g.ume_ne_id || "",
      managed_ne_id: g.managed_ne_id || "",
      ne_ip: g.ip || "",
      vendor: g.vendor || "",
      managed_source: g.managed_source || "",
    };
    if (
      node.data.connect_status === next.connect_status &&
      (node.data.level ?? null) === next.level &&
      (node.data.fabric_role || "") === next.fabric_role &&
      (node.data.ume_ne_id || "") === next.ume_ne_id &&
      (node.data.managed_ne_id || "") === next.managed_ne_id &&
      (node.data.ne_ip || "") === next.ne_ip &&
      (node.data.vendor || "") === next.vendor &&
      (node.data.managed_source || "") === next.managed_source
    ) {
      return node;
    }
    changed = true;
    return { ...node, data: { ...node.data, ...next } };
  });
  return { nodes, changed };
}

export function applyViewGraph(
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
