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

function toAlignedSeries(
  current: PortTrafficSamplePoint[],
  baseline: PortTrafficSamplePoint[],
): [number[], number[], number[], number[], number[]] {
  type Row = { x: number; inC?: number; outC?: number; inB?: number; outB?: number };
  const map = new Map<number, Row>();
  for (const p of current) {
    const d = parseApiTime(p.ts);
    if (!d) continue;
    const x = Math.floor(d.getTime() / 1000);
    const row = map.get(x) || { x };
    row.inC = Number(p.in_bps) || 0;
    row.outC = Number(p.out_bps) || 0;
    map.set(x, row);
  }
  for (const p of baseline) {
    const d = parseApiTime(p.ts);
    if (!d) continue;
    const x = Math.floor(d.getTime() / 1000);
    const row = map.get(x) || { x };
    row.inB = Number(p.in_bps) || 0;
    row.outB = Number(p.out_bps) || 0;
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
  const width = Math.max(320, el.clientWidth || el.parentElement?.clientWidth || 800);
  const height = Math.max(260, Math.min(440, Math.floor(width * 0.34)));
  return { width, height };
}

const EMPTY_POINTS: PortTrafficSamplePoint[] = [];

type Props = {
  target: PortTrafficTarget | null;
  points: PortTrafficSamplePoint[];
  baselinePoints?: PortTrafficSamplePoint[];
  rangeLabel: string;
  loading?: boolean;
  hint?: string;
};

export function PortTrafficWall({
  target,
  points,
  baselinePoints = EMPTY_POINTS,
  rangeLabel,
  loading,
  hint,
}: Props) {
  const { t } = useI18n();
  const mountRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const pointsRef = useRef(points);
  const baselineRef = useRef(baselinePoints);
  pointsRef.current = points;
  baselineRef.current = baselinePoints;

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
          label: t("portTraffic.seriesCurrentIn"),
          stroke: "#2dd4bf",
          width: 2,
          fill: "rgba(45, 212, 191, 0.12)",
          points: { show: true, size: 5, fill: "#2dd4bf" },
          spanGaps: false,
        },
        {
          label: t("portTraffic.seriesCurrentOut"),
          stroke: "#f59e0b",
          width: 2,
          fill: "rgba(245, 158, 11, 0.10)",
          points: { show: true, size: 5, fill: "#f59e0b" },
          spanGaps: false,
        },
        {
          label: t("portTraffic.seriesBaselineIn"),
          stroke: "rgba(45, 212, 191, 0.45)",
          width: 1.5,
          dash: [6, 4],
          points: { show: false },
          spanGaps: false,
          show: false,
        },
        {
          label: t("portTraffic.seriesBaselineOut"),
          stroke: "rgba(245, 158, 11, 0.45)",
          width: 1.5,
          dash: [6, 4],
          points: { show: false },
          spanGaps: false,
          show: false,
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
            const inBps = Number(u.data[1]?.[idx]);
            const outBps = Number(u.data[2]?.[idx]);
            const inBase = Number(u.data[3]?.[idx]);
            const outBase = Number(u.data[4]?.[idx]);
            const sample = pointsRef.current.find((p) => {
              const d = parseApiTime(p.ts);
              return d && Math.floor(d.getTime() / 1000) === tsSec;
            });
            const baseSample = baselineRef.current.find((p) => {
              const d = parseApiTime(p.ts);
              return d && Math.floor(d.getTime() / 1000) === tsSec;
            });

            if (!tip) return;
            tip.style.display = "block";
            const rows = [
              `<div class="pt-wall__tip-time">${formatHoverTime(tsSec)}</div>`,
              Number.isFinite(inBps)
                ? `<div class="pt-wall__tip-row pt-wall__tip-row--in"><span>In</span><strong>${formatBps(inBps)} bit/s</strong></div>`
                : "",
              Number.isFinite(outBps)
                ? `<div class="pt-wall__tip-row pt-wall__tip-row--out"><span>Out</span><strong>${formatBps(outBps)} bit/s</strong></div>`
                : "",
              sample
                ? `<div class="pt-wall__tip-row"><span>Util</span><strong>${Number(sample.in_util_pct || 0).toFixed(1)}% / ${Number(sample.out_util_pct || 0).toFixed(1)}%</strong></div>`
                : "",
            ];
            if (Number.isFinite(inBase) || Number.isFinite(outBase)) {
              rows.push(
                `<div class="pt-wall__tip-row"><span>${t("portTraffic.baseline")}</span><strong>${
                  Number.isFinite(inBase) ? formatBps(inBase) : "—"
                } / ${Number.isFinite(outBase) ? formatBps(outBase) : "—"} bit/s</strong></div>`,
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
    return () => {
      window.removeEventListener("resize", onResize);
      plotRef.current?.over.removeEventListener("mouseleave", onLeave);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    // Always setData with resetScales; toggling series.show before setData can leave
    // uPlot stuck on a blank [0,1] y-range until a later update.
    const data = toAlignedSeries(points, baselinePoints);
    const showBaseline = baselinePoints.length > 0;
    plot.setData(data, true);
    const s3 = Boolean(plot.series[3]?.show);
    const s4 = Boolean(plot.series[4]?.show);
    if (s3 !== showBaseline || s4 !== showBaseline) {
      plot.setSeries(3, { show: showBaseline }, false);
      plot.setSeries(4, { show: showBaseline }, false);
    }
    const el = mountRef.current;
    if (el) {
      // Legend show/hide changes layout; size after paint.
      requestAnimationFrame(() => {
        if (!plotRef.current || !mountRef.current) return;
        plotRef.current.setSize(chartSize(mountRef.current));
      });
    }
  }, [points, baselinePoints]);

  const empty = points.length === 0 && baselinePoints.length === 0;
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
