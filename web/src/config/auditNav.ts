export type AuditNavItem = {
  id: string;
  path: string;
  labelKey: string;
};

export const AUDIT_NAV: readonly AuditNavItem[] = [
  { id: "tasks", path: "/audit/tasks", labelKey: "audit.nav.tasks" },
  { id: "logs", path: "/audit/logs", labelKey: "audit.nav.logs" },
] as const;
