import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from "@xyflow/react";

/** Offset parallel physical links so they do not stack on one line (Visio-like elbows). */
export function ParallelEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  label,
  labelStyle,
  data,
  selected,
}: EdgeProps) {
  const index = Number((data as { parallelIndex?: number } | undefined)?.parallelIndex || 0);
  const count = Math.max(1, Number((data as { parallelCount?: number } | undefined)?.parallelCount || 1));
  const offset = (index - (count - 1) / 2) * 12;
  const dx = targetX - sourceX;
  const dy = targetY - sourceY;
  const len = Math.hypot(dx, dy) || 1;
  const ox = (-dy / len) * offset;
  const oy = (dx / len) * offset;
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX: sourceX + ox,
    sourceY: sourceY + oy,
    targetX: targetX + ox,
    targetY: targetY + oy,
    sourcePosition,
    targetPosition,
    borderRadius: 8,
  });
  return (
    <>
      <BaseEdge id={id} path={path} style={style} markerEnd={markerEnd} />
      {label ? (
        <EdgeLabelRenderer>
          <div
            className={`topo-edge-label${selected ? " is-selected" : ""}`}
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: "none",
              ...labelStyle,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}
