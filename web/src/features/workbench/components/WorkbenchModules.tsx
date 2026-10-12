import { useMemo, useState } from "react";
import { Button } from "@heroui/react";
import type { ModuleDefinition } from "../../../config/modules";
import { useI18n } from "../../../i18n";
import { openOrFocusModule } from "../../../utils/moduleWindows";
import { ConsoleIcon } from "./ConsoleIcon";
import { WorkbenchCardIcon } from "./WorkbenchCardIcon";

export type DisplaySection = { key: "operations" | "system"; mods: ModuleDefinition[] };

function moduleDescription(mod: ModuleDefinition, t: (key: string) => string) {
  const key = `workbench.descriptions.${mod.moduleId}`;
  const description = t(key);
  return description === key ? t(mod.titleKey) : description;
}

export function WorkbenchModules({ sections }: { sections: DisplaySection[] }) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => {
    const term = search.trim().toLocaleLowerCase();
    return sections.map((section) => ({
      ...section,
      mods: section.mods.filter((mod) =>
        [t(mod.labelKey), moduleDescription(mod, t), mod.moduleId]
          .some((value) => value.toLocaleLowerCase().includes(term)),
      ),
    })).filter((section) => section.mods.length > 0);
  }, [sections, search, t]);
  const count = filtered.reduce((sum, section) => sum + section.mods.length, 0);

  return (
    <section className="wb-modules wb-panel" aria-label={t("workbench.modules")}>
      <div className="wb-modules__head">
        <div>
          <h2 className="wb-panel-title">{t("workbench.modules")}</h2>
        </div>
        <div className="wb-search">
          <ConsoleIcon kind="search" />
          <input
            type="search"
            value={search}
            aria-label={t("workbench.searchModules")}
            placeholder={t("workbench.searchModules")}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Escape") setSearch(""); }}
          />
          {search && <Button isIconOnly size="sm" variant="ghost" className="wb-search__clear" aria-label={t("workbench.clearSearch")} onPress={() => setSearch("")}><ConsoleIcon kind="close" /></Button>}
        </div>
      </div>
      <span className="wb-sr-only" role="status">{t("workbench.moduleCount", { count })}</span>
      {filtered.map((section) => (
        <div key={section.key} className={`wb-section wb-section--${section.key}`}>
          <div className={section.key === "operations" ? "wb-sr-only" : "wb-section__head"}>
            <h3 className="wb-section__title">{t(`workbench.${section.key}`)}</h3>
          </div>
          <div className="wb-tiles">
            {section.mods.map((mod) => (
              <Button key={mod.moduleId} variant="ghost" className={`wb-tile wb-tile--${mod.iconTone}`} aria-label={`${t(mod.labelKey)} · ${t("workbench.openModule")}`} onPress={() => openOrFocusModule({ moduleId: mod.moduleId, path: mod.path })}>
                <WorkbenchCardIcon tone={mod.iconTone} kind={mod.iconKind} />
                <span className="wb-tile__label">{t(mod.labelKey)}</span>
              </Button>
            ))}
          </div>
        </div>
      ))}
      {count === 0 && <div className="wb-modules__empty"><ConsoleIcon kind="search" /><p>{t("workbench.noModules")}</p><Button size="sm" variant="ghost" onPress={() => setSearch("")}>{t("workbench.clearSearch")}</Button></div>}
    </section>
  );
}
