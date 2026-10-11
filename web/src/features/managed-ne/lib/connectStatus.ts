export function connectStatusClass(status: string): string {
  if (status === "pass") return "pt-list-status--ok";
  if (status === "fail") return "pt-list-status--failed";
  if (status === "testing") return "pt-list-status--running";
  return "pt-list-status--unknown";
}

export function connectPillLevel(status: string): "up" | "down" | "unknown" | "warn" {
  const s = String(status || "").toLowerCase();
  if (s === "pass" || s === "ok") return "up";
  if (s === "fail" || s === "error") return "down";
  if (s === "testing") return "warn";
  return "unknown";
}
