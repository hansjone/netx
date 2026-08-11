import { Fragment } from "react";
import type { TopologyTreeFolderItem, TopologyTreeViewItem } from "../../types";
import { useI18n } from "../../i18n";
import {
  displayViewName,
  folderNeCount,
  formatNeCount,
  isUmeWorldContainer,
  regionDisplayName,
} from "./treeUtils";
import { LayerGlyph, RegionGlyph } from "./topologyIcons";

export type UmeWorldHexModules = {
  drill: TopologyTreeFolderItem | null;
  flatView: TopologyTreeViewItem | null;
};

export type TopologyHexBrowserProps = {
  titleText: string;
  breadcrumbFolders: TopologyTreeFolderItem[];
  hexBrowseRegion: TopologyTreeFolderItem | null;
  umeWorldHexModules: UmeWorldHexModules | null;
  browseEntries: TopologyTreeViewItem[];
  regions: TopologyTreeFolderItem[];
  rootFolderId: string;
  selectedFolderId: string;
  mapId: string;
  hotBrowseKey: string;
  treeLoading: boolean;
  treeFailed: boolean;
  treeError: unknown;
  createRegionPending: boolean;
  onTreeRetry: () => void;
  onHotBrowseKey: (key: string) => void;
  onClearHotBrowseKey: (key: string) => void;
  goRoot: () => void;
  goRegion: (folderId: string) => void;
  goUmeWorldNav: () => void;
  goCanvas: (viewId: string, folderId: string) => void;
  promptNewRegion: () => void;
  promptNewSubRegion: () => void;
  onSelectFolder: (folderId: string) => void;
};

