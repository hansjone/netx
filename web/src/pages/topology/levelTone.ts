/** Map fabric level/role to a short CSS tone key for canvas chrome. */

export type LevelToneKey = "external" | "core" | "aggregation" | "access" | "edge" | "";

export function levelToneKey(level: number | null | undefined, role?: string | null): LevelToneKey {
  const r = String(role || "").trim().toLowerCase();
  if (r === "external" || r === "core" || r === "access" || r === "edge") return r;
  if (r === "aggregation" || r === "aggregate" || r === "agg") return "aggregation";
  if (level == null || Number.isNaN(Number(level))) return "";
  const maj = Math.floor(Number(level));
  if (maj <= 0) return "external";
  if (maj === 1) return "core";
  if (maj === 2) return "aggregation";
  if (maj === 3) return "access";
  return "edge";
}

export function levelLabelKey(tone: LevelToneKey): string {
  if (!tone) return "topology.levelUnmatched";
  return `topology.level.${tone}`;
}
