import dagre from "dagre";
import type { Edge, Node } from "@xyflow/react";

export type LayoutKind = "hierarchical-tb" | "hierarchical-lr" | "force" | "grid" | "radial";

const NODE_W = 140;
const NODE_H = 72;

type XY = { x: number; y: number };

function applyPositions<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  positions: Map<string, XY>,
): Node<T>[] {
  return nodes.map((n) => {
    const p = positions.get(n.id);
    if (!p) return n;
    return { ...n, position: { x: p.x, y: p.y } };
  });
}

function layoutDagre<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  edges: Edge[],
  rankdir: "TB" | "LR",
): Node<T>[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir, nodesep: 48, ranksep: 72, marginx: 24, marginy: 24 });
  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
  }
  for (const e of edges) {
    if (g.hasNode(e.source) && g.hasNode(e.target)) {
      g.setEdge(e.source, e.target);
    }
  }
  dagre.layout(g);
  const positions = new Map<string, XY>();
  for (const n of nodes) {
    const dn = g.node(n.id);
    if (!dn) continue;
    positions.set(n.id, { x: dn.x - NODE_W / 2, y: dn.y - NODE_H / 2 });
  }
  return applyPositions(nodes, positions);
}

/** Lightweight spring layout (no d3 dependency). */
function layoutForce<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  edges: Edge[],
  iterations = 120,
): Node<T>[] {
  if (nodes.length === 0) return nodes;
  const pos = new Map<string, XY>();
  const vel = new Map<string, XY>();
  const cx = 400;
  const cy = 300;
  nodes.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length;
    const r = 80 + nodes.length * 4;
    pos.set(n.id, {
      x: n.position?.x || cx + Math.cos(angle) * r,
      y: n.position?.y || cy + Math.sin(angle) * r,
    });
    vel.set(n.id, { x: 0, y: 0 });
  });
  const ids = nodes.map((n) => n.id);
  const links = edges
    .filter((e) => pos.has(e.source) && pos.has(e.target))
    .map((e) => ({ s: e.source, t: e.target }));

  const repulsion = 2800;
  const attraction = 0.04;
  const ideal = 160;
  const damping = 0.85;

  for (let iter = 0; iter < iterations; iter++) {
    for (const id of ids) {
      const p = pos.get(id)!;
      let fx = 0;
      let fy = 0;
      for (const other of ids) {
        if (other === id) continue;
        const q = pos.get(other)!;
        let dx = p.x - q.x;
        let dy = p.y - q.y;
        let dist2 = dx * dx + dy * dy;
        if (dist2 < 1) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          dist2 = dx * dx + dy * dy;
        }
        const dist = Math.sqrt(dist2);
        const force = repulsion / dist2;
        fx += (dx / dist) * force;
        fy += (dy / dist) * force;
      }
      for (const link of links) {
        if (link.s !== id && link.t !== id) continue;
        const other = link.s === id ? link.t : link.s;
        const q = pos.get(other)!;
        const dx = q.x - p.x;
        const dy = q.y - p.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - ideal) * attraction;
        fx += (dx / dist) * force;
        fy += (dy / dist) * force;
      }
      const v = vel.get(id)!;
      v.x = (v.x + fx) * damping;
      v.y = (v.y + fy) * damping;
      p.x += v.x;
      p.y += v.y;
    }
  }
  return applyPositions(nodes, pos);
}

function layoutGrid<T extends Record<string, unknown>>(nodes: Node<T>[]): Node<T>[] {
  const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
  const gapX = NODE_W + 40;
  const gapY = NODE_H + 48;
  return nodes.map((n, i) => ({
    ...n,
    position: {
      x: (i % cols) * gapX + 40,
      y: Math.floor(i / cols) * gapY + 40,
    },
  }));
}

function layoutRadial<T extends Record<string, unknown>>(nodes: Node<T>[]): Node<T>[] {
  if (nodes.length === 0) return nodes;
  if (nodes.length === 1) {
    return [{ ...nodes[0], position: { x: 320, y: 240 } }];
  }
  const cx = 400;
  const cy = 300;
  const r = 60 + nodes.length * 18;
  return nodes.map((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    return {
      ...n,
      position: {
        x: cx + Math.cos(angle) * r - NODE_W / 2,
        y: cy + Math.sin(angle) * r - NODE_H / 2,
      },
    };
  });
}

export function layoutGraph<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  edges: Edge[],
  kind: LayoutKind,
  opts?: { onlyIds?: Set<string> },
): Node<T>[] {
  const only = opts?.onlyIds;
  const targets = only && only.size > 0 ? nodes.filter((n) => only.has(n.id)) : nodes;
  const targetIds = new Set(targets.map((n) => n.id));
  const subEdges = edges.filter((e) => targetIds.has(e.source) && targetIds.has(e.target));

  let laid: Node<T>[];
  switch (kind) {
    case "hierarchical-lr":
      laid = layoutDagre(targets, subEdges, "LR");
      break;
    case "force":
      laid = layoutForce(targets, subEdges);
      break;
    case "grid":
      laid = layoutGrid(targets);
      break;
    case "radial":
      laid = layoutRadial(targets);
      break;
    case "hierarchical-tb":
    default:
      laid = layoutDagre(targets, subEdges, "TB");
      break;
  }

  if (!only || only.size === 0) return laid;
  const byId = new Map(laid.map((n) => [n.id, n]));
  return nodes.map((n) => byId.get(n.id) || n);
}

export function alignNodes<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  ids: string[],
  kind: "left" | "right" | "top" | "bottom" | "h-center" | "v-center" | "h-distribute" | "v-distribute",
): Node<T>[] {
  const selected = nodes.filter((n) => ids.includes(n.id));
  if (selected.length < 2) return nodes;
  const xs = selected.map((n) => n.position.x);
  const ys = selected.map((n) => n.position.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const midX = (minX + maxX) / 2;
  const midY = (minY + maxY) / 2;

  const pos = new Map<string, XY>();
  if (kind === "h-distribute") {
    const sorted = [...selected].sort((a, b) => a.position.x - b.position.x);
    const span = maxX - minX;
    sorted.forEach((n, i) => {
      const x = sorted.length === 1 ? minX : minX + (span * i) / (sorted.length - 1);
      pos.set(n.id, { x, y: n.position.y });
    });
  } else if (kind === "v-distribute") {
    const sorted = [...selected].sort((a, b) => a.position.y - b.position.y);
    const span = maxY - minY;
    sorted.forEach((n, i) => {
      const y = sorted.length === 1 ? minY : minY + (span * i) / (sorted.length - 1);
      pos.set(n.id, { x: n.position.x, y });
    });
  } else {
    for (const n of selected) {
      let x = n.position.x;
      let y = n.position.y;
      if (kind === "left") x = minX;
      if (kind === "right") x = maxX;
      if (kind === "top") y = minY;
      if (kind === "bottom") y = maxY;
      if (kind === "h-center") x = midX;
      if (kind === "v-center") y = midY;
      pos.set(n.id, { x, y });
    }
  }
  return applyPositions(nodes, pos);
}
