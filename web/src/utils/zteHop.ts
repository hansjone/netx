/** ZTE device CLI jump commands: ssh/telnet <ip> [vrf <name>]. */

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

export function isAutoHopTemplate(template: string, protocol: string, vrf: string): boolean {
  const t = String(template || "").trim();
  if (!t || LEGACY_HOP_TEMPLATES.has(t)) return true;
  return t === zteHopTemplate(protocol, vrf);
}
