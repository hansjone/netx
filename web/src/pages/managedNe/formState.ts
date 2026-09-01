import {
  defaultHopTemplate,
  isAutoHopTemplate,
  type HopVendor,
} from "../../utils/hopProxy";
import type { ManagedNeItem } from "../../types";

export type ManagedNeFormState = {
  name: string;
  vendor: string;
  device_type: string;
  ip_address: string;
  port: number;
  protocol: string;
  username: string;
  password: string;
  tags: string;
  remark: string;
  hop_enabled: boolean;
  hop_vendor: HopVendor;
  hop_host: string;
  hop_port: number;
  hop_protocol: string;
  hop_username: string;
  hop_password: string;
  hop_command_template: string;
  hop_vrf: string;
  hop_target_auth_mode: "bastion_managed" | "manual";
  hop_enter_system_view: boolean;
};

export function deviceTypeForVendor(vendor: string): string {
  if (vendor === "ZTE") return "zte_zxros";
  if (vendor === "Huawei") return "huawei";
  if (vendor === "Cisco") return "cisco_ios";
  if (vendor === "Juniper") return "juniper_junos";
  if (vendor === "Nokia") return "nokia_sros";
  return "generic";
}

export function emptyManagedNeForm(): ManagedNeFormState {
  return {
    name: "",
    vendor: "ZTE",
    device_type: "zte_zxros",
    ip_address: "",
    port: 22,
    protocol: "ssh",
    username: "",
    password: "",
    tags: "",
    remark: "",
    hop_enabled: false,
    hop_vendor: "zte",
    hop_host: "",
    hop_port: 22,
    hop_protocol: "ssh",
    hop_username: "",
    hop_password: "",
    hop_command_template: defaultHopTemplate("zte", "ssh", ""),
    hop_vrf: "",
    hop_target_auth_mode: "bastion_managed",
    hop_enter_system_view: false,
  };
}

export function applyHopTemplate(
  prev: ManagedNeFormState,
  protocol: string,
  vrf: string,
  force = false,
): Partial<ManagedNeFormState> {
  if (
    !force &&
    !isAutoHopTemplate(prev.hop_command_template, prev.hop_vendor, prev.hop_protocol, prev.hop_vrf)
  ) {
    return {};
  }
  return { hop_command_template: defaultHopTemplate(prev.hop_vendor, protocol, vrf) };
}

export function formFromManagedNe(row: ManagedNeItem): ManagedNeFormState {
  const hopVendor = (["linux", "huawei", "cisco", "zte", "bastion"].includes(row.hop_vendor)
    ? row.hop_vendor
    : "zte") as HopVendor;
  return {
    name: row.name,
    vendor: row.vendor,
    device_type: row.device_type,
    ip_address: row.ip_address,
    port: row.port,
    protocol: row.protocol,
    username: row.username,
    password: "",
    tags: row.tags,
    remark: row.remark,
    hop_enabled: row.hop_enabled,
    hop_vendor: hopVendor,
    hop_host: row.hop_host,
    hop_port: row.hop_port,
    hop_protocol: row.hop_protocol,
    hop_username: row.hop_username,
    hop_password: "",
    hop_command_template: isAutoHopTemplate(
      row.hop_command_template,
      row.hop_vendor,
      row.hop_protocol,
      row.hop_vrf,
    )
      ? defaultHopTemplate(row.hop_vendor, row.hop_protocol, row.hop_vrf)
      : row.hop_command_template || defaultHopTemplate(row.hop_vendor, row.hop_protocol, row.hop_vrf),
    hop_vrf: row.hop_vrf,
    hop_target_auth_mode: row.hop_target_auth_mode === "manual" ? "manual" : "bastion_managed",
    hop_enter_system_view: Boolean(row.hop_enter_system_view),
  };
}

export function managedSourceKey(
  source: string | undefined,
): "manual" | "ume_sync" | "webcrt" | "lldp" | "topology" | "" {
  const s = String(source || "").trim().toLowerCase();
  if (!s) return "manual";
  if (s === "ume_sync" || s === "webcrt" || s === "lldp" || s === "topology") return s;
  return "";
}

/** Build API body + hop validation. Throws Error with message key text already translated by caller. */
export function buildManagedNeSaveBody(
  form: ManagedNeFormState,
  opts: { editing: boolean; hopHostRequired: string; hopUserRequired: string; hopPasswordRequired: string },
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: form.name,
    vendor: form.vendor,
    device_type: form.device_type,
    ip_address: form.ip_address,
    port: form.port,
    protocol: form.protocol,
    username: form.username,
    tags: form.tags,
    remark: form.remark,
    hop_enabled: form.hop_enabled,
    hop_vendor: form.hop_vendor,
    hop_host: form.hop_host,
    hop_port: form.hop_port,
    hop_protocol: form.hop_protocol,
    hop_username: form.hop_username,
    hop_command_template: form.hop_command_template,
    hop_vrf: form.hop_vrf,
    hop_target_auth_mode: form.hop_target_auth_mode,
    hop_enter_system_view: form.hop_enter_system_view,
    ...(form.password ? { password: form.password } : {}),
    ...(form.hop_password ? { hop_password: form.hop_password } : {}),
  };
  if (form.hop_enabled) {
    if (!form.hop_host.trim()) throw new Error(opts.hopHostRequired);
    if (!form.hop_username.trim()) throw new Error(opts.hopUserRequired);
    if (!opts.editing && !form.hop_password) throw new Error(opts.hopPasswordRequired);
  }
  if (opts.editing) {
    if (!form.password) delete body.password;
    if (!form.hop_password) delete body.hop_password;
  } else {
    body.password = form.password || "";
  }
  return body;
}
