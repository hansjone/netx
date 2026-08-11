import type { TopologyTreeFolderItem, TopologyTreeViewItem } from "../../types";
import { useI18n } from "../../i18n";
import { WORLD_MAP_ENABLED } from "./constants";
import {
  displayViewName,
  folderNeCount,
  formatNeCount,
  isManualRootMapFolder,
  isRegionCanvasFolder,
  isUmeStructuralFolder,
  isUmeSyncedSubRegion,
  isUmeWorldNavFolder,
  isWorldDrillFolder,
  isWorldFlatViewName,
  regionDisplayName,
} from "./treeUtils";
import {
  ChevronIcon,
  CloseIcon,
  LayerGlyph,
  PencilIcon,
  RegionGlyph,
} from "./topologyIcons";

export type TopologyTreeNavFolderProps = {
  folder: TopologyTreeFolderItem;
  depth: number;
  treeRootId: string;
  expandedIds: Record<string, boolean>;
  mapId: string;
  worldViewId: string;
  selectedFolderId: string;
  hotBrowseKey: string;
  dirty: boolean;
  onToggleExpand: (folderId: string, nextOpen: boolean) => void;
  onHotBrowseKey: (key: string) => void;
  onClearHotBrowseKey: (key: string) => void;
  onWorldFocusClear: () => void;
  goUmeWorldNav: () => void;
  goRegion: (folderId: string) => void;
  goCanvas: (viewId: string, folderId: string) => void;
  promptRenameRegion: (folderId: string, name: string) => void;
  promptRenameMap: (viewId: string, name: string) => void;
  renameRegionPending: boolean;
  renameMapPending: boolean;
  onDeleteFolder: (folderId: string) => void;
  deleteFolderPending: boolean;
  onDeleteMap: (viewId: string) => void;
};

