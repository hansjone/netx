import { Button, Dropdown } from "@heroui/react";
import { Fragment, type Key, type RefObject, useMemo } from "react";
import type { Edge, Node, ReactFlowInstance } from "@xyflow/react";
import type { TopologyTreeFolderItem, TopologyTreeViewItem } from "../../types";
import { HelpHint } from "../../components/HelpHint";
import { useI18n } from "../../i18n";
import { displayViewName, isRegionCanvasFolder, regionDisplayName } from "./treeUtils";
import type { NeNodeData } from "./TopologyReactFlowView";
import type { ToolMode } from "./toolMode";

export type TopologyToolbarProps = {
  readOnly?: boolean;
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

type MoreActionKey =
  | "live-sync"
  | "back"
  | "undo"
  | "redo"
  | "fit"
  | "export-svg"
  | "export-xml"
  | "remove-stale";

export function TopologyToolbar({
  readOnly = false,
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

  const canvasEmpty = !isWorldFlatCanvas && nodes.length === 0;

  const moreDisabledKeys = useMemo(() => {
    const keys: MoreActionKey[] = [];
    if (readOnly || !canUndo) keys.push("undo");
    if (readOnly || !canRedo) keys.push("redo");
    if (canvasEmpty) keys.push("fit");
    if (exporting || canvasEmpty) keys.push("export-svg");
    if (exporting || !mapId) keys.push("export-xml");
    if (readOnly || !staleEdgeCount) keys.push("remove-stale");
    return keys;
  }, [
    readOnly,
    canUndo,
    canRedo,
    canvasEmpty,
    exporting,
    mapId,
    staleEdgeCount,
  ]);

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

  const handleMoreAction = (key: Key) => {
    switch (key as MoreActionKey) {
      case "live-sync":
        onLiveSyncToggle();
        break;
      case "back":
        onBackUp();
        break;
      case "undo":
        onUndo();
        break;
      case "redo":
        onRedo();
        break;
      case "fit":
        onFit();
        break;
      case "export-svg":
        void onExport("svg");
        break;
      case "export-xml":
        void onExport("xml");
        break;
      case "remove-stale":
        void onRemoveStale();
        break;
      default:
        break;
    }
  };

  return (
    <div className="topo-toolbar">
      <div className="topo-toolbar__row">
        <div className="topo-toolbar__title">
          <div className="topo-breadcrumb">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="topo-breadcrumb__link"
              onPress={() => goRoot()}
            >
              {t("topology.rootName")}
            </Button>
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
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="topo-breadcrumb__link"
                      onPress={() => goRegion(folder.id)}
                    >
                      {regionDisplayName(folder, t)}
                    </Button>
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
        <div className="topo-toolbar__actions topo-toolbar__primary">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            isDisabled={readOnly || isWorldFlatCanvas}
            onPress={onAddNe}
          >
            {t("topology.addNe")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            isDisabled={readOnly || isWorldFlatCanvas}
            onPress={handleCreateNe}
          >
            {t("topology.createNe")}
          </Button>
          <Button
            type="button"
            size="sm"
            className={dirty ? "topo-toolbar__save topo-toolbar__save--dirty" : "topo-toolbar__save"}
            variant={dirty ? "primary" : "ghost"}
            isDisabled={readOnly || savePending || !dirty}
            onPress={onSave}
          >
            {savePending ? t("topology.saving") : dirty ? t("topology.saveDirty") : t("topology.save")}
          </Button>
          <Dropdown>
            <Button type="button" size="sm" variant="ghost" aria-label={t("topology.more")}>
              {t("topology.more")}
            </Button>
            <Dropdown.Popover className="topo-toolbar__more-popover" placement="bottom end">
              <Dropdown.Menu
                aria-label={t("topology.more")}
                disabledKeys={moreDisabledKeys}
                onAction={handleMoreAction}
              >
                <Dropdown.Item id="live-sync" textValue={liveSync ? t("topology.liveSyncOn") : t("topology.liveSync")}>
                  {liveSync ? t("topology.liveSyncOn") : t("topology.liveSync")}
                </Dropdown.Item>
                <Dropdown.Item id="back" textValue={t("topology.backUp")}>
                  {t("topology.backUp")}
                </Dropdown.Item>
                <Dropdown.Item id="undo" textValue={t("topology.undo")}>
                  {t("topology.undo")}
                </Dropdown.Item>
                <Dropdown.Item id="redo" textValue={t("topology.redo")}>
                  {t("topology.redo")}
                </Dropdown.Item>
                <Dropdown.Item id="fit" textValue={t("topology.fit")}>
                  {t("topology.fit")}
                </Dropdown.Item>
                <Dropdown.Item
                  id="export-svg"
                  textValue={exporting ? t("topology.exporting") : t("topology.exportSvg")}
                >
                  {exporting ? t("topology.exporting") : t("topology.exportSvg")}
                </Dropdown.Item>
                <Dropdown.Item
                  id="export-xml"
                  textValue={exporting ? t("topology.exporting") : t("topology.exportXml")}
                >
                  {exporting ? t("topology.exporting") : t("topology.exportXml")}
                </Dropdown.Item>
                <Dropdown.Item
                  id="remove-stale"
                  textValue={t("topology.removeStale").replace("{{count}}", String(staleEdgeCount))}
                >
                  {t("topology.removeStale").replace("{{count}}", String(staleEdgeCount))}
                </Dropdown.Item>
              </Dropdown.Menu>
            </Dropdown.Popover>
          </Dropdown>
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
          ).map(([mode, label, key]) => {
            const active = toolMode === mode;
            return (
              <Button
                key={mode}
                type="button"
                className={`topo-tools__btn${active ? " is-active" : ""}`}
                variant={active ? "primary" : "ghost"}
                size="sm"
                aria-pressed={active}
                isDisabled={readOnly && mode === "connect"}
                onPress={() => {
                  onToolModeChange(mode);
                  onConnectClickReset();
                }}
              >
                <span className="topo-tools__label">{label}</span>
                <kbd className="topo-tools__key">{key}</kbd>
              </Button>
            );
          })}
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
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="topo-toolbar__outside-peers"
            onPress={onOpenOutsidePeers}
          >
            {t("topology.outsidePeersView").replace("{{count}}", String(outsidePeerCount))}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
