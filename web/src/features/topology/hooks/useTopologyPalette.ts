import { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Edge, Node, ReactFlowInstance } from "@xyflow/react";
import { queryKeys } from "../../../constants/queryKeys";
import {
  addTopologyViewNodes,
  fetchManagedNe,
  fetchTopologyGraph,
  fetchUmeNe,
  patchTopologyPositions,
} from "../../../services/api";
import type { TopologyOutsidePeer } from "../../../types";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import { PALETTE_DND, SEP } from "../lib/constants";
import type { PaletteItem, PaletteSource } from "../lib/pageTypes";
import { fuzzyIncludes } from "../lib/searchUtils";
import { isWorldFlatViewName } from "../lib/treeUtils";

type Args = {
  canWriteTopology: boolean;
  mapId: string;
  activeViewName?: string | null;
  nodes: Node<NeNodeData>[];
  keyword: string;
  debouncedKeyword: string;
  addNeOpen: boolean;
  paletteSource: PaletteSource;
  outsidePeers: TopologyOutsidePeer[];
  outsidePeerQuery: string;
  dirtyRef: React.MutableRefObject<boolean>;
  rfRef: React.MutableRefObject<ReactFlowInstance<Node<NeNodeData>, Edge> | null>;
  commitCanvas: () => Promise<unknown>;
  resetLocalFromServer: (graph: Awaited<ReturnType<typeof fetchTopologyGraph>>) => void;
  showOk: (msg: string) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
  setOutsidePeersOpen: React.Dispatch<React.SetStateAction<boolean>>;
  setOutsidePeerSelectedIds: React.Dispatch<React.SetStateAction<string[]>>;
};

