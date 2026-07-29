/** React Query keys — keep stable references for invalidation. */

export const queryKeys = {
  integrationsStatus: ["integrationsStatus"] as const,
  umeTokenStatus: ["umeTokenStatus"] as const,
  umeAlarmSubscription: ["umeAlarmSubscription"] as const,
  umeKeyAlertMonitorAll: ["umeKeyAlertMonitor"] as const,
  umeInventoryNeTypes: ["umeInventoryNeTypes"] as const,
  umeKeyAlertMonitor: (
    page: number,
    pageSize: number,
    keyword: string,
    enabled: string,
    matchType: string,
  ) => ["umeKeyAlertMonitor", page, pageSize, keyword, enabled, matchType] as const,
  /** Prefix keys for invalidating all pages/variants of a domain. */
  umeSyncStatusAll: ["umeSyncStatus"] as const,
  umeNEAll: ["umeNE"] as const,
  umeCurrentAlarmsAll: ["umeCurrentAlarms"] as const,
  umeSyncStatus: (page: number, pageSize: number) => ["umeSyncStatus", page, pageSize] as const,
  umeNE: (keyword: string, page: number, pageSize: number) => ["umeNE", keyword, page, pageSize] as const,
  managedNeMeta: ["managedNeMeta"] as const,
  managedNeStats: ["managedNeStats"] as const,
  managedNeAll: ["managedNe"] as const,
  managedNe: (keyword: string, vendor: string, connectStatus: string, page: number, pageSize: number) =>
    ["managedNe", keyword, vendor, connectStatus, page, pageSize] as const,
  collectionEligibleNeAll: ["collectionEligibleNe"] as const,
  collectionEligibleNe: (page: number, keyword: string) => ["collectionEligibleNe", page, keyword] as const,
  neCollectionsAll: ["neCollections"] as const,
  neCollections: (page: number) => ["neCollections", page] as const,
  neCollectionDetail: (jobId: string) => ["neCollection", jobId] as const,
  neCollectionRunsAll: ["neCollectionRuns"] as const,
  neCollectionRuns: (jobId: string, page: number, status: string, keyword: string) =>
    ["neCollectionRuns", jobId, page, status, keyword] as const,
  cliMeta: ["cliMeta"] as const,
  cliProfiles: ["cliProfiles"] as const,
  cliTargetsAll: ["cliTargets"] as const,
  cliTargets: (keyword: string, page: number, pageSize: number) =>
    ["cliTargets", keyword, page, pageSize] as const,
  umeCliOverride: (umeNeId: string) => ["umeCliOverride", umeNeId] as const,
  umeCurrentAlarms: (
    severity: string,
    cleared: string,
    hostName: string,
    keyword: string,
    page: number,
    pageSize: number,
  ) => ["umeCurrentAlarms", severity, cleared, hostName, keyword, page, pageSize] as const,
  topologyMaps: ["topologyMaps"] as const,
  topologyGraph: (mapId: string) => ["topologyGraph", mapId] as const,
};
