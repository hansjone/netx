import { useI18n } from "../i18n";
import { WorkbenchCardIcon } from "../components/WorkbenchCardIcon";
import { modulesInSection, type WorkbenchSection } from "../config/modules";
import { openOrFocusModule } from "../utils/moduleWindows";
import { useAuth } from "../auth/AuthContext";

const SECTIONS: WorkbenchSection[] = ["monitoring", "operations", "system"];

export function WorkbenchPage() {
  const { t } = useI18n();
  const { isAdmin, hasScope } = useAuth();

  return (
    <div className="workbench">
      {SECTIONS.map((section) => (
        <section key={section} className="wb-section">
          <h2 className="wb-section__title">{t(`workbench.${section}`)}</h2>
          <div className="wb-grid">
            {modulesInSection(section)
              .filter((mod) => !mod.workbenchHidden)
              .filter((mod) => !mod.adminOnly || isAdmin)
              .filter((mod) => !mod.requiredScope || hasScope(mod.requiredScope) || isAdmin)
              .map((mod) => (
              <button
                key={mod.moduleId}
                type="button"
                className="wb-card"
                title={t("workbench.openModule")}
                onClick={() => openOrFocusModule({ moduleId: mod.moduleId, path: mod.path })}
              >
                <WorkbenchCardIcon tone={mod.iconTone} />
                <span className="wb-card__text">
                  <span className="wb-card__label">{t(mod.labelKey)}</span>
                  {mod.descKey ? <span className="wb-card__desc">{t(mod.descKey)}</span> : null}
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
