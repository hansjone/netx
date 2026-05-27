import { useEffect } from "react";
import { isWorkbenchPath, moduleIdFromPath } from "../config/modules";
import { registerModuleWindow } from "../utils/moduleWindows";
import { registerWorkbenchWindow } from "../utils/workbench";

/** Register workbench or module tab for cross-tab focus (see utils/tabChannel). */
export function useAppWindowRegistration(pathname: string): void {
  useEffect(() => {
    if (isWorkbenchPath(pathname)) return registerWorkbenchWindow();
    const moduleId = moduleIdFromPath(pathname);
    if (moduleId) return registerModuleWindow(moduleId);
    return undefined;
  }, [pathname]);
}
