/** Prefetch cutover compare route chunks so nav switches don't wait on first download. */

let started = false;

export function prefetchCutoverPages(): void {
  if (started) return;
  started = true;
  // Fire-and-forget dynamic imports (same modules as App.tsx lazy()).
  void import("./BizStatePage");
  void import("./BizComparePage");
  void import("./BizMigrationPage");
  void import("./BizMonitorTemplatesPage");
}