export function TopologyHexBrowser({
  titleText,
  breadcrumbFolders,
  hexBrowseRegion,
  umeWorldHexModules,
  browseEntries,
  regions,
  rootFolderId,
  selectedFolderId,
  mapId,
  hotBrowseKey,
  treeLoading,
  treeFailed,
  treeError,
  createRegionPending,
  onTreeRetry,
  onHotBrowseKey,
  onClearHotBrowseKey,
  goRoot,
  goRegion,
  goUmeWorldNav,
  goCanvas,
  promptNewRegion,
  promptNewSubRegion,
  onSelectFolder,
}: TopologyHexBrowserProps) {
  const { t } = useI18n();

  const regionCountLabel = hexBrowseRegion
    ? isUmeWorldContainer(hexBrowseRegion) && umeWorldHexModules
      ? t("topology.browserRegionSub").replace(
          "{{count}}",
          String((umeWorldHexModules.drill ? 1 : 0) + (umeWorldHexModules.flatView ? 1 : 0)),
        )
      : t("topology.browserRegionSub").replace("{{count}}", String(browseEntries.length))
    : t("topology.browserRegionsSub").replace("{{count}}", String(regions.length));

  return (
    <div className="topo-browser" aria-label={t("topology.browserTitle")}>
      <div className="topo-browser__head">
        <div>
          {hexBrowseRegion ? (
            <div className="topo-breadcrumb">
              <button type="button" className="topo-breadcrumb__link" onClick={() => goRoot()}>
                {t("topology.rootName")}
              </button>
              {breadcrumbFolders.map((folder, idx) => {
                const isLast = idx === breadcrumbFolders.length - 1;
                return (
                  <Fragment key={folder.id}>
                    <span className="topo-breadcrumb__sep">/</span>
                    {isLast ? (
                      <span className="topo-breadcrumb__current">{regionDisplayName(folder, t)}</span>
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
            </div>
          ) : (
            <strong>{titleText}</strong>
          )}
          <p className="topo-browser__sub">{regionCountLabel}</p>
        </div>
      </div>
      {!hexBrowseRegion ? (
        treeLoading ? (
          <div className="topo-browser__empty topo-browser__empty--loading" role="status">
            <span className="topo-loading-spinner" aria-hidden="true" />
            <p>{t("topology.treeLoading")}</p>
          </div>
        ) : treeFailed ? (
          <div className="topo-browser__empty">
            <p>{t("topology.treeLoadFailed")}</p>
            {treeError ? <span className="muted"> ({String(treeError)})</span> : null}
            <div className="topo-browser__empty-actions">
              <button type="button" className="btn btn--sm btn--ghost" onClick={onTreeRetry}>
                {t("topology.treeRetry")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                onClick={promptNewRegion}
                disabled={createRegionPending}
              >
                {t("topology.newRegion")}
              </button>
            </div>
          </div>
        ) : regions.length === 0 ? (
          <div className="topo-browser__empty">
            <span className="topo-browser__empty-icon" aria-hidden="true">
              <RegionGlyph size={36} />
            </span>
            <p>{t("topology.emptyMaps")}</p>
            <button
              type="button"
              className="btn btn--sm"
              onClick={promptNewRegion}
              disabled={createRegionPending}
            >
              {t("topology.newRegion")}
            </button>
          </div>
        ) : (
          <div className="topo-browser__grid topo-browser__grid--regions">
            {regions.map((region, idx) => (
              <button
                key={region.id}
                type="button"
                className={`topo-region-hex topo-region-hex--tone-${idx % 5}${
                  selectedFolderId === region.id ? " is-selected" : ""
                }${hotBrowseKey === `region:${region.id}` ? " is-hot" : ""}`}
                onMouseEnter={() => onHotBrowseKey(`region:${region.id}`)}
                onMouseLeave={() => onClearHotBrowseKey(`region:${region.id}`)}
                onClick={() =>
                  isUmeWorldContainer(region) ? goUmeWorldNav() : goRegion(region.id)
                }
                title={t("topology.openRegion")}
              >
                <span className="topo-region-hex__icon" aria-hidden="true">
                  <RegionGlyph size={22} />
                </span>
                <span className="topo-region-hex__title">
                  <span className="topo-region-hex__name">{regionDisplayName(region, t)}</span>
                  <span className="topo-region-hex__meta">{formatNeCount(folderNeCount(region))}</span>
                </span>
              </button>
            ))}
            <button
              type="button"
              className="topo-region-hex topo-region-hex--add"
              onClick={promptNewRegion}
              disabled={createRegionPending}
              title={t("topology.newRegion")}
            >
              <span className="topo-region-hex__plus" aria-hidden="true">
                +
              </span>
              <span className="topo-region-hex__name">{t("topology.newRegion")}</span>
            </button>
          </div>
        )
      ) : isUmeWorldContainer(hexBrowseRegion) && umeWorldHexModules ? (
        <div className="topo-browser__grid topo-browser__grid--regions">
          {umeWorldHexModules.drill ? (
            <button
              key={umeWorldHexModules.drill.id}
              type="button"
              className={`topo-region-hex topo-region-hex--tone-0${
                hotBrowseKey === `region:${umeWorldHexModules.drill.id}` ? " is-hot" : ""
              }`}
              onMouseEnter={() => onHotBrowseKey(`region:${umeWorldHexModules.drill!.id}`)}
              onMouseLeave={() => onClearHotBrowseKey(`region:${umeWorldHexModules.drill!.id}`)}
              onClick={() => goRegion(umeWorldHexModules.drill!.id)}
              title={t("topology.openRegion")}
            >
              <span className="topo-region-hex__icon" aria-hidden="true">
                <RegionGlyph size={22} />
              </span>
              <span className="topo-region-hex__title">
                <span className="topo-region-hex__name">
                  {regionDisplayName(umeWorldHexModules.drill, t)}
                </span>
                <span className="topo-region-hex__meta">
                  {formatNeCount(folderNeCount(umeWorldHexModules.drill))}
                </span>
              </span>
            </button>
          ) : null}
          {umeWorldHexModules.flatView ? (
            <button
              key={umeWorldHexModules.flatView.id}
              type="button"
              className={`topo-region-hex topo-region-hex--tone-1${
                hotBrowseKey === `view:${umeWorldHexModules.flatView.id}` ? " is-hot" : ""
              }`}
              onMouseEnter={() => onHotBrowseKey(`view:${umeWorldHexModules.flatView!.id}`)}
              onMouseLeave={() => onClearHotBrowseKey(`view:${umeWorldHexModules.flatView!.id}`)}
              onClick={() => goCanvas(umeWorldHexModules.flatView!.id, hexBrowseRegion.id)}
              title={t("topology.openMap")}
            >
              <span className="topo-region-hex__icon" aria-hidden="true">
                <RegionGlyph size={22} />
              </span>
              <span className="topo-region-hex__title">
                <span className="topo-region-hex__name">{umeWorldHexModules.flatView.name}</span>
                <span className="topo-region-hex__meta">
                  {formatNeCount(umeWorldHexModules.flatView.node_count)}
                </span>
              </span>
            </button>
          ) : null}
        </div>
      ) : (
        <div className="topo-browser__grid topo-browser__grid--regions">
          {(hexBrowseRegion.children || []).map((region, idx) => (
            <button
              key={region.id}
              type="button"
              className={`topo-region-hex topo-region-hex--tone-${idx % 5}${
                selectedFolderId === region.id ? " is-selected" : ""
              }${hotBrowseKey === `region:${region.id}` ? " is-hot" : ""}`}
              onMouseEnter={() => onHotBrowseKey(`region:${region.id}`)}
              onMouseLeave={() => onClearHotBrowseKey(`region:${region.id}`)}
              onClick={() => goRegion(region.id)}
              title={t("topology.openRegion")}
            >
              <span className="topo-region-hex__icon" aria-hidden="true">
                <RegionGlyph size={22} />
              </span>
              <span className="topo-region-hex__title">
                <span className="topo-region-hex__name">{regionDisplayName(region, t)}</span>
                <span className="topo-region-hex__meta">{formatNeCount(folderNeCount(region))}</span>
              </span>
            </button>
          ))}
          {browseEntries.map((v, idx) => {
            const isPhysical = String(v.kind) === "physical";
            const tone = isPhysical ? "physical" : String((idx + 1) % 5);
            return (
              <button
                key={v.id}
                type="button"
                className={`topo-region-hex topo-region-hex--tone-${tone}${
                  mapId === v.id ? " is-selected" : ""
                }${hotBrowseKey === `view:${v.id}` ? " is-hot" : ""}`}
                onMouseEnter={() => onHotBrowseKey(`view:${v.id}`)}
                onMouseLeave={() => onClearHotBrowseKey(`view:${v.id}`)}
                onClick={() => goCanvas(v.id, hexBrowseRegion.id)}
                title={t("topology.openMap")}
              >
                <span className="topo-region-hex__icon" aria-hidden="true">
                  <LayerGlyph role={isPhysical ? "core" : "aggregation"} size={22} />
                </span>
                <span className="topo-region-hex__title">
                  <span className="topo-region-hex__name">{displayViewName(v.name, t)}</span>
                  <span className="topo-region-hex__meta">{formatNeCount(v.node_count)}</span>
                </span>
              </button>
            );
          })}
          {String(hexBrowseRegion.parent_id || "") !== rootFolderId ? (
            <button
              type="button"
              className="topo-region-hex topo-region-hex--add"
              onClick={() => {
                onSelectFolder(hexBrowseRegion.id);
                promptNewSubRegion();
              }}
              title={t("topology.newSubRegion")}
            >
              <span className="topo-region-hex__plus" aria-hidden="true">
                +
              </span>
              <span className="topo-region-hex__name">{t("topology.newSubRegion")}</span>
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}
