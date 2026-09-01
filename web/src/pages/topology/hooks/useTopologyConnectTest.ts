import { useCallback, useEffect, useState } from "react";
import type { Node } from "@xyflow/react";
import { connectTestManagedNe, fetchManagedNeById } from "../../../services/api";
import { useI18n } from "../../../i18n";
import { useToast } from "../../../hooks/useToast";
import type { ManagedNeItem } from "../../../types";
import type { NeNodeData } from "../TopologyReactFlowView";

type Args = {
  closeCtxMenu: () => void;
};

export function useTopologyConnectTest({ closeCtxMenu }: Args) {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [detailRow, setDetailRow] = useState<ManagedNeItem | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const refreshRow = useCallback(async (id: string) => {
    const row = await fetchManagedNeById(id);
    setDetailRow(row);
    return row;
  }, []);

  useEffect(() => {
    if (!detailRow || detailRow.connect_status !== "testing") return;
    const id = detailRow.id;
    const timer = window.setInterval(() => {
      void refreshRow(id).catch(() => {
        /* ignore transient poll errors */
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [detailRow?.id, detailRow?.connect_status, refreshRow]);

  const runForNode = useCallback(
    async (node: Node<NeNodeData> | null) => {
      closeCtxMenu();
      const managedId = String(node?.data.managed_ne_id || "").trim();
      if (!managedId) {
        showError(t("topology.connectTestNeedManaged"));
        return;
      }
      setSubmitting(true);
      try {
        const res = await connectTestManagedNe([managedId]);
        showOk(t("managedNe.connect.submitted", { n: res.submitted }));
        await refreshRow(managedId);
      } catch (err) {
        showError(String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [closeCtxMenu, refreshRow, showError, showOk, t],
  );

  /** Batch: submit connect tests; open detail only when exactly one managed NE. */
  const runForNodes = useCallback(
    async (nodes: Node<NeNodeData>[]) => {
      closeCtxMenu();
      const ids = [
        ...new Set(
          nodes
            .map((n) => String(n.data.managed_ne_id || "").trim())
            .filter(Boolean),
        ),
      ];
      if (!ids.length) {
        showError(t("topology.connectTestNeedManaged"));
        return;
      }
      const skipped = nodes.length - ids.length;
      setSubmitting(true);
      try {
        const res = await connectTestManagedNe(ids);
        const msg = t("managedNe.connect.submitted", { n: res.submitted });
        showOk(
          skipped > 0
            ? `${msg} · ${t("topology.connectTestSkipped").replace("{{count}}", String(skipped))}`
            : msg,
        );
        if (ids.length === 1) {
          await refreshRow(ids[0]);
        }
      } catch (err) {
        showError(String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [closeCtxMenu, refreshRow, showError, showOk, t],
  );

  const onRetestSubmitted = useCallback(
    (rowId: string) => {
      showOk(t("managedNe.connect.submitted", { n: 1 }));
      void refreshRow(rowId);
    },
    [refreshRow, showOk, t],
  );

  return {
    connectDetailRow: detailRow,
    connectTestSubmitting: submitting,
    closeConnectDetail: () => setDetailRow(null),
    runConnectTestForNode: runForNode,
    runConnectTestForNodes: runForNodes,
    onConnectRetestSubmitted: onRetestSubmitted,
  };
}
