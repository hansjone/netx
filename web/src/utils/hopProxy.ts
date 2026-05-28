/** Jump-host (hop) templates per vendor. */

import { ciscoHopTemplate, huaweiHopTemplate, isAutoHopTemplate, zteHopTemplate } from "./zteHop";

export type HopVendor = "zte" | "huawei" | "cisco" | "linux";

export const HOP_VENDORS: HopVendor[] = ["zte", "huawei", "cisco", "linux"];

export function isLinuxHopVendor(vendor: string): boolean {
  return String(vendor || "").toLowerCase() === "linux";
}

export function isCliHopVendor(vendor: string): boolean {
  const v = String(vendor || "").toLowerCase();
  return v === "zte" || v === "huawei" || v === "cisco";
}

export function defaultHopTemplate(vendor: string, protocol: string, vrf: string): string {
  const v = String(vendor || "zte").toLowerCase();
  if (v === "huawei") return huaweiHopTemplate(protocol, vrf);
  if (v === "cisco") return ciscoHopTemplate(protocol, vrf);
  if (v === "linux") return "";
  return zteHopTemplate(protocol, vrf);
}

export function patchHopVendorChange(
  vendor: HopVendor,
  prev: { hop_protocol: string; hop_vrf: string; hop_command_template: string; hop_vendor?: string },
): { hop_vendor: HopVendor; hop_protocol: string; hop_vrf: string; hop_command_template: string } {
  if (vendor === "linux") {
    return { hop_vendor: "linux", hop_protocol: "ssh", hop_vrf: "", hop_command_template: "" };
  }
  const protocol = prev.hop_protocol || "ssh";
  const vrf = prev.hop_vrf || "";
  return {
    hop_vendor: vendor,
    hop_protocol: protocol,
    hop_vrf: vrf,
    hop_command_template: defaultHopTemplate(vendor, protocol, vrf),
  };
}

export { ciscoHopTemplate, huaweiHopTemplate, isAutoHopTemplate, zteHopTemplate };
