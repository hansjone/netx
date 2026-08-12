import { useCallback, useMemo, useRef, useState } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { Edge, Node } from "@xyflow/react";
import { queryKeys } from "../../../constants/queryKeys";
import {
  addTopologyViewNodes,
  createFabricManualEdge,
  deleteFabricEdges,
  fetchLldpDiscoverJob,
  fetchTopologyGraph,
  patchTopologyPositions,
  projectTopologyNeighbors,
  removeTopologyViewNodes,
  startLldpDiscover,
  stopLldpCollectJob,
  formatErr,
} from "../../../services/api";
import type {
  TopologyDiscoverJob,
  TopologyDiscoverNeResult,
  TopologyDiscoverOut,
  TopologyViewGraph,
} from "../../../types";
import type { DiscoverProgress, DiscoverSummary } from "../TopologyDiscoverPanel";
import type { EdgeDefaults, EdgeStyleData } from "../edgeStyle";
import { flowToPositions, graphToFlow } from "../graphFlow";
import { discoverResultKind, isLocalPendingEdgeId } from "../idUtils";
import { layoutGraph } from "../layoutGraph";
import type { NeNodeData } from "../TopologyReactFlowView";

export type UseTopologyDiscoverOptions = {
  mapId: string;
  nodes: Node<NeNodeData>[];
  edges: Edge[];
  graphNodes: TopologyViewGraph["nodes"] | undefined;
  edgeDefaults: EdgeDefaults;
  autoLayoutAfterDiscover: boolean;
  discoverAutoAddUnmatched: boolean;
  discoverProjectNeighbors: boolean;
  queryClient: QueryClient;
  setNodes: React.Dispatch<React.SetStateAction<Node<NeNodeData>[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  dirtyRef: React.MutableRefObject<boolean>;
  pendingEdgeDeletesRef: React.MutableRefObject<Set<string>>;
  pendingEdgeCreatesRef: React.MutableRefObject<Set<string>>;
  historyLockRef: React.MutableRefObject<boolean>;
  appliedMapIdRef: React.MutableRefObject<string>;
  markDirty: () => void;
  clearDirty: () => void;
  showOk: (msg: string) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
};

export function useTopologyDiscover(opts: UseTopologyDiscoverOptions) {
  const {
    mapId,
    nodes,
    edges,
    graphNodes,
    edgeDefaults,
    autoLayoutAfterDiscover,
    discoverAutoAddUnmatched,
    discoverProjectNeighbors,
    queryClient,
    setNodes,
    setEdges,
    dirtyRef,
    pendingEdgeDeletesRef,
    pendingEdgeCreatesRef,
    historyLockRef,
    appliedMapIdRef,
    markDirty,
    clearDirty,
    showOk,
    showError,
    t,
  } = opts;

  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoverReport, setDiscoverReport] = useState<TopologyDiscoverOut | null>(null);
  const [discoverLiveResults, setDiscoverLiveResults] = useState<TopologyDiscoverNeResult[]>([]);
  const [discoverProgress, setDiscoverProgress] = useState<DiscoverProgress>({
    index: 0,
    total: 0,
    neName: "",
    neIp: "",
    edgesAdded: 0,
    edgesUpdated: 0,
  });
  const [discoverError, setDiscoverError] = useState("");
  const [discoverJobId, setDiscoverJobId] = useState("");
  const discoverAbortRef = useRef(false);

  const flushDirtyCanvas = useCallback(async () => {
    if (!mapId || !dirtyRef.current) return;
    const createIds = [...pendingEdgeCreatesRef.current];
    for (const id of createIds) {
      const e = edges.find((x) => x.id === id);
      if (!e) continue;
      const d = (e.data || {}) as EdgeStyleData;
      await createFabricManualEdge({
        a_node_id: e.source,
        b_node_id: e.target,
        a_port: String(d.source_port || ""),
        b_port: String(d.target_port || ""),
      });
    }
    pendingEdgeCreatesRef.current.clear();
    const pendingEdges = [...pendingEdgeDeletesRef.current].filter((id) => !isLocalPendingEdgeId(id));
    if (pendingEdges.length) {
      await deleteFabricEdges(pendingEdges);
    }
    pendingEdgeDeletesRef.current.clear();
    const serverIds = (graphNodes || []).map((n) => n.fabric_node_id).filter(Boolean);
    const localIds = nodes.map((n) => n.id);
    const localSet = new Set(localIds);
    const serverSet = new Set(serverIds);
    const toAdd = localIds.filter((id) => !serverSet.has(id));
    const toRemove = serverIds.filter((id) => !localSet.has(id));
    if (toAdd.length) {
      await addTopologyViewNodes(mapId, { fabric_node_ids: toAdd });
    }
    if (toRemove.length) {
      await removeTopologyViewNodes(mapId, toRemove);
    }
    await patchTopologyPositions(mapId, flowToPositions(nodes));
    clearDirty();
  }, [
    mapId,
    edges,
    graphNodes,
    nodes,
    dirtyRef,
    pendingEdgeCreatesRef,
    pendingEdgeDeletesRef,
    clearDirty,
  ]);

  const runDiscover = useCallback(
    async (neIds?: string[]) => {
      if (!mapId || discovering) return;
      const filterIds = (neIds || []).map((x) => String(x || "").trim()).filter(Boolean);
      if (!filterIds.length) {
        showError(t("topology.discoverOneNeedNe"));
        return;
      }
      setDiscoverOpen(true);
      setDiscovering(true);
      setDiscoverReport(null);
      setDiscoverLiveResults([]);
      setDiscoverError("");
      setDiscoverJobId("");
      discoverAbortRef.current = false;
      const scannable = nodes.filter((n) => Boolean(n.data.managed_ne_id || n.data.ume_ne_id));
      const scoped = scannable.filter(
        (n) =>
          filterIds.includes(String(n.data.managed_ne_id || "")) ||
          filterIds.includes(String(n.data.ume_ne_id || "")),
      );
      setDiscoverProgress({
        index: 0,
        total: scoped.length,
        neName: "",
        neIp: "",
        edgesAdded: 0,
        edgesUpdated: 0,
      });
      try {
        await flushDirtyCanvas();
        if (!scoped.length) {
          throw new Error(t("topology.discoverOneNeedNe"));
        }
        const jobStart = await startLldpDiscover({
          scope: "ne_ids",
          ne_ids: filterIds,
          concurrency: 4,
          auto_add_unmatched: discoverAutoAddUnmatched,
          trigger_mode: "topology",
        });
        setDiscoverJobId(jobStart.id);
        let job: TopologyDiscoverJob = jobStart;
        let cancelled = false;
        const isTerminalStatus = (status: string) =>
          status === "done" ||
          status === "failed" ||
          status === "cancelled" ||
          status === "stopped";
        for (let i = 0; i < 600; i++) {
          if (discoverAbortRef.current) {
            cancelled = true;
            break;
          }
          await new Promise((r) => window.setTimeout(r, 500));
          if (discoverAbortRef.current) {
            cancelled = true;
            break;
          }
          job = await fetchLldpDiscoverJob(jobStart.id, { page: 1, pageSize: 5 });
          setDiscoverProgress((p) => ({
            ...p,
            index: job.done,
            total: job.total || p.total,
            edgesAdded: job.edges_added,
            edgesUpdated: job.edges_updated,
            neName: job.items?.[job.items.length - 1]?.ne_name || p.neName,
            neIp: job.items?.[job.items.length - 1]?.ne_ip || p.neIp,
          }));
          setDiscoverLiveResults((job.items || []) as TopologyDiscoverNeResult[]);
          if (isTerminalStatus(job.status)) {
            if (job.status === "cancelled" || job.status === "stopped") cancelled = true;
            break;
          }
        }
        if (cancelled || discoverAbortRef.current) {
          setDiscoverError(t("topology.discoverCancelled"));
          setDiscoverReport({
            map_id: mapId,
            protocol: "lldp",
            job_id: job.id,
            scanned: job.done,
            edges_added: job.edges_added,
            edges_updated: job.edges_updated,
            edges_stale: job.edges_missing ?? job.edges_stale,
            results: (job.items || []) as TopologyDiscoverNeResult[],
            graph: null,
          });
          return;
        }
        if (!isTerminalStatus(job.status)) {
          throw new Error(t("topology.discoverTimeout"));
        }
        if (job.status === "failed") {
          throw new Error(job.error || "discover_failed");
        }
        try {
          job = await fetchLldpDiscoverJob(jobStart.id, { page: 1, pageSize: 100 });
        } catch {
          /* keep last polled job */
        }
        const baseGraph = await fetchTopologyGraph(mapId);
        const projected = discoverProjectNeighbors
          ? await projectTopologyNeighbors(mapId, {
              seed_fabric_node_ids: scoped.map((n) => n.id),
              dry_run: true,
            })
          : baseGraph;
        queryClient.setQueryData(queryKeys.topologyGraph(mapId), baseGraph);
        if (discoverProjectNeighbors && projected.truncate_reason === "membership_frozen") {
          showError(t("topology.truncatedFrozen"));
        }
        appliedMapIdRef.current = mapId;
        let { rfNodes, rfEdges } = graphToFlow(projected.nodes, projected.edges, edgeDefaults);
        const localPos = new Map(nodes.map((n) => [n.id, n.position]));
        const serverNodeIds = new Set((baseGraph.nodes || []).map((n) => n.fabric_node_id));
        rfNodes = rfNodes.map((n) => {
          const p = localPos.get(n.id);
          return p ? { ...n, position: { ...p } } : n;
        });
        let didAutoLayout = false;
        if (autoLayoutAfterDiscover && rfNodes.length > 1) {
          rfNodes = await layoutGraph(rfNodes, rfEdges, "hierarchical-tb");
          didAutoLayout = true;
        }
        historyLockRef.current = true;
        setNodes(rfNodes);
        setEdges(rfEdges);
        historyLockRef.current = false;
        const localOnly = rfNodes.some((n) => !serverNodeIds.has(n.id));
        if (localOnly || didAutoLayout) {
          markDirty();
        } else {
          clearDirty();
        }
        const out: TopologyDiscoverOut = {
          map_id: mapId,
          protocol: "lldp",
          job_id: job.id,
          scanned: job.done,
          edges_added: job.edges_added,
          edges_updated: job.edges_updated,
          edges_stale: job.edges_missing ?? job.edges_stale,
          results: (job.items || []) as TopologyDiscoverNeResult[],
          graph: projected,
        };
        setDiscoverReport(out);
        await queryClient.invalidateQueries({ queryKey: queryKeys.topologyMaps });
        await queryClient.invalidateQueries({ queryKey: queryKeys.fabricSummary });
        showOk(
          t("topology.discovered")
            .replace("{{added}}", String(out.edges_added))
            .replace("{{updated}}", String(out.edges_updated))
            .replace("{{stale}}", String(out.edges_stale || 0)),
        );
      } catch (err) {
        if (discoverAbortRef.current) {
          setDiscoverError(t("topology.discoverCancelled"));
        } else {
          const detail = formatErr(err);
          setDiscoverError(detail);
          showError(t("topology.discoverFail").replace("{{detail}}", detail));
        }
      } finally {
        setDiscovering(false);
      }
    },
    [
      mapId,
      discovering,
      nodes,
      queryClient,
      setNodes,
      setEdges,
      showOk,
      showError,
      t,
      autoLayoutAfterDiscover,
      discoverAutoAddUnmatched,
      discoverProjectNeighbors,
      edgeDefaults,
      clearDirty,
      markDirty,
      flushDirtyCanvas,
      historyLockRef,
      appliedMapIdRef,
    ],
  );

  const cancelDiscover = useCallback(async () => {
    discoverAbortRef.current = true;
    const jobId = discoverJobId;
    if (jobId) {
      try {
        await stopLldpCollectJob(jobId);
      } catch {
        /* best-effort */
      }
    }
  }, [discoverJobId]);

  const discoverResults = discoverReport?.results?.length
    ? discoverReport.results
    : discoverLiveResults;

  const discoverCounts = useMemo(() => {
    let ok = 0;
    let warn = 0;
    let fail = 0;
    for (const r of discoverResults) {
      const k = discoverResultKind(r);
      if (k === "fail") fail += 1;
      else if (k === "warn") warn += 1;
      else ok += 1;
    }
    return { ok, warn, fail, issues: warn + fail };
  }, [discoverResults]);

  const discoverSummary: DiscoverSummary = discoverReport
    ? {
        scanned: discoverReport.scanned,
        added: discoverReport.edges_added,
        updated: discoverReport.edges_updated,
        missing: discoverReport.edges_stale || 0,
        failed: discoverCounts.fail,
      }
    : {
        scanned: discoverLiveResults.length,
        added: discoverProgress.edgesAdded,
        updated: discoverProgress.edgesUpdated,
        missing: 0,
        failed: discoverCounts.fail,
      };

  const discoverPct =
    discovering && discoverProgress.total > 0
      ? Math.min(100, Math.round((discoverProgress.index / discoverProgress.total) * 100))
      : discoverReport
        ? 100
        : 0;

  return {
    discoverOpen,
    setDiscoverOpen,
    discovering,
    discoverReport,
    discoverProgress,
    discoverError,
    discoverResults,
    discoverSummary,
    discoverPct,
    runDiscover,
    cancelDiscover,
  };
}
