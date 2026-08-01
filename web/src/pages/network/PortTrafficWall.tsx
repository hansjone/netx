import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { PortTrafficSamplePoint, PortTrafficTarget } from "../../types";
import { formatSystemTime, parseApiTime } from "../../utils/time";
import { useI18n } from "../../i18n";

export type WallYMode = "auto" | "current" | "util";

function formatBps(n: number): string {
  const v = Math.abs(n);
  if (v >= 1e9) return `${(n / 1e9).toFixed(2)} G`;
  if (v >= 1e6) return `${(n / 1e6).toFixed(2)} M`;
  if (v >= 1e3) return `${(n / 1e3).toFixed(1)} K`;
  if (v > 0 && v < 1) return n.toFixed(2);
  return `${Math.round(n)}`;
}

function formatBwLabel(bps: number): string {
  if (!bps) return "—";
  return `${formatBps(bps)}bit/s`;
}

function formatUtilPct(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0%";
  if (n < 0.01) return "<0.01%";
  if (n < 1) return `${n.toFixed(2)}%`;
  return `${n.toFixed(1)}%`;
}

function resolveUtil(vendorUtil: number, bps: number, bw: number): number {
  if (vendorUtil > 0) return vendorUtil;
  if (bw > 0 && bps > 0) return (bps / bw) * 100;
  return vendorUtil || 0;
}

function formatHoverTime(sec: number): string {
  return formatSystemTime(new Date(sec * 1000));
}

function yPadRange(min: number, max: number): [number, number] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) {
    const pad = Math.max(max * 0.25 || 0.1, max > 0 && max < 1 ? max * 0.5 : 10);
    return [Math.max(0, min - pad * 0.1), max + pad];
  }
  return [Math.max(0, min * 0.95), max * 1.05];
}

function finiteMax(vals: number[]): number {
  let m = Number.NEGATIVE_INFINITY;
  for (const v of vals) {
    if (Number.isFinite(v) && v > m) m = v;
  }
  return Number.isFinite(m) ? m : 0;
}

function pointValue(
  p: PortTrafficSamplePoint,
  dir: "in" | "out",
  mode: WallYMode,
  bwFallback: number,
): number {
  const bps = dir === "in" ? Number(p.in_bps) || 0 : Number(p.out_bps) || 0;
  if (mode !== "util") return bps;
  const bw = Number(p.bw_bps) || bwFallback || 0;
  const vendor = dir === "in" ? Number(p.in_util_pct) || 0 : Number(p.out_util_pct) || 0;
  return resolveUtil(vendor, bps, bw);
}

function toAlignedSeries(
  current: PortTrafficSamplePoint[],
  baseline: PortTrafficSamplePoint[],
  mode: WallYMode,
  bwFallback: number,
  baseBwFallback: number,
): [number[], number[], number[], number[], number[]] {
  type Row = { x: number; inC?: number; outC?: number; inB?: number; outB?: number };
  // Shared X = union of both timelines; each series only has values at its own samples.
  // Gaps stay NaN; series use spanGaps so each line connects on its own cadence.
  const map = new Map<number, Row>();
  for (const p of current) {
    const d = parseApiTime(p.ts);
    if (!d) continue;
    const x = Math.floor(d.getTime() / 1000);
    const row = map.get(x) || { x };
    row.inC = pointValue(p, "in", mode, bwFallback);
    row.outC = pointValue(p, "out", mode, bwFallback);
    map.set(x, row);
  }
  for (const p of baseline) {
    const d = parseApiTime(p.ts);
    if (!d) continue;
    const x = Math.floor(d.getTime() / 1000);
    const row = map.get(x) || { x };
    row.inB = pointValue(p, "in", mode, baseBwFallback || bwFallback);
    row.outB = pointValue(p, "out", mode, baseBwFallback || bwFallback);
    map.set(x, row);
  }
  const rows = [...map.values()].sort((a, b) => a.x - b.x);
  if (rows.length === 1) {
    rows.push({ ...rows[0], x: rows[0].x + 60 });
  }
  return [
    rows.map((r) => r.x),
    rows.map((r) => r.inC ?? NaN),
    rows.map((r) => r.outC ?? NaN),
    rows.map((r) => r.inB ?? NaN),
    rows.map((r) => r.outB ?? NaN),
  ];
}

