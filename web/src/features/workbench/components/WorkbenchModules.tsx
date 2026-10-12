import { Button } from "@heroui/react";
import type { ModuleDefinition } from "../../../config/modules";
import { useI18n } from "../../../i18n";
import { openOrFocusModule } from "../../../utils/moduleWindows";
import { WorkbenchCardIcon } from "./WorkbenchCardIcon";

export type DisplaySection = { key: "operations" | "system"; mods: ModuleDefinition[] };

/** Primary modules as a grid of tiles, system admin as a quiet link row. */
export function WorkbenchModules({ sections }: { sections: DisplaySection[] }) {
  const { t } = useI18n();

  return (
    <section className="wb-modules wb-panel" aria-label={t("workbench.modules")}>
      {sections.map((section) => (
        <div key={section.key} className={`wb-section wb-section--${section.key}`} role="group" aria-label={t(`workbench.${section.key}`)}>
          {section.mods.map((mod) => (
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
      ))}
    </section>
  );
}
