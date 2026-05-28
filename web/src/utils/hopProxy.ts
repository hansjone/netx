import { isAutoHopTemplate, zteHopTemplate } from "./zteHop";

export type HopVendor = "zte" | "linux";

export const HOP_VENDORS: HopVendor[] = ["zte", "linux"];

export function isLinuxHopVendor(vendor: string): boolean {
  return String(vendor || "").toLowerCase() === "linux";
}

export function patchHopVendorChange(
  vendor: HopVendor,
  prev: { hop_protocol: string; hop_vrf: string; hop_command_template: string },
): { hop_vendor: HopVendor; hop_protocol: string; hop_vrf: string; hop_command_template: string } {
  if (vendor === "linux") {
    return { hop_vendor: "linux", hop_protocol: "ssh", hop_vrf: "", hop_command_template: "" };
  }
  return {
    hop_vendor: "zte",
    hop_protocol: prev.hop_protocol || "ssh",
    hop_vrf: prev.hop_vrf,
    hop_command_template: zteHopTemplate(prev.hop_protocol || "ssh", prev.hop_vrf),
  };
}

export { isAutoHopTemplate, zteHopTemplate };
