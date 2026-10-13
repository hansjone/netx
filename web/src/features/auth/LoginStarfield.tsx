import { useEffect, useRef } from "react";

/** Seeded PRNG so the sky is the same on every visit. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rand: () => number) {
  return Math.sqrt(-2 * Math.log(rand() || 1e-9)) * Math.cos(2 * Math.PI * rand());
}

/** Larger stars, drawn last with a small soft halo. */
type BrightStar = { x: number; y: number; r: number; a: number; hue: number; sat: number };

/**
 * Milky-way band running lower-left → upper-right, behind the earth.
 * Returns a point on the band and the unit normal, both in px.
 */
function bandFrame(w: number, h: number) {
  const x0 = -0.1 * w;
  const y0 = 0.9 * h;
  const x1 = 1.1 * w;
  const y1 = 0.2 * h;
  const len = Math.hypot(x1 - x0, y1 - y0);
  const ux = (x1 - x0) / len;
  const uy = (y1 - y0) / len;
  return { x0, y0, len, ux, uy, nx: -uy, ny: ux };
}

/** Static sky: gradient, nebula band, dust lanes, distant galaxies, thousands of stars. */
function paintSky(ctx: CanvasRenderingContext2D, w: number, h: number): BrightStar[] {
  const rand = mulberry32(20261013);
  const diag = Math.hypot(w, h);
  const band = bandFrame(w, h);
  const along = (t: number, off: number) => ({
    x: band.x0 + band.ux * band.len * t + band.nx * off,
    y: band.y0 + band.uy * band.len * t + band.ny * off,
  });

  const bg = ctx.createLinearGradient(0, 0, w, h);
  bg.addColorStop(0, "#01040b");
  bg.addColorStop(0.45, "#020916");
  bg.addColorStop(1, "#010409");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  // Nebula clouds along the band (hues 195–215, never violet).
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < 90; i++) {
    const t = rand();
    const p = along(t, gauss(rand) * diag * 0.06);
    const r = diag * (0.04 + rand() * 0.11);
    const hue = 196 + rand() * 18;
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
    const a = 0.02 + rand() * 0.045;
    g.addColorStop(0, `hsla(${hue}, 70%, ${38 + rand() * 18}%, ${a})`);
    g.addColorStop(1, `hsla(${hue}, 70%, 30%, 0)`);
    ctx.fillStyle = g;
    ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
  }
  // Pale core of the band.
  for (let i = 0; i < 36; i++) {
    const p = along(0.15 + rand() * 0.7, gauss(rand) * diag * 0.02);
    const r = diag * (0.03 + rand() * 0.05);
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
    g.addColorStop(0, `hsla(205, 35%, 78%, ${0.014 + rand() * 0.02})`);
    g.addColorStop(1, "hsla(205, 35%, 70%, 0)");
    ctx.fillStyle = g;
    ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
  }

  // Dark dust lanes cutting the band.
  ctx.globalCompositeOperation = "source-over";
  for (let i = 0; i < 46; i++) {
    const p = along(rand(), diag * (0.008 + gauss(rand) * 0.014));
    const r = diag * (0.015 + rand() * 0.045);
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
    g.addColorStop(0, `rgba(1, 3, 8, ${0.18 + rand() * 0.22})`);
    g.addColorStop(1, "rgba(1, 3, 8, 0)");
    ctx.fillStyle = g;
    ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
  }

  // A few far galaxies: tiny tilted smudges.
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < 4; i++) {
    const x = w * (0.08 + rand() * 0.84);
    const y = h * (0.06 + rand() * 0.88);
    const rx = 6 + rand() * 10;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rand() * Math.PI);
    ctx.scale(1, 0.28 + rand() * 0.3);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, rx);
    g.addColorStop(0, "hsla(205, 40%, 82%, 0.22)");
    g.addColorStop(0.35, "hsla(205, 45%, 60%, 0.06)");
    g.addColorStop(1, "hsla(205, 45%, 50%, 0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, rx, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // Stars: half scattered, half crowding the band.
  const count = Math.round((w * h) / 300);
  const bright: BrightStar[] = [];
  for (let i = 0; i < count; i++) {
    let x: number;
    let y: number;
    const inBand = rand() < 0.55;
    if (inBand) {
      const p = along(rand(), gauss(rand) * diag * 0.055);
      x = p.x;
      y = p.y;
    } else {
      x = rand() * w;
      y = rand() * h;
    }
    if (x < 0 || y < 0 || x > w || y > h) continue;

    const m = rand();
    const r = m < 0.86 ? 0.25 + rand() * 0.45 : m < 0.985 ? 0.6 + rand() * 0.55 : 1.1 + rand() * 0.6;
    const a = Math.min(0.85, (inBand ? 0.12 : 0.18) + rand() * 0.5 + (r > 1 ? 0.2 : 0));
    // Mostly blue-white; a few pale warm stars for realism.
    const warm = rand() < 0.12;
    const hue = warm ? 38 + rand() * 12 : 200 + rand() * 16;
    const sat = warm ? 45 : 25 + rand() * 30;
    const light = 82 + rand() * 12;

    if (r > 1 && bright.length < 90) {
      bright.push({ x, y, r, a, hue, sat });
      continue;
    }
    ctx.fillStyle = `hsla(${hue}, ${sat}%, ${light}%, ${a})`;
    if (r < 0.6) {
      ctx.fillRect(x, y, r * 1.6, r * 1.6);
    } else {
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.globalCompositeOperation = "source-over";
  return bright;
}

function drawStar(ctx: CanvasRenderingContext2D, s: BrightStar) {
  const halo = s.r * 3.2;
  const g = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, halo);
  g.addColorStop(0, `hsla(${s.hue}, ${s.sat}%, 92%, ${s.a})`);
  g.addColorStop(0.3, `hsla(${s.hue}, ${s.sat}%, 80%, ${s.a * 0.3})`);
  g.addColorStop(1, `hsla(${s.hue}, ${s.sat}%, 70%, 0)`);
  ctx.fillStyle = g;
  ctx.fillRect(s.x - halo, s.y - halo, halo * 2, halo * 2);
}

/** Procedural deep-space backdrop, painted once (and again on resize). Fully static. */
export function LoginStarfield() {
  const skyRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const sky = skyRef.current;
    const ctx = sky?.getContext("2d");
    if (!sky || !ctx) return;
    let resizeTimer = 0;

    const paint = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = window.innerWidth;
      const h = window.innerHeight;
      sky.width = Math.round(w * dpr);
      sky.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (const s of paintSky(ctx, w, h)) drawStar(ctx, s);
    };

    const onResize = () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(paint, 150);
    };

    paint();
    window.addEventListener("resize", onResize);
    return () => {
      window.clearTimeout(resizeTimer);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return <canvas ref={skyRef} className="login-sky" aria-hidden="true" />;
}
