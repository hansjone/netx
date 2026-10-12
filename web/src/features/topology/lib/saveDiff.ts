/**
 * Pure helpers that turn local canvas state into a view /commit body.
 * Runtime-import-free so Node --experimental-strip-types tests can load this file.
 */

export type SaveDiffNode = {
  id: string;
  data?: { kind?: string };
};

export type SaveDiffEdge = {
  id: string;
  source: string;
  target: string;
  data?: {
    source_port?: string;
    target_port?: string;
  };
};

export type SaveDiffPosition = {
  fabric_node_id: string;
  x: number;
  y: number;
  label?: string;
};

export type ViewCommitBody = {
  edge_creates: Array<{
    client_id: string;
    a_node_id: string;
    b_node_id: string;
    a_port: string;
    b_port: string;
  }>;
  edge_deletes: string[];
  edge_port_edits: Array<{ id: string; a_port: string; b_port: string }>;
  edge_styles: Array<{
    fabric_edge_id: string;
    stroke_color: string;
    stroke_width: number;
    line_style: string;
  }>;
  node_adds: string[];
  node_removes: string[];
  positions: SaveDiffPosition[];
};

export function isRegionNodeId(id: string): boolean {
  return String(id || "").startsWith("region:");
}

export function isLocalPendingEdgeId(id: string): boolean {
  return String(id || "").startsWith("local:");
}

export type BuildCommitInput = {
  nodes: SaveDiffNode[];
  edges: SaveDiffEdge[];
  serverNodeIds: string[];
  positions: SaveDiffPosition[];
  pendingCreates: Iterable<string>;
  pendingDeletes: Iterable<string>;
  pendingPortEdits?: Iterable<[string, { a_port: string; b_port: string }]>;
  pendingStyles?: Iterable<
    [string, { stroke_color: string; stroke_width: number; line_style: string }]
  >;
};

export function buildViewCommitBody(input: BuildCommitInput): ViewCommitBody {
  const createSet = new Set(
    [...input.pendingCreates].map((x) => String(x || "").trim()).filter(Boolean),
  );
  // Deletes that were also re-created (or are still local) must not be sent.
  const deleteSet = new Set(
    [...input.pendingDeletes]
      .map((x) => String(x || "").trim())
      .filter((id) => id && !isLocalPendingEdgeId(id) && !createSet.has(id)),
  );

  const edgeById = new Map(input.edges.map((e) => [e.id, e]));
  const edge_creates = [...createSet]
    .map((id) => edgeById.get(id))
    .filter((e): e is SaveDiffEdge => Boolean(e))
    .map((e) => ({
      client_id: e.id,
      a_node_id: e.source,
      b_node_id: e.target,
      a_port: String(e.data?.source_port || ""),
      b_port: String(e.data?.target_port || ""),
    }));

  const localIds = input.nodes.map((n) => n.id);
  const localSet = new Set(localIds);
  const serverSet = new Set(
    input.serverNodeIds.map((x) => String(x || "").trim()).filter(Boolean),
  );
  const node_adds = localIds.filter((id) => !serverSet.has(id) && !isRegionNodeId(id));
  const node_removes = [...serverSet].filter((id) => !localSet.has(id) && !isRegionNodeId(id));

  const edge_port_edits = [...(input.pendingPortEdits || [])]
    .map(([id, ports]) => ({
      id: String(id || "").trim(),
      a_port: String(ports.a_port || ""),
      b_port: String(ports.b_port || ""),
    }))
    .filter(
      (x) => x.id && !isLocalPendingEdgeId(x.id) && !createSet.has(x.id) && !deleteSet.has(x.id),
    );

  const edge_styles = [...(input.pendingStyles || [])]
    .map(([id, style]) => ({
      fabric_edge_id: String(id || "").trim(),
      stroke_color: String(style.stroke_color || ""),
      stroke_width: Number(style.stroke_width || 0),
      line_style: String(style.line_style || ""),
    }))
    // Deleted edges drop their style; a new local edge's style is keyed by its client id
    // (the server maps it through edge_id_map) only while that edge is still being created.
    .filter(
      (x) =>
        x.fabric_edge_id &&
        !deleteSet.has(x.fabric_edge_id) &&
        (!isLocalPendingEdgeId(x.fabric_edge_id) || createSet.has(x.fabric_edge_id)),
    );

  return {
    edge_creates,
    edge_deletes: [...deleteSet],
    edge_port_edits,
    edge_styles,
    node_adds,
    node_removes,
    positions: input.positions,
  };
}
