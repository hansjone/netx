import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { PortTrafficSamplePoint, PortTrafficTarget } from "../../types";
import { formatSystemTime, parseApiTime } from "../../utils/time";

function formatBps(n: number): string {
  const v = Math.abs(n);
  if (v >= 1e9) return `${(n / 1e9).toFixed(2)} G`;
  if (v >= 1e6) return `${(n / 1e6).toFixed(2)} M`;
  if (v >= 1e3) return `${(n / 1e3).toFixed(1)} K`;
  return `${Math.round(n)}`;
}

function formatBwLabel(bps: number): string {
  if (!bps) return "—";
  return `${formatBps(bps)}bit/s`;
}

function toSeries(points: PortTrafficSamplePoint[]): [number[], number[], number[]] {
  const xs: number[] = [];
  const ins: number[] = [];
  const outs: number[] = [];
  for (const p of points) {
    const d = parseApiTime(p.ts);
    if (!d) continue;
    xs.push(Math.floor(d.getTime() / 1000));
    ins.push(Number(p.in_bps) || 0);
    outs.push(Number(p.out_bps) || 0);
  }
  // Single sample: stretch a short segment so the line is visible.
  if (xs.length === 1) {
    xs.push(xs[0] + 60);
    ins.push(ins[0]);
    outs.push(outs[0]);
  }
  return [xs, ins, outs];
}

function chartSize(el: HTMLElement): { width: number; height: number } {
  const width = Math.max(320, el.clientWidth || el.parentElement?.clientWidth || 800);
  const height = Math.max(260, Math.min(440, Math.floor(width * 0.34)));
  return { width, height };
}

type Props = {
  target: PortTrafficTarget | null;
  points: PortTrafficSamplePoint[];
  rangeLabel: string;
  loading?: boolean;
  hint?: string;
};

export function PortTrafficWall({ target, points, rangeLabel, loading, hint }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);

  const latest = points.length ? points[points.length - 1] : null;
  const kpi = useMemo(() => {
    const bw = latest?.bw_bps || target?.bw_bps || 0;
    return {
      bw,
      inBps: latest?.in_bps ?? 0,
      outBps: latest?.out_bps ?? 0,
      inUtil: latest?.in_util_pct ?? 0,
      outUtil: latest?.out_util_pct ?? 0,
    };
  }, [latest, target]);

  // Create plot once; keep resize listener for lifetime of mount.
  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;

    const { width, height } = chartSize(el);
    const opts: uPlot.Options = {
      width,
      height,
      series: [
        {},
        {
          label: "In bit/s",
          stroke: "#2dd4bf",
          width: 2,
          fill: "rgba(45, 212, 191, 0.12)",
          points: { show: true, size: 5, fill: "#2dd4bf" },
        },
        {
          label: "Out bit/s",
          stroke: "#f59e0b",
          width: 2,
          fill: "rgba(245, 158, 11, 0.10)",
          points: { show: true, size: 5, fill: "#f59e0b" },
        },
      ],
      axes: [
        {
          stroke: "#94a3b8",
          grid: { stroke: "rgba(148,163,184,0.18)", width: 1 },
          ticks: { stroke: "rgba(148,163,184,0.35)" },
        },
        {
          stroke: "#94a3b8",
          grid: { stroke: "rgba(148,163,184,0.12)", width: 1 },
          ticks: { stroke: "rgba(148,163,184,0.35)" },
          values: (_u, splits) => splits.map((v) => formatBps(v)),
          size: 64,
          label: "bit/s",
          labelFont: "12px sans-serif",
          labelSize: 14,
        },
      ],
      scales: {
        x: { time: true },
        y: {
          auto: true,
          range: (_u, min, max) => {
            if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
            if (min === max) {
              const pad = Math.max(10, Math.abs(max) * 0.25 || 10);
              return [Math.max(0, min - pad), max + pad];
            }
            return [Math.max(0, min * 0.95), max * 1.05];
          },
        },
      },
      legend: { show: true },
      cursor: { drag: { x: true, y: false } },
    };

    plotRef.current = new uPlot(opts, [[], [], []], el);
    const onResize = () => {
      if (!plotRef.current || !mountRef.current) return;
      plotRef.current.setSize(chartSize(mountRef.current));
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, []);

  // Push live samples into the existing chart.
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    const data = toSeries(points);
    plot.setData(data);
    if (mountRef.current) plot.setSize(chartSize(mountRef.current));
  }, [points]);

  const empty = points.length === 0;

  return (
    <div className="pt-wall">
      <div className="pt-wall__head">
        <div className="pt-wall__title">
          <span className="pt-wall__ne">{target ? target.ne_name || target.ne_ip || "—" : "—"}</span>
          <span className="pt-wall__if">{target?.ifname || "Select interface"}</span>
        </div>
        <div className="pt-wall__range">
          {rangeLabel}
          {latest?.ts ? ` · last ${formatSystemTime(latest.ts)}` : ""}
        </div>
      </div>
      <div className="pt-wall__kpis">
        <div className="pt-wall__kpi">
          <div className="pt-wall__kpi-label">Bandwidth</div>
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
        <div className="pt-wall__kpi">
          <div className="pt-wall__kpi-label">Util In / Out</div>
          <div className="pt-wall__kpi-value">
            {kpi.inUtil.toFixed(1)}%
            <span className="pt-wall__kpi-sep">/</span>
            {kpi.outUtil.toFixed(1)}%
          </div>
        </div>
      </div>
      <div className="pt-wall__chart-wrap">
        <div className="pt-wall__chart" ref={mountRef} />
        {empty ? (
          <div className="pt-wall__empty">
            {loading ? "Loading samples…" : hint || "Waiting for samples…"}
          </div>
        ) : null}
      </div>
    </div>
  );
}
