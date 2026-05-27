/** React Query keys — keep stable references for invalidation. */

export const queryKeys = {
  integrationsStatus: ["integrationsStatus"] as const,
  umeTokenStatus: ["umeTokenStatus"] as const,
  umeAlarmSubscription: ["umeAlarmSubscription"] as const,
  /** Prefix keys for invalidating all pages/variants of a domain. */
  umeSyncStatusAll: ["umeSyncStatus"] as const,
  umeNEAll: ["umeNE"] as const,
  umeCurrentAlarmsAll: ["umeCurrentAlarms"] as const,
  umeSyncStatus: (page: number, pageSize: number) => ["umeSyncStatus", page, pageSize] as const,
  umeNE: (keyword: string, page: number, pageSize: number) => ["umeNE", keyword, page, pageSize] as const,
  umeCurrentAlarms: (
    severity: string,
    cleared: string,
    hostName: string,
    keyword: string,
    page: number,
    pageSize: number,
  ) => ["umeCurrentAlarms", severity, cleared, hostName, keyword, page, pageSize] as const,
};
