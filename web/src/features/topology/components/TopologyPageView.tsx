import type { CSSProperties } from "react";
import { Suspense } from "react";
import { Button } from "@heroui/react";
import { TopologyDiscoverPanel } from "./TopologyDiscoverPanel";
import { TopologyHexBrowser } from "./TopologyHexBrowser";
import { TopologyToolbar } from "./TopologyToolbar";
import { TopologyViewTools } from "./TopologyViewTools";
import { TopologySidebar } from "./TopologySidebar";
import { TopologyModals } from "./TopologyModals";
import { TopologyCtxMenu } from "./TopologyCtxMenu";
import type { TopologyPageViewModel } from "../lib/pageViewModel";
import type { PaletteItem } from "../lib/pageTypes";
import { canvasBgLuminance, canvasFloatChromeVars } from "../lib/treeUtils";
import type { PlaceholderCreateDialogState } from "./modals/PlaceholderCreateDialog";


type Props = {
  v: TopologyPageViewModel;
};

export function TopologyPageView({ v }: Props) {
  const {
    TopologyReactFlowView,
    activeRegion,
    activeView,
    addNeOpen,
    addOutsidePeers,
    addPaletteItems,
    alarmByNodeId,
    applyAlign,
    applyWorldMut,
    autoLayoutAfterDiscover,
    breadcrumbFolders,
    browseEntries,
    canRedo,
    canUndo,
    canWriteTopology,
    cancelDiscover,
    canvasBg,
    canvasGraphEmpty,
    canvasGraphError,
    canvasGraphErrorMsg,
    canvasGraphLoading,
    canvasGraphRefreshing,
    canvasHits,
    canvasMode,
    canvasQuery,
    canvasRef,
    canvasZoom,
    clearSelection,
    closeConnectDetail,
    closeCreateNeMode,
    closeCtxMenu,
    closeManagedForm,
    closePlaceholder,
    connectClickRef,
    connectDetailRow,
    connectTestSelectedFor,
    connectTestSubmitting,
    createNeModeOpen,
    createRegionMut,
    ctxMenu,
    ctxMenuRef,
    debouncedTreeNeQuery,
    deleteFolderMut,
    dirty,
    discoverAutoAddUnmatched,
    discoverError,
    discoverOneFor,
    discoverOpen,
    discoverPct,
    discoverProgress,
    discoverProjectNeighbors,
    discoverReport,
    discoverResults,
    discoverSelectedFor,
    discoverSummary,
    discovering,
    displayEdges,
    displayMenuRef,
    displayOpts,
    edgeDefaults,
    edgeFlow,
    edges,
    expandPhysicalLinks,
    expandedIds,
    exporting,
    findActiveIdx,
    findBoxRef,
    findOnCanvas,
    findOpen,
    fitCanvas,
    focusEdge,
    focusNode,
    fullscreen,
    goBackBrowse,
    goCanvas,
    goRegion,
    goRoot,
    goUmeWorldNav,
    graphQuery,
    graphTruncated,
    hexBrowseRegion,
    hideIp,
    hidePorts,
    hideVendor,
    hotBrowseKey,
    isValidConnection,
    isWorldFlatCanvas,
    jumpToTreeSearchHit,
    keyword,
    labelColors,
    liveSync,
    managedFormInitial,
    managedFormOpen,
    mapId,
    markDirty,
    newRootDialog,
    nodes,
    nodesRef,
    onCanvasDragOver,
    onCanvasDrop,
    onConnect,
    onConnectRetestSubmitted,
    onManagedFormSaved,
    onNodeClick,
    onNodeDoubleClick,
    onPaletteDragStart,
    onSidebarResizeDown,
    onTreeClearHotBrowseKey,
    onTreeDeleteFolder,
    onTreeDeleteMap,
    onTreeHotBrowseKey,
    onTreeToggleExpand,
    openAlarmsFor,
    openCreateNeAt,
    openNeFor,
    openPortTrafficForEdge,
    openWebcrtFor,
    outsidePeerNameById,
    outsidePeerQuery,
    outsidePeerSelectedIds,
    outsidePeers,
    outsidePeersAdding,
    outsidePeersOpen,
    outsidePeersVisible,
    paletteAdding,
    paletteLoading,
    paletteSelectedKeys,
    paletteSource,
    paletteVisible,
    patchSelectedEdgeStyle,
    pendingFitRef,
    pickCreateManaged,
    pickCreatePlaceholder,
    placeCtxMenu,
    placeholderBusy,
    placeholderDialog,
    popHistory,
    primaryViewOfFolder,
    promptNewRegion,
    promptNewSubRegion,
    promptRenameMap,
    promptRenameRegion,
    purgePlaceholderById,
    pushHistory,
    redo,
    regions,
    removeEdgeById,
    removeNodeById,
    removeSelected,
    removeStaleEdges,
    renameMapMut,
    renameRegionMut,
    renameSelectedNode,
    resetEdgeDefaults,
    rfRef,
    rootFolderId,
    runConnectTestForNode,
    runTopologyExport,
    saveMut,
    scaleBundleWidth,
    scheduleFlatViewportRefresh,
    searchHitIds,
    selectedConnectableCount,
    selectedDiscoverableCount,
    selectedEdge,
    selectedEdgeData,
    selectedEdgeId,
    selectedEdgeResolved,
    selectedEdgeSourceNode,
    selectedEdgeTargetNode,
    selectedFolderId,
    selectedNode,
    selectedNodeIds,
    selectedNodes,
    setAddNeOpen,
    setAutoLayoutAfterDiscover,
    setCanvasBg,
    setCanvasQuery,
    setCanvasZoom,
    setCtxMenu,
    setDiscoverAutoAddUnmatched,
    setDiscoverOpen,
    setDiscoverProjectNeighbors,
    setEdgeFlow,
    setEdges,
    setExpandPhysicalLinks,
    setFindActiveIdx,
    setFindOpen,
    setHideIp,
    setHidePorts,
    setHideVendor,
    setHotBrowseKey,
    setKeyword,
    setLabelColors,
    setLiveSync,
    setNewRootDialog,
    setNodes,
    setOutsidePeerQuery,
    setOutsidePeerSelectedIds,
    setOutsidePeersOpen,
    setPaletteSelectedKeys,
    setPaletteSource,
    setPlaceholderDialog,
    setScaleBundleWidth,
    setSelectedFolderId,
    setShowAlarmOverlay,
    setShowConnectStatus,
    setShowLevelColors,
    setShowPlaceholderBadge,
    setSidebarCollapsed,
    setSnapToGrid,
    setToolMode,
    setTreeNeQuery,
    setTreeSearchOpen,
    setVendorColors,
    setViewToolsToolbarSlot,
    setWorldFocusFolderId,
    showAlarmOverlay,
    showConnectStatus,
    showError,
    showLevelColors,
    showPlaceholderBadge,
    showWorldScatter,
    sidebarCollapsed,
    sidebarResizing,
    sidebarWidth,
    snapToGrid,
    staleEdgeIds,
    submitNewRoot,
    submitPlaceholder,
    t,
    titleText,
    toggleFullscreen,
    toolBehavior,
    toolMode,
    topoPageRef,
    treeFailed,
    treeLoading,
    treeNeQuery,
    treeNeSearchQuery,
    treeQuery,
    treeRoot,
    treeSearchOpen,
    treeSearchRef,
    truncateBannerText,
    truncateReason,
    umeWorldHexModules,
    undo,
    updateEdgeDefault,
    vendorColors,
    viewToolsToolbarSlot,
    worldDockMe,
    worldNeedsApply,
    worldScatter,
    worldTotal,
    worldTransformRef,
    worldViewId,
    worldVisualLod,
  } = v;

  return (
    <div
      ref={topoPageRef}
      className={`topo-page${sidebarCollapsed ? " is-sidebar-collapsed" : ""}${
        sidebarResizing ? " is-sidebar-resizing" : ""
      }`}
      style={
        sidebarCollapsed
          ? undefined
          : ({ "--topo-sidebar-w": `${sidebarWidth}px` } as CSSProperties)
      }
    >
      <TopologySidebar
        collapsed={sidebarCollapsed}
        resizing={sidebarResizing}
        treeSearchRef={treeSearchRef}
        treeNeQuery={treeNeQuery}
        onTreeNeQueryChange={setTreeNeQuery}
        treeSearchOpen={treeSearchOpen}
        onTreeSearchOpen={setTreeSearchOpen}
        debouncedTreeNeQuery={debouncedTreeNeQuery}
        treeSearchFetching={treeNeSearchQuery.isFetching}
        treeSearchItems={treeNeSearchQuery.data?.items || []}
        treeSearchTotal={treeNeSearchQuery.data?.total || 0}
        onJumpToSearchHit={jumpToTreeSearchHit}
        promptNewRegion={promptNewRegion}
        createRegionPending={createRegionMut.isPending}
        onCollapse={() => setSidebarCollapsed(true)}
        onExpand={() => setSidebarCollapsed(false)}
        treeRoot={treeRoot}
        treeLoading={treeLoading}
        treeFailed={treeFailed}
        treeError={treeQuery.error}
        onTreeRetry={() => void treeQuery.refetch()}
        regions={regions}
        treeNav={{
          treeRootId: String(treeRoot?.id || ""),
          expandedIds,
          mapId,
          worldViewId,
          selectedFolderId,
          hotBrowseKey,
          dirty,
          onToggleExpand: onTreeToggleExpand,
          onHotBrowseKey: onTreeHotBrowseKey,
          onClearHotBrowseKey: onTreeClearHotBrowseKey,
          onWorldFocusClear: () => setWorldFocusFolderId(""),
          goUmeWorldNav,
          goRegion,
          goCanvas,
          promptRenameRegion,
          promptRenameMap,
          renameRegionPending: renameRegionMut.isPending,
          renameMapPending: renameMapMut.isPending,
          onDeleteFolder: onTreeDeleteFolder,
          deleteFolderPending: deleteFolderMut.isPending,
          onDeleteMap: onTreeDeleteMap,
        }}
        onResizeDown={onSidebarResizeDown}
      />

      <main className="topo-main">
        {canvasMode ? (
          <TopologyToolbar
            readOnly={!canWriteTopology}
            breadcrumbFolders={breadcrumbFolders}
            activeView={activeView}
            activeRegion={activeRegion}
            rootFolderId={rootFolderId}
            dirty={dirty}
            selectedNodes={selectedNodes}
            selectedEdgeId={selectedEdgeId}
            liveSync={liveSync}
            onLiveSyncToggle={() => setLiveSync((v: boolean) => !v)}
            isWorldFlatCanvas={isWorldFlatCanvas}
            onAddNe={() => {
              if (isWorldFlatCanvas) {
                showError(t("topology.worldMapNoDirectNes"));
                return;
              }
              setKeyword("");
              setPaletteSelectedKeys([]);
              setAddNeOpen(true);
            }}
            onCreateNe={(flowX, flowY) => {
              if (isWorldFlatCanvas) {
                showError(t("topology.worldMapNoDirectNes"));
                return;
              }
              openCreateNeAt(flowX, flowY);
            }}
            nodes={nodes}
            rfRef={rfRef}
            onBackUp={goBackBrowse}
            canUndo={canUndo}
            onUndo={undo}
            canRedo={canRedo}
            onRedo={redo}
            savePending={saveMut.isPending}
            onSave={() => saveMut.mutate()}
            onFit={fitCanvas}
            exporting={exporting}
            onExport={runTopologyExport}
            mapId={mapId}
            staleEdgeCount={staleEdgeIds.length}
            onRemoveStale={removeStaleEdges}
            toolMode={toolMode}
            onToolModeChange={setToolMode}
            onConnectClickReset={() => {
              connectClickRef.current = null;
            }}
            fullscreen={fullscreen}
            viewToolsToolbarSlotRef={setViewToolsToolbarSlot}
            outsidePeerCount={outsidePeers.length}
            onOpenOutsidePeers={() => {
              setOutsidePeerQuery("");
              setOutsidePeerSelectedIds([]);
              setOutsidePeersOpen(true);
            }}
            goRoot={goRoot}
            goRegion={goRegion}
            primaryViewOfFolder={primaryViewOfFolder}
          />
        ) : null}

        {discoverOpen ? (
          <TopologyDiscoverPanel
            discovering={discovering}
            discoverProgress={discoverProgress}
            discoverPct={discoverPct}
            discoverReport={discoverReport}
            discoverError={discoverError}
            hasResults={discoverResults.length > 0 || Boolean(discoverReport)}
            discoverSummary={discoverSummary}
            onCancelDiscover={cancelDiscover}
            onClose={() => setDiscoverOpen(false)}
          />
        ) : null}

        {!canvasMode ? (
          <TopologyHexBrowser
            titleText={titleText}
            breadcrumbFolders={breadcrumbFolders}
            hexBrowseRegion={hexBrowseRegion}
            umeWorldHexModules={umeWorldHexModules}
            browseEntries={browseEntries}
            regions={regions}
            rootFolderId={rootFolderId}
            selectedFolderId={selectedFolderId}
            mapId={mapId}
            hotBrowseKey={hotBrowseKey}
            treeLoading={treeLoading}
            treeFailed={treeFailed}
            treeError={treeQuery.error}
            createRegionPending={createRegionMut.isPending}
            onTreeRetry={() => void treeQuery.refetch()}
            onHotBrowseKey={setHotBrowseKey}
            onClearHotBrowseKey={(key: string) => setHotBrowseKey((k: string) => (k === key ? "" : k))}
            goRoot={goRoot}
            goRegion={goRegion}
            goUmeWorldNav={goUmeWorldNav}
            goCanvas={goCanvas}
            promptNewRegion={promptNewRegion}
            promptNewSubRegion={promptNewSubRegion}
            onSelectFolder={setSelectedFolderId}
          />
        ) : (
          <div
            className={`topo-canvas${fullscreen ? " is-fullscreen" : ""}${
              toolMode === "pan" ? " is-pan-mode" : ""
            }${canvasBgLuminance(canvasBg) > 0.55 ? " is-canvas-light" : ""}`}
            ref={canvasRef}
            style={
              {
                "--topo-canvas-bg": canvasBg,
                "--topo-label-color": labelColors.name,
                "--topo-edge-label-color": labelColors.edgeLabel,
                "--topo-vendor-cisco": vendorColors.cisco,
                "--topo-vendor-huawei": vendorColors.huawei,
                "--topo-vendor-zte": vendorColors.zte,
                "--topo-vendor-juniper": vendorColors.juniper,
                "--topo-vendor-nokia": vendorColors.nokia,
                "--topo-vendor-ericsson": vendorColors.ericsson,
                "--topo-vendor-h3c": vendorColors.h3c,
                "--topo-vendor-ruijie": vendorColors.ruijie,
                "--topo-vendor-mikrotik": vendorColors.mikrotik,
                "--topo-vendor-gray": vendorColors.gray,
                ...canvasFloatChromeVars(canvasBg),
              } as CSSProperties
            }
            onDragOver={onCanvasDragOver}
            onDrop={onCanvasDrop}
          >
            {truncateBannerText ? (
              <div className="topo-truncate-banner" role="status">
                {truncateBannerText}
              </div>
            ) : null}
            {!canWriteTopology && mapId ? (
              <div className="topo-readonly-banner" role="status">
                {t("topology.readOnlyBanner")}
              </div>
            ) : null}
            <TopologyViewTools
              fullscreen={fullscreen}
              toolbarSlot={viewToolsToolbarSlot}
              displayMenuRef={displayMenuRef}
              findBoxRef={findBoxRef}
              hideIp={hideIp}
              onHideIpChange={setHideIp}
              hideVendor={hideVendor}
              onHideVendorChange={setHideVendor}
              showPlaceholderBadge={showPlaceholderBadge}
              onShowPlaceholderBadgeChange={setShowPlaceholderBadge}
              showConnectStatus={showConnectStatus}
              onShowConnectStatusChange={setShowConnectStatus}
              showAlarmOverlay={showAlarmOverlay}
              onShowAlarmOverlayChange={setShowAlarmOverlay}
              showLevelColors={showLevelColors}
              onShowLevelColorsChange={setShowLevelColors}
              hidePorts={hidePorts}
              onHidePortsChange={setHidePorts}
              expandPhysicalLinks={expandPhysicalLinks}
              onExpandPhysicalLinksChange={setExpandPhysicalLinks}
              scaleBundleWidth={scaleBundleWidth}
              onScaleBundleWidthChange={setScaleBundleWidth}
              edgeFlow={edgeFlow}
              onEdgeFlowChange={setEdgeFlow}
              snapToGrid={snapToGrid}
              onSnapToGridChange={setSnapToGrid}
              autoLayoutAfterDiscover={autoLayoutAfterDiscover}
              onAutoLayoutAfterDiscoverChange={setAutoLayoutAfterDiscover}
              discoverAutoAddUnmatched={discoverAutoAddUnmatched}
              onDiscoverAutoAddUnmatchedChange={setDiscoverAutoAddUnmatched}
              discoverProjectNeighbors={discoverProjectNeighbors}
              onDiscoverProjectNeighborsChange={setDiscoverProjectNeighbors}
              canvasBg={canvasBg}
              onCanvasBgChange={setCanvasBg}
              labelColors={labelColors}
              onLabelColorsChange={setLabelColors}
              vendorColors={vendorColors}
              onVendorColorsChange={setVendorColors}
              edgeDefaults={edgeDefaults}
              onUpdateEdgeDefault={updateEdgeDefault}
              onResetEdgeDefaults={resetEdgeDefaults}
              selectedNodeCount={selectedNodes.length}
              onAlign={applyAlign}
              mapId={mapId}
              nodeCount={nodes.length}
              canvasQuery={canvasQuery}
              onCanvasQueryChange={setCanvasQuery}
              findOpen={findOpen}
              onFindOpenChange={setFindOpen}
              findActiveIdx={findActiveIdx}
              onFindActiveIdxChange={setFindActiveIdx}
              canvasHits={canvasHits}
              onFindOnCanvas={findOnCanvas}
            />
            {fullscreen ? (
              <div className="topo-fs-toolbar" role="toolbar" aria-label={t("topology.toolModes")}>
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
                    className={`topo-fs-toolbar__btn${toolMode === mode ? " is-active" : ""}`}
                    title={`${label} (${key})`}
                    aria-pressed={toolMode === mode}
                    onClick={() => {
                      setToolMode(mode);
                      connectClickRef.current = null;
                    }}
                  >
                    {label}
                    <kbd>{key}</kbd>
                  </button>
                ))}
                <button
                  type="button"
                  className="topo-fs-toolbar__btn"
                  title={t("topology.fit")}
                  onClick={() => fitCanvas()}
                >
                  {t("topology.fit")}
                </button>
                <button
                  type="button"
                  className="topo-fs-toolbar__btn"
                  title={t("topology.exitFullscreen")}
                  onClick={() => void toggleFullscreen()}
                >
                  {t("topology.exitFullscreen")}
                </button>
              </div>
            ) : null}
            {treeRoot ? (
              <>
                {isWorldFlatCanvas ? (
                  <div className="topo-world-hud" role="status">
                    <span>
                      {t("topology.worldHud")
                        .replace("{{total}}", String(worldTotal || worldScatter.length || 0))
                        .replace("{{cached}}", String(nodes.length))
                        .replace("{{zoom}}", canvasZoom.toFixed(3))
                        .replace("{{lod}}", worldVisualLod)}
                    </span>
                    {graphTruncated ? (
                      <span className="muted"> · {truncateReason || "truncated"}</span>
                    ) : null}
                  </div>
                ) : null}
                <Suspense
                  fallback={
                    <div className="topo-canvas__empty topo-canvas__status" role="status">
                      <span className="topo-loading-spinner" aria-hidden="true" />
                      <p>{t("topology.treeLoading")}</p>
                    </div>
                  }
                >
                  <TopologyReactFlowView
                    displayOpts={displayOpts}
                    alarmByNodeId={alarmByNodeId}
                    nodes={nodes}
                    setNodes={setNodes}
                    edges={edges}
                    setEdges={setEdges}
                    displayEdges={displayEdges}
                    searchHitIds={searchHitIds}
                    isWorldFlatCanvas={isWorldFlatCanvas}
                    worldVisualLod={worldVisualLod}
                    toolBehavior={toolBehavior}
                    toolMode={toolMode}
                    snapToGrid={snapToGrid}
                    canvasBg={canvasBg}
                    worldScatter={worldScatter}
                    showWorldScatter={showWorldScatter}
                    fullscreen={fullscreen}
                    connectHint={t("topology.connectHint")}
                    fullscreenLabel={t("topology.fullscreen")}
                    exitFullscreenLabel={t("topology.exitFullscreen")}
                    worldTransform={worldTransformRef.current || graphQuery.data?.world_transform}
                    nodesRef={nodesRef}
                    rfRef={rfRef}
                    pendingFitRef={pendingFitRef}
                    markDirty={markDirty}
                    pushHistory={pushHistory}
                    popHistory={popHistory}
                    onConnect={onConnect}
                    isValidConnection={isValidConnection}
                    onNodeClick={onNodeClick}
                    onNodeDoubleClick={onNodeDoubleClick}
                    focusEdge={focusEdge}
                    focusNode={focusNode}
                    clearSelection={clearSelection}
                    setCtxMenu={setCtxMenu}
                    placeCtxMenu={placeCtxMenu}
                    selectedNodeIds={selectedNodeIds}
                    closeCtxMenu={closeCtxMenu}
                    setCanvasZoom={setCanvasZoom}
                    scheduleFlatViewportRefresh={scheduleFlatViewportRefresh}
                    toggleFullscreen={toggleFullscreen}
                  />
                </Suspense>
              </>
            ) : treeLoading ? (
              <div className="topo-canvas__empty topo-canvas__status" role="status">
                <span className="topo-loading-spinner" aria-hidden="true" />
                <p>{t("topology.treeLoading")}</p>
              </div>
            ) : treeFailed ? (
              <div className="topo-canvas__empty">
                <p className="muted">{t("topology.treeLoadFailed")}</p>
                <div className="topo-browser__empty-actions">
                  <Button
                    size="sm"
                    variant="ghost"
                    onPress={() => void treeQuery.refetch()}
                  >
                    {t("topology.treeRetry")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onPress={promptNewRegion}
                    isDisabled={createRegionMut.isPending}
                  >
                    {t("topology.newRegion")}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="topo-canvas__empty">
                <p className="muted">{t("topology.emptyMaps")}</p>
                <Button
                  size="sm"
                  variant="secondary"
                  onPress={promptNewRegion}
                  isDisabled={createRegionMut.isPending}
                >
                  {t("topology.newRegion")}
                </Button>
              </div>
            )}
            {treeRoot && canvasGraphError ? (
              <div className="topo-canvas__overlay topo-canvas__overlay--error" role="alert">
                <p className="topo-canvas__overlay-title">{t("topology.graphLoadFailed")}</p>
                {canvasGraphErrorMsg ? (
                  <p className="topo-canvas__overlay-hint muted">{canvasGraphErrorMsg}</p>
                ) : null}
                <div className="topo-browser__empty-actions">
                  <Button
                    size="sm"
                    variant="ghost"
                    onPress={() => void graphQuery.refetch()}
                  >
                    {t("topology.treeRetry")}
                  </Button>
                </div>
              </div>
            ) : null}
            {treeRoot && canvasGraphLoading ? (
              <div className="topo-canvas__overlay topo-canvas__overlay--loading" role="status">
                <span className="topo-loading-spinner topo-loading-spinner--lg" aria-hidden="true" />
                <p className="topo-canvas__overlay-title">{t("topology.graphLoading")}</p>
              </div>
            ) : null}
            {treeRoot && canvasGraphEmpty ? (
              <div
                className={`topo-canvas__overlay topo-canvas__overlay--empty${
                  worldNeedsApply ? " is-interactive" : ""
                }`}
                role="status"
              >
                <p className="topo-canvas__overlay-title">
                  {worldNeedsApply ? t("topology.worldEmptyCoords") : t("topology.canvasEmpty")}
                </p>
                <p className="topo-canvas__overlay-hint">
                  {worldNeedsApply
                    ? t("topology.worldEmptyCoordsHint").replace("{{dock}}", String(worldDockMe))
                    : t("topology.canvasEmptyHint")}
                </p>
                {worldNeedsApply ? (
                  <div className="btn-row" style={{ marginTop: 10, justifyContent: "center" }}>
                    <Button
                      size="sm"
                      variant="secondary"
                      isDisabled={applyWorldMut.isPending}
                      onPress={() => applyWorldMut.mutate()}
                    >
                      {applyWorldMut.isPending
                        ? t("topology.worldApplying")
                        : t("topology.worldApply")}
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : null}
            {treeRoot && canvasGraphRefreshing ? (
              <div className="topo-canvas__refresh" role="status" aria-live="polite">
                <span className="topo-loading-spinner" aria-hidden="true" />
                {t("topology.graphRefreshing")}
              </div>
            ) : null}
          </div>
        )}
      </main>

      <TopologyModals
        canvasMode={canvasMode}
        newRootDialog={newRootDialog}
        onNewRootNameChange={(name) => setNewRootDialog({ name })}
        onCloseNewRoot={() => setNewRootDialog(null)}
        onSubmitNewRoot={submitNewRoot}
        createRegionPending={createRegionMut.isPending}
        createNeModeOpen={createNeModeOpen}
        onCloseCreateNeMode={closeCreateNeMode}
        onPickCreateManaged={pickCreateManaged}
        onPickCreatePlaceholder={pickCreatePlaceholder}
        placeholderDialog={placeholderDialog}
        placeholderBusy={placeholderBusy}
        onPlaceholderChange={(patch: Partial<PlaceholderCreateDialogState>) =>
          setPlaceholderDialog((prev: PlaceholderCreateDialogState | null) =>
            prev ? { ...prev, ...patch } : prev,
          )
        }
        onClosePlaceholder={closePlaceholder}
        onSubmitPlaceholder={submitPlaceholder}
        managedFormOpen={managedFormOpen}
        managedFormInitial={managedFormInitial}
        onCloseManagedForm={closeManagedForm}
        onManagedFormSaved={(item) => void onManagedFormSaved(item)}
        connectDetailRow={connectDetailRow}
        onCloseConnectDetail={closeConnectDetail}
        onConnectRetestSubmitted={onConnectRetestSubmitted}
        outsidePeersOpen={outsidePeersOpen}
        outsidePeers={outsidePeers}
        outsidePeersVisible={outsidePeersVisible}
        outsidePeerQuery={outsidePeerQuery}
        onOutsidePeerQueryChange={setOutsidePeerQuery}
        outsidePeerSelectedIds={outsidePeerSelectedIds}
        onOutsidePeerSelectedIdsChange={setOutsidePeerSelectedIds}
        outsidePeerNameById={outsidePeerNameById}
        outsidePeersAdding={outsidePeersAdding}
        onCloseOutsidePeers={() => setOutsidePeersOpen(false)}
        onAddOutsidePeers={addOutsidePeers}
        addNeOpen={addNeOpen}
        paletteSource={paletteSource}
        onPaletteSourceChange={(source) => {
          setPaletteSource(source);
          setPaletteSelectedKeys([]);
        }}
        keyword={keyword}
        onKeywordChange={(value) => {
          setKeyword(value);
          setPaletteSelectedKeys([]);
        }}
        paletteVisible={paletteVisible}
        paletteSelectedKeys={paletteSelectedKeys}
        onPaletteSelectedKeysChange={setPaletteSelectedKeys}
        paletteLoading={paletteLoading}
        paletteAdding={paletteAdding}
        onCloseAddNe={() => setAddNeOpen(false)}
        onPaletteDragStart={onPaletteDragStart}
        onAddSelectedPalette={async () => {
          const selected = (paletteVisible as PaletteItem[]).filter((item: PaletteItem) =>
            paletteSelectedKeys.includes(item.key),
          );
          if (selected.length === 0) return;
          const ok = await addPaletteItems(selected);
          if (!ok) return;
          setPaletteSelectedKeys([]);
          setAddNeOpen(false);
        }}
      />

      {ctxMenu ? (
        <TopologyCtxMenu
          ctxMenu={ctxMenu}
          menuRef={ctxMenuRef}
          portalTarget={canvasRef.current}
          fullscreen={fullscreen}
          selectedNodeCount={selectedNodes.length}
          selectedNode={selectedNode}
          selectedEdge={selectedEdge}
          selectedEdgeData={selectedEdgeData}
          selectedEdgeResolved={selectedEdgeResolved}
          selectedEdgeSourceNode={selectedEdgeSourceNode || null}
          selectedEdgeTargetNode={selectedEdgeTargetNode || null}
          expandPhysicalLinks={expandPhysicalLinks}
          discovering={discovering}
          isWorldFlatCanvas={isWorldFlatCanvas}
          createRegionPending={createRegionMut.isPending}
          onClose={closeCtxMenu}
          onToggleFullscreen={toggleFullscreen}
          onRemoveSelected={removeSelected}
          onDiscoverSelected={discoverSelectedFor}
          onConnectTestSelected={connectTestSelectedFor}
          selectedDiscoverableCount={selectedDiscoverableCount}
          selectedConnectableCount={selectedConnectableCount}
          onOpenCreateNe={openCreateNeAt}
          onPromptNewSubRegion={promptNewSubRegion}
          onRenameSelectedNode={renameSelectedNode}
          onDiscoverOne={discoverOneFor}
          onOpenWebcrt={openWebcrtFor}
          onOpenNe={openNeFor}
          onOpenAlarms={openAlarmsFor}
          onConnectTest={runConnectTestForNode}
          connectTestBusy={connectTestSubmitting}
          onPurgePlaceholder={purgePlaceholderById}
          onRemoveNode={removeNodeById}
          onExpandPhysicalLinks={() => setExpandPhysicalLinks(true)}
          onPushHistory={pushHistory}
          onPatchSelectedEdgeStyle={patchSelectedEdgeStyle}
          onOpenPortTraffic={openPortTrafficForEdge}
          onRemoveEdge={removeEdgeById}
        />
      ) : null}
    </div>
  );
}
