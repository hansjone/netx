export type DshAlarmHubConnection = {
  id: string;
  user: string;
  remote?: string;
  client?: string;
  connected_at?: string;
  last_seen_at?: string;
};

export type DshAlarmHubStatus = {
  enabled: boolean;
  path?: string;
  subscribers: number;
  published?: number;
  deliver_ok?: number;
  deliver_fail?: number;
  connections?: DshAlarmHubConnection[];
};

export type IntegrationStatus = {
  netx_api: { status: "up" | "down" | "unknown"; [k: string]: unknown };
  db: { status: "up" | "down" | "unknown"; latency_ms?: number; error?: string; [k: string]: unknown };
  dsh_alarm_hub?: DshAlarmHubStatus;
};
