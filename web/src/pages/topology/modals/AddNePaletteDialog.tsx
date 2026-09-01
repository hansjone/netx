import type { DragEvent } from "react";
import { useI18n } from "../../../i18n";
import type { PaletteItem, PaletteSource } from "../pageTypes";

export type AddNePaletteDialogProps = {
  open: boolean;
  paletteSource: PaletteSource;
  onPaletteSourceChange: (source: PaletteSource) => void;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  paletteVisible: PaletteItem[];
  paletteSelectedKeys: string[];
  onPaletteSelectedKeysChange: (keys: string[] | ((prev: string[]) => string[])) => void;
  paletteLoading: boolean;
  paletteAdding: boolean;
  onClose: () => void;
  onPaletteDragStart: (e: DragEvent, item: PaletteItem) => void;
  onAddSelected: () => void;
};

export function AddNePaletteDialog({
  open,
  paletteSource,
  onPaletteSourceChange,
  keyword,
  onKeywordChange,
  paletteVisible,
  paletteSelectedKeys,
  onPaletteSelectedKeysChange,
  paletteLoading,
  paletteAdding,
  onClose,
  onPaletteDragStart,
  onAddSelected,
}: AddNePaletteDialogProps) {
  const { t } = useI18n();
  if (!open) return null;

  return (
    <div className="topo-modal" role="dialog" aria-modal="true" aria-label={t("topology.addNe")}>
      <div
        className="topo-modal__backdrop"
        onClick={() => {
          if (paletteAdding) return;
          onClose();
        }}
      />
      <div className="topo-modal__panel">
        <div className="topo-modal__head">
          <strong>{t("topology.addNe")}</strong>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={paletteAdding}
            onClick={onClose}
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
              {t("topology.selectedCount").replace("{{count}}", String(paletteSelectedKeys.length))}
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
            onClick={onClose}
          >
            {t("topology.discoverClose")}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={paletteSelectedKeys.length === 0 || paletteAdding}
            onClick={() => void onAddSelected()}
          >
            {paletteAdding
              ? t("topology.addingNe")
              : t("topology.addSelected").replace("{{count}}", String(paletteSelectedKeys.length))}
          </button>
        </div>
      </div>
    </div>
  );
}
