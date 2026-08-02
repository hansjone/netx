import { useMemo } from "react";
import { useI18n } from "../i18n";
import { WorkbenchCardIcon } from "../components/WorkbenchCardIcon";
import { modulesInSection, type ModuleDefinition, type WorkbenchSection } from "../config/modules";
import { openOrFocusModule } from "../utils/moduleWindows";
import { useAuth } from "../auth/AuthContext";

const SECTIONS: WorkbenchSection[] = ["monitoring", "operations", "system"];

export function WorkbenchPage() {
  const { t } = useI18n();
  const { isAdmin, hasScope } = useAuth();

  const visibleBySection = useMemo(() => {
    const canSee = (mod: ModuleDefinition) =>
      !mod.workbenchHidden &&
      (!mod.adminOnly || isAdmin) &&
      (!mod.requiredScope || hasScope(mod.requiredScope) || isAdmin);

    return Object.fromEntries(
      SECTIONS.map((section) => [section, modulesInSection(section).filter(canSee)]),
    ) as Record<WorkbenchSection, ModuleDefinition[]>;
  }, [isAdmin, hasScope]);

  return (
    <div className="workbench">
      <header className="wb-head">
        <h1 className="wb-head__title">{t("workbench.title")}</h1>
      </header>

      {SECTIONS.map((section) => {
        const mods = visibleBySection[section];
        if (mods.length === 0) return null;
        return (
          <section key={section} className={`wb-section wb-section--${section}`}>
            <div className="wb-section__head">
              <h2 className="wb-section__title">{t(`workbench.${section}`)}</h2>
              <span className="wb-section__count">{mods.length}</span>
            </div>
            <div className="wb-grid">
              {mods.map((mod) => (
                <button
                  key={mod.moduleId}
                  type="button"
                  className={`wb-card wb-card--${mod.iconTone}`}
                  title={t("workbench.openModule")}
                  onClick={() => openOrFocusModule({ moduleId: mod.moduleId, path: mod.path })}
                >
                  <WorkbenchCardIcon tone={mod.iconTone} kind={mod.iconKind} />
                  <span className="wb-card__label">{t(mod.labelKey)}</span>
                </button>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
