import type { Edge } from "@xyflow/react";

export type LinkMember = {
  id: string;
  a_port: string;
  b_port: string;
  source: string;
};

export type LinkEdgeData = {
  source?: string;
  source_port?: string;
  target_port?: string;
  stroke_color?: string;
  stroke_width?: number;
  line_style?: string;
  discovered_at?: string | null;
  /** Logical bundle of parallel physical links between the same NE pair. */
  aggregated?: boolean;
  member_count?: number;
  members?: LinkMember[];
  parallelIndex?: number;
  parallelCount?: number;
};

export function pairKey(a: string, b: string): string {
  return a < b ? `${a}::${b}` : `${b}::${a}`;
}

export function isAggregateEdgeId(id: string): boolean {
  return String(id || "").startsWith("agg:");
}

export function aggregateIdForPair(a: string, b: string): string {
  return `agg:${pairKey(a, b)}`;
}

/** Clear A↔B port label (not ambiguous "local/remote"). */
export function formatPortPairLabel(aPort: string, bPort: string): string {
  const a = String(aPort || "").trim();
  const b = String(bPort || "").trim();
  if (a && b) return `${a} ↔ ${b}`;
  return a || b || "";
}

export function formatBundleLabel(
  count: number,
  sample?: { a_port?: string; b_port?: string },
  opts?: { hidePorts?: boolean },
): string {
  if (count <= 1) {
    return opts?.hidePorts ? "" : formatPortPairLabel(sample?.a_port || "", sample?.b_port || "");
  }
  if (opts?.hidePorts) return `×${count}`;
  const sampleLabel = formatPortPairLabel(sample?.a_port || "", sample?.b_port || "");
  return sampleLabel ? `×${count} · ${sampleLabel}` : `×${count}`;
}

function memberFrom(edge: Edge): LinkMember {
  const d = (edge.data || {}) as LinkEdgeData;
  return {
    id: edge.id,
    a_port: String(d.source_port || "").trim(),
    b_port: String(d.target_port || "").trim(),
    source: String(d.source || "manual"),
  };
}

function statusRank(source: string): number {
  if (source === "stale") return 2;
  if (source === "manual") return 1;
  return 0; // discovered / lldp preferred
}

/** Pick representative physical edge for an aggregate (prefer live discovered). */
function pickPrimary(list: Edge[]): Edge {
  return [...list].sort((a, b) => {
    const da = (a.data || {}) as LinkEdgeData;
    const db = (b.data || {}) as LinkEdgeData;
    return statusRank(String(da.source || "")) - statusRank(String(db.source || ""));
  })[0];
}

/**
 * Default: one logical edge per NE pair (bundle parallel links).
 * Expanded: all physical edges, offset when count > 1.
 */
export function buildLinkDisplayEdges(edges: Edge[], expandPhysical: boolean): Edge[] {
  const groups = new Map<string, Edge[]>();
  for (const e of edges) {
    const k = pairKey(e.source, e.target);
    const list = groups.get(k);
    if (list) list.push(e);
    else groups.set(k, [e]);
  }

  const out: Edge[] = [];
  for (const [, list] of groups) {
    if (expandPhysical) {
      const count = list.length;
      list.forEach((e, i) => {
        const prev = (e.data || {}) as LinkEdgeData;
        const data: LinkEdgeData = {
          ...prev,
          aggregated: false,
          member_count: count,
          members: list.map(memberFrom),
          parallelIndex: i,
          parallelCount: count,
        };
        out.push({
          ...e,
          type: count > 1 ? "topoParallel" : e.type || "straight",
          label: formatPortPairLabel(data.source_port || "", data.target_port || "") || e.label,
          data,
        });
      });
      continue;
    }

    if (list.length === 1) {
      const e = list[0];
      const prev = (e.data || {}) as LinkEdgeData;
      const data: LinkEdgeData = {
        ...prev,
        aggregated: false,
        member_count: 1,
        members: [memberFrom(e)],
      };
      out.push({
        ...e,
        type: "straight",
        label: formatPortPairLabel(data.source_port || "", data.target_port || "") || e.label,
        data,
      });
      continue;
    }

    const primary = pickPrimary(list);
    const prev = (primary.data || {}) as LinkEdgeData;
    const members = list.map(memberFrom);
    const anyStale = members.some((m) => m.source === "stale");
    const data: LinkEdgeData = {
      ...prev,
      source: anyStale ? "stale" : prev.source,
      aggregated: true,
      member_count: list.length,
      members,
      // Slightly thicker logical bundle.
      stroke_width: Math.max(Number(prev.stroke_width || 0), Math.min(8, 2 + list.length)),
    };
    out.push({
      ...primary,
      id: aggregateIdForPair(primary.source, primary.target),
      type: "straight",
      label: formatBundleLabel(list.length, {
        a_port: data.source_port,
        b_port: data.target_port,
      }),
      data,
    });
  }
  return out;
}

export function physicalIdsForDisplayEdge(edge: Edge | null | undefined, allEdges: Edge[]): string[] {
  if (!edge) return [];
  const d = (edge.data || {}) as LinkEdgeData;
  if (d.members?.length) return d.members.map((m) => m.id);
  if (isAggregateEdgeId(edge.id)) {
    const k = edge.id.slice(4);
    return allEdges.filter((e) => pairKey(e.source, e.target) === k).map((e) => e.id);
  }
  return [edge.id];
}
