/** Alarm severity helpers for topology overlay (UME current alarms). */

export type AlarmSeverity =
  | "critical"
  | "major"
  | "minor"
  | "warning"
  | "indeterminate"
  | "cleared"
  | "unknown";

export type NodeAlarmSummary = {
  severity: AlarmSeverity;
  count: number;
};

const SEV_RANK: Record<string, number> = {
  critical: 60,
  major: 50,
  minor: 40,
  warning: 30,
  indeterminate: 20,
  unknown: 10,
  cleared: 0,
};

export function normalizeAlarmSeverity(raw: string | undefined | null): AlarmSeverity {
  const s = String(raw || "").trim().toLowerCase();
  if (!s) return "unknown";
  if (s.includes("critical") || s === "5") return "critical";
  if (s.includes("major") || s === "4") return "major";
  if (s.includes("minor") || s === "3") return "minor";
  if (s.includes("warning") || s.includes("warn") || s === "2") return "warning";
  if (s.includes("indeterminate") || s === "1") return "indeterminate";
  if (s.includes("clear") || s === "0") return "cleared";
  return "unknown";
}

export function severityRank(sev: string): number {
  return SEV_RANK[normalizeAlarmSeverity(sev)] ?? 0;
}

export function worseSeverity(a: string, b: string): AlarmSeverity {
  return severityRank(a) >= severityRank(b) ? normalizeAlarmSeverity(a) : normalizeAlarmSeverity(b);
}

export function normalizeHostKey(value: string | undefined | null): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

/** IPv4 / IPv6-ish — safe for exact alarm join. */
export function looksLikeIp(value: string | undefined | null): boolean {
  const s = normalizeHostKey(value);
  if (!s) return false;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(s)) return true;
  // Compact check: colon + hex/digits (avoid matching short names).
  return s.includes(":") && /^[0-9a-f:%.]+$/i.test(s) && s.length >= 4;
}

/**
 * Host/name keys used for alarm overlay join.
 * Reject short bare labels (e.g. "r1") that collide across sites.
 */
export function isStrongHostKey(value: string | undefined | null): boolean {
  const k = normalizeHostKey(value);
  if (!k) return false;
  if (looksLikeIp(k)) return true;
  if (k.includes(".")) return true; // FQDN / dotted inventory name
  return k.length >= 8;
}
