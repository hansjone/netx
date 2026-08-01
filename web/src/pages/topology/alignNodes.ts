import type { Node } from "@xyflow/react";

export type AlignKind = "left" | "right" | "top" | "bottom" | "hDistribute" | "vDistribute";

export function alignNodes<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  kind: AlignKind,
  selectedIds: Set<string>,
): Node<T>[] {
  const selected = nodes.filter((n) => selectedIds.has(n.id));
  if (selected.length < 2) return nodes;

  const xs = selected.map((n) => n.position.x);
  const ys = selected.map((n) => n.position.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  const byX = [...selected].sort((a, b) => a.position.x - b.position.x);
  const byY = [...selected].sort((a, b) => a.position.y - b.position.y);

  const next = new Map<string, { x: number; y: number }>();
  if (kind === "left") {
    for (const n of selected) next.set(n.id, { x: minX, y: n.position.y });
  } else if (kind === "right") {
    for (const n of selected) next.set(n.id, { x: maxX, y: n.position.y });
  } else if (kind === "top") {
    for (const n of selected) next.set(n.id, { x: n.position.x, y: minY });
  } else if (kind === "bottom") {
    for (const n of selected) next.set(n.id, { x: n.position.x, y: maxY });
  } else if (kind === "hDistribute") {
    if (byX.length === 2) {
      next.set(byX[0].id, { ...byX[0].position });
      next.set(byX[1].id, { ...byX[1].position });
    } else {
      const span = maxX - minX;
      byX.forEach((n, i) => {
        next.set(n.id, {
          x: minX + (span * i) / (byX.length - 1),
          y: n.position.y,
        });
      });
    }
  } else if (kind === "vDistribute") {
    if (byY.length === 2) {
      next.set(byY[0].id, { ...byY[0].position });
      next.set(byY[1].id, { ...byY[1].position });
    } else {
      const span = maxY - minY;
      byY.forEach((n, i) => {
        next.set(n.id, {
          x: n.position.x,
          y: minY + (span * i) / (byY.length - 1),
        });
      });
    }
  }

  return nodes.map((n) => {
    const p = next.get(n.id);
    return p ? { ...n, position: p } : n;
  });
}
