import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { PortTrafficSamplePoint, PortTrafficTarget } from "../../types";

function formatBps(n: number): string {
  const v = Math.abs(n);
  if (v >= 1e9) return `${(n / 1e9).toFixed(2)} G`;
  if (v >= 1e6) return `${(n / 1e6).toFixed(2)} M`;
  if (v >= 1e3) return `${(n / 1e3).toFixed(1)} K`;
  return `${n.toFixed(0)}`;
}

function formatBwLabel(bps: number): string {
  if (!bps) return "—";
  return `${formatBps(bps)}bit/s`;
}

type Props = {
  target: PortTrafficTarget | null;
  points: PortTrafficSamplePoint[];
  rangeLabel: string;
};

export function PortTrafficWall({ target, points, rangeLabel }: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
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

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;

    const xs = points.map((p) => Math.floor(new Date(p.ts).getTime() / 1000));
    const inSeries = points.map((p) => p.in_bps);
    const outSeries = points.map((p) => p.out_bps);

    const opts: uPlot.Options = {
      width: Math.max(320, el.clientWidth || 800),
      height: Math.max(220, Math.min(420, Math.floor((el.clientWidth || 800) * 0.32))),
      series: [
        {},
        {
          label: "In",
          stroke: "#2dd4bf",
          width: 2,
          points: { show: false },
        },
        {
          label: "Out",
          stroke: "#f59e0b",
          width: 2,
          points: { show: false },
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
          size: 56,
        },
      ],
      scales: {
        x: { time: true },
      },
      legend: { show: true },
      cursor: {
        drag: { x: true, y: false },
      },
    };

    plotRef.current?.destroy();
    plotRef.current = null;

    if (xs.length === 0) {
      el.replaceChildren();
      return;
    }

    plotRef.current = new uPlot(opts, [xs, inSeries, outSeries], el);

    const onResize = () => {
      if (!plotRef.current || !rootRef.current) return;
      plotRef.current.setSize({
        width: Math.max(320, rootRef.current.clientWidth),
        height: Math.max(220, Math.min(420, Math.floor(rootRef.current.clientWidth * 0.32))),
      });
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, [points]);

  return (
    <div className="pt-wall">
      <div className="pt-wall__head">
        <div className="pt-wall__title">
          <span className="pt-wall__ne">{target ? target.ne_name || target.ne_ip || "—" : "—"}</span>
          <span className="pt-wall__if">{target?.ifname || "Select interface"}</span>
        </div>
        <div className="pt-wall__range">{rangeLabel}</div>
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
      <div className="pt-wall__chart" ref={rootRef}>
        {!points.length ? <div className="pt-wall__empty">No samples in range</div> : null}
      </div>
    </div>
  );
}
