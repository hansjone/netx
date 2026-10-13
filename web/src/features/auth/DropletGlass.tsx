import { useEffect, useState, type RefObject } from "react";

/**
 * Displacement map for a rounded rect: neutral (128,128) in the middle, and
 * within `bezel` px of the edge the sample point is pushed inward along the
 * edge normal — the background bends at the rim like light through a droplet.
 */
function buildMap(w: number, h: number, radius: number, bezel: number): string {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";
  const img = ctx.createImageData(w, h);
  const hx = w / 2;
  const hy = h / 2;
  const r = Math.min(radius, hx, hy);

  // Signed distance to the rounded-rect edge (negative inside).
  const sdf = (x: number, y: number) => {
    const qx = Math.abs(x - hx) - (hx - r);
    const qy = Math.abs(y - hy) - (hy - r);
    const ox = Math.max(qx, 0);
    const oy = Math.max(qy, 0);
    return Math.hypot(ox, oy) + Math.min(Math.max(qx, qy), 0) - r;
  };

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const px = x + 0.5;
      const py = y + 0.5;
      const depth = -sdf(px, py);
      let dx = 0;
      let dy = 0;
      if (depth < bezel) {
        // Outward normal from the SDF gradient.
        const gx = sdf(px + 1, py) - sdf(px - 1, py);
        const gy = sdf(px, py + 1) - sdf(px, py - 1);
        const gl = Math.hypot(gx, gy) || 1;
        const t = 1 - Math.max(depth, 0) / bezel;
        const f = t * t; // strongest right at the rim, fading inward
        dx = -(gx / gl) * f;
        dy = -(gy / gl) * f;
      }
      img.data[i] = Math.round(128 + dx * 127);
      img.data[i + 1] = Math.round(128 + dy * 127);
      img.data[i + 2] = 128;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return canvas.toDataURL();
}

/**
 * SVG filter that refracts whatever sits behind `target`; reference it with
 * `backdrop-filter: url(#id)` (Chromium only — other engines keep the plain
 * blur declared before it).
 */
export function DropletGlassFilter({
  id,
  target,
  radius = 30,
  bezel = 26,
  strength = 46,
}: {
  id: string;
  target: RefObject<HTMLElement | null>;
  radius?: number;
  bezel?: number;
  strength?: number;
}) {
  const [map, setMap] = useState<{ url: string; w: number; h: number } | null>(null);

  useEffect(() => {
    const el = target.current;
    if (!el) return;
    let timer = 0;
    const rebuild = () => {
      const w = Math.round(el.offsetWidth);
      const h = Math.round(el.offsetHeight);
      if (!w || !h) return;
      setMap((prev) => (prev && prev.w === w && prev.h === h ? prev : { url: buildMap(w, h, radius, bezel), w, h }));
    };
    const ro = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(rebuild, 60);
    });
    ro.observe(el);
    rebuild();
    return () => {
      ro.disconnect();
      window.clearTimeout(timer);
    };
  }, [target, radius, bezel]);

  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false">
      <defs>
        <filter id={id} colorInterpolationFilters="sRGB">
          {map ? (
            <>
              <feImage href={map.url} x="0" y="0" width={map.w} height={map.h} result="map" preserveAspectRatio="none" />
              <feGaussianBlur in="SourceGraphic" stdDeviation="1.6" result="soft" />
              <feDisplacementMap in="soft" in2="map" scale={strength} xChannelSelector="R" yChannelSelector="G" />
            </>
          ) : (
            <feGaussianBlur in="SourceGraphic" stdDeviation="1.6" />
          )}
        </filter>
      </defs>
    </svg>
  );
}
