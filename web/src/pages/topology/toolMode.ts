/** Topology editor interaction modes (NMS-style). */

export type ToolMode = "select" | "pan" | "connect";

export type ToolModeBehavior = {
  nodesDraggable: boolean;
  nodesConnectable: boolean;
  elementsSelectable: boolean;
  /** false = no pane pan on drag; true = left-drag pans; array = button indices */
  panOnDrag: boolean | number[];
  selectionOnDrag: boolean;
  panOnScroll: boolean;
};

export function behaviorForMode(mode: ToolMode): ToolModeBehavior {
  switch (mode) {
    case "pan":
      return {
        nodesDraggable: false,
        nodesConnectable: false,
        elementsSelectable: false,
        panOnDrag: true,
        selectionOnDrag: false,
        panOnScroll: true,
      };
    case "connect":
      return {
        nodesDraggable: false,
        nodesConnectable: true,
        elementsSelectable: true,
        panOnDrag: [1, 2],
        selectionOnDrag: false,
        panOnScroll: true,
      };
    case "select":
    default:
      return {
        nodesDraggable: true,
        nodesConnectable: false,
        elementsSelectable: true,
        panOnDrag: [1, 2],
        selectionOnDrag: true,
        panOnScroll: true,
      };
  }
}

export function toolModeFromKey(key: string): ToolMode | null {
  const k = key.toLowerCase();
  if (k === "v") return "select";
  if (k === "h") return "pan";
  if (k === "c") return "connect";
  return null;
}
