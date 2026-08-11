import type { DragEvent } from "react";
import type { TopologyOutsidePeer } from "../../types";
import { useI18n } from "../../i18n";
import type { PaletteItem, PaletteSource } from "./pageTypes";

export type CreateNeDialogState = {
  flowX: number;
  flowY: number;
  name: string;
  ip_address: string;
};

export type TopologyModalsProps = {
  canvasMode: boolean;
  newRootDialog: { name: string } | null;
  onNewRootNameChange: (name: string) => void;
  onCloseNewRoot: () => void;
  onSubmitNewRoot: () => void;
  createRegionPending: boolean;
  createNeDialog: CreateNeDialogState | null;
  createNeBusy: boolean;
  onCreateNeChange: (patch: Partial<Pick<CreateNeDialogState, "name" | "ip_address">>) => void;
  onCloseCreateNe: () => void;
  onSubmitCreateNe: () => void;
  outsidePeersOpen: boolean;
  outsidePeers: TopologyOutsidePeer[];
  outsidePeersVisible: TopologyOutsidePeer[];
  outsidePeerQuery: string;
  onOutsidePeerQueryChange: (q: string) => void;
  outsidePeerSelectedIds: string[];
  onOutsidePeerSelectedIdsChange: (ids: string[] | ((prev: string[]) => string[])) => void;
  outsidePeerNameById: Map<string, string>;
  outsidePeersAdding: boolean;
  onCloseOutsidePeers: () => void;
  onAddOutsidePeers: (ids: string[]) => void;
  addNeOpen: boolean;
  paletteSource: PaletteSource;
  onPaletteSourceChange: (source: PaletteSource) => void;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  paletteVisible: PaletteItem[];
  paletteSelectedKeys: string[];
  onPaletteSelectedKeysChange: (keys: string[] | ((prev: string[]) => string[])) => void;
  paletteLoading: boolean;
  paletteAdding: boolean;
  onCloseAddNe: () => void;
  onPaletteDragStart: (e: DragEvent, item: PaletteItem) => void;
  onAddSelectedPalette: () => void;
};

