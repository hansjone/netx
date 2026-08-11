import type { NeNodeData } from "./TopologyReactFlowView";
import { VENDOR_TONE_KEYS, type VendorColors, type VendorToneKey } from "./displayPrefs";

export type { VendorToneKey, VendorColors };

export function nodeIconTone(
  vendor: string,
  managedNeId: string,
  umeNeId = "",
): VendorToneKey {
  const hasManaged = Boolean(String(managedNeId || "").trim());
  const hasUme = Boolean(String(umeNeId || "").trim());
  if (!hasManaged && !hasUme) return "gray";
  const v = String(vendor || "").trim().toLowerCase();
  if (!v || v === "other" || v === "unknown" || v === "generic") {
    return hasUme ? "zte" : "gray";
  }
  if (v.includes("cisco")) return "cisco";
  if (v.includes("huawei")) return "huawei";
  if (v.includes("zte")) return "zte";
  if (v.includes("juniper")) return "juniper";
  if (v.includes("nokia") || v.includes("alcatel")) return "nokia";
  if (v.includes("ericsson")) return "ericsson";
  if (v.includes("h3c") || v.includes("comware")) return "h3c";
  if (v.includes("ruijie") || v.includes("锐捷")) return "ruijie";
  if (v.includes("mikrotik")) return "mikrotik";
  return hasUme ? "zte" : "gray";
}

export function vendorColorForNode(data: NeNodeData, colors: VendorColors): string {
  if (data.kind === "region") return "#38bdf8";
  return colors[nodeIconTone(data.vendor, data.managed_ne_id, data.ume_ne_id)] || colors.gray;
}

export { VENDOR_TONE_KEYS };
