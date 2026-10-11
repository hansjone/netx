export type CliConnectProfileItem = {
  id: string;
  name: string;
  is_default: boolean;
  username: string;
  port: number;
  protocol: string;
  device_type_default: string;
  vendor_default: string;
  ne_type_rules: string;
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
  created_at: string;
  updated_at: string;
};

export type CliMeta = {
  credentials_configured: boolean;
  default_profile_configured: boolean;
  cli_profile_ready: boolean;
};

export type CliTargetItem = {
  source: string;
  id: string;
  ume_ne_id?: string | null;
  name: string;
  ip_address: string;
  ne_type?: string;
  vendor?: string;
  device_type?: string;
  protocol?: string;
  port?: number;
  username?: string;
  has_password?: boolean;
  hop_enabled?: boolean;
  connect_status: string;
  cli_profile_ready?: boolean;
  /** ManagedNE.source (lldp / webcrt / ume_sync / …). */
  ne_source?: string;
};

export type CliTargetListResponse = {
  total: number;
  page: number;
  page_size: number;
  items: CliTargetItem[];
};

export type UmeCliOverrideItem = {
  ume_ne_id: string;
  profile_id: string | null;
  username_override: string;
  device_type_override: string;
  vendor_override: string;
  connect_status: string;
  connect_message: string;
  connect_detail: string;
  connect_tested_at: string | null;
  updated_at: string;
};
