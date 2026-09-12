import { Button, Chip, Input, Modal } from "@heroui/react";
import { TopoModalShell } from "../../../components/ui/TopoModalShell";
import type { TopologyOutsidePeer } from "../../../types";
import { useI18n } from "../../../i18n";

export type OutsidePeersDialogProps = {
  open: boolean;
  outsidePeers: TopologyOutsidePeer[];
  outsidePeersVisible: TopologyOutsidePeer[];
  outsidePeerQuery: string;
  onOutsidePeerQueryChange: (q: string) => void;
  outsidePeerSelectedIds: string[];
  onOutsidePeerSelectedIdsChange: (ids: string[] | ((prev: string[]) => string[])) => void;
  outsidePeerNameById: Map<string, string>;
  outsidePeersAdding: boolean;
  onClose: () => void;
  onAddOutsidePeers: (ids: string[]) => void;
};

export function OutsidePeersDialog({
  open,
  outsidePeers,
  outsidePeersVisible,
  outsidePeerQuery,
  onOutsidePeerQueryChange,
  outsidePeerSelectedIds,
  onOutsidePeerSelectedIdsChange,
  outsidePeerNameById,
  outsidePeersAdding,
  onClose,
  onAddOutsidePeers,
}: OutsidePeersDialogProps) {
  const { t } = useI18n();

  return (
    <TopoModalShell open={open} onClose={onClose} dismissible={!outsidePeersAdding} size="lg">
      <Modal.Header>
        <Modal.Heading>
          {t("topology.outsidePeersTitle")}
          <span className="topo-modal__count"> · {outsidePeers.length}</span>
        </Modal.Heading>
        <Modal.CloseTrigger isDisabled={outsidePeersAdding} />
      </Modal.Header>
      <Modal.Body className="flex flex-col gap-3">
        <p className="text-sm text-muted">{t("topology.outsidePeersHint")}</p>
        <Input
          className="w-full"
          value={outsidePeerQuery}
          onChange={(e) => onOutsidePeerQueryChange(e.target.value)}
          placeholder={t("topology.outsidePeersFilterPh")}
          disabled={outsidePeersAdding}
          autoFocus
        />
        {outsidePeersVisible.length > 0 ? (
          <div className="topo-modal__selectbar">
            <label className="topo-modal__selectall">
              <input
                type="checkbox"
                checked={
                  outsidePeersVisible.length > 0 &&
                  outsidePeersVisible.every((p) =>
                    outsidePeerSelectedIds.includes(p.fabric_node_id),
                  )
                }
                disabled={outsidePeersAdding}
                onChange={(e) => {
                  if (e.target.checked) {
                    onOutsidePeerSelectedIdsChange((prev) => [
                      ...new Set([...prev, ...outsidePeersVisible.map((p) => p.fabric_node_id)]),
                    ]);
                    return;
                  }
                  const drop = new Set(outsidePeersVisible.map((p) => p.fabric_node_id));
                  onOutsidePeerSelectedIdsChange((prev) => prev.filter((id) => !drop.has(id)));
                }}
                aria-label={t("topology.selectAllVisible")}
              />
              <span>{t("topology.selectAllVisible")}</span>
            </label>
            <Chip size="sm" variant="soft">
              <Chip.Label>
                {t("topology.selectedCount").replace(
                  "{{count}}",
                  String(outsidePeerSelectedIds.length),
                )}
              </Chip.Label>
            </Chip>
          </div>
        ) : null}
        <ul className="topo-palette topo-modal__list">
          {outsidePeersVisible.length === 0 ? (
            <li className="topo-palette__empty">
              <span className="panel__hint">{t("topology.outsidePeersEmpty")}</span>
            </li>
          ) : (
            outsidePeersVisible.map((peer) => {
              const checked = outsidePeerSelectedIds.includes(peer.fabric_node_id);
              const viaName =
                outsidePeerNameById.get(peer.via_node_id) || peer.via_node_id.slice(0, 8);
              const title = peer.name || peer.ip || peer.fabric_node_id.slice(0, 8);
              return (
                <li key={peer.fabric_node_id}>
                  <div className={`topo-palette__row${checked ? " is-selected" : ""}`}>
                    <label className="topo-palette__check">
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={outsidePeersAdding}
                        onChange={(e) => {
                          onOutsidePeerSelectedIdsChange((prev) =>
                            e.target.checked
                              ? [...new Set([...prev, peer.fabric_node_id])]
                              : prev.filter((id) => id !== peer.fabric_node_id),
                          );
                        }}
                        aria-label={title}
                      />
                    </label>
                    <button
                      type="button"
                      className="topo-palette__item"
                      disabled={outsidePeersAdding}
                      onClick={() => {
                        onOutsidePeerSelectedIdsChange((prev) =>
                          prev.includes(peer.fabric_node_id)
                            ? prev.filter((id) => id !== peer.fabric_node_id)
                            : [...prev, peer.fabric_node_id],
                        );
                      }}
                    >
                      <span className="topo-palette__name">{title}</span>
                      <span className="topo-palette__meta">
                        {[peer.ip, t("topology.outsidePeersVia").replace("{{name}}", viaName)]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </button>
                  </div>
                </li>
              );
            })
          )}
        </ul>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="tertiary" size="sm" isDisabled={outsidePeersAdding} onPress={onClose}>
          {t("topology.discoverClose")}
        </Button>
        <Button
          variant="primary"
          size="sm"
          isDisabled={outsidePeerSelectedIds.length === 0 || outsidePeersAdding}
          onPress={() => void onAddOutsidePeers(outsidePeerSelectedIds)}
        >
          {outsidePeersAdding
            ? t("topology.addingNe")
            : t("topology.addSelected").replace(
                "{{count}}",
                String(outsidePeerSelectedIds.length),
              )}
        </Button>
      </Modal.Footer>
    </TopoModalShell>
  );
}