function ViewRow({
  v,
  folder,
  umeNav,
  mapId,
  dirty,
  hotBrowseKey,
  renameMapPending,
  onHotBrowseKey,
  onClearHotBrowseKey,
  onWorldFocusClear,
  goCanvas,
  promptRenameMap,
  onDeleteMap,
}: {
  v: TopologyTreeViewItem;
  folder: TopologyTreeFolderItem;
  umeNav: boolean;
  mapId: string;
  dirty: boolean;
  hotBrowseKey: string;
  renameMapPending: boolean;
  onHotBrowseKey: (key: string) => void;
  onClearHotBrowseKey: (key: string) => void;
  onWorldFocusClear: () => void;
  goCanvas: (viewId: string, folderId: string) => void;
  promptRenameMap: (viewId: string, name: string) => void;
  onDeleteMap: (viewId: string) => void;
}) {
  const { t } = useI18n();
  const isPhysical = String(v.kind) === "physical";
  const viewHot = hotBrowseKey === `view:${v.id}`;
  const viewActive = mapId === v.id;

  return (
    <li
      key={v.id}
      className={`topo-region-list__view${viewActive ? " is-active" : ""}${viewHot ? " is-hot" : ""}`}
    >
      <div
        className="topo-map-list__row"
        onMouseEnter={() => onHotBrowseKey(`view:${v.id}`)}
        onMouseLeave={() => onClearHotBrowseKey(`view:${v.id}`)}
      >
        <span className="topo-region-list__chevron-spacer" aria-hidden="true" />
        <button
          type="button"
          className="topo-map-list__item"
          onClick={() => {
            onWorldFocusClear();
            goCanvas(v.id, folder.id);
          }}
          onDoubleClick={() => (!umeNav ? promptRenameMap(v.id, v.name) : undefined)}
          title={!umeNav ? t("topology.renameHint") : undefined}
        >
          <span className="topo-map-list__name">
            <span
              className={`topo-region-list__glyph topo-dir__icon--${
                isPhysical ? "core" : "aggregation"
              }`}
              aria-hidden="true"
            >
              <LayerGlyph role={isPhysical ? "core" : "aggregation"} size={13} />
            </span>
            <span className="topo-map-list__title">
              {displayViewName(v.name, t)}
              {viewActive && dirty ? " *" : ""}
            </span>
            <span className="topo-map-list__count">{formatNeCount(v.node_count)}</span>
          </span>
        </button>
        {!umeNav ? (
          <div className="topo-map-list__actions">
            <button
              type="button"
              className="topo-map-list__icon"
              title={t("topology.rename")}
              aria-label={t("topology.rename")}
              disabled={renameMapPending}
              onClick={() => promptRenameMap(v.id, v.name)}
            >
              <PencilIcon />
            </button>
            <button
              type="button"
              className="topo-map-list__icon"
              title={t("topology.deleteMap")}
              aria-label={t("topology.deleteMap")}
              disabled={isPhysical}
              onClick={() => {
                const msg = t("topology.deleteMapConfirm").replace("{{name}}", v.name);
                if (window.confirm(msg)) onDeleteMap(v.id);
              }}
            >
              <CloseIcon />
            </button>
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function TopologyTreeNavFolder({
  folder,
  depth,
  treeRootId,
  expandedIds,
  mapId,
  worldViewId,
  selectedFolderId,
  hotBrowseKey,
  dirty,
  onToggleExpand,
  onHotBrowseKey,
  onClearHotBrowseKey,
  onWorldFocusClear,
  goUmeWorldNav,
  goRegion,
  goCanvas,
  promptRenameRegion,
  promptRenameMap,
  renameRegionPending,
  renameMapPending,
  onDeleteFolder,
  deleteFolderPending,
  onDeleteMap,
}: TopologyTreeNavFolderProps) {
  const { t } = useI18n();
  const kids = folder.children || [];
  const containerFolder = folder.external_ref === "ume:world" || folder.name === "UME World";
  const drillFolder = isWorldDrillFolder(folder);
  const umeNav = isUmeWorldNavFolder(folder);
  const canvasRegion = isRegionCanvasFolder(folder, treeRootId);
  const visibleViews = (
    canvasRegion
      ? (folder.views || []).filter((v) => String(v.kind) !== "physical")
      : containerFolder
        ? (folder.views || []).filter((v) => isWorldFlatViewName(v.name))
        : folder.views || []
  ).filter((v) => WORLD_MAP_ENABLED || !isWorldFlatViewName(v.name));
  const hasKids = kids.length > 0 || visibleViews.length > 0;
  const open = Boolean(expandedIds[folder.id]);
  const viewActiveUnder =
    (folder.views || []).some((v) => v.id === mapId) ||
    (drillFolder && Boolean(worldViewId) && mapId === worldViewId);
  const isSelected = selectedFolderId === folder.id;
  const focused = viewActiveUnder || (isSelected && Boolean(mapId));
  const regionActive = isSelected && !mapId;
  const hot = hotBrowseKey === `region:${folder.id}`;

  const canRenameFolder =
    !isUmeStructuralFolder(folder) && (!folder.is_system || isManualRootMapFolder(folder));
  const canDeleteFolder =
    !isUmeStructuralFolder(folder) &&
    !isManualRootMapFolder(folder) &&
    (!folder.is_system || isUmeSyncedSubRegion(folder));

  return (
    <li
      key={folder.id}
      className={`topo-region-list__block${focused ? " is-branch-active" : ""}${
        regionActive ? " is-active" : ""
      }${hot ? " is-hot" : ""}`}
    >
      <div
        className="topo-map-list__row topo-region-list__row"
        onMouseEnter={() => onHotBrowseKey(`region:${folder.id}`)}
        onMouseLeave={() => onClearHotBrowseKey(`region:${folder.id}`)}
      >
        <button
          type="button"
          className="topo-map-list__icon topo-region-list__chevron"
          title={open ? t("topology.collapseRegion") : t("topology.expandRegion")}
          aria-label={open ? t("topology.collapseRegion") : t("topology.expandRegion")}
          aria-expanded={open}
          disabled={!hasKids}
          onClick={(e) => {
            e.stopPropagation();
            if (!hasKids) return;
            onToggleExpand(folder.id, !open);
          }}
        >
          <ChevronIcon open={open} />
        </button>
        <button
          type="button"
          className="topo-map-list__item"
          onClick={() => {
            if (containerFolder) {
              goUmeWorldNav();
            } else {
              goRegion(folder.id);
            }
          }}
          title={t("topology.openRegion")}
        >
          <span className="topo-map-list__name">
            <span className="topo-region-list__glyph" aria-hidden="true">
              <RegionGlyph size={14} />
            </span>
            <span className="topo-map-list__title">{regionDisplayName(folder, t)}</span>
            <span className="topo-map-list__count">{formatNeCount(folderNeCount(folder))}</span>
          </span>
        </button>
        {canRenameFolder || canDeleteFolder ? (
          <div className="topo-map-list__actions">
            {canRenameFolder ? (
              <button
                type="button"
                className="topo-map-list__icon"
                title={t("topology.renameRegion")}
                aria-label={t("topology.renameRegion")}
                disabled={renameRegionPending}
                onClick={() => promptRenameRegion(folder.id, folder.name)}
              >
                <PencilIcon />
              </button>
            ) : null}
            {canDeleteFolder ? (
              <button
                type="button"
                className="topo-map-list__icon"
                title={t("topology.deleteRegion")}
                aria-label={t("topology.deleteRegion")}
                disabled={deleteFolderPending}
                onClick={() => {
                  const msg = t("topology.deleteRegionConfirm").replace("{{name}}", folder.name);
                  if (window.confirm(msg)) onDeleteFolder(folder.id);
                }}
              >
                <CloseIcon />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {open ? (
        <ul className="topo-map-list topo-region-list__maps">
          {containerFolder ? (
            <>
              {kids.map((child) => (
                <TopologyTreeNavFolder
                  key={child.id}
                  folder={child}
                  depth={depth + 1}
                  treeRootId={treeRootId}
                  expandedIds={expandedIds}
                  mapId={mapId}
                  worldViewId={worldViewId}
                  selectedFolderId={selectedFolderId}
                  hotBrowseKey={hotBrowseKey}
                  dirty={dirty}
                  onToggleExpand={onToggleExpand}
                  onHotBrowseKey={onHotBrowseKey}
                  onClearHotBrowseKey={onClearHotBrowseKey}
                  onWorldFocusClear={onWorldFocusClear}
                  goUmeWorldNav={goUmeWorldNav}
                  goRegion={goRegion}
                  goCanvas={goCanvas}
                  promptRenameRegion={promptRenameRegion}
                  promptRenameMap={promptRenameMap}
                  renameRegionPending={renameRegionPending}
                  renameMapPending={renameMapPending}
                  onDeleteFolder={onDeleteFolder}
                  deleteFolderPending={deleteFolderPending}
                  onDeleteMap={onDeleteMap}
                />
              ))}
              {visibleViews.map((v) => (
                <ViewRow
                  key={v.id}
                  v={v}
                  folder={folder}
                  umeNav={umeNav}
                  mapId={mapId}
                  dirty={dirty}
                  hotBrowseKey={hotBrowseKey}
                  renameMapPending={renameMapPending}
                  onHotBrowseKey={onHotBrowseKey}
                  onClearHotBrowseKey={onClearHotBrowseKey}
                  onWorldFocusClear={onWorldFocusClear}
                  goCanvas={goCanvas}
                  promptRenameMap={promptRenameMap}
                  onDeleteMap={onDeleteMap}
                />
              ))}
            </>
          ) : (
            <>
              {visibleViews.map((v) => (
                <ViewRow
                  key={v.id}
                  v={v}
                  folder={folder}
                  umeNav={umeNav}
                  mapId={mapId}
                  dirty={dirty}
                  hotBrowseKey={hotBrowseKey}
                  renameMapPending={renameMapPending}
                  onHotBrowseKey={onHotBrowseKey}
                  onClearHotBrowseKey={onClearHotBrowseKey}
                  onWorldFocusClear={onWorldFocusClear}
                  goCanvas={goCanvas}
                  promptRenameMap={promptRenameMap}
                  onDeleteMap={onDeleteMap}
                />
              ))}
              {kids.map((child) => (
                <TopologyTreeNavFolder
                  key={child.id}
                  folder={child}
                  depth={depth + 1}
                  treeRootId={treeRootId}
                  expandedIds={expandedIds}
                  mapId={mapId}
                  worldViewId={worldViewId}
                  selectedFolderId={selectedFolderId}
                  hotBrowseKey={hotBrowseKey}
                  dirty={dirty}
                  onToggleExpand={onToggleExpand}
                  onHotBrowseKey={onHotBrowseKey}
                  onClearHotBrowseKey={onClearHotBrowseKey}
                  onWorldFocusClear={onWorldFocusClear}
                  goUmeWorldNav={goUmeWorldNav}
                  goRegion={goRegion}
                  goCanvas={goCanvas}
                  promptRenameRegion={promptRenameRegion}
                  promptRenameMap={promptRenameMap}
                  renameRegionPending={renameRegionPending}
                  renameMapPending={renameMapPending}
                  onDeleteFolder={onDeleteFolder}
                  deleteFolderPending={deleteFolderPending}
                  onDeleteMap={onDeleteMap}
                />
              ))}
            </>
          )}
        </ul>
      ) : null}
    </li>
  );
}
