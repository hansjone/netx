import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { PortTrafficSamplePoint, PortTrafficTarget } from "../../types";
import { formatSystemTime, parseApiTime } from "../../utils/time";
import { useI18n } from "../../i18n";

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

function formatHoverTime(sec: number): string {
  return formatSystemTime(new Date(sec * 1000));
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
  const { t } = useI18n();
  const mountRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const pointsRef = useRef(points);
  pointsRef.current = points;

  const latest = points.length ? points[points.length - 1] : null;
  const kpi = useMemo(() => {
    const bw = latest?.bw_bps || target?.bw_bps || 0;
    return {
      bw,
      inBps: latest?.in_bps ?? 0,
      outBps: latest?.out_bps ?? 0,
      inUtil: latest?.in_util_pct ?? 0,
      outUtil: latest?.out_util_pct ?? 0,
      ts: latest?.ts ?? null,
    };
  }, [latest, target]);

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
          label: "In",
          stroke: "#2dd4bf",
          width: 2,
          fill: "rgba(45, 212, 191, 0.12)",
          points: { show: true, size: 5, fill: "#2dd4bf" },
        },
        {
          label: "Out",
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
      // Color keys only — no live numeric values (those are in the hover tip).
      legend: { show: true, live: false },
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
            const inBps = Number(u.data[1]?.[idx] ?? 0);
            const outBps = Number(u.data[2]?.[idx] ?? 0);
            const sample = pointsRef.current.find((p) => {
              const d = parseApiTime(p.ts);
              return d && Math.floor(d.getTime() / 1000) === tsSec;
            });

            if (!tip) return;
            tip.style.display = "block";
            tip.innerHTML = [
              `<div class="pt-wall__tip-time">${formatHoverTime(tsSec)}</div>`,
              `<div class="pt-wall__tip-row pt-wall__tip-row--in"><span>In</span><strong>${formatBps(inBps)} bit/s</strong></div>`,
              `<div class="pt-wall__tip-row pt-wall__tip-row--out"><span>Out</span><strong>${formatBps(outBps)} bit/s</strong></div>`,
              sample
                ? `<div class="pt-wall__tip-row"><span>Util</span><strong>${Number(sample.in_util_pct || 0).toFixed(1)}% / ${Number(sample.out_util_pct || 0).toFixed(1)}%</strong></div>`
                : "",
            ].join("");
            placeTip(u, left, top);
          },
        ],
      },
    };

    plotRef.current = new uPlot(opts, [[], [], []], el);

    const onLeave = () => {
      if (tipRef.current) tipRef.current.style.display = "none";
    };
    plotRef.current.over.addEventListener("mouseleave", onLeave);

    const onResize = () => {
      if (!plotRef.current || !mountRef.current) return;
      plotRef.current.setSize(chartSize(mountRef.current));
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plotRef.current?.over.removeEventListener("mouseleave", onLeave);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, []);

  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    plot.setData(toSeries(points));
    if (mountRef.current) plot.setSize(chartSize(mountRef.current));
  }, [points]);

  const empty = points.length === 0;
  const neLabel = target ? target.ne_name || target.ne_ip || "—" : "—";
  const ifLabel = target?.ifname || "—";
  const ipLabel = target?.ne_ip || "";

  return (
    <div className="pt-wall">
      <div className="pt-wall__head">
        <div className="pt-wall__head-title">{t("portTraffic.wallLiveTitle")}</div>
        <div className="pt-wall__range">
          {rangeLabel}
          {kpi.ts ? ` · ${t("portTraffic.latestAt")} ${formatSystemTime(kpi.ts)}` : ""}
        </div>
      </div>
      <div className="pt-wall__kpis">
        <div className="pt-wall__kpi">
          <div className="pt-wall__kpi-label">
            {t("portTraffic.kpiBw")} · {t("portTraffic.kpiLatest")}
          </div>
          <div className="pt-wall__kpi-value">{formatBwLabel(kpi.bw)}</div>
        </div>
        <div className="pt-wall__kpi pt-wall__kpi--in">
          <div className="pt-wall__kpi-label">
            In · {t("portTraffic.kpiLatest")}
          </div>
          <div className="pt-wall__kpi-value">
            {formatBps(kpi.inBps)}
            <span className="pt-wall__kpi-unit">bit/s</span>
          </div>
        </div>
        <div className="pt-wall__kpi pt-wall__kpi--out">
          <div className="pt-wall__kpi-label">
            Out · {t("portTraffic.kpiLatest")}
          </div>
          <div className="pt-wall__kpi-value">
            {formatBps(kpi.outBps)}
            <span className="pt-wall__kpi-unit">bit/s</span>
          </div>
        </div>
        <div className="pt-wall__kpi">
          <div className="pt-wall__kpi-label">
            Util · {t("portTraffic.kpiLatest")}
          </div>
          <div className="pt-wall__kpi-value">
            {kpi.inUtil.toFixed(1)}%
            <span className="pt-wall__kpi-sep">/</span>
            {kpi.outUtil.toFixed(1)}%
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
      <div className="pt-wall__foot">
        <div className="pt-wall__foot-ne" title={neLabel}>
          <span className="pt-wall__foot-label">{t("portTraffic.wallDevice")}</span>
          <span className="pt-wall__foot-value">{neLabel}</span>
          {ipLabel ? <span className="pt-wall__foot-ip">{ipLabel}</span> : null}
        </div>
        <div className="pt-wall__foot-if" title={ifLabel}>
          <span className="pt-wall__foot-label">{t("portTraffic.wallPort")}</span>
          <span className="pt-wall__foot-value pt-wall__foot-value--mono">{ifLabel}</span>
        </div>
      </div>
    </div>
  );
}
