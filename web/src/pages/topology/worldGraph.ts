import type {
  TopologyViewEdgeItem,
  TopologyViewGraph,
  TopologyViewNodeItem,
} from "../../types";
import { WORLD_FLAT_ACCUM_CAP } from "./constants";

/** Merge newly fetched flat LOD into the existing cache so visited areas stay. */
export function mergeFlatWorldGraph(
  prev: TopologyViewGraph | undefined,
  next: TopologyViewGraph,
  opts?: { centerX?: number; centerY?: number },
): TopologyViewGraph {
  const scatter = next.scatter?.length ? next.scatter : prev?.scatter;
  if (!prev?.nodes?.length) {
    return scatter?.length && !next.scatter?.length ? { ...next, scatter } : next;
  }
  if (!next?.nodes?.length) {
    return {
      ...prev,
      view: next.view || prev.view,
      nodes: [],
      edges: [],
      scatter: scatter || [],
      world_transform: next.world_transform ?? prev.world_transform,
      truncated: Boolean(next.truncated || prev.truncated),
      truncate_reason: next.truncate_reason || prev.truncate_reason || "",
      outside_peers: next.outside_peers?.length ? next.outside_peers : prev.outside_peers,
    };
  }

  const nodeMap = new Map<string, TopologyViewNodeItem>();
  for (const n of prev.nodes) nodeMap.set(String(n.fabric_node_id), n);
  for (const n of next.nodes) nodeMap.set(String(n.fabric_node_id), n);

  const edgeMap = new Map<string, TopologyViewEdgeItem>();
  for (const e of prev.edges || []) edgeMap.set(String(e.id), e);
  for (const e of next.edges || []) edgeMap.set(String(e.id), e);

  let nodes = [...nodeMap.values()];
  const cx = opts?.centerX;
  const cy = opts?.centerY;
  if (nodes.length > WORLD_FLAT_ACCUM_CAP && cx != null && cy != null) {
    const prefer = new Set(next.nodes.map((n) => String(n.fabric_node_id)));
    const kept = next.nodes.slice();
    const extras = nodes
      .filter((n) => !prefer.has(String(n.fabric_node_id)))
      .sort((a, b) => {
        const da = (Number(a.x) - cx) ** 2 + (Number(a.y) - cy) ** 2;
        const db = (Number(b.x) - cx) ** 2 + (Number(b.y) - cy) ** 2;
        return da - db;
      });
    const room = Math.max(0, WORLD_FLAT_ACCUM_CAP - kept.length);
    nodes = kept.concat(extras.slice(0, room));
  }

  const idSet = new Set(nodes.map((n) => String(n.fabric_node_id)));
  const edges = [...edgeMap.values()].filter(
    (e) => idSet.has(String(e.a_node_id)) && idSet.has(String(e.b_node_id)),
  );

  return {
    ...next,
    view: next.view || prev.view,
    nodes,
    edges,
    world_transform: next.world_transform ?? prev.world_transform,
    scatter: scatter || [],
    truncated: Boolean(prev.truncated || next.truncated),
    truncate_reason: next.truncate_reason || prev.truncate_reason || "",
    outside_peers: next.outside_peers?.length ? next.outside_peers : prev.outside_peers,
  };
}