export function useTopologyPalette({
  canWriteTopology,
  mapId,
  activeViewName,
  nodes,
  keyword,
  debouncedKeyword,
  addNeOpen,
  paletteSource,
  outsidePeers,
  outsidePeerQuery,
  dirtyRef,
  rfRef,
  commitCanvas,
  resetLocalFromServer,
  showOk,
  showError,
  t,
  setOutsidePeersOpen,
  setOutsidePeerSelectedIds,
}: Args) {
  const queryClient = useQueryClient();
  const [paletteAdding, setPaletteAdding] = useState(false);
  const [outsidePeersAdding, setOutsidePeersAdding] = useState(false);

  const neQuery = useQuery({
    queryKey: [...queryKeys.managedNeAll, "topology-add-ne", debouncedKeyword],
    queryFn: () =>
      fetchManagedNe({
        keyword: debouncedKeyword,
        vendor: "",
        connectStatus: "",
        page: 1,
        pageSize: 100,
      }),
    enabled: addNeOpen && paletteSource === "managed",
  });

  const umeQuery = useQuery({
    queryKey: ["umeInventoryNe", "topology-add-ne", debouncedKeyword],
    queryFn: () =>
      fetchUmeNe({
        keyword: debouncedKeyword,
        page: 1,
        pageSize: 100,
      }),
    enabled: addNeOpen && paletteSource === "ume",
  });

  const addPaletteItems = useCallback(
    async (items: PaletteItem[], origin?: { x: number; y: number }): Promise<boolean> => {
      if (!canWriteTopology) return false;
      if (!mapId) {
        showError(t("topology.selectMap"));
        return false;
      }
      if (isWorldFlatViewName(activeViewName)) {
        showError(t("topology.worldMapNoDirectNes"));
        return false;
      }
      if (items.length === 0 || paletteAdding) return false;

      const onCanvasManaged = new Set(nodes.map((n) => n.data.managed_ne_id).filter(Boolean));
      const onCanvasUme = new Set(nodes.map((n) => n.data.ume_ne_id).filter(Boolean));
      const managedIds = [
        ...new Set(
          items
            .filter((item) => item.source === "managed")
            .map((item) => item.managed_ne_id)
            .filter((id) => id && !onCanvasManaged.has(id)),
        ),
      ];
      const umeIds = [
        ...new Set(
          items
            .filter((item) => item.source === "ume")
            .map((item) => item.ume_ne_id)
            .filter((id) => id && !onCanvasUme.has(id)),
        ),
      ];
      if (managedIds.length === 0 && umeIds.length === 0) return false;

      if (dirtyRef.current) {
        if (!window.confirm(t("topology.unsavedSaveBeforeAdd"))) return false;
        try {
          await commitCanvas();
        } catch (err) {
          showError(String(err));
          return false;
        }
      }

      setPaletteAdding(true);
      try {
        const graph = await addTopologyViewNodes(mapId, {
          managed_ne_ids: managedIds,
          ume_ne_ids: umeIds,
          layout: "grid",
        });
        const managedSet = new Set(managedIds);
        const umeSet = new Set(umeIds);
        const added = graph.nodes.filter(
          (n) =>
            (n.managed_ne_id && managedSet.has(n.managed_ne_id)) ||
            (n.ume_ne_id && umeSet.has(n.ume_ne_id)),
        );
        if (added.length > 0) {
          const start = origin || { x: 80 + nodes.length * 24, y: 80 + nodes.length * 24 };
          const cols = Math.max(1, Math.ceil(Math.sqrt(added.length)));
          await patchTopologyPositions(
            mapId,
            added.map((n, i) => ({
              fabric_node_id: n.fabric_node_id,
              x: start.x + (i % cols) * 180,
              y: start.y + Math.floor(i / cols) * 120,
            })),
          );
        }
        const refreshed = await fetchTopologyGraph(mapId);
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), refreshed);
        resetLocalFromServer(refreshed);
        const count = added.length || managedIds.length + umeIds.length;
        showOk(t("topology.addSelectedDone").replace("{{count}}", String(count)));
        return count > 0;
      } catch (err) {
        showError(String(err));
        return false;
      } finally {
        setPaletteAdding(false);
      }
    },
    [
      mapId,
      nodes,
      paletteAdding,
      showError,
      showOk,
      t,
      queryClient,
      resetLocalFromServer,
      commitCanvas,
      dirtyRef,
      activeViewName,
      canWriteTopology,
    ],
  );

  const addNodeAt = useCallback(
    async (item: PaletteItem, position: { x: number; y: number }) => {
      await addPaletteItems([item], position);
    },
    [addPaletteItems],
  );

  const onPaletteDragStart = (e: React.DragEvent, item: PaletteItem) => {
    e.dataTransfer.setData(PALETTE_DND, JSON.stringify(item));
    e.dataTransfer.effectAllowed = "copy";
  };

  const onCanvasDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes(PALETTE_DND)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };

  const onCanvasDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (!canWriteTopology) return;
    const raw = e.dataTransfer.getData(PALETTE_DND);
    if (!raw || !rfRef.current) return;
    try {
      const item = JSON.parse(raw) as PaletteItem;
      const pos = rfRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      void addNodeAt(item, pos);
    } catch {
      /* ignore */
    }
  };

  const addOutsidePeers = useCallback(
    async (fabricNodeIds: string[]): Promise<boolean> => {
      if (!mapId) return false;
      const ids = [...new Set(fabricNodeIds.map((x) => String(x || "").trim()).filter(Boolean))];
      if (!ids.length || outsidePeersAdding) return false;

      if (dirtyRef.current) {
        if (!window.confirm(t("topology.unsavedSaveBeforeAdd"))) return false;
        try {
          await commitCanvas();
        } catch (err) {
          showError(String(err));
          return false;
        }
      }

      setOutsidePeersAdding(true);
      try {
        const onCanvas = new Set(nodes.map((n) => n.id));
        const toAdd = ids.filter((id) => !onCanvas.has(id));
        if (!toAdd.length) {
          setOutsidePeersOpen(false);
          setOutsidePeerSelectedIds([]);
          return false;
        }
        const graph = await addTopologyViewNodes(mapId, {
          fabric_node_ids: toAdd,
          layout: "grid",
        });
        if (graph.truncate_reason === "membership_frozen") {
          showError(t("topology.truncatedFrozen"));
          return false;
        }
        const addedSet = new Set(toAdd);
        const added = (graph.nodes || []).filter((n) => addedSet.has(n.fabric_node_id));
        if (added.length > 0) {
          const viaAnchors = new Map(
            outsidePeers
              .filter((p) => addedSet.has(p.fabric_node_id))
              .map((p) => [p.fabric_node_id, p.via_node_id] as const),
          );
          const cols = Math.max(1, Math.ceil(Math.sqrt(added.length)));
          await patchTopologyPositions(
            mapId,
            added.map((n, i) => {
              const viaId = viaAnchors.get(n.fabric_node_id);
              const viaNode = viaId ? nodes.find((x) => x.id === viaId) : undefined;
              const base = viaNode
                ? { x: viaNode.position.x + 200, y: viaNode.position.y }
                : { x: 80 + nodes.length * 24, y: 80 + nodes.length * 24 };
              return {
                fabric_node_id: n.fabric_node_id,
                x: base.x + (i % cols) * 180,
                y: base.y + Math.floor(i / cols) * 120,
              };
            }),
          );
        }
        const refreshed = await fetchTopologyGraph(mapId);
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), refreshed);
        resetLocalFromServer(refreshed);
        const count = added.length || toAdd.length;
        showOk(t("topology.projectedNeighbors").replace("{{count}}", String(count)));
        setOutsidePeerSelectedIds([]);
        setOutsidePeersOpen(false);
        return count > 0;
      } catch (err) {
        showError(String(err));
        return false;
      } finally {
        setOutsidePeersAdding(false);
      }
    },
    [
      mapId,
      nodes,
      outsidePeers,
      outsidePeersAdding,
      resetLocalFromServer,
      commitCanvas,
      dirtyRef,
      queryClient,
      showOk,
      showError,
      t,
      setOutsidePeersOpen,
      setOutsidePeerSelectedIds,
    ],
  );

  const outsidePeerNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) {
      const label = String(n.data?.label || n.data?.subtitle || "").trim();
      m.set(n.id, label || n.id.slice(0, 8));
    }
    return m;
  }, [nodes]);

  const outsidePeersVisible = useMemo(() => {
    const q = outsidePeerQuery.trim().toLowerCase();
    if (!q) return outsidePeers;
    return outsidePeers.filter((p) => {
      const viaName = outsidePeerNameById.get(p.via_node_id) || p.via_node_id;
      const bits = [p.name, p.ip, viaName, p.fabric_node_id];
      return bits.some((b) => String(b || "").toLowerCase().includes(q));
    });
  }, [outsidePeers, outsidePeerQuery, outsidePeerNameById]);

  const onCanvasManagedIds = useMemo(
    () => new Set(nodes.map((n) => n.data.managed_ne_id).filter(Boolean)),
    [nodes],
  );
  const onCanvasUmeIds = useMemo(
    () => new Set(nodes.map((n) => n.data.ume_ne_id).filter(Boolean)),
    [nodes],
  );

  const palette = useMemo((): PaletteItem[] => {
    if (paletteSource === "ume") {
      return (umeQuery.data?.items || []).map((ne) => {
        const name = (ne.host_name || ne.ne_name || ne.user_label || ne.ip_address || ne.ne_id).trim();
        return {
          key: `ume:${ne.ne_id}`,
          source: "ume" as const,
          managed_ne_id: "",
          ume_ne_id: ne.ne_id,
          name,
          ip: ne.ip_address || "",
          vendor: "ZTE",
          meta: `${ne.ip_address || "-"}${SEP}${ne.ne_type || "UME"}`,
          connect_status: ne.connection_status || "",
        };
      });
    }
    return (neQuery.data?.items || []).map((ne) => ({
      key: `managed:${ne.id}`,
      source: "managed" as const,
      managed_ne_id: ne.id,
      ume_ne_id: "",
      name: ne.name || ne.ip_address,
      ip: ne.ip_address,
      vendor: ne.vendor,
      meta: `${ne.ip_address}${SEP}${ne.vendor}`,
      connect_status: ne.connect_status,
    }));
  }, [paletteSource, neQuery.data, umeQuery.data]);

  const paletteVisible = useMemo(() => {
    const tokens = keyword
      .trim()
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);
    let list = palette;
    if (tokens.length) {
      list = list.filter((item) => {
        const bits = [item.name, item.ip, item.vendor, item.managed_ne_id, item.ume_ne_id, item.meta];
        return tokens.every((tok) => bits.some((b) => fuzzyIncludes(String(b || ""), tok)));
      });
    }
    return list.filter((item) =>
      item.source === "managed"
        ? !onCanvasManagedIds.has(item.managed_ne_id)
        : !onCanvasUmeIds.has(item.ume_ne_id),
    );
  }, [palette, onCanvasManagedIds, onCanvasUmeIds, keyword]);

  const paletteLoading =
    (paletteSource === "managed" && neQuery.isLoading) ||
    (paletteSource === "ume" && umeQuery.isLoading);

  return {
    paletteAdding,
    outsidePeersAdding,
    neQuery,
    umeQuery,
    addPaletteItems,
    addNodeAt,
    onPaletteDragStart,
    onCanvasDragOver,
    onCanvasDrop,
    addOutsidePeers,
    outsidePeerNameById,
    outsidePeersVisible,
    palette,
    paletteVisible,
    paletteLoading,
  };
}
