import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, type QueryClient } from "@tanstack/react-query";
import type { Edge, Node } from "@xyflow/react";
import type { SetURLSearchParams } from "react-router-dom";
import { queryKeys } from "../../../constants/queryKeys";
import {
  createTopologyFolder,
  deleteTopologyFolder,
  deleteTopologyMap,
  fetchTopologyTree,
  fetchTopologyWorld,
  searchFabricNodes,
  updateTopologyFolder,
  updateTopologyMap,
} from "../../../services/api";
import type { TopologyTreeFolderItem, TopologyTreeViewItem } from "../../../types";
import { LAST_LEAF_KEY, TREE_EXPAND_KEY, WORLD_MAP_ENABLED } from "../constants";
import type { CtxMenu } from "../pageTypes";
import {
  findFolderInTree,
  findViewInRegion,
  folderPathFolders,
  folderPathIds,
  isRegionCanvasFolder,
  isUmeWorldContainer,
  isWorldDrillFolder,
  isWorldFlatViewName,
  regionDisplayName,
} from "../treeUtils";
import type { NeNodeData } from "../TopologyReactFlowView";

export type UseTopologyTreeNavOptions = {
  liveSync: boolean;
  searchParams: URLSearchParams;
  setSearchParams: SetURLSearchParams;
  locale: string;
  queryClient: QueryClient;
  confirmDiscardIfDirty: () => boolean;
  clearDirty: () => void;
  showOk: (msg: string) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
  setNodes: React.Dispatch<React.SetStateAction<Node<NeNodeData>[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  setCtxMenu: React.Dispatch<React.SetStateAction<CtxMenu | null>>;
  activeViewName?: string;
};

export function useTopologyTreeNav(opts: UseTopologyTreeNavOptions) {
  const {
    liveSync,
    searchParams,
    setSearchParams,
    locale,
    queryClient,
    confirmDiscardIfDirty,
    clearDirty,
    showOk,
    showError,
    t,
    setNodes,
    setEdges,
    setCtxMenu,
    activeViewName,
  } = opts;

  const [mapId, setMapId] = useState("");
  const [worldFocusFolderId, setWorldFocusFolderId] = useState("");
  const [worldViewId, setWorldViewId] = useState("");
  const [selectedFolderId, setSelectedFolderId] = useState("");
  const [hotBrowseKey, setHotBrowseKey] = useState("");
  const [pendingHighlightNe, setPendingHighlightNe] = useState("");
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>(() => {
    try {
      const raw = localStorage.getItem(TREE_EXPAND_KEY);
      return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
    } catch {
      return {};
    }
  });
  const [treeNeQuery, setTreeNeQuery] = useState("");
  const [debouncedTreeNeQuery, setDebouncedTreeNeQuery] = useState("");
  const [treeSearchOpen, setTreeSearchOpen] = useState(false);
  const treeSearchRef = useRef<HTMLDivElement | null>(null);
  const [newRootDialog, setNewRootDialog] = useState<{ name: string } | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedTreeNeQuery(treeNeQuery.trim()), 200);
    return () => window.clearTimeout(timer);
  }, [treeNeQuery]);

  const treeQuery = useQuery({
    queryKey: queryKeys.topologyTree,
    queryFn: fetchTopologyTree,
    staleTime: liveSync ? 0 : 30_000,
    refetchOnWindowFocus: liveSync,
    refetchInterval: liveSync ? 5000 : false,
    refetchIntervalInBackground: false,
  });

  const treeFlatMap = useMemo(() => {
    if (!mapId) return false;
    const kids = treeQuery.data?.root?.children || [];
    return isWorldFlatViewName(findViewInRegion(kids, mapId)?.view?.name);
  }, [mapId, treeQuery.data?.root?.children]);

  const treeNeSearchQuery = useQuery({
    queryKey: queryKeys.fabricNodeSearch(debouncedTreeNeQuery, 1),
    queryFn: () => searchFabricNodes({ q: debouncedTreeNeQuery, page: 1, pageSize: 30 }),
    enabled: debouncedTreeNeQuery.length >= 1,
  });

  const treeRoot = treeQuery.data?.root || null;
  const regions = useMemo(() => treeRoot?.children || [], [treeRoot]);
  const treeLoading = treeQuery.isPending && !treeQuery.data;
  const treeFailed = treeQuery.isError && !treeQuery.data;
  const rootFolderId = String(treeRoot?.id || "").trim();
  const canvasModeLocal = Boolean(mapId);

  useEffect(() => {
    if (!treeRoot || !regions.length) return;
    if (mapId) {
      const hit = findViewInRegion(regions, mapId);
      if (hit && selectedFolderId !== hit.region.id) {
        setSelectedFolderId(hit.region.id);
        setExpandedIds((prev) => ({ ...prev, [hit.region.id]: true }));
      }
      return;
    }
    if (selectedFolderId && !regions.some((r) => r.id === selectedFolderId)) {
      setSelectedFolderId("");
    }
  }, [mapId, selectedFolderId, treeRoot, regions]);

  const activeRegion = useMemo(() => {
    if (!selectedFolderId) return null;
    return findFolderInTree(regions, selectedFolderId);
  }, [regions, selectedFolderId]);

  const activeView = useMemo(() => {
    if (!mapId) return null;
    return findViewInRegion(regions, mapId)?.view || null;
  }, [mapId, regions]);

  const browseEntries = useMemo((): TopologyTreeViewItem[] => {
    if (
      !activeRegion ||
      isRegionCanvasFolder(activeRegion, rootFolderId) ||
      isUmeWorldContainer(activeRegion)
    ) {
      return [];
    }
    return activeRegion.views || [];
  }, [activeRegion, rootFolderId]);

  const hexBrowseRegion = useMemo(() => {
    if (!activeRegion) return null;
    if (isUmeWorldContainer(activeRegion)) return activeRegion;
    if (isRegionCanvasFolder(activeRegion, rootFolderId)) return null;
    return activeRegion;
  }, [activeRegion, rootFolderId]);

  const umeWorldHexModules = useMemo(() => {
    if (!activeRegion || !isUmeWorldContainer(activeRegion)) return null;
    const drill = (activeRegion.children || []).find((c) => isWorldDrillFolder(c)) || null;
    const flatView = WORLD_MAP_ENABLED
      ? (activeRegion.views || []).find((v) => isWorldFlatViewName(v.name)) || null
      : null;
    return { drill, flatView };
  }, [activeRegion]);

  useEffect(() => {
    if (!WORLD_MAP_ENABLED) {
      setWorldViewId("");
      return;
    }
    let cancelled = false;
    fetchTopologyWorld()
      .then((w) => {
        if (cancelled) return;
        setWorldViewId(w.view_id);
      })
      .catch(() => {
        /* world not seeded yet */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(TREE_EXPAND_KEY, JSON.stringify(expandedIds));
    } catch {
      /* ignore */
    }
  }, [expandedIds]);

  useEffect(() => {
    try {
      if (mapId) localStorage.setItem(LAST_LEAF_KEY, mapId);
      else localStorage.removeItem(LAST_LEAF_KEY);
    } catch {
      /* ignore */
    }
  }, [mapId]);

  useEffect(() => {
    if (WORLD_MAP_ENABLED || !mapId || !treeFlatMap) return;
    const container = regions.find(
      (r) => r.external_ref === "ume:world" || r.name === "UME World",
    );
    setWorldFocusFolderId("");
    if (container) {
      setSelectedFolderId(container.id);
      setExpandedIds((p) => ({ ...p, [container.id]: true }));
    }
    setMapId("");
    clearDirty();
  }, [mapId, treeFlatMap, regions, clearDirty]);

  useEffect(() => {
    const viewId = String(searchParams.get("view") || "").trim();
    const neId = String(searchParams.get("ne") || "").trim();
    if (!viewId || !regions.length) return;
    const hit = findViewInRegion(regions, viewId);
    if (!hit) return;
    setSelectedFolderId(hit.region.id);
    setExpandedIds((p) => ({ ...p, [hit.region.id]: true }));
    setMapId(viewId);
    if (neId) setPendingHighlightNe(neId);
    setSearchParams({}, { replace: true });
  }, [regions, searchParams, setSearchParams]);

  useEffect(() => {
    if (!mapId && !selectedFolderId) return;
    const root = document.querySelector(".topo-region-list");
    if (!root) return;
    const hit =
      root.querySelector("li.is-active") ||
      root.querySelector(".topo-region-list__block.is-branch-active");
    if (hit && "scrollIntoView" in hit) {
      (hit as HTMLElement).scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [mapId, selectedFolderId, expandedIds]);

  useEffect(() => {
    if (!treeSearchOpen) return;
    const onDoc = (e: MouseEvent) => {
      const el = treeSearchRef.current;
      if (el && e.target instanceof Element && !el.contains(e.target)) {
        setTreeSearchOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [treeSearchOpen]);

  const expandFolderPath = useCallback(
    (folderId: string) => {
      const path = folderPathIds(regions, folderId);
      if (!path.length) return;
      setExpandedIds((p) => {
        const next = { ...p };
        for (const id of path) next[id] = true;
        return next;
      });
    },
    [regions],
  );

  const primaryViewOfFolder = useCallback((folder: TopologyTreeFolderItem | null | undefined) => {
    if (!folder) return null;
    const views = folder.views || [];
    if (isWorldDrillFolder(folder)) {
      return views.find((v) => v.name === "World") || views[0] || null;
    }
    if (folder.external_ref === "ume:world" || folder.name === "UME World") {
      const drill = (folder.children || []).find((c) => isWorldDrillFolder(c));
      if (drill) {
        const dv = drill.views || [];
        return dv.find((v) => v.name === "World") || dv[0] || null;
      }
      return views.find((v) => !isWorldFlatViewName(v.name)) || null;
    }
    const physical = views.find((v) => String(v.kind) === "physical") || null;
    return physical || views[0] || null;
  }, []);

  const goUmeWorldNav = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    const container = regions.find(
      (r) => r.external_ref === "ume:world" || r.name === "UME World",
    );
    if (!container) return;
    setWorldFocusFolderId("");
    setSelectedFolderId(container.id);
    setExpandedIds((p) => ({ ...p, [container.id]: true }));
    setMapId("");
    clearDirty();
  }, [confirmDiscardIfDirty, clearDirty, regions]);

  const goRegion = useCallback(
    (folderId: string) => {
      if (!confirmDiscardIfDirty()) return;
      setSelectedFolderId(folderId);
      expandFolderPath(folderId);
      const folder = findFolderInTree(regions, folderId);
      if (isRegionCanvasFolder(folder, String(treeRoot?.id || ""))) {
        setWorldFocusFolderId("");
        const view = primaryViewOfFolder(folder);
        if (view) {
          setMapId(view.id);
          clearDirty();
          return;
        }
      }
      setMapId("");
      setWorldFocusFolderId("");
      clearDirty();
      setNodes([]);
      setEdges([]);
    },
    [
      confirmDiscardIfDirty,
      clearDirty,
      setNodes,
      setEdges,
      regions,
      primaryViewOfFolder,
      expandFolderPath,
      treeRoot?.id,
    ],
  );

  const goRoot = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    setSelectedFolderId("");
    setWorldFocusFolderId("");
    setMapId("");
    clearDirty();
    setNodes([]);
    setEdges([]);
  }, [confirmDiscardIfDirty, clearDirty, setNodes, setEdges]);

  const goCanvas = useCallback(
    (viewId: string, folderId?: string) => {
      if (!confirmDiscardIfDirty()) return;
      const hit = findViewInRegion(regions, viewId);
      if (!WORLD_MAP_ENABLED && isWorldFlatViewName(hit?.view?.name)) {
        showError(t("topology.worldMapOffline"));
        return;
      }
      const regionId = folderId || hit?.region.id || selectedFolderId;
      if (regionId) {
        setSelectedFolderId(regionId);
        expandFolderPath(regionId);
      }
      setWorldFocusFolderId("");
      setMapId(viewId);
    },
    [confirmDiscardIfDirty, regions, selectedFolderId, expandFolderPath, showError, t],
  );

  const goBackBrowse = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    const folder = findFolderInTree(regions, selectedFolderId);
    const parentId = String(folder?.parent_id || "").trim();
    const parent = parentId ? findFolderInTree(regions, parentId) : null;
    if (parent) {
      setSelectedFolderId(parent.id);
      expandFolderPath(parent.id);
      if (isRegionCanvasFolder(parent, String(treeRoot?.id || ""))) {
        setWorldFocusFolderId("");
        const view = primaryViewOfFolder(parent);
        if (view) {
          setMapId(view.id);
          clearDirty();
          return;
        }
      }
      setWorldFocusFolderId("");
      setMapId("");
      clearDirty();
      setNodes([]);
      setEdges([]);
      return;
    }
    setSelectedFolderId("");
    setWorldFocusFolderId("");
    setMapId("");
    clearDirty();
    setNodes([]);
    setEdges([]);
  }, [
    confirmDiscardIfDirty,
    clearDirty,
    setNodes,
    setEdges,
    regions,
    selectedFolderId,
    treeRoot?.id,
    expandFolderPath,
    primaryViewOfFolder,
  ]);

  const renameMapMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updateTopologyMap(id, { name }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      showOk(t("topology.renamed"));
    },
    onError: (err) => showError(String(err)),
  });

  const promptRenameMap = useCallback(
    (id: string, currentName: string) => {
      const next = window.prompt(t("topology.renamePrompt"), currentName);
      if (next == null) return;
      const name = next.trim();
      if (!name || name === currentName) return;
      renameMapMut.mutate({ id, name });
    },
    [renameMapMut, t],
  );

  const createRegionMut = useMutation({
    mutationFn: (input: { name: string; parent_id?: string }) => {
      const parentId = String(input.parent_id || "").trim();
      return createTopologyFolder(
        parentId
          ? { name: input.name, kind: "region", parent_id: parentId, locale }
          : { name: input.name, kind: "region", locale },
      );
    },
    onSuccess: async (folder, input) => {
      setNewRootDialog(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      const tree = await fetchTopologyTree();
      const regionsList = tree.root?.children || [];
      const hit = findFolderInTree(regionsList, folder.id);
      const parent = String(input.parent_id || folder.parent_id || hit?.parent_id || "").trim();
      const rootId = String(tree.root?.id || "").trim();
      const isNested = Boolean(parent && parent !== rootId);
      setWorldFocusFolderId("");
      setExpandedIds((prev) => {
        const next = { ...prev, [folder.id]: true };
        if (parent) next[parent] = true;
        return next;
      });
      if (isNested) {
        setSelectedFolderId(parent);
        expandFolderPath(parent);
        const parentHit = findFolderInTree(regionsList, parent);
        const parentView =
          primaryViewOfFolder(parentHit) ||
          (parentHit?.views || []).find((v) => String(v.kind) === "physical") ||
          (parentHit?.views || [])[0];
        const stayViewId = mapId || parentView?.id || "";
        if (stayViewId) {
          setMapId(stayViewId);
          await queryClient.invalidateQueries({
            queryKey: queryKeys.topologyGraph(stayViewId),
          });
        }
      } else {
        setSelectedFolderId(folder.id);
        setMapId("");
      }
      showOk(t("topology.regionCreated"));
    },
    onError: (err) => showError(String(err)),
  });

  const renameRegionMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updateTopologyFolder(id, { name }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      showOk(t("topology.regionRenamed"));
    },
    onError: (err) => showError(String(err)),
  });

  const promptRenameRegion = useCallback(
    (id: string, currentName: string) => {
      const next = window.prompt(t("topology.renameRegionPrompt"), currentName);
      if (next == null) return;
      const name = next.trim();
      if (!name || name === currentName) return;
      renameRegionMut.mutate({ id, name });
    },
    [renameRegionMut, t],
  );

  const deleteMapMut = useMutation({
    mutationFn: (id: string) => deleteTopologyMap(id),
    onSuccess: async (_out, id) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
      if (mapId === id) {
        clearDirty();
        setMapId("");
        setNodes([]);
        setEdges([]);
      }
    },
    onError: (err) => showError(String(err)),
  });

  const deleteFolderMut = useMutation({
    mutationFn: (id: string) => deleteTopologyFolder(id, false),
    onSuccess: async (_out, id) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
      if (selectedFolderId === id) {
        setSelectedFolderId("");
        setMapId("");
      }
      showOk(t("topology.regionDeleted"));
    },
    onError: (err) => showError(String(err)),
  });

  const promptNewRegion = useCallback(() => {
    setNewRootDialog({ name: t("topology.newRegionName") });
  }, [t]);

  const submitNewRoot = useCallback(() => {
    const name = String(newRootDialog?.name || "").trim();
    if (!name) {
      showError(t("topology.newRegionPrompt"));
      return;
    }
    createRegionMut.mutate({ name });
  }, [newRootDialog, createRegionMut, showError, t]);

  const promptNewSubRegion = useCallback(() => {
    const viewName = activeView?.name || activeViewName;
    if (isWorldFlatViewName(viewName)) {
      showError(t("topology.subRegionNotOnFlat"));
      return;
    }
    let parentId = selectedFolderId;
    if (!parentId && worldViewId && mapId === worldViewId) {
      const container = regions.find(
        (r) => r.external_ref === "ume:world" || r.name === "UME World",
      );
      const drill = (container?.children || []).find((c) => isWorldDrillFolder(c));
      parentId = drill?.id || container?.id || "";
    }
    if (!parentId) {
      showError(t("topology.subRegionNeedParent"));
      return;
    }
    const name = window.prompt(t("topology.newSubRegionPrompt"), t("topology.newSubRegionName"));
    if (!name?.trim()) return;
    createRegionMut.mutate({ name: name.trim(), parent_id: parentId });
    setCtxMenu(null);
  }, [
    activeView?.name,
    activeViewName,
    selectedFolderId,
    worldViewId,
    mapId,
    regions,
    createRegionMut,
    t,
    showError,
    setCtxMenu,
  ]);

  const onTreeToggleExpand = useCallback((folderId: string, nextOpen: boolean) => {
    setExpandedIds((p) => ({ ...p, [folderId]: nextOpen }));
  }, []);

  const onTreeHotBrowseKey = useCallback((key: string) => {
    setHotBrowseKey(key);
  }, []);

  const onTreeClearHotBrowseKey = useCallback((key: string) => {
    setHotBrowseKey((k) => (k === key ? "" : k));
  }, []);

  const onTreeDeleteFolder = useCallback(
    (folderId: string) => {
      deleteFolderMut.mutate(folderId);
    },
    [deleteFolderMut],
  );

  const onTreeDeleteMap = useCallback(
    (viewId: string) => {
      deleteMapMut.mutate(viewId);
    },
    [deleteMapMut],
  );

  const breadcrumbFolders = useMemo(
    () => folderPathFolders(regions, selectedFolderId),
    [regions, selectedFolderId],
  );

  const titleText = useMemo(() => {
    if (activeRegion) return regionDisplayName(activeRegion, t);
    return t("topology.rootName");
  }, [activeRegion, t]);

  return {
    mapId,
    setMapId,
    worldFocusFolderId,
    setWorldFocusFolderId,
    worldViewId,
    selectedFolderId,
    setSelectedFolderId,
    hotBrowseKey,
    setHotBrowseKey,
    expandedIds,
    setExpandedIds,
    treeNeQuery,
    setTreeNeQuery,
    debouncedTreeNeQuery,
    setDebouncedTreeNeQuery,
    treeSearchOpen,
    setTreeSearchOpen,
    treeSearchRef,
    newRootDialog,
    setNewRootDialog,
    treeQuery,
    treeFlatMap,
    treeNeSearchQuery,
    treeRoot,
    regions,
    treeLoading,
    treeFailed,
    rootFolderId,
    canvasMode: canvasModeLocal,
    activeRegion,
    activeView,
    browseEntries,
    hexBrowseRegion,
    umeWorldHexModules,
    expandFolderPath,
    primaryViewOfFolder,
    goUmeWorldNav,
    goRegion,
    goRoot,
    goCanvas,
    goBackBrowse,
    renameMapMut,
    promptRenameMap,
    createRegionMut,
    renameRegionMut,
    promptRenameRegion,
    deleteMapMut,
    deleteFolderMut,
    promptNewRegion,
    submitNewRoot,
    promptNewSubRegion,
    onTreeToggleExpand,
    onTreeHotBrowseKey,
    onTreeClearHotBrowseKey,
    onTreeDeleteFolder,
    onTreeDeleteMap,
    breadcrumbFolders,
    titleText,
    pendingHighlightNe,
    setPendingHighlightNe,
  };
}
