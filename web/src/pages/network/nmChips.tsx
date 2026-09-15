import { Chip } from "@heroui/react";
import type { ReactNode } from "react";

export type NmChipColor = "success" | "danger" | "warning" | "default" | "accent";

export function sourceChipColor(source: string | null | undefined): NmChipColor {
  const s = String(source || "").trim().toLowerCase();
  if (s === "ume") return "accent";
  if (s === "managed" || s === "webcrt") return "default";
  return "default";
}

/** Connection / health style statuses. */
export function connectChipColor(status: string | null | undefined): NmChipColor {
  const s = String(status || "")
    .trim()
    .toLowerCase();
  if (!s || s === "-" || s === "—" || s === "unknown") return "default";
  if (s.includes("fail") || s.includes("down") || s.includes("error") || s.includes("critical")) {
    return "danger";
  }
  if (s.includes("ok") || s.includes("up") || s.includes("connected") || s.includes("success") || s.includes("pass")) {
    return "success";
  }
  if (s.includes("warn") || s.includes("degrad") || s.includes("test") || s.includes("major") || s.includes("minor")) {
    return "warning";
  }
  return "accent";
}

/** Job / cycle / task lifecycle statuses. */
export function jobChipColor(status: string | null | undefined): NmChipColor {
  const s = String(status || "")
    .trim()
    .toLowerCase();
  if (!s) return "default";
  if (s === "running" || s === "pending" || s === "collect") return "success";
  if (s === "paused" || s === "draft" || s === "skipped") return "warning";
  if (s === "failed" || s === "fail" || s === "error" || s === "stopped") return "danger";
  if (s === "success" || s === "completed" || s === "ok" || s === "done") return "success";
  return "default";
}

/** Fabric / LLDP edge status (active / missing / stale). */
export function edgeChipColor(status: string | null | undefined): NmChipColor {
  const s = String(status || "")
    .trim()
    .toLowerCase();
  if (s === "active" || s === "ok" || s === "up") return "success";
  if (s === "missing" || s === "stale" || s === "down") return "warning";
  if (s.includes("fail") || s.includes("error")) return "danger";
  return "default";
}

/** Alarm perceived severity. */
export function severityChipColor(sev: string | null | undefined): NmChipColor {
  const s = String(sev || "")
    .trim()
    .toLowerCase();
  if (s.includes("critical")) return "danger";
  if (s.includes("major")) return "warning";
  if (s.includes("minor") || s.includes("warning") || s.includes("warn")) return "warning";
  if (s.includes("info") || s.includes("indeterminate")) return "accent";
  return "default";
}

type NmStatusChipProps = {
  color: NmChipColor;
  children: ReactNode;
  className?: string;
};

/** Soft status/source chip used across Network Management lists. */
export function NmStatusChip({ color, children, className }: NmStatusChipProps) {
  return (
    <Chip size="sm" variant="soft" color={color} className={className}>
      <Chip.Label>{children}</Chip.Label>
    </Chip>
  );
}
