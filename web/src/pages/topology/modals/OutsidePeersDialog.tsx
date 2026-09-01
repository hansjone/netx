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
  if (!open) return null;

  return (
    <div
      className="topo-modal"
      role="dialog"
      aria-modal="true"
      aria-label={t("topology.outsidePeersTitle")}
    >
      <div
        className="topo-modal__backdrop"
        onClick={() => {
          if (outsidePeersAdding) return;
          onClose();
        }}
      />
      <div className="topo-modal__panel topo-modal__panel--wide">
        <div className="topo-modal__head">
          <strong>
            {t("topology.outsidePeersTitle")}
            <span className="topo-modal__count"> · {outsidePeers.length}</span>
          </strong>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={outsidePeersAdding}
            onClick={onClose}
          >
            {t("topology.discoverClose")}
          </button>
        </div>
        <p className="panel__hint topo-modal__hint">{t("topology.outsidePeersHint")}</p>
        <input
          className="input"
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
            <span className="panel__hint">
              {t("topology.selectedCount").replace(
                "{{count}}",
                String(outsidePeerSelectedIds.length),
              )}
            </span>
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
        <div className="topo-modal__foot">
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={outsidePeersAdding}
            onClick={onClose}
          >
            {t("topology.discoverClose")}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={outsidePeerSelectedIds.length === 0 || outsidePeersAdding}
            onClick={() => void onAddOutsidePeers(outsidePeerSelectedIds)}
          >
            {outsidePeersAdding
              ? t("topology.addingNe")
              : t("topology.addSelected").replace(
                  "{{count}}",
                  String(outsidePeerSelectedIds.length),
                )}
          </button>
        </div>
      </div>
    </div>
  );
}
