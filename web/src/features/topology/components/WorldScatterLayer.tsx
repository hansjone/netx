import { useEffect, useRef } from "react";
import { Panel, useStore, type ReactFlowState } from "@xyflow/react";

export type WorldScatterPoint = { x: number; y: number };

type Props = {
  points: WorldScatterPoint[];
  /** "dot" = tiny starfield; "pin" = slightly larger. */
  mode: "dot" | "pin";
  visible: boolean;
};

const selectViewport = (s: ReactFlowState) => s.transform;

/**
 * Screen-space starfield for the flat world map.
 * Mounted via Panel (outside the RF viewport transform) so dots stay ~constant
 * pixel size when zoomed far out — unlike RF DOM nodes.
 */
export function WorldScatterLayer({ points, mode, visible }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const transform = useStore(selectViewport);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = parent.clientWidth || 1;
    const h = parent.clientHeight || 1;
    if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!points.length) return;

    const [tx, ty, zoom] = transform;
    const z = Math.max(Number(zoom) || 0.001, 0.001);
    const r = mode === "pin" ? 3.5 : 2;
    const useDensity = points.length > 6000 || z < 0.02;

    if (useDensity) {
      const cell = mode === "pin" ? 8 : 6;
      const cols = Math.ceil(w / cell) + 1;
      const rows = Math.ceil(h / cell) + 1;
      const counts = new Uint16Array(cols * rows);
      let maxC = 1;
      for (const p of points) {
        const sx = Number(p.x) * z + tx;
        const sy = Number(p.y) * z + ty;
        if (sx < -cell || sy < -cell || sx > w + cell || sy > h + cell) continue;
        const cx = Math.min(cols - 1, Math.max(0, Math.floor(sx / cell)));
        const cy = Math.min(rows - 1, Math.max(0, Math.floor(sy / cell)));
        const i = cy * cols + cx;
        const n = (counts[i] = (counts[i] + 1) as number);
        if (n > maxC) maxC = n;
      }
      for (let i = 0; i < counts.length; i++) {
        const c = counts[i];
        if (!c) continue;
        const cx = (i % cols) * cell + cell / 2;
        const cy = Math.floor(i / cols) * cell + cell / 2;
        const t = Math.min(1, c / Math.max(4, maxC * 0.35));
        const radius = r * (0.7 + t * 1.4);
        ctx.beginPath();
        ctx.fillStyle = `rgba(56, 189, 248, ${0.25 + t * 0.7})`;
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fill();
      }
      return;
    }

    ctx.fillStyle = "rgba(56, 189, 248, 0.85)";
    for (const p of points) {
      const sx = Number(p.x) * z + tx;
      const sy = Number(p.y) * z + ty;
      if (sx < -4 || sy < -4 || sx > w + 4 || sy > h + 4) continue;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [points, mode, visible, transform]);

  if (!visible) return null;
  return (
    <Panel
      position="top-left"
      className="topo-world-scatter-panel"
      style={{
        margin: 0,
        width: "100%",
        height: "100%",
        maxWidth: "none",
        pointerEvents: "none",
        zIndex: 1,
      }}
    >
      <canvas ref={canvasRef} className="topo-world-scatter" aria-hidden="true" />
    </Panel>
  );
}
