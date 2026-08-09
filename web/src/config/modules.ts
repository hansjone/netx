/**
 * Single registry for workbench modules. Add new modules here only.
 */

export type ModuleIconTone = "blue" | "green" | "amber" | "slate";
export type ModuleIconKind =
  | "sync"
  | "server"
  | "network"
  | "topology"
  | "terminal"
  | "wall"
  | "users"
  | "audit"
  | "key";
export type WorkbenchSection = "monitoring" | "operations" | "system";

export type ModuleDefinition = {
  moduleId: string;
  path: string;
  section: WorkbenchSection;
  labelKey: string;
  iconTone: ModuleIconTone;
  iconKind: ModuleIconKind;
  titleKey: string;
  /** Required capability scope to show in workbench (admin bypasses). */
  requiredScope?: string;
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
    iconTone: "blue",
    iconKind: "sync",
    titleKey: "layout.titleUme",
    requiredScope: "alarms:read",
  },
  {
    moduleId: "ne",
    path: "/ne",
    section: "operations",
    labelKey: "workbench.cards.managedNe",
    iconTone: "green",
    iconKind: "server",
    titleKey: "layout.titleManagedNe",
    requiredScope: "ne:read",
  },
  {
    moduleId: "network",
    path: "/network",
    section: "operations",
    labelKey: "workbench.cards.network",
    iconTone: "slate",
    iconKind: "network",
    titleKey: "layout.titleNetwork",
    requiredScope: "ne:read",
  },
  {
    moduleId: "topology",
    path: "/topology",
    section: "operations",
    labelKey: "workbench.cards.topology",
    iconTone: "amber",
    iconKind: "topology",
    titleKey: "layout.titleTopology",
    requiredScope: "ne:read",
  },
  {
    moduleId: "webcrt",
    path: "/webcrt",
    section: "operations",
    labelKey: "workbench.cards.webcrt",
    iconTone: "slate",
    iconKind: "terminal",
    titleKey: "layout.titleWebcrt",
    requiredScope: "webcrt:session",
  },
  {
    moduleId: "port-traffic-wall",
    path: "/port-traffic/wall",
    section: "operations",
    labelKey: "workbench.cards.portTrafficWall",
    iconTone: "amber",
    iconKind: "wall",
    titleKey: "layout.titlePortTrafficWall",
    workbenchHidden: true,
    requiredScope: "ne:read",
  },
  {
    moduleId: "users",
    path: "/users",
    section: "system",
    labelKey: "workbench.cards.users",
    iconTone: "slate",
    iconKind: "users",
    titleKey: "layout.titleUsers",
    adminOnly: true,
  },
  {
    moduleId: "audit",
    path: "/audit",
    section: "system",
    labelKey: "workbench.cards.audit",
    iconTone: "amber",
    iconKind: "audit",
    titleKey: "layout.titleAudit",
  },
  {
    moduleId: "api-keys",
    path: "/api-keys",
    section: "system",
    labelKey: "workbench.cards.apiKeys",
    iconTone: "green",
    iconKind: "key",
    titleKey: "layout.titleApiKeys",
  },
  {
    moduleId: "sessions",
    path: "/sessions",
    section: "system",
    labelKey: "workbench.cards.sessions",
    iconTone: "slate",
    iconKind: "key",
    titleKey: "layout.titleSessions",
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
  if (pathname.startsWith("/audit/tasks")) return "audit.tasks.title";
  if (pathname.startsWith("/audit/logs") || pathname === "/audit") return "audit.logsTitle";
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
