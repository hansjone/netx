import type { Node } from "@xyflow/react";
import type { NeNodeData } from "./TopologyReactFlowView";

export function normalizeSearchText(s: string): string {
  return String(s || "")
    .toLowerCase()
    .replace(/[\s_\-./]+/g, "");
}

/** Case-insensitive substring + loose subsequence (e.g. r1 → router-1). */
export function fuzzyIncludes(haystack: string, needle: string): boolean {
  const h = normalizeSearchText(haystack);
  const n = normalizeSearchText(needle);
  if (!n) return true;
  if (!h) return false;
  if (h.includes(n)) return true;
  let i = 0;
  for (const ch of h) {
    if (ch === n[i]) i += 1;
    if (i >= n.length) return true;
  }
  return false;
}

export function nodeMatchesQuery(n: Node<NeNodeData>, query: string): boolean {
  const tokens = String(query || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (!tokens.length) return false;
  const bits = [n.data.label, n.data.ne_ip, n.data.vendor, n.data.managed_ne_id, n.data.ume_ne_id];
  return tokens.every((tok) => bits.some((b) => fuzzyIncludes(String(b || ""), tok)));
}

export function isPlaceholderSource(source: string | undefined, neIp: string): boolean {
  const src = String(source || "").trim().toLowerCase();
  if (src === "lldp" || src === "topology") return true;
  return !String(neIp || "").trim() && Boolean(src);
}
