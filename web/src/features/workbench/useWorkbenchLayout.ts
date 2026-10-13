import { useCallback, useMemo } from "react";
import type { ModuleDefinition } from "../../config/modules";
import { apiPut } from "../../services/api";
import { useAuth, type UserPrefs } from "../auth/AuthContext";

export type WorkbenchLayoutPrefs = {
  order: string[];
  hidden: string[];
};

const LS_PREFIX = "netx.wb.layout.";

function readLocalLayout(userId: string): WorkbenchLayoutPrefs | null {
  try {
    const raw = localStorage.getItem(`${LS_PREFIX}${userId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const order = (parsed as { order?: unknown }).order;
    const hidden = (parsed as { hidden?: unknown }).hidden;
    if (!Array.isArray(order) || !Array.isArray(hidden)) return null;
    return {
      order: order.filter((x): x is string => typeof x === "string"),
      hidden: hidden.filter((x): x is string => typeof x === "string"),
    };
  } catch {
    return null;
  }
}

function writeLocalLayout(userId: string, layout: WorkbenchLayoutPrefs): void {
  try {
    localStorage.setItem(`${LS_PREFIX}${userId}`, JSON.stringify(layout));
  } catch {
    // ignore quota / private mode
  }
}

function asWorkbenchPrefs(value: unknown): WorkbenchLayoutPrefs | null {
  if (!value || typeof value !== "object") return null;
  const order = (value as { order?: unknown }).order;
  const hidden = (value as { hidden?: unknown }).hidden;
  if (!Array.isArray(order) && !Array.isArray(hidden)) return null;
  return {
    order: Array.isArray(order) ? order.filter((x): x is string => typeof x === "string") : [],
    hidden: Array.isArray(hidden) ? hidden.filter((x): x is string => typeof x === "string") : [],
  };
}

/** Pure layout resolver — permission-filtered `all` in registry order. */
export function resolveLayout(
  all: ModuleDefinition[],
  prefs: WorkbenchLayoutPrefs | null | undefined,
): { shown: ModuleDefinition[]; available: ModuleDefinition[] } {
  const byId = new Map(all.map((m) => [m.moduleId, m]));
  const prefOrder = prefs?.order ?? [];
  const hiddenSet = new Set((prefs?.hidden ?? []).filter((id) => byId.has(id)));
  const orderIds = prefOrder.filter((id) => byId.has(id));
  const inOrder = new Set(orderIds);

  const ranked: ModuleDefinition[] = [];
  const seen = new Set<string>();
  for (const id of orderIds) {
    const mod = byId.get(id);
    if (!mod || seen.has(id)) continue;
    ranked.push(mod);
    seen.add(id);
  }
  for (const mod of all) {
    if (seen.has(mod.moduleId)) continue;
    ranked.push(mod);
    seen.add(mod.moduleId);
  }

  const shown: ModuleDefinition[] = [];
  const available: ModuleDefinition[] = [];
  for (const mod of ranked) {
    const hide =
      hiddenSet.has(mod.moduleId) || (Boolean(mod.defaultHidden) && !inOrder.has(mod.moduleId));
    if (hide) available.push(mod);
    else shown.push(mod);
  }
  return { shown, available };
}

export function useWorkbenchLayout(all: ModuleDefinition[]) {
  const { user, prefs, setPrefs, ready } = useAuth();
  const userId = user?.id ?? "";

  const layoutPrefs = useMemo(() => {
    // After auth is ready, server prefs win (missing workbench => default).
    // Before ready, fall back to localStorage to avoid a layout flash.
    if (ready) return asWorkbenchPrefs(prefs?.workbench);
    return userId ? readLocalLayout(userId) : null;
  }, [ready, prefs, userId]);

  const { shown, available } = useMemo(
    () => resolveLayout(all, layoutPrefs),
    [all, layoutPrefs],
  );

  const persist = useCallback(
    async (next: WorkbenchLayoutPrefs) => {
      const res = await apiPut<{ prefs: UserPrefs }>("/v1/auth/me/prefs/workbench", next);
      const merged =
        res.prefs && typeof res.prefs === "object" ? res.prefs : { ...prefs, workbench: next };
      setPrefs(merged);
      if (userId) writeLocalLayout(userId, next);
    },
    [prefs, setPrefs, userId],
  );

  const save = useCallback(
    async (order: string[], hidden: string[]) => {
      await persist({ order, hidden });
    },
    [persist],
  );

  const reset = useCallback(async () => {
    await persist({ order: [], hidden: [] });
  }, [persist]);

  return { shown, available, save, reset, layoutPrefs };
}
