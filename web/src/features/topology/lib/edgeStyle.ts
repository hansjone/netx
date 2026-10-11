import type { Edge } from "@xyflow/react";
import type { LinkEdgeData } from "./linkDisplay";
import { EDGE_DEFAULTS_KEY } from "./constants";

export type EdgeLineStyle = "solid" | "dashed" | "dotted";
export type EdgeSourceKind = "manual" | "ume" | "discovered" | "stale";

export type EdgeDefaultStyle = {
  stroke_color: string;
  stroke_width: number;
  line_style: EdgeLineStyle;
};

export type EdgeDefaults = Record<EdgeSourceKind, EdgeDefaultStyle>;
export type EdgeStyleData = LinkEdgeData;

const BUILTIN_EDGE_DEFAULTS: EdgeDefaults = {
  ume: { stroke_color: "#2563eb", stroke_width: 2, line_style: "solid" },
  discovered: { stroke_color: "#0ea5e9", stroke_width: 2, line_style: "dashed" },
  stale: { stroke_color: "#dc2626", stroke_width: 2, line_style: "dashed" },
  manual: { stroke_color: "#64748b", stroke_width: 2, line_style: "solid" },
};

export function sourceKind(source: string): EdgeSourceKind {
  const src = (source || "manual").toLowerCase();
  if (src === "stale" || src === "missing") return "stale";
  if (src === "ume") return "ume";
  if (src === "lldp" || src === "cdp") return "discovered";
  return "manual";
}

export function loadEdgeDefaults(): EdgeDefaults {
  try {
    const raw = localStorage.getItem(EDGE_DEFAULTS_KEY);
    if (!raw) {
      return {
        ume: { ...BUILTIN_EDGE_DEFAULTS.ume },
        discovered: { ...BUILTIN_EDGE_DEFAULTS.discovered },
        stale: { ...BUILTIN_EDGE_DEFAULTS.stale },
        manual: { ...BUILTIN_EDGE_DEFAULTS.manual },
      };
    }
    const parsed = JSON.parse(raw) as Partial<EdgeDefaults>;
    const pick = (kind: EdgeSourceKind): EdgeDefaultStyle => {
      const base = BUILTIN_EDGE_DEFAULTS[kind];
      const cur = parsed?.[kind];
      const color = String(cur?.stroke_color || base.stroke_color).trim() || base.stroke_color;
      const width = Math.max(1, Math.min(12, Number(cur?.stroke_width || base.stroke_width) || base.stroke_width));
      const line = String(cur?.line_style || base.line_style).toLowerCase();
      const line_style: EdgeLineStyle =
        line === "dashed" || line === "dotted" || line === "solid" ? line : base.line_style;
      return { stroke_color: color, stroke_width: width, line_style };
    };
    return {
      ume: pick("ume"),
      discovered: pick("discovered"),
      stale: pick("stale"),
      manual: pick("manual"),
    };
  } catch {
    return {
      ume: { ...BUILTIN_EDGE_DEFAULTS.ume },
      discovered: { ...BUILTIN_EDGE_DEFAULTS.discovered },
      stale: { ...BUILTIN_EDGE_DEFAULTS.stale },
      manual: { ...BUILTIN_EDGE_DEFAULTS.manual },
    };
  }
}

export function persistEdgeDefaults(next: EdgeDefaults) {
  try {
    localStorage.setItem(EDGE_DEFAULTS_KEY, JSON.stringify(next));
  } catch {
    /* ignore quota */
  }
}

export function dashForLineStyle(lineStyle: string): string | undefined {
  const s = (lineStyle || "").trim().toLowerCase();
  if (s === "dashed") return "6 4";
  if (s === "dotted") return "2 2";
  return undefined;
}

export function edgeStyleBySource(
  source: string,
  defaults: EdgeDefaults,
): { stroke: string; strokeDasharray?: string; strokeWidth: number } {
  const d = defaults[sourceKind(source)];
  return {
    stroke: d.stroke_color,
    strokeWidth: d.stroke_width,
    strokeDasharray: dashForLineStyle(d.line_style),
  };
}

export function resolveEdgeStyle(
  data: EdgeStyleData | undefined,
  defaults: EdgeDefaults,
): { stroke: string; strokeDasharray?: string; strokeWidth: number } {
  const base = edgeStyleBySource(data?.source || "manual", defaults);
  const color = String(data?.stroke_color || "").trim();
  const width = Number(data?.stroke_width || 0);
  const line = String(data?.line_style || "").trim().toLowerCase();
  return {
    stroke: color || base.stroke,
    strokeWidth: width > 0 ? width : base.strokeWidth,
    strokeDasharray: line ? dashForLineStyle(line) : base.strokeDasharray,
  };
}

export function withEdgeVisual(edge: Edge, defaults: EdgeDefaults): Edge {
  const data = (edge.data || {}) as EdgeStyleData;
  const style = resolveEdgeStyle(data, defaults);
  return { ...edge, style, markerEnd: undefined, labelShowBg: false };
}

export { BUILTIN_EDGE_DEFAULTS };
