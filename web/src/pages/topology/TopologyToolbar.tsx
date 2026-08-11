import { Fragment, type RefObject } from "react";
import type { Edge, Node, ReactFlowInstance } from "@xyflow/react";
import type { TopologyTreeFolderItem, TopologyTreeViewItem } from "../../types";
import { HelpHint } from "../../components/HelpHint";
import { useI18n } from "../../i18n";
import { displayViewName, isRegionCanvasFolder, regionDisplayName } from "./treeUtils";
import type { NeNodeData } from "./TopologyReactFlowView";
import type { ToolMode } from "./toolMode";

export type TopologyToolbarProps = {
  breadcrumbFolders: TopologyTreeFolderItem[];
  activeView: TopologyTreeViewItem | null | undefined;
  activeRegion: TopologyTreeFolderItem | null | undefined;
  rootFolderId: string;
  dirty: boolean;
  selectedNodes: Node<NeNodeData>[];
  selectedEdgeId: string | null;
  liveSync: boolean;
  onLiveSyncToggle: () => void;
  isWorldFlatCanvas: boolean;
  onAddNe: () => void;
  onCreateNe: (flowX: number, flowY: number) => void;
  nodes: Node<NeNodeData>[];
  rfRef: RefObject<ReactFlowInstance<Node<NeNodeData>, Edge> | null>;
  onBackUp: () => void;
  canUndo: boolean;
  onUndo: () => void;
  canRedo: boolean;
  onRedo: () => void;
  savePending: boolean;
  onSave: () => void;
  onFit: () => void;
  exportMenuRef: RefObject<HTMLDetailsElement | null>;
  exporting: boolean;
  onExport: (kind: "svg" | "xml") => void;
  mapId: string;
  staleEdgeCount: number;
  onRemoveStale: () => void;
  toolMode: ToolMode;
  onToolModeChange: (mode: ToolMode) => void;
  onConnectClickReset: () => void;
  fullscreen: boolean;
  viewToolsToolbarSlotRef: (el: HTMLDivElement | null) => void;
  outsidePeerCount: number;
  onOpenOutsidePeers: () => void;
  goRoot: () => void;
  goRegion: (folderId: string) => void;
  primaryViewOfFolder: (
    folder: TopologyTreeFolderItem | null | undefined,
  ) => TopologyTreeViewItem | null | undefined;
};

