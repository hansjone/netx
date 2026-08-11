import type { TopologyDiscoverNeResult } from "../../types";

export function newId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
}

export function newLocalEdgeId(): string {
  return `local:${newId()}`;
}

export function isLocalPendingEdgeId(id: string): boolean {
  return String(id || "").startsWith("local:");
}

export function discoverResultKind(r: TopologyDiscoverNeResult): "ok" | "warn" | "fail" {
  if (!r.ok) return "fail";
  const unmatchedCount = r.unmatched_count ?? (r.unmatched?.length || 0);
  if (r.parser_stub || unmatchedCount > 0) return "warn";
  return "ok";
}
