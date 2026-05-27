/**
 * Single registry for workbench modules. Add new modules here only.
 */

export type ModuleIconTone = "blue" | "green" | "amber";
export type WorkbenchSection = "monitoring" | "operations";

export type ModuleDefinition = {
  moduleId: string;
  path: string;
  section: WorkbenchSection;
  labelKey: string;
  descKey?: string;
  iconTone: ModuleIconTone;
  titleKey: string;
};

export const MODULES: readonly ModuleDefinition[] = [
  {
    moduleId: "ume",
    path: "/ume",
    section: "monitoring",
    labelKey: "workbench.cards.umeSync",
    descKey: "workbench.cards.umeSyncDesc",
    iconTone: "blue",
    titleKey: "layout.titleUme",
  },
  {
    moduleId: "managed-ne",
    path: "/ne",
    section: "operations",
    labelKey: "workbench.cards.managedNe",
    descKey: "workbench.cards.managedNeDesc",
    iconTone: "green",
    titleKey: "layout.titleManagedNe",
  },
  {
    moduleId: "collect",
    path: "/collect",
    section: "operations",
    labelKey: "workbench.cards.collect",
    descKey: "workbench.cards.collectDesc",
    iconTone: "amber",
    titleKey: "layout.titleCollect",
  },
] as const;

export function getModuleById(moduleId: string): ModuleDefinition | undefined {
  return MODULES.find((m) => m.moduleId === moduleId);
}

export function moduleIdFromPath(pathname: string): string | null {
  for (const m of MODULES) {
    if (pathname === m.path || pathname.startsWith(`${m.path}/`)) return m.moduleId;
  }
  return null;
}

export function getPageTitleKey(pathname: string): string {
  const moduleId = moduleIdFromPath(pathname);
  if (moduleId) {
    const mod = getModuleById(moduleId);
    if (mod) return mod.titleKey;
  }
  return "workbench.title";
}

export function modulesInSection(section: WorkbenchSection): ModuleDefinition[] {
  return MODULES.filter((m) => m.section === section);
}

export function isWorkbenchPath(pathname: string): boolean {
  return pathname === "/" || pathname === "/workbench";
}