export function TopologyToolbar({
  breadcrumbFolders,
  activeView,
  activeRegion,
  rootFolderId,
  dirty,
  selectedNodes,
  selectedEdgeId,
  liveSync,
  onLiveSyncToggle,
  isWorldFlatCanvas,
  onAddNe,
  onCreateNe,
  nodes,
  rfRef,
  onBackUp,
  canUndo,
  onUndo,
  canRedo,
  onRedo,
  savePending,
  onSave,
  onFit,
  exportMenuRef,
  exporting,
  onExport,
  mapId,
  staleEdgeCount,
  onRemoveStale,
  toolMode,
  onToolModeChange,
  onConnectClickReset,
  fullscreen,
  viewToolsToolbarSlotRef,
  outsidePeerCount,
  onOpenOutsidePeers,
  goRoot,
  goRegion,
  primaryViewOfFolder,
}: TopologyToolbarProps) {
  const { t } = useI18n();

  const handleCreateNe = () => {
    let flowX = 80 + nodes.length * 24;
    let flowY = 80 + nodes.length * 24;
    if (rfRef.current) {
      const pane = document.querySelector(".react-flow__pane");
      const rect = pane?.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) {
        const center = rfRef.current.screenToFlowPosition({
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
        });
        flowX = center.x;
        flowY = center.y;
      }
    }
    onCreateNe(flowX, flowY);
  };

  return (
    <div className="topo-toolbar">
      <div className="topo-toolbar__row">
        <div className="topo-toolbar__title">
          <div className="topo-breadcrumb">
            <button type="button" className="topo-breadcrumb__link" onClick={() => goRoot()}>
              {t("topology.rootName")}
            </button>
            {breadcrumbFolders.map((folder, idx) => {
              const isLast = idx === breadcrumbFolders.length - 1;
              const primaryView = primaryViewOfFolder(folder);
              const showExtraView =
                isLast &&
                Boolean(activeView) &&
                !(
                  isRegionCanvasFolder(folder, rootFolderId) &&
                  primaryView?.id === activeView?.id
                );
              const asCurrent = isLast && !showExtraView;
              return (
                <Fragment key={folder.id}>
                  <span className="topo-breadcrumb__sep">/</span>
                  {asCurrent ? (
                    <span className="topo-breadcrumb__current">
                      {regionDisplayName(folder, t)}
                      {dirty ? " *" : ""}
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="topo-breadcrumb__link"
                      onClick={() => goRegion(folder.id)}
                    >
                      {regionDisplayName(folder, t)}
                    </button>
                  )}
                </Fragment>
              );
            })}
            {activeView &&
            !(
              activeRegion &&
              isRegionCanvasFolder(activeRegion, rootFolderId) &&
              primaryViewOfFolder(activeRegion)?.id === activeView.id
            ) ? (
              <>
                <span className="topo-breadcrumb__sep">/</span>
                <span className="topo-breadcrumb__current">
                  {displayViewName(activeView.name, t)}
                  {dirty ? " *" : ""}
                </span>
              </>
            ) : null}
          </div>
          {selectedNodes.length > 0 || selectedEdgeId ? (
            <span className="topo-toolbar__meta">
              {selectedNodes.length > 0
                ? t("topology.selectedCount").replace("{{count}}", String(selectedNodes.length))
                : t("topology.selectedEdge")}
            </span>
          ) : null}
          <HelpHint text={t("topology.canvasHint")} ariaLabel={t("common.help")} />
        </div>
        <div className="topo-toolbar__actions">
          <button
            type="button"
            className={`btn btn--sm${liveSync ? "" : " btn--ghost"}`}
            aria-pressed={liveSync}
            title={t("topology.liveSyncHint")}
            onClick={onLiveSyncToggle}
          >
            {liveSync ? t("topology.liveSyncOn") : t("topology.liveSync")}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={isWorldFlatCanvas}
            title={isWorldFlatCanvas ? t("topology.worldMapNoDirectNes") : undefined}
            onClick={onAddNe}
          >
            {t("topology.addNe")}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={isWorldFlatCanvas}
            title={isWorldFlatCanvas ? t("topology.worldMapNoDirectNes") : undefined}
            onClick={handleCreateNe}
          >
            {t("topology.createNe")}
          </button>
          <button type="button" className="btn btn--sm btn--ghost" onClick={onBackUp}>
            {t("topology.backUp")}
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={!canUndo}
            onClick={onUndo}
            title="Ctrl+Z"
          >
            {t("topology.undo")}
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={!canRedo}
            onClick={onRedo}
            title="Ctrl+Y"
          >
            {t("topology.redo")}
          </button>
          <button
            type="button"
            className={`btn btn--sm${dirty ? "" : " btn--ghost"}`}
            disabled={savePending || !dirty}
            onClick={onSave}
            title="Ctrl+S"
          >
            {savePending ? t("topology.saving") : dirty ? t("topology.saveDirty") : t("topology.save")}
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={!isWorldFlatCanvas && nodes.length === 0}
            onClick={onFit}
          >
            {t("topology.fit")}
          </button>
          <details className="topo-toolbar__export" ref={exportMenuRef}>
            <summary>{exporting ? t("topology.exporting") : t("topology.export")}</summary>
            <div className="topo-export-menu" role="menu" aria-label={t("topology.export")}>
              <button
                type="button"
                className="topo-export-menu__btn"
                role="menuitem"
                disabled={exporting || (!isWorldFlatCanvas && nodes.length === 0)}
                onClick={() => void onExport("svg")}
              >
                {t("topology.exportSvg")}
              </button>
              <button
                type="button"
                className="topo-export-menu__btn"
                role="menuitem"
                disabled={exporting || !mapId}
                onClick={() => void onExport("xml")}
              >
                {t("topology.exportXml")}
              </button>
            </div>
          </details>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={!staleEdgeCount}
            title={t("topology.removeStaleHint")}
            onClick={() => void onRemoveStale()}
          >
            {t("topology.removeStale").replace("{{count}}", String(staleEdgeCount))}
          </button>
        </div>
      </div>
      <div className="topo-toolbar__row topo-toolbar__row--tools">
        <div className="topo-tools" role="toolbar" aria-label={t("topology.toolModes")}>
          {(
            [
              ["select", t("topology.toolSelect"), "V"],
              ["pan", t("topology.toolPan"), "H"],
              ["connect", t("topology.toolConnect"), "C"],
            ] as const
          ).map(([mode, label, key]) => (
            <button
              key={mode}
              type="button"
              className={`topo-tools__btn${toolMode === mode ? " is-active" : ""}`}
              title={`${label} (${key})`}
              aria-pressed={toolMode === mode}
              onClick={() => {
                onToolModeChange(mode);
                onConnectClickReset();
              }}
            >
              <span className="topo-tools__label">{label}</span>
              <kbd className="topo-tools__key">{key}</kbd>
            </button>
          ))}
        </div>
        {!fullscreen ? (
          <div
            className="topo-toolbar__cluster"
            role="toolbar"
            aria-label={t("topology.display")}
            ref={viewToolsToolbarSlotRef}
          />
        ) : null}
        {outsidePeerCount > 0 ? (
          <button
            type="button"
            className="btn btn--sm btn--ghost topo-toolbar__outside-peers"
            title={t("topology.outsidePeers").replace("{{count}}", String(outsidePeerCount))}
            onClick={onOpenOutsidePeers}
          >
            {t("topology.outsidePeersView").replace("{{count}}", String(outsidePeerCount))}
          </button>
        ) : null}
      </div>
    </div>
  );
}