export function TopologyModals({
  canvasMode,
  newRootDialog,
  onNewRootNameChange,
  onCloseNewRoot,
  onSubmitNewRoot,
  createRegionPending,
  createNeDialog,
  createNeBusy,
  onCreateNeChange,
  onCloseCreateNe,
  onSubmitCreateNe,
  outsidePeersOpen,
  outsidePeers,
  outsidePeersVisible,
  outsidePeerQuery,
  onOutsidePeerQueryChange,
  outsidePeerSelectedIds,
  onOutsidePeerSelectedIdsChange,
  outsidePeerNameById,
  outsidePeersAdding,
  onCloseOutsidePeers,
  onAddOutsidePeers,
  addNeOpen,
  paletteSource,
  onPaletteSourceChange,
  keyword,
  onKeywordChange,
  paletteVisible,
  paletteSelectedKeys,
  onPaletteSelectedKeysChange,
  paletteLoading,
  paletteAdding,
  onCloseAddNe,
  onPaletteDragStart,
  onAddSelectedPalette,
}: TopologyModalsProps) {
  const { t } = useI18n();

  return (
    <>
      {newRootDialog ? (
        <div
          className="topo-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="topo-new-root-title"
        >
          <div className="topo-modal__backdrop" onClick={onCloseNewRoot} />
          <div className="topo-modal__panel" style={{ maxWidth: 420 }}>
            <div className="topo-modal__head">
              <strong id="topo-new-root-title">{t("topology.newRegion")}</strong>
              <button type="button" className="btn btn--sm btn--ghost" onClick={onCloseNewRoot}>
                {t("topology.discoverClose")}
              </button>
            </div>
            <p className="panel__hint topo-modal__hint">{t("topology.folderHint")}</p>
            <div className="form-grid" style={{ padding: "0 16px 8px" }}>
              <label>
                <span className="form-label">{t("topology.newRegionPrompt")}</span>
                <input
                  autoFocus
                  value={newRootDialog.name}
                  disabled={createRegionPending}
                  onChange={(e) => onNewRootNameChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onSubmitNewRoot();
                  }}
                />
              </label>
            </div>
            <div className="topo-modal__foot">
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={createRegionPending}
                onClick={onCloseNewRoot}
              >
                {t("topology.discoverClose")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={createRegionPending || !String(newRootDialog.name || "").trim()}
                onClick={onSubmitNewRoot}
              >
                {createRegionPending ? "…" : t("topology.newRegion")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {createNeDialog && canvasMode ? (
        <div
          className="topo-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="topo-create-ne-title"
        >
          <div
            className="topo-modal__backdrop"
            onClick={() => {
              if (createNeBusy) return;
              onCloseCreateNe();
            }}
          />
          <div className="topo-modal__panel" style={{ maxWidth: 420 }}>
            <div className="topo-modal__head">
              <strong id="topo-create-ne-title">{t("topology.createNeTitle")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={createNeBusy}
                onClick={onCloseCreateNe}
              >
                {t("topology.discoverClose")}
              </button>
            </div>
            <p className="panel__hint topo-modal__hint">{t("topology.createNeHint")}</p>
            <div className="form-grid" style={{ padding: "0 16px 8px" }}>
              <label>
                <span className="form-label">
                  {t("topology.createNeName")}
                  <span className="form-label__required" aria-hidden="true">
                    {" "}
                    *
                  </span>
                </span>
                <input
                  autoFocus
                  value={createNeDialog.name}
                  placeholder={t("topology.createNeNamePh")}
                  disabled={createNeBusy}
                  onChange={(e) => onCreateNeChange({ name: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onSubmitCreateNe();
                  }}
                />
              </label>
              <label>
                <span className="form-label">{t("topology.createNeIp")}</span>
                <input
                  value={createNeDialog.ip_address}
                  placeholder={t("topology.createNeIpPh")}
                  disabled={createNeBusy}
                  onChange={(e) => onCreateNeChange({ ip_address: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onSubmitCreateNe();
                  }}
                />
              </label>
            </div>
            <div className="topo-modal__foot">
              <button
                type="button"
                className="btn btn--ghost"
                disabled={createNeBusy}
                onClick={onCloseCreateNe}
              >
                {t("topology.discoverClose")}
              </button>
              <button
                type="button"
                className="btn btn--primary"
                disabled={createNeBusy}
                onClick={() => void onSubmitCreateNe()}
              >
                {createNeBusy ? t("topology.createNeBusy") : t("topology.createNe")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {outsidePeersOpen && canvasMode ? (
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
              onCloseOutsidePeers();
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
                onClick={onCloseOutsidePeers}
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
                          ...new Set([
                            ...prev,
                            ...outsidePeersVisible.map((p) => p.fabric_node_id),
                          ]),
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
                onClick={onCloseOutsidePeers}
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
      ) : null}

      {addNeOpen && canvasMode ? (
        <div className="topo-modal" role="dialog" aria-modal="true" aria-label={t("topology.addNe")}>
          <div
            className="topo-modal__backdrop"
            onClick={() => {
              if (paletteAdding) return;
              onCloseAddNe();
            }}
          />
          <div className="topo-modal__panel">
            <div className="topo-modal__head">
              <strong>{t("topology.addNe")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={paletteAdding}
                onClick={onCloseAddNe}
              >
                {t("topology.discoverClose")}
              </button>
            </div>
            <p className="panel__hint topo-modal__hint">{t("topology.paletteHint")}</p>
            <div className="topo-palette-source" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={paletteSource === "managed"}
                className={`topo-palette-source__btn${paletteSource === "managed" ? " is-active" : ""}`}
                disabled={paletteAdding}
                onClick={() => onPaletteSourceChange("managed")}
              >
                {t("topology.paletteManaged")}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={paletteSource === "ume"}
                className={`topo-palette-source__btn${paletteSource === "ume" ? " is-active" : ""}`}
                disabled={paletteAdding}
                onClick={() => onPaletteSourceChange("ume")}
              >
                {t("topology.paletteUme")}
              </button>
            </div>
            <input
              className="input"
              value={keyword}
              onChange={(e) => onKeywordChange(e.target.value)}
              placeholder={t("topology.filterPh")}
              disabled={paletteAdding}
              autoFocus
            />
            {paletteVisible.length > 0 ? (
              <div className="topo-modal__selectbar">
                <label className="topo-modal__selectall">
                  <input
                    type="checkbox"
                    checked={
                      paletteVisible.length > 0 &&
                      paletteVisible.every((item) => paletteSelectedKeys.includes(item.key))
                    }
                    disabled={paletteAdding}
                    onChange={(e) => {
                      if (e.target.checked) {
                        onPaletteSelectedKeysChange(paletteVisible.map((item) => item.key));
                        return;
                      }
                      onPaletteSelectedKeysChange([]);
                    }}
                    aria-label={t("topology.selectAllVisible")}
                  />
                  <span>{t("topology.selectAllVisible")}</span>
                </label>
                <span className="panel__hint">
                  {t("topology.selectedCount").replace(
                    "{{count}}",
                    String(paletteSelectedKeys.length),
                  )}
                </span>
              </div>
            ) : null}
            <ul className="topo-palette topo-modal__list">
              {paletteLoading ? (
                <li className="topo-palette__empty">
                  <span className="panel__hint">{t("topology.paletteLoading")}</span>
                </li>
              ) : paletteVisible.length === 0 ? (
                <li className="topo-palette__empty">
                  <span className="panel__hint">{t("topology.paletteEmpty")}</span>
                </li>
              ) : (
                paletteVisible.map((item) => {
                  const checked = paletteSelectedKeys.includes(item.key);
                  return (
                    <li key={item.key}>
                      <div className={`topo-palette__row${checked ? " is-selected" : ""}`}>
                        <label className="topo-palette__check">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={paletteAdding}
                            onChange={(e) => {
                              onPaletteSelectedKeysChange((prev) =>
                                e.target.checked
                                  ? [...new Set([...prev, item.key])]
                                  : prev.filter((key) => key !== item.key),
                              );
                            }}
                            aria-label={item.name}
                          />
                        </label>
                        <button
                          type="button"
                          className="topo-palette__item"
                          draggable={!paletteAdding}
                          disabled={paletteAdding}
                          onClick={() => {
                            onPaletteSelectedKeysChange((prev) =>
                              prev.includes(item.key)
                                ? prev.filter((key) => key !== item.key)
                                : [...prev, item.key],
                            );
                          }}
                          onDragStart={(e) => onPaletteDragStart(e, item)}
                          title={t("topology.paletteDragHint")}
                        >
                          <span className="topo-palette__name">{item.name}</span>
                          <span className="topo-palette__meta">{item.meta}</span>
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
                disabled={paletteAdding}
                onClick={onCloseAddNe}
              >
                {t("topology.discoverClose")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={paletteSelectedKeys.length === 0 || paletteAdding}
                onClick={() => void onAddSelectedPalette()}
              >
                {paletteAdding
                  ? t("topology.addingNe")
                  : t("topology.addSelected").replace(
                      "{{count}}",
                      String(paletteSelectedKeys.length),
                    )}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
