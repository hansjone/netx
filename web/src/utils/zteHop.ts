/** Vendor CLI jump command templates (placeholders). */

const LEGACY_HOP_TEMPLATES = new Set([
  "ssh {target_user}@{target_ip}",
  "ssh {target_ip}",
  "telnet {target_ip}",
]);

export function zteHopTemplate(protocol: string, vrf: string): string {
  const cmd = String(protocol || "ssh").toLowerCase() === "telnet" ? "telnet" : "ssh";
  const v = String(vrf || "").trim();
  return v ? `${cmd} {target_ip} vrf {vrf}` : `${cmd} {target_ip}`;
}

/** Cisco: ssh -vrf VRF IP; telnet IP [/vrf VRF]. */
export function ciscoHopTemplate(protocol: string, vrf: string): string {
  const v = String(vrf || "").trim();
  if (String(protocol || "ssh").toLowerCase() === "telnet") {
    return v ? `telnet {target_ip} /vrf {vrf}` : `telnet {target_ip}`;
  }
  return v ? `ssh -vrf {vrf} {target_ip}` : `ssh {target_ip}`;
}

/** Huawei: telnet [vpn-instance VRF] IP; SSH uses stelnet. */
export function huaweiHopTemplate(protocol: string, vrf: string): string {
  const v = String(vrf || "").trim();
  if (String(protocol || "ssh").toLowerCase() === "telnet") {
    return v ? `telnet vpn-instance {vrf} {target_ip}` : `telnet {target_ip}`;
  }
  return v ? `stelnet {target_ip} -vpn-instance {vrf}` : `stelnet {target_ip}`;
}

export function isAutoHopTemplate(
  template: string,
  vendor: string,
  protocol: string,
  vrf: string,
): boolean {
  const t = String(template || "").trim();
  if (!t || LEGACY_HOP_TEMPLATES.has(t)) return true;
  const v = String(vendor || "zte").toLowerCase();
  if (v === "huawei") return t === huaweiHopTemplate(protocol, vrf);
  if (v === "cisco") return t === ciscoHopTemplate(protocol, vrf);
  if (v === "linux") return t === "";
  return t === zteHopTemplate(protocol, vrf);
}