function chartSize(el: HTMLElement): { width: number; height: number } {
  const width = Math.max(120, el.clientWidth || el.parentElement?.clientWidth || 800);
  const parentH = el.clientHeight || el.parentElement?.clientHeight || 0;
  // Follow container height in board/fullscreen panels (may be well under 180px).
  if (parentH > 0) {
    return { width, height: Math.max(64, parentH) };
  }
  return { width, height: Math.max(220, Math.min(440, Math.floor(width * 0.34))) };
}

const EMPTY_POINTS: PortTrafficSamplePoint[] = [];

type Props = {
  target: PortTrafficTarget | null;
  baselineTarget?: PortTrafficTarget | null;
  points: PortTrafficSamplePoint[];
  baselinePoints?: PortTrafficSamplePoint[];
  rangeLabel: string;
  yMode?: WallYMode;
  loading?: boolean;
  hint?: string;
  /** Board fullscreen: denser chrome, no per-panel legend. */
  dense?: boolean;
};

export function PortTrafficWall({
  target,
  baselineTarget = null,
  points,
  baselinePoints = EMPTY_POINTS,
  rangeLabel,
  yMode = "auto",
  loading,
  hint,
  dense = false,
}: Props) {
  const { t } = useI18n();
  const mountRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const pointsRef = useRef(points);
  const baselineRef = useRef(baselinePoints);
  const yModeRef = useRef(yMode);
  const bwRef = useRef(0);
  pointsRef.current = points;
  baselineRef.current = baselinePoints;
  yModeRef.current = yMode;

  const latest = points.length ? points[points.length - 1] : null;
  const kpi = useMemo(() => {
    const bw = latest?.bw_bps || target?.bw_bps || 0;
    const inBps = latest?.in_bps ?? 0;
    const outBps = latest?.out_bps ?? 0;
    return {
      bw,
      inBps,
      outBps,
      inUtil: resolveUtil(latest?.in_util_pct ?? 0, inBps, bw),
      outUtil: resolveUtil(latest?.out_util_pct ?? 0, outBps, bw),
      ts: latest?.ts ?? null,
    };
  }, [latest, target]);
  bwRef.current = kpi.bw;

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;

    const { width, height } = chartSize(el);

    const placeTip = (u: uPlot, left: number, top: number) => {
      const tip = tipRef.current;
      if (!tip) return;
      const wrap = tip.parentElement;
      if (!wrap) return;
      const overRect = u.over.getBoundingClientRect();
      const wrapRect = wrap.getBoundingClientRect();
      const tipW = tip.offsetWidth || 160;
      const tipH = tip.offsetHeight || 72;
      let x = overRect.left - wrapRect.left + left + 14;
      let y = overRect.top - wrapRect.top + top - tipH - 8;
      x = Math.min(Math.max(8, x), Math.max(8, wrapRect.width - tipW - 8));
      y = Math.max(8, y);
      tip.style.transform = `translate(${x}px, ${y}px)`;
    };

    const opts: uPlot.Options = {
      width,
      height,
      series: [
        {},
        {
          label: t("portTraffic.seriesCurrentIn"),
          stroke: "#2dd4bf",
          width: 2,
          fill: "rgba(45, 212, 191, 0.08)",
          points: { show: false },
          spanGaps: true,
        },
        {
          label: t("portTraffic.seriesCurrentOut"),
          stroke: "#fbbf24",
          width: 2,
          fill: "rgba(251, 191, 36, 0.07)",
          points: { show: false },
          spanGaps: true,
        },
        {
          label: t("portTraffic.seriesBaselineIn"),
          stroke: "rgba(45, 212, 191, 0.45)",
          width: 1.5,
          dash: [6, 4],
          points: { show: false },
          spanGaps: true,
          show: false,
        },
        {
          label: t("portTraffic.seriesBaselineOut"),
          stroke: "rgba(251, 191, 36, 0.4)",
          width: 1.5,
          dash: [6, 4],
          points: { show: false },
          spanGaps: true,
          show: false,
        },
      ],
      axes: [
        {
          stroke: "#94a3b8",
          grid: { show: false },
          ticks: { stroke: "rgba(148,163,184,0.35)", size: 4 },
          border: { show: false },
          gap: 6,
        },
        {
          stroke: "#94a3b8",
          grid: { stroke: "rgba(148,163,184,0.12)", width: 1 },
          ticks: { stroke: "rgba(148,163,184,0.35)" },
          border: { show: false },
          values: (_u, splits) =>
            splits.map((v) => (yModeRef.current === "util" ? `${v}` : formatBps(v))),
          size: 64,
          label: yMode === "util" ? "%" : "bit/s",
          labelFont: "12px sans-serif",
          labelSize: 14,
        },
      ],
      scales: {
        x: { time: true },
        y: {
          auto: true,
          range: (u, min, max) => {
            const mode = yModeRef.current;
            // "current": scale to live series only. "auto"/util: include baseline too,
            // otherwise a near-zero current + busy mapped baseline clips baseline off-chart.
            const seriesIdxs =
              mode === "current" ? [1, 2] : mode === "util" ? [1, 2, 3, 4] : [1, 2, 3, 4];
            const vals: number[] = [];
            for (const i of seriesIdxs) {
              for (const v of u.data[i] || []) {
                if (Number.isFinite(v)) vals.push(v as number);
              }
            }
            if (vals.length) return yPadRange(0, finiteMax(vals));
            return yPadRange(min, max);
          },
        },
      },
      legend: { show: false },
      cursor: {
        drag: { x: true, y: false },
        focus: { prox: 32 },
        points: { show: true, size: 8 },
      },
      hooks: {
        setCursor: [
          (u) => {
            const tip = tipRef.current;
            const idx = u.cursor.idx;
            const left = u.cursor.left ?? 0;
            const top = u.cursor.top ?? 0;

            if (idx == null || idx < 0 || !u.data[0]?.length) {
              if (tip) tip.style.display = "none";
              return;
            }

            const tsSec = Number(u.data[0][idx]);
            const sample = pointsRef.current.find((p) => {
              const d = parseApiTime(p.ts);
              return d && Math.floor(d.getTime() / 1000) === tsSec;
            });
            const baseSample = baselineRef.current.find((p) => {
              const d = parseApiTime(p.ts);
              return d && Math.floor(d.getTime() / 1000) === tsSec;
            });
            const mode = yModeRef.current;
            const yIn = Number(u.data[1]?.[idx]);
            const yOut = Number(u.data[2]?.[idx]);
            const yInB = Number(u.data[3]?.[idx]);
            const yOutB = Number(u.data[4]?.[idx]);
            const unit = mode === "util" ? "%" : "bit/s";
            const fmtY = (v: number) =>
              mode === "util" ? formatUtilPct(v).replace(/%$/, "") : formatBps(v);

            if (!tip) return;
            tip.style.display = "block";
            const rows = [
              `<div class="pt-wall__tip-time">${formatHoverTime(tsSec)}</div>`,
              Number.isFinite(yIn)
                ? `<div class="pt-wall__tip-row pt-wall__tip-row--in"><span>In</span><strong>${fmtY(yIn)} ${unit}</strong></div>`
                : "",
              Number.isFinite(yOut)
                ? `<div class="pt-wall__tip-row pt-wall__tip-row--out"><span>Out</span><strong>${fmtY(yOut)} ${unit}</strong></div>`
                : "",
            ];
            if (sample) {
              const bw = Number(sample.bw_bps) || bwRef.current || 0;
              const inU = resolveUtil(Number(sample.in_util_pct) || 0, Number(sample.in_bps) || 0, bw);
              const outU = resolveUtil(Number(sample.out_util_pct) || 0, Number(sample.out_bps) || 0, bw);
              rows.push(
                `<div class="pt-wall__tip-row"><span>Util</span><strong>${formatUtilPct(inU)} / ${formatUtilPct(outU)}</strong></div>`,
              );
            }
            if (Number.isFinite(yInB) || Number.isFinite(yOutB)) {
              rows.push(
                `<div class="pt-wall__tip-row"><span>${t("portTraffic.baseline")}</span><strong>${
                  Number.isFinite(yInB) ? fmtY(yInB) : "—"
                } / ${Number.isFinite(yOutB) ? fmtY(yOutB) : "—"} ${unit}</strong></div>`,
              );
              if (baseSample?.ts_raw) {
                rows.push(
                  `<div class="pt-wall__tip-row"><span>${t("portTraffic.baselineAt")}</span><strong>${formatSystemTime(baseSample.ts_raw)}</strong></div>`,
                );
              }
            }
            tip.innerHTML = rows.join("");
            placeTip(u, left, top);
          },
        ],
      },
    };

    plotRef.current = new uPlot(opts, [[], [], [], [], []], el);

    const onLeave = () => {
      if (tipRef.current) tipRef.current.style.display = "none";
    };
    plotRef.current.over.addEventListener("mouseleave", onLeave);

    const onResize = () => {
      if (!plotRef.current || !mountRef.current) return;
      plotRef.current.setSize(chartSize(mountRef.current));
    };
    window.addEventListener("resize", onResize);
    document.addEventListener("fullscreenchange", onResize);
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => onResize())
        : null;
    ro?.observe(el);
    const wrap = el.parentElement;
    if (wrap) ro?.observe(wrap);
    // Observe panel / stage ancestors so fullscreen grid row shrinks resize plots.
    let node: HTMLElement | null = wrap?.parentElement ?? null;
    for (let i = 0; i < 4 && node; i += 1) {
      ro?.observe(node);
      node = node.parentElement;
    }
    return () => {
      window.removeEventListener("resize", onResize);
      document.removeEventListener("fullscreenchange", onResize);
      ro?.disconnect();
      plotRef.current?.over.removeEventListener("mouseleave", onLeave);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    const bw = kpi.bw || target?.bw_bps || 0;
    const baseBw = baselineTarget?.bw_bps || bw;
    const data = toAlignedSeries(points, baselinePoints, yMode, bw, baseBw);
    const showBaseline = baselinePoints.length > 0;
    plot.setData(data, true);
    const s3 = Boolean(plot.series[3]?.show);
    const s4 = Boolean(plot.series[4]?.show);
    if (s3 !== showBaseline || s4 !== showBaseline) {
      plot.setSeries(3, { show: showBaseline }, false);
      plot.setSeries(4, { show: showBaseline }, false);
    }
    const yAxis = plot.axes[1];
    if (yAxis) {
      const nextLabel = yMode === "util" ? "%" : "bit/s";
      if (yAxis.label !== nextLabel) {
        yAxis.label = nextLabel;
        plot.redraw(false, false);
      }
    }
    const el = mountRef.current;
    if (el) {
      requestAnimationFrame(() => {
        if (!plotRef.current || !mountRef.current) return;
        plotRef.current.setSize(chartSize(mountRef.current));
      });
    }
  }, [points, baselinePoints, yMode, kpi.bw, target?.bw_bps, baselineTarget?.bw_bps]);

  const empty = points.length === 0 && baselinePoints.length === 0;
  const neLabel = target ? target.ne_name || target.ne_ip || "—" : "—";
  const ifLabel = target?.ifname || "—";
  const ipLabel = target?.ne_ip || "";
  const showBaselineMeta = Boolean(baselineTarget) && baselinePoints.length > 0;
  const baseNeLabel = baselineTarget
    ? baselineTarget.ne_name || baselineTarget.ne_ip || "—"
    : "—";
  const baseIfLabel = baselineTarget?.ifname || "—";
  const baseIpLabel = baselineTarget?.ne_ip || "";

  return (
    <div className={`pt-wall${dense ? " pt-wall--dense" : ""}`}>
      <div className="pt-wall__head">
        <div className="pt-wall__title">
          <div className="pt-wall__ne">
            {neLabel}
            {ipLabel ? ` · ${ipLabel}` : ""}
            {dense && showBaselineMeta ? (
              <span className="pt-wall__ne-base" title={`${baseNeLabel} · ${baseIfLabel}`}>
                {" · "}
                {t("portTraffic.baseline")}: {baseNeLabel} · {baseIfLabel}
              </span>
            ) : null}
          </div>
          <div className="pt-wall__if" title={ifLabel}>
            {ifLabel}
          </div>
        </div>
        <div className="pt-wall__range">
          {rangeLabel}
          {kpi.ts ? ` · ${formatSystemTime(kpi.ts)}` : ""}
        </div>
      </div>
      <div className="pt-wall__kpis">
        <div className="pt-wall__kpi">
          <div className="pt-wall__kpi-label">{t("portTraffic.kpiBw")}</div>
          <div className="pt-wall__kpi-value">{formatBwLabel(kpi.bw)}</div>
        </div>
        <div className="pt-wall__kpi pt-wall__kpi--in">
          <div className="pt-wall__kpi-label">In</div>
          <div className="pt-wall__kpi-value">
            {formatBps(kpi.inBps)}
            <span className="pt-wall__kpi-unit">bit/s</span>
          </div>
        </div>
        <div className="pt-wall__kpi pt-wall__kpi--out">
          <div className="pt-wall__kpi-label">Out</div>
          <div className="pt-wall__kpi-value">
            {formatBps(kpi.outBps)}
            <span className="pt-wall__kpi-unit">bit/s</span>
          </div>
        </div>
        <div className="pt-wall__kpi pt-wall__kpi--util">
          <div className="pt-wall__kpi-label">Util</div>
          <div className="pt-wall__kpi-value">
            {formatUtilPct(kpi.inUtil)}
            <span className="pt-wall__kpi-sep">/</span>
            {formatUtilPct(kpi.outUtil)}
          </div>
        </div>
      </div>
      <div className="pt-wall__chart-wrap">
        <div className="pt-wall__chart" ref={mountRef} />
        <div className="pt-wall__tip" ref={tipRef} style={{ display: "none" }} />
        {empty ? (
          <div className="pt-wall__empty">
            {loading ? "Loading samples…" : hint || "Waiting for samples…"}
          </div>
        ) : null}
      </div>
      {!dense ? (
        <div className="pt-wall__foot">
          <div className="pt-wall__legend" aria-hidden={!points.length && !baselinePoints.length}>
            <span className="pt-wall__legend-item pt-wall__legend-item--in">
              <i className="pt-wall__legend-swatch" />
              {t("portTraffic.seriesCurrentIn")}
            </span>
            <span className="pt-wall__legend-item pt-wall__legend-item--out">
              <i className="pt-wall__legend-swatch" />
              {t("portTraffic.seriesCurrentOut")}
            </span>
            {showBaselineMeta ? (
              <>
                <span className="pt-wall__legend-item pt-wall__legend-item--base-in">
                  <i className="pt-wall__legend-swatch" />
                  {t("portTraffic.seriesBaselineIn")}
                </span>
                <span className="pt-wall__legend-item pt-wall__legend-item--base-out">
                  <i className="pt-wall__legend-swatch" />
                  {t("portTraffic.seriesBaselineOut")}
                </span>
              </>
            ) : null}
          </div>
          {showBaselineMeta ? (
            <div className="pt-wall__foot-meta">
              <div className="pt-wall__foot-ne" title={baseNeLabel}>
                <span className="pt-wall__foot-label">{t("portTraffic.baselineDevice")}</span>
                <span className="pt-wall__foot-value">{baseNeLabel}</span>
                {baseIpLabel ? <span className="pt-wall__foot-ip">{baseIpLabel}</span> : null}
              </div>
              <div className="pt-wall__foot-if" title={baseIfLabel}>
                <span className="pt-wall__foot-label">{t("portTraffic.baselinePort")}</span>
                <span className="pt-wall__foot-value pt-wall__foot-value--mono">{baseIfLabel}</span>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
