import type { Edge, Node } from "@xyflow/react";
import type { NeNodeData } from "../components/TopologyReactFlowView";

export type HistorySnap = {
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  pendingEdgeDeletes: string[];
  pendingEdgeCreates: string[];
  pendingEdgePortEdits: Array<[string, { a_port: string; b_port: string }]>;
  pendingEdgeStyles: Array<
    [string, { stroke_color: string; stroke_width: number; line_style: string }]
  >;
};

export type PaletteSource = "managed" | "ume";

export type PaletteItem = {
  key: string;
  source: PaletteSource;
  managed_ne_id: string;
  ume_ne_id: string;
  name: string;
  ip: string;
  vendor: string;
  meta: string;
  connect_status: string;
};

export type CtxMenu =
  | { kind: "node"; id: string; x: number; y: number }
  | { kind: "edge"; id: string; x: number; y: number }
  | { kind: "selection"; x: number; y: number }
  | { kind: "pane"; x: number; y: number; flowX: number; flowY: number };
