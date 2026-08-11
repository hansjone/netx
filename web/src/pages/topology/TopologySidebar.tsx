import type { MouseEvent as ReactMouseEvent, RefObject } from "react";
import type { FabricNodeSearchHit, TopologyTreeFolderItem } from "../../types";
import { useI18n } from "../../i18n";
import { WORLD_MAP_ENABLED } from "./constants";
import { isWorldFlatViewName } from "./treeUtils";
import { PlusIcon, SidebarFoldIcon } from "./topologyIcons";
import {
  TopologyTreeNavFolder,
  type TopologyTreeNavFolderProps,
} from "./TopologyTreeNavFolder";

export type TopologySidebarProps = {
  collapsed: boolean;
  resizing: boolean;
  treeSearchRef: RefObject<HTMLDivElement | null>;
  treeNeQuery: string;
  onTreeNeQueryChange: (value: string) => void;
  treeSearchOpen: boolean;
  onTreeSearchOpen: (open: boolean) => void;
  debouncedTreeNeQuery: string;
  treeSearchFetching: boolean;
  treeSearchItems: FabricNodeSearchHit[];
  treeSearchTotal: number;
  onJumpToSearchHit: (
    hit: FabricNodeSearchHit,
    view?: NonNullable<FabricNodeSearchHit["views"]>[number],
  ) => void;
  promptNewRegion: () => void;
  createRegionPending: boolean;
  onCollapse: () => void;
  onExpand: () => void;
  treeRoot: TopologyTreeFolderItem | null | undefined;
  treeLoading: boolean;
  treeFailed: boolean;
  treeError: unknown;
  onTreeRetry: () => void;
  regions: TopologyTreeFolderItem[];
  treeNav: Omit<TopologyTreeNavFolderProps, "folder" | "depth">;
  onResizeDown: (e: ReactMouseEvent<HTMLDivElement>) => void;
};

