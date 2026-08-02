/**
 * Left-nav tree for the Network Management module.
 * Add leaf items here when extending (devices / topology / alarms / tasks).
 */

export type NetworkNavGroupId = "detail" | "topologyMgmt" | "tasks";

export type NetworkNavItem = {
  id: string;
  path: string;
  labelKey: string;
  group: NetworkNavGroupId;
};

export type NetworkNavGroup = {
  id: NetworkNavGroupId;
  labelKey: string;
  items: NetworkNavItem[];
};

export const NETWORK_NAV: readonly NetworkNavGroup[] = [
  {
    id: "detail",
    labelKey: "network.nav.detail",
    items: [
      { id: "devices", path: "/network/devices", labelKey: "network.nav.devices", group: "detail" },
      { id: "alarms", path: "/network/alarms", labelKey: "network.nav.alarms", group: "detail" },
      { id: "configs", path: "/network/configs", labelKey: "network.nav.configs", group: "detail" },
    ],
  },
  {
    id: "topologyMgmt",
    labelKey: "network.nav.topologyMgmt",
    items: [
      {
        id: "lldp-links",
        path: "/network/topology/lldp",
        labelKey: "network.nav.lldpLinks",
        group: "topologyMgmt",
      },
      {
        id: "topo-classify",
        path: "/network/topology/classify",
        labelKey: "network.nav.topoClassify",
        group: "topologyMgmt",
      },
    ],
  },
  {
    id: "tasks",
    labelKey: "network.nav.tasks",
    items: [
      {
        id: "collect",
        path: "/network/tasks/collect",
        labelKey: "network.nav.collectTasks",
        group: "tasks",
      },
      {
        id: "config-sync",
        path: "/network/tasks/config-sync",
        labelKey: "network.nav.configSync",
        group: "tasks",
      },
      {
        id: "port-traffic",
        path: "/network/tasks/port-traffic",
        labelKey: "network.nav.portTraffic",
        group: "tasks",
      },
      {
        id: "port-traffic-wall",
        path: "/network/tasks/port-traffic/wall",
        labelKey: "network.nav.portTrafficWall",
        group: "tasks",
      },
    ],
  },
] as const;
