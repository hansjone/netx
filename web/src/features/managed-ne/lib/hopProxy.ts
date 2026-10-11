/** Jump-host (hop) templates per vendor. */

import { ciscoHopTemplate, huaweiHopTemplate, isAutoHopTemplate, zteHopTemplate } from "./zteHop";

export type HopVendor = "zte" | "huawei" | "cisco" | "linux" | "bastion";

export type HopTargetAuthMode = "bastion_managed" | "manual";

export const HOP_VENDORS: HopVendor[] = ["zte", "huawei", "cisco", "linux", "bastion"];

export function bastionHopTemplate(): string {
  return "{hop_user}@{target_user}@{target_ip}";
}

export const LEGACY_BASTION_HOP_TEMPLATE = "{hop_user}@{target_user}@{target_ip}@{hop_host}";

export function isBastionHopVendor(vendor: string): boolean {
  return String(vendor || "").toLowerCase() === "bastion";
}

export function isLinuxHopVendor(vendor: string): boolean {
  return String(vendor || "").toLowerCase() === "linux";
}

export function isCliHopVendor(vendor: string): boolean {
  const v = String(vendor || "").toLowerCase();
  return v === "zte" || v === "huawei" || v === "cisco";
}

/** Strip ssh:// / trailing slash / :port from bastion host (IP or FQDN). */
export function normalizeHopHost(value: string): string {
  let host = String(value || "").trim();
  if (!host) return "";
  host = host.replace(/^ssh(?:\s+-p\s+\d+)?\s+/i, "").trim();
  if (host.includes("://")) host = host.split("://", 2)[1] || host;
  host = host.trim().replace(/\/+$/, "");
  if (host.startsWith("[") && host.includes("]")) {
    const inside = host.slice(1, host.indexOf("]"));
    const rest = host.slice(host.indexOf("]") + 1);
    if (!rest || rest.startsWith(":")) return inside.trim();
    return host;
  }
  if ((host.match(/:/g) || []).length === 1) {
    const [left, right] = host.split(":");
    if (/^\d+$/.test(right || "") && left && !left.includes("@")) return left.trim();
  }
  return host;
}

/**
 * Parse OpenSSH-style bastion destination.
 * Example: bastion-user@target-user@198.51.100.20@ssh-bastion.example.com
 */
export function parseBastionSshDestination(value: string): {
  hop_host: string;
  hop_username: string;
  target_user: string;
  target_ip: string;
  ssh_username: string;
} {
  let raw = String(value || "").trim().replace(/^["']|["']$/g, "");
  raw = raw.replace(/^ssh(?:\s+-p\s+\d+)?\s+/i, "").trim();
  if (!raw) {
    return { hop_host: "", hop_username: "", target_user: "", target_ip: "", ssh_username: "" };
  }
  if (!raw.includes("@")) {
    return {
      hop_host: normalizeHopHost(raw),
      hop_username: "",
      target_user: "",
      target_ip: "",
      ssh_username: "",
    };
  }
  const at = raw.lastIndexOf("@");
  const userPart = raw.slice(0, at).trim();
  const hopHost = normalizeHopHost(raw.slice(at + 1));
  const parts = userPart.split("@").filter(Boolean);
  return {
    hop_host: hopHost,
    hop_username: parts[0] || "",
    target_user: parts[1] || "",
    target_ip: parts[2] || "",
    ssh_username: userPart,
  };
}

/** If hop_host is a pasted user@…@bastion string, split into host + hop username. */
export function expandBastionHopFields(
  hopHost: string,
  hopUsername = "",
): {
  hop_host: string;
  hop_username: string;
  target_user: string;
  target_ip: string;
} {
  const curUser = String(hopUsername || "").trim();
  const raw = String(hopHost || "").trim();
  if (!raw.includes("@")) {
    return { hop_host: normalizeHopHost(raw), hop_username: curUser, target_user: "", target_ip: "" };
  }
  const parsed = parseBastionSshDestination(raw);
  return {
    hop_host: parsed.hop_host,
    hop_username: parsed.hop_username || curUser,
    target_user: parsed.target_user,
    target_ip: parsed.target_ip,
  };
}

export function defaultHopTemplate(vendor: string, protocol: string, vrf: string): string {
  const v = String(vendor || "zte").toLowerCase();
  if (v === "huawei") return huaweiHopTemplate(protocol, vrf);
  if (v === "cisco") return ciscoHopTemplate(protocol, vrf);
  if (v === "linux") return "";
  if (v === "bastion") return bastionHopTemplate();
  return zteHopTemplate(protocol, vrf);
}

export function patchHopVendorChange(
  vendor: HopVendor,
  prev: { hop_protocol: string; hop_vrf: string; hop_command_template: string; hop_vendor?: string },
): {
  hop_vendor: HopVendor;
  hop_protocol: string;
  hop_vrf: string;
  hop_command_template: string;
  hop_port?: number;
  hop_target_auth_mode?: HopTargetAuthMode;
  hop_enter_system_view?: boolean;
} {
  if (vendor === "linux") {
    return {
      hop_vendor: "linux",
      hop_protocol: "ssh",
      hop_vrf: "",
      hop_command_template: "",
      hop_enter_system_view: false,
    };
  }
  if (vendor === "bastion") {
    return {
      hop_vendor: "bastion",
      hop_protocol: "ssh",
      hop_port: 22,
      hop_vrf: "",
      hop_command_template: bastionHopTemplate(),
      hop_target_auth_mode: "bastion_managed" as HopTargetAuthMode,
      hop_enter_system_view: false,
    };
  }
  const protocol = prev.hop_protocol || "ssh";
  const vrf = prev.hop_vrf || "";
  return {
    hop_vendor: vendor,
    hop_protocol: protocol,
    hop_vrf: vrf,
    hop_command_template: defaultHopTemplate(vendor, protocol, vrf),
    // Explicit Huawei option only; reset when leaving/entering other CLI hop vendors.
    hop_enter_system_view: false,
  };
}

export { ciscoHopTemplate, huaweiHopTemplate, isAutoHopTemplate, zteHopTemplate };
