export type ConnectStatus = "unknown" | "testing" | "pass" | "fail";

export type ManagedNeItem = {
  id: string;
  name: string;
  vendor: string;
  device_type: string;
  ip_address: string;
  port: number;
  protocol: string;
  username: string;
  /** True when a password is stored server-side (secret never returned). */
  has_password?: boolean;
  connect_status: ConnectStatus;
  connect_message: string;
  connect_detail: string;
  connect_tested_at: string | null;
  tags: string;
  remark: string;
  source?: string;
  source_ref?: string;
  hop_enabled: boolean;
  hop_vendor: string;
  hop_host: string;
  hop_port: number;
  hop_protocol: string;
  hop_username: string;
  hop_command_template: string;
  hop_vrf: string;
  hop_target_auth_mode: string;
  hop_enter_system_view?: boolean;
  /** MCP/API CLI gate: readonly | linux_shell | unrestricted */
  exec_policy?: "readonly" | "linux_shell" | "unrestricted";
  created_at: string;
  updated_at: string;
};

export type ManagedNeListResponse = {
  total: number;
  page: number;
  page_size: number;
  items: ManagedNeItem[];
};

export type ManagedNeMeta = {
  device_types: string[];
  vendors: string[];
  /** When false (default), exec_policy UI/API open policies are disabled. */
  exec_policy_enabled?: boolean;
};

export type ManagedNeImportResult = {
  inserted: number;
  updated: number;
  failed: Array<{ row: number; reason: string }>;
};