export function TopologySidebar({
  collapsed,
  resizing,
  treeSearchRef,
  treeNeQuery,
  onTreeNeQueryChange,
  treeSearchOpen,
  onTreeSearchOpen,
  debouncedTreeNeQuery,
  treeSearchFetching,
  treeSearchItems,
  treeSearchTotal,
  onJumpToSearchHit,
  promptNewRegion,
  createRegionPending,
  onCollapse,
  onExpand,
  treeRoot,
  treeLoading,
  treeFailed,
  treeError,
  onTreeRetry,
  regions,
  treeNav,
  onResizeDown,
}: TopologySidebarProps) {
  const { t } = useI18n();

  return (
    <aside className="topo-sidebar" aria-label={t("topology.maps")}>
      {collapsed ? (
        <div className="topo-sidebar__rail-wrap">
          <button
            type="button"
            className="topo-sidebar__rail"
            title={t("topology.expandSidebar")}
            aria-label={t("topology.expandSidebar")}
            onClick={onExpand}
          >
            <span className="topo-sidebar__rail-icon" aria-hidden="true">
              <SidebarFoldIcon expand />
            </span>
          </button>
        </div>
      ) : (
        <>
          <div className="topo-sidebar__section">
            <div className="topo-tree-search" ref={treeSearchRef}>
              <div className="topo-tree-search__bar">
                <input
                  className="input"
                  type="search"
                  value={treeNeQuery}
                  placeholder={t("topology.treeSearchPh")}
                  aria-label={t("topology.treeSearch")}
                  onChange={(e) => {
                    onTreeNeQueryChange(e.target.value);
                    onTreeSearchOpen(true);
                  }}
                  onFocus={() => onTreeSearchOpen(true)}
                />
                <button
                  type="button"
                  className="topo-sidebar__icon-btn"
                  onClick={promptNewRegion}
                  disabled={createRegionPending}
                  title={t("topology.newRegion")}
                  aria-label={t("topology.newRegion")}
                >
                  <PlusIcon />
                </button>
                <button
                  type="button"
                  className="topo-sidebar__icon-btn"
                  title={t("topology.collapseSidebar")}
                  aria-label={t("topology.collapseSidebar")}
                  onClick={onCollapse}
                >
                  <SidebarFoldIcon />
                </button>
              </div>
              {treeSearchOpen && treeNeQuery.trim() ? (
                <div className="topo-tree-search__panel" role="listbox">
                  {debouncedTreeNeQuery.length < 1 || treeSearchFetching ? (
                    <p className="topo-tree-search__hint muted">…</p>
                  ) : !treeSearchItems.length ? (
                    <p className="topo-tree-search__hint muted">{t("topology.treeSearchEmpty")}</p>
                  ) : (
                    treeSearchItems.map((hit) => {
                      const views = hit.views || [];
                      const title = hit.name || hit.ip || hit.id.slice(0, 8);
                      return (
                        <div key={hit.id} className="topo-tree-search__item">
                          <button
                            type="button"
                            className="topo-tree-search__ne"
                            onClick={() => onJumpToSearchHit(hit)}
                            title={
                              views.length
                                ? t("topology.locateOnCanvas")
                                : t("topology.treeSearchNotOnMap")
                            }
                          >
                            <span className="topo-tree-search__name">{title}</span>
                            {hit.ip ? (
                              <span className="topo-tree-search__ip muted">{hit.ip}</span>
                            ) : null}
                            {!views.length ? (
                              <span className="topo-tree-search__meta muted">
                                {t("topology.treeSearchNoViews")}
                              </span>
                            ) : (
                              <span className="topo-tree-search__meta muted">
                                {t("topology.treeSearchViewCount").replace(
                                  "{{count}}",
                                  String(views.length),
                                )}
                              </span>
                            )}
                          </button>
                          {views.length > 0 ? (
                            <div className="topo-tree-search__views">
                              {views
                                .filter(
                                  (v) => WORLD_MAP_ENABLED || !isWorldFlatViewName(v.view_name),
                                )
                                .map((v) => (
                                  <button
                                    key={`${hit.id}-${v.view_id}`}
                                    type="button"
                                    className="topo-tree-search__view"
                                    onClick={() => onJumpToSearchHit(hit, v)}
                                    title={`${v.folder_name ? `${v.folder_name} / ` : ""}${v.view_name}`}
                                  >
                                    {v.folder_name
                                      ? `${v.folder_name} / ${v.view_name || v.view_id.slice(0, 8)}`
                                      : v.view_name || v.view_id.slice(0, 8)}
                                  </button>
                                ))}
                            </div>
                          ) : null}
                        </div>
                      );
                    })
                  )}
                  {treeSearchTotal > 30 ? (
                    <p className="topo-tree-search__hint muted">
                      {t("topology.treeSearchTruncated").replace(
                        "{{total}}",
                        String(treeSearchTotal),
                      )}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
            {!treeRoot ? (
              <p className="panel__hint topo-tree-status" role="status">
                {treeLoading ? (
                  <>
                    <span className="topo-loading-spinner" aria-hidden="true" />
                    {t("topology.treeLoading")}
                  </>
                ) : treeFailed ? (
                  <>
                    {t("topology.treeLoadFailed")}
                    {treeError ? <span className="muted"> ({String(treeError)})</span> : null}{" "}
                    <button type="button" className="btn btn--sm btn--ghost" onClick={onTreeRetry}>
                      {t("topology.treeRetry")}
                    </button>
                  </>
                ) : (
                  t("topology.emptyMaps")
                )}
              </p>
            ) : regions.length === 0 ? (
              <p className="panel__hint">{t("topology.emptyMaps")}</p>
            ) : (
              <div className="topo-region-list-scroll">
                <ul className="topo-map-list topo-region-list">
                  {regions.map((region) => (
                    <TopologyTreeNavFolder key={region.id} folder={region} depth={0} {...treeNav} />
                  ))}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
      {!collapsed ? (
        <div
          className={`topo-sidebar__resizer${resizing ? " is-dragging" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label={t("topology.resizeSidebar")}
          title={t("topology.resizeSidebar")}
          onMouseDown={onResizeDown}
        />
      ) : null}
    </aside>
  );
}
