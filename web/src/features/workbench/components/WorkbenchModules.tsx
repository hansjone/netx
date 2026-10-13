import { useCallback, useEffect, useMemo, useState, type DragEvent } from "react";
import { Button } from "@heroui/react";
import type { ModuleDefinition } from "../../../config/modules";
import { useI18n } from "../../../i18n";
import { useToast } from "../../../hooks/useToast";
import { openOrFocusModule } from "../../../utils/moduleWindows";
import { useWorkbenchLayout } from "../useWorkbenchLayout";
import { ConsoleIcon } from "./ConsoleIcon";
import { WorkbenchCardIcon } from "./WorkbenchCardIcon";

type Props = {
  /** Permission-filtered customizable modules (monitoring + operations). */
  opsCandidates: ModuleDefinition[];
  systemMods: ModuleDefinition[];
};

function draftFromLayout(shown: ModuleDefinition[], available: ModuleDefinition[]) {
  return {
    order: shown.map((m) => m.moduleId),
    hidden: available.map((m) => m.moduleId),
  };
}

/** Primary modules as a grid of tiles, system admin as a quiet link row. */
export function WorkbenchModules({ opsCandidates, systemMods }: Props) {
  const { t } = useI18n();
  const { showError } = useToast();
  const { shown, available, save, reset, layoutPrefs } = useWorkbenchLayout(opsCandidates);

  const [editing, setEditing] = useState(false);
  const [draftOrder, setDraftOrder] = useState<string[]>([]);
  const [draftHidden, setDraftHidden] = useState<string[]>([]);
  const [dragId, setDragId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const byId = useMemo(() => new Map(opsCandidates.map((m) => [m.moduleId, m])), [opsCandidates]);

  const enterEdit = useCallback(() => {
    const d = draftFromLayout(shown, available);
    setDraftOrder(d.order);
    setDraftHidden(d.hidden);
    setEditing(true);
  }, [shown, available]);

  const exitEdit = useCallback(() => {
    setEditing(false);
    setDragId(null);
    setDraftOrder([]);
    setDraftHidden([]);
  }, []);

  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        exitEdit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, exitEdit]);

  const draftShown = useMemo(
    () => draftOrder.map((id) => byId.get(id)).filter((m): m is ModuleDefinition => Boolean(m)),
    [draftOrder, byId],
  );
  const draftAvailable = useMemo(
    () => draftHidden.map((id) => byId.get(id)).filter((m): m is ModuleDefinition => Boolean(m)),
    [draftHidden, byId],
  );

  const displayOps = editing ? draftShown : shown;
  const canCustomize = opsCandidates.length > 0;

  const removeTile = (moduleId: string) => {
    setDraftOrder((prev) => prev.filter((id) => id !== moduleId));
    setDraftHidden((prev) => (prev.includes(moduleId) ? prev : [...prev, moduleId]));
  };

  const addTile = (moduleId: string) => {
    setDraftHidden((prev) => prev.filter((id) => id !== moduleId));
    setDraftOrder((prev) => (prev.includes(moduleId) ? prev : [...prev, moduleId]));
  };

  const onDragStart = (moduleId: string) => (e: DragEvent) => {
    setDragId(moduleId);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", moduleId);
  };

  const onDragOver = (overId: string) => (e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (!dragId || dragId === overId) return;
    setDraftOrder((prev) => {
      const from = prev.indexOf(dragId);
      const to = prev.indexOf(overId);
      if (from < 0 || to < 0 || from === to) return prev;
      const next = [...prev];
      next.splice(from, 1);
      next.splice(to, 0, dragId);
      return next;
    });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragId(null);
  };

  const defaults = useMemo(
    () => ({
      order: opsCandidates.filter((m) => !m.defaultHidden).map((m) => m.moduleId),
      hidden: opsCandidates.filter((m) => m.defaultHidden).map((m) => m.moduleId),
    }),
    [opsCandidates],
  );

  const onDone = async () => {
    setSaving(true);
    try {
      const sameAsDefault =
        draftOrder.join(",") === defaults.order.join(",") &&
        [...draftHidden].sort().join(",") === [...defaults.hidden].sort().join(",");
      if (sameAsDefault) {
        // Store nothing so later registry changes (new modules, reordering) still apply.
        await reset();
      } else {
        // Keep entries for modules this user can't see right now (e.g. scope temporarily
        // revoked) so their choice survives until the permission comes back.
        const keepOrder = (layoutPrefs?.order ?? []).filter((id) => !byId.has(id));
        const keepHidden = (layoutPrefs?.hidden ?? []).filter((id) => !byId.has(id));
        await save([...draftOrder, ...keepOrder], [...draftHidden, ...keepHidden]);
      }
      exitEdit();
    } catch {
      showError(t("workbench.customizeSaveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const onReset = () => {
    setDraftOrder(defaults.order);
    setDraftHidden(defaults.hidden);
  };

  return (
    <section
      className={`wb-modules wb-panel${editing ? " wb-modules--editing" : ""}`}
      aria-label={t("workbench.modules")}
    >
      {canCustomize ? (
        <div className="wb-modules__toolbar">
          {!editing ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="wb-modules__gear"
              aria-label={t("workbench.customize")}
              onPress={enterEdit}
            >
              <ConsoleIcon kind="settings" />
            </Button>
          ) : null}
        </div>
      ) : null}

      {canCustomize || displayOps.length > 0 ? (
        <div className="wb-section wb-section--operations" role="group" aria-label={t("workbench.operations")}>
          {displayOps.length === 0 && !editing ? (
            <div className="wb-modules__empty">
              <Button type="button" variant="ghost" className="wb-modules__empty-btn" onPress={enterEdit}>
                {t("workbench.customizeEmpty")}
              </Button>
            </div>
          ) : (
            displayOps.map((mod) => (
              <div
                key={mod.moduleId}
                className={`wb-tile-wrap${editing ? " wb-tile-wrap--edit" : ""}${dragId === mod.moduleId ? " is-dragging" : ""}`}
                // Launcher labels may be abbreviated; hover shows the full module title.
                title={editing ? undefined : t(mod.titleKey)}
                draggable={editing}
                onDragStart={editing ? onDragStart(mod.moduleId) : undefined}
                onDragOver={editing ? onDragOver(mod.moduleId) : undefined}
                onDrop={editing ? onDrop : undefined}
                onDragEnd={() => setDragId(null)}
              >
                <Button
                  variant="ghost"
                  className={`wb-tile wb-tile--${mod.iconTone}`}
                  aria-label={
                    editing
                      ? t(mod.titleKey)
                      : `${t(mod.titleKey)} · ${t("workbench.openModule")}`
                  }
                  onPress={() => {
                    if (editing) return;
                    openOrFocusModule({ moduleId: mod.moduleId, path: mod.path });
                  }}
                >
                  <WorkbenchCardIcon tone={mod.iconTone} kind={mod.iconKind} />
                  <span className="wb-tile__label">{t(mod.labelKey)}</span>
                </Button>
                {editing ? (
                  <button
                    type="button"
                    className="wb-tile__remove"
                    aria-label={`${t(mod.labelKey)} ×`}
                    onClick={() => removeTile(mod.moduleId)}
                  >
                    <ConsoleIcon kind="close" />
                  </button>
                ) : null}
              </div>
            ))
          )}
        </div>
      ) : null}

      {editing && draftAvailable.length > 0 ? (
        <div className="wb-modules__tray" role="group" aria-label={t("workbench.customizeAvailable")}>
          <div className="wb-modules__tray-label">{t("workbench.customizeAvailable")}</div>
          <div className="wb-modules__tray-grid">
            {draftAvailable.map((mod) => (
              <Button
                key={mod.moduleId}
                variant="ghost"
                className={`wb-tile wb-tile--${mod.iconTone} wb-tile--available`}
                aria-label={t(mod.labelKey)}
                onPress={() => addTile(mod.moduleId)}
              >
                <WorkbenchCardIcon tone={mod.iconTone} kind={mod.iconKind} />
                <span className="wb-tile__label">{t(mod.labelKey)}</span>
              </Button>
            ))}
          </div>
        </div>
      ) : null}

      {editing ? (
        <div className="wb-modules__actions">
          <Button type="button" variant="ghost" size="sm" onPress={onReset} isDisabled={saving}>
            {t("workbench.customizeReset")}
          </Button>
          <Button type="button" variant="primary" size="sm" onPress={() => void onDone()} isDisabled={saving}>
            {t("workbench.customizeDone")}
          </Button>
        </div>
      ) : null}

      {systemMods.length > 0 ? (
        <div className="wb-section wb-section--system" role="group" aria-label={t("workbench.system")}>
          {systemMods.map((mod) => (
            <Button
              key={mod.moduleId}
              variant="ghost"
              className={`wb-tile wb-tile--${mod.iconTone}`}
              aria-label={`${t(mod.labelKey)} · ${t("workbench.openModule")}`}
              onPress={() => openOrFocusModule({ moduleId: mod.moduleId, path: mod.path })}
            >
              <WorkbenchCardIcon tone={mod.iconTone} kind={mod.iconKind} />
              <span className="wb-tile__label">{t(mod.labelKey)}</span>
            </Button>
          ))}
        </div>
      ) : null}
    </section>
  );
}
