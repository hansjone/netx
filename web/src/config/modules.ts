/**
 * Single registry for workbench modules. Add new modules here only.
 */

export type ModuleIconTone = "blue" | "green" | "amber" | "slate";
export type WorkbenchSection = "monitoring" | "operations" | "system";

export type ModuleDefinition = {
  moduleId: string;
  path: string;
  section: WorkbenchSection;
  labelKey: string;
  descKey?: string;
  iconTone: ModuleIconTone;
  titleKey: string;
  adminOnly?: boolean;
  /** Hide from workbench launcher; still used for module window registration. */
  workbenchHidden?: boolean;
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
    moduleId: "ne",
    path: "/ne",
    section: "operations",
    labelKey: "workbench.cards.managedNe",
    descKey: "workbench.cards.managedNeDesc",
    iconTone: "green",
    titleKey: "layout.titleManagedNe",
  },
  {
    moduleId: "network",
    path: "/network",
    section: "operations",
    labelKey: "workbench.cards.network",
    descKey: "workbench.cards.networkDesc",
    iconTone: "slate",
    titleKey: "layout.titleNetwork",
  },
  {
    moduleId: "topology",
    path: "/topology",
    section: "operations",
    labelKey: "workbench.cards.topology",
    descKey: "workbench.cards.topologyDesc",
    iconTone: "amber",
    titleKey: "layout.titleTopology",
  },
  {
    moduleId: "webcrt",
    path: "/webcrt",
    section: "operations",
    labelKey: "workbench.cards.webcrt",
    descKey: "workbench.cards.webcrtDesc",
    iconTone: "slate",
    titleKey: "layout.titleWebcrt",
  },
  {
    moduleId: "port-traffic-wall",
    path: "/port-traffic/wall",
    section: "operations",
    labelKey: "workbench.cards.portTrafficWall",
    descKey: "workbench.cards.portTrafficWallDesc",
    iconTone: "amber",
    titleKey: "layout.titlePortTrafficWall",
    workbenchHidden: true,
  },
  {
    moduleId: "users",
    path: "/users",
    section: "system",
    labelKey: "workbench.cards.users",
    descKey: "workbench.cards.usersDesc",
    iconTone: "slate",
    titleKey: "layout.titleUsers",
    adminOnly: true,
  },
  {
    moduleId: "audit",
    path: "/audit",
    section: "system",
    labelKey: "workbench.cards.audit",
    descKey: "workbench.cards.auditDesc",
    iconTone: "amber",
    titleKey: "layout.titleAudit",
  },
  {
    moduleId: "api-keys",
    path: "/api-keys",
    section: "system",
    labelKey: "workbench.cards.apiKeys",
    descKey: "workbench.cards.apiKeysDesc",
    iconTone: "green",
    titleKey: "layout.titleApiKeys",
  },
] as const satisfies readonly ModuleDefinition[];

export function getModuleById(moduleId: string): ModuleDefinition | undefined {
  return MODULES.find((m) => m.moduleId === moduleId);
}

export function moduleIdFromPath(pathname: string): string | null {
  // Longer paths first so /port-traffic/wall wins over shorter prefixes if added later.
  const ordered = [...MODULES].sort((a, b) => b.path.length - a.path.length);
  for (const m of ordered) {
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
