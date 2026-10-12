import type { Edge, Node } from "@xyflow/react";
import { fetchManagedNeById } from "../../../services/api";
import { openOrFocusModule } from "../../../utils/moduleWindows";
import type { NeNodeData } from "../components/TopologyReactFlowView";
import type { EdgeStyleData } from "./edgeStyle";

export function openNeInventory(opts: {
  neId?: string;
  create?: boolean;
  name?: string;
  ip_address?: string;
  vendor?: string;
}) {
  const q = new URLSearchParams();
  if (opts.neId) {
    q.set("ne_id", opts.neId);
  } else if (opts.create) {
    q.set("create", "1");
    if (opts.name) q.set("name", opts.name);
    if (opts.ip_address) q.set("ip_address", opts.ip_address);
    if (opts.vendor) q.set("vendor", opts.vendor);
  }
  const qs = q.toString();
  openOrFocusModule({
    moduleId: "ne",
    path: qs ? `/ne?${qs}` : "/ne",
  });
}

type LinkToast = {
  showOk: (msg: string) => void;
  showError: (msg: string) => void;
  t: (key: string) => string;
  closeCtxMenu: () => void;
};

export function openWebcrtFor(
  node: Node<NeNodeData> | null,
  { showOk, showError, t, closeCtxMenu }: LinkToast,
) {
  closeCtxMenu();
  if (!node) return;
  const managedId = String(node.data.managed_ne_id || "").trim();
  const umeId = String(node.data.ume_ne_id || "").trim();

  if (managedId) {
    void (async () => {
      try {
        const ne = await fetchManagedNeById(managedId);
        const src = String(ne.source || "").trim().toLowerCase();
        const hasIp = Boolean(String(ne.ip_address || "").trim());
        // Ready inventory hosts: reuse stored credentials (incl. hop/proxy).
        // LLDP / topology placeholders / incomplete rows → full NE edit form.
        const needsSetup = !hasIp || src === "lldp" || src === "topology";
        if (!needsSetup) {
          openOrFocusModule({
            moduleId: "webcrt",
            path: `/webcrt?ne_id=${encodeURIComponent(managedId)}`,
          });
          return;
        }
        showOk(t("topology.completeNeFirst"));
        openNeInventory({ neId: managedId });
      } catch (err) {
        showError(String(err));
      }
    })();
    return;
  }

  if (umeId) {
    openOrFocusModule({
      moduleId: "webcrt",
      path: `/webcrt?ne_id=${encodeURIComponent(umeId)}&source=ume`,
    });
    return;
  }

  // No inventory link — open NE create with topology hint fields.
  showOk(t("topology.completeNeFirst"));
  openNeInventory({
    create: true,
    name: String(node.data.label || "").trim(),
    ip_address: String(node.data.ne_ip || "").trim(),
    vendor: String(node.data.vendor || "").trim(),
  });
}

export function openNeFor(
  node: Node<NeNodeData> | null,
  { showError, t, closeCtxMenu }: Pick<LinkToast, "showError" | "t" | "closeCtxMenu">,
) {
  closeCtxMenu();
  const managedId = String(node?.data.managed_ne_id || "").trim();
  const umeId = String(node?.data.ume_ne_id || "").trim();
  if (managedId) {
    openNeInventory({ neId: managedId });
    return;
  }
  if (umeId) {
    openOrFocusModule({
      moduleId: "ume",
      path: "/ume",
    });
    return;
  }
  showError(t("topology.noNeLink"));
}

export function openAlarmsFor(
  node: Node<NeNodeData> | null,
  { showError, t, closeCtxMenu }: Pick<LinkToast, "showError" | "t" | "closeCtxMenu">,
) {
  closeCtxMenu();
  if (!node || node.data.kind === "region") {
    showError(t("topology.noAlarmLink"));
    return;
  }
  const host = String(node.data.label || node.data.ne_ip || "").trim();
  const q = new URLSearchParams();
  if (host) q.set("host", host);
  q.set("cleared", "false");
  openOrFocusModule({
    moduleId: "network",
    path: `/network/alarms?${q.toString()}`,
  });
}

export function openPortTrafficForEdge(
  edge: Edge | null,
  nodes: Node<NeNodeData>[],
  { showError, t, closeCtxMenu }: Pick<LinkToast, "showError" | "t" | "closeCtxMenu">,
) {
  closeCtxMenu();
  if (!edge) {
    showError(t("topology.noNeLink"));
    return;
  }
  const srcNode = nodes.find((n) => n.id === edge.source);
  const data = (edge.data || {}) as EdgeStyleData;
  const ifname = String(data.source_port || "").trim();
  const managedId = String(srcNode?.data.managed_ne_id || "").trim();
  const umeId = String(srcNode?.data.ume_ne_id || "").trim();
  if (managedId) {
    const q = new URLSearchParams({ ne_id: managedId, source: "managed" });
    if (ifname) q.set("ifname", ifname);
    openOrFocusModule({
      moduleId: "network",
      path: `/network/tasks/port-traffic?${q.toString()}`,
    });
    return;
  }
  if (umeId) {
    const q = new URLSearchParams({ ne_id: umeId, source: "ume" });
    if (ifname) q.set("ifname", ifname);
    openOrFocusModule({
      moduleId: "network",
      path: `/network/tasks/port-traffic?${q.toString()}`,
    });
    return;
  }
  showError(t("topology.noNeLink"));
}
