import { useCallback, useMemo, useState } from "react";
import type { Edge, Node } from "@xyflow/react";
import type { TopologyTreeFolderItem, TopologyTreeViewItem, TopologyViewGraph, TopologyViewItem } from "../../../types";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import type { LabelColors, VendorColors } from "../lib/displayPrefs";
import { buildExportGraph, exportTopologySvg, exportTopologyXml } from "../lib/exportTopology";
import { regionDisplayName } from "../lib/treeUtils";

type Args = {
  canvasMode: boolean;
  isWorldFlatCanvas: boolean;
  activeLeafName: string;
  activeRegion: TopologyTreeFolderItem | null;
  activeView: TopologyTreeViewItem | null;
  graphQueryData?: TopologyViewGraph;
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  displayEdges: Edge[];
  labelColors: LabelColors;
  vendorColors: VendorColors;
  hideIp: boolean;
  hideVendor: boolean;
  showOk: (msg: string) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
};

export function useTopologyExport({
  canvasMode,
  isWorldFlatCanvas,
  activeLeafName,
  activeRegion,
  activeView,
  graphQueryData,
  nodes,
  edges,
  displayEdges,
  labelColors,
  vendorColors,
  hideIp,
  hideVendor,
  showOk,
  showError,
  t,
}: Args) {
  const [exporting, setExporting] = useState(false);

  const runTopologyExport = useCallback(
    async (format: "svg" | "xml") => {
      const view: TopologyViewItem | undefined =
        graphQueryData?.view ??
        (activeView
          ? {
              id: activeView.id,
              name: activeView.name,
              remark: "",
              node_count: activeView.node_count,
              kind: activeView.kind,
              role: activeView.role,
              sort_order: activeView.sort_order,
            }
          : undefined);
      if (!view) {
        showError(t("topology.selectMap"));
        return;
      }
      if (format !== "xml" && nodes.length === 0 && !isWorldFlatCanvas) {
        showError(t("topology.exportEmpty"));
        return;
      }
      setExporting(true);
      const filenameBase = activeLeafName || view.name || "topology";
      try {
        if (format === "svg") {
          await exportTopologySvg(
            nodes,
            displayEdges,
            filenameBase,
            {
              labelColor: labelColors.name,
              edgeLabelColor: labelColors.edgeLabel,
              vendorColors,
              hideIp,
              hideVendor,
            },
            filenameBase,
          );
          showOk(t("topology.exportedSvg"));
        } else {
          exportTopologyXml(buildExportGraph(view, nodes, edges, graphQueryData), filenameBase);
          showOk(t("topology.exportedXml"));
        }
      } catch (err) {
        showError(t("topology.exportFailed").replace("{{detail}}", String(err)));
      } finally {
        setExporting(false);
      }
    },
    [
      activeView,
      graphQueryData,
      nodes,
      edges,
      displayEdges,
      isWorldFlatCanvas,
      activeLeafName,
      labelColors,
      vendorColors,
      hideIp,
      hideVendor,
      showError,
      showOk,
      t,
    ],
  );

  const titleText = useMemo(() => {
    if (canvasMode) return activeLeafName || t("topology.selectMap");
    if (activeRegion) return regionDisplayName(activeRegion, t);
    return t("topology.rootName");
  }, [canvasMode, activeLeafName, activeRegion, t]);

  return { exporting, runTopologyExport, titleText };
}
