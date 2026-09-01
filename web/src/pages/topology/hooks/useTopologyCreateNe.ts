import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Edge, Node } from "@xyflow/react";
import {
  addTopologyViewNodes,
  createTopologyPlaceholder,
  fetchTopologyGraph,
  patchTopologyPositions,
} from "../../../services/api";
import { queryKeys } from "../../../constants/queryKeys";
import { useI18n } from "../../../i18n";
import { useToast } from "../../../hooks/useToast";
import type { ManagedNeItem } from "../../../types";
import { applyViewGraph } from "../graphFlow";
import type { EdgeDefaults } from "../edgeStyle";
import type { PlaceholderCreateDialogState } from "../modals/PlaceholderCreateDialog";
import type { ManagedNeFormState } from "../../managedNe/formState";
import { isWorldFlatViewName } from "../treeUtils";
import type { NeNodeData } from "../TopologyReactFlowView";

type FocusNode = (fabricNodeId: string, fit?: boolean) => void;

type Args = {
  mapId: string | null | undefined;
  activeViewName: string | undefined;
  canWrite: boolean;
  edgeDefaults: EdgeDefaults;
  setNodes: Dispatch<SetStateAction<Node<NeNodeData>[]>>;
  setEdges: Dispatch<SetStateAction<Edge[]>>;
  clearDirty: () => void;
  historyLockRef: MutableRefObject<boolean>;
  focusNode: FocusNode;
  closeCtxMenu: () => void;
};

export function useTopologyCreateNe({
  mapId,
  activeViewName,
  canWrite,
  edgeDefaults,
  setNodes,
  setEdges,
  clearDirty,
  historyLockRef,
  focusNode,
  closeCtxMenu,
}: Args) {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();

  const [modeOpen, setModeOpen] = useState(false);
  const [placeAt, setPlaceAt] = useState<{ flowX: number; flowY: number } | null>(null);
  const [placeholderDialog, setPlaceholderDialog] = useState<PlaceholderCreateDialogState | null>(
    null,
  );
  const [placeholderBusy, setPlaceholderBusy] = useState(false);
  const [managedFormOpen, setManagedFormOpen] = useState(false);
  const [managedFormInitial, setManagedFormInitial] = useState<Partial<ManagedNeFormState> | undefined>();
  const [managedBusy, setManagedBusy] = useState(false);
  const placeAtRef = useRef(placeAt);
  placeAtRef.current = placeAt;

  const openCreateNeAt = useCallback(
    (flowX: number, flowY: number) => {
      if (!canWrite) {
        showError(t("topology.readOnlyHint"));
        return;
      }
      if (isWorldFlatViewName(activeViewName)) {
        showError(t("topology.worldMapNoDirectNes"));
        return;
      }
      closeCtxMenu();
      setPlaceAt({ flowX, flowY });
      setModeOpen(true);
    },
    [canWrite, activeViewName, closeCtxMenu, showError, t],
  );

  const closeMode = useCallback(() => setModeOpen(false), []);

  const pickManaged = useCallback(() => {
    setModeOpen(false);
    setManagedFormInitial(undefined);
    setManagedFormOpen(true);
  }, []);

  const pickPlaceholder = useCallback(() => {
    const at = placeAtRef.current;
    if (!at) return;
    setModeOpen(false);
    setPlaceholderDialog({ ...at, name: "", ip_address: "" });
  }, []);

  const closePlaceholder = useCallback(() => setPlaceholderDialog(null), []);

  const closeManagedForm = useCallback(() => {
    if (managedBusy) return;
    setManagedFormOpen(false);
  }, [managedBusy]);

  const applyGraph = useCallback(
    (graph: Awaited<ReturnType<typeof fetchTopologyGraph>>) => {
      if (!mapId) return;
      queryClient.setQueryData(queryKeys.topologyGraph(mapId), graph);
      historyLockRef.current = true;
      applyViewGraph(graph, edgeDefaults, setNodes, setEdges);
      historyLockRef.current = false;
      clearDirty();
    },
    [mapId, queryClient, edgeDefaults, setNodes, setEdges, clearDirty, historyLockRef],
  );

  const submitPlaceholder = useCallback(async () => {
    if (!mapId || !placeholderDialog) return;
    const name = placeholderDialog.name.trim();
    if (!name) {
      showError(t("topology.createNeNameRequired"));
      return;
    }
    setPlaceholderBusy(true);
    try {
      const graph = await createTopologyPlaceholder(mapId, {
        name,
        ip_address: placeholderDialog.ip_address.trim(),
        x: placeholderDialog.flowX,
        y: placeholderDialog.flowY,
      });
      applyGraph(graph);
      const created = graph.nodes.find(
        (n) =>
          String(n.name || "").trim() === name &&
          Math.abs(Number(n.x) - placeholderDialog.flowX) < 0.5 &&
          Math.abs(Number(n.y) - placeholderDialog.flowY) < 0.5,
      );
      if (created?.fabric_node_id) {
        focusNode(created.fabric_node_id, false);
      }
      setPlaceholderDialog(null);
      showOk(t("topology.createNeDone").replace("{{name}}", name));
      void queryClient.invalidateQueries({ queryKey: queryKeys.managedNeAll });
      void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
    } catch (err) {
      showError(String(err));
    } finally {
      setPlaceholderBusy(false);
    }
  }, [
    mapId,
    placeholderDialog,
    applyGraph,
    focusNode,
    queryClient,
    showError,
    showOk,
    t,
  ]);

  const onManagedFormSaved = useCallback(
    async (item: ManagedNeItem) => {
      setManagedFormOpen(false);
      if (!mapId) {
        showOk(t("managedNe.form.created"));
        return;
      }
      const at = placeAtRef.current || { flowX: 80, flowY: 80 };
      setManagedBusy(true);
      try {
        const graph = await addTopologyViewNodes(mapId, {
          managed_ne_ids: [item.id],
          layout: "grid",
        });
        const added = graph.nodes.find((n) => n.managed_ne_id === item.id);
        if (added?.fabric_node_id) {
          await patchTopologyPositions(mapId, [
            {
              fabric_node_id: added.fabric_node_id,
              x: at.flowX,
              y: at.flowY,
              label: added.label || added.name || item.name || "",
            },
          ]);
        }
        const refreshed = await fetchTopologyGraph(mapId);
        applyGraph(refreshed);
        if (added?.fabric_node_id) {
          focusNode(added.fabric_node_id, false);
        }
        showOk(t("topology.createManagedNeDone").replace("{{name}}", item.name || item.ip_address));
        void queryClient.invalidateQueries({ queryKey: queryKeys.managedNeAll });
        void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
      } catch (err) {
        showError(String(err));
      } finally {
        setManagedBusy(false);
      }
    },
    [mapId, applyGraph, focusNode, queryClient, showError, showOk, t],
  );

  return {
    modeOpen,
    openCreateNeAt,
    closeMode,
    pickManaged,
    pickPlaceholder,
    placeholderDialog,
    setPlaceholderDialog,
    placeholderBusy,
    closePlaceholder,
    submitPlaceholder,
    managedFormOpen,
    managedFormInitial,
    closeManagedForm,
    onManagedFormSaved,
  };
}
