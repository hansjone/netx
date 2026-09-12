import type { DragEvent } from "react";
import { Button, Chip, Input, Modal } from "@heroui/react";
import { TopoModalShell } from "../../../components/ui/TopoModalShell";
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

  return (
    <TopoModalShell open={open} onClose={onClose} dismissible={!paletteAdding} size="md">
      <Modal.Header>
        <Modal.Heading>{t("topology.addNe")}</Modal.Heading>
        <Modal.CloseTrigger isDisabled={paletteAdding} />
      </Modal.Header>
      <Modal.Body className="flex flex-col gap-3">
        <p className="text-sm text-muted">{t("topology.paletteHint")}</p>
        <div className="topo-palette-source flex gap-2" role="tablist">
          <Button
            size="sm"
            variant={paletteSource === "managed" ? "primary" : "secondary"}
            isDisabled={paletteAdding}
            className="flex-1"
            onPress={() => onPaletteSourceChange("managed")}
          >
            {t("topology.paletteManaged")}
          </Button>
          <Button
            size="sm"
            variant={paletteSource === "ume" ? "primary" : "secondary"}
            isDisabled={paletteAdding}
            className="flex-1"
            onPress={() => onPaletteSourceChange("ume")}
          >
            {t("topology.paletteUme")}
          </Button>
        </div>
        <Input
          className="w-full"
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
            <Chip size="sm" variant="soft">
              <Chip.Label>
                {t("topology.selectedCount").replace("{{count}}", String(paletteSelectedKeys.length))}
              </Chip.Label>
            </Chip>
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
      </Modal.Body>
      <Modal.Footer>
        <Button variant="tertiary" size="sm" isDisabled={paletteAdding} onPress={onClose}>
          {t("topology.discoverClose")}
        </Button>
        <Button
          variant="primary"
          size="sm"
          isDisabled={paletteSelectedKeys.length === 0 || paletteAdding}
          onPress={() => void onAddSelected()}
        >
          {paletteAdding
            ? t("topology.addingNe")
            : t("topology.addSelected").replace("{{count}}", String(paletteSelectedKeys.length))}
        </Button>
      </Modal.Footer>
    </TopoModalShell>
  );
}
