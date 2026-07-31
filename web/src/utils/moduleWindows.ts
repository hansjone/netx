import { getModuleById } from "../config/modules";
import {
  broadcastMessage,
  createFocusRequestId,
  focusWindowSafe,
  registerBroadcastListener,
  waitForFocusAck,
} from "./tabChannel";

export type ModuleWindowSpec = {
  moduleId: string;
  path: string;
};

const MODULE_CHANNEL = "netx-module-nav";

type FocusModuleMessage = {
  type: "focus-module";
  moduleId: string;
  path: string;
  requestId: string;
};

export function moduleWindowName(moduleId: string): string {
  return `netx-module-${moduleId}`;
}

function moduleAckKey(moduleId: string): string {
  return `netx-mod-focus-ack-${moduleId}`;
}

function resolveModulePath(moduleId: string, path: string): string {
  const mod = getModuleById(moduleId);
  const base = mod?.path ?? "/";
  // Prefer caller path so query strings like /webcrt?ne_id=... are preserved.
  if (path && (path === base || path.startsWith(`${base}?`) || path.startsWith(`${base}/`))) {
    return path;
  }
  return base;
}

export function registerModuleWindow(moduleId: string): () => void {
  const sharedName = moduleWindowName(moduleId);
  try {
    // Keep unique names from openNewModuleWindow; only claim the singleton if unnamed.
    if (!window.name) {
      window.name = sharedName;
    }
  } catch {
    /* ignore */
  }

  return registerBroadcastListener<FocusModuleMessage>(
    MODULE_CHANNEL,
    moduleAckKey(moduleId),
    (data) => {
      // One-shot session windows (unique name) must not merge into the singleton module tab.
      if (window.name && window.name !== sharedName) {
        return;
      }
      const targetPath = String(data.path || "").trim();
      if (targetPath) {
        try {
          const url = new URL(targetPath, window.location.origin);
          const next = `${url.pathname}${url.search}${url.hash}`;
          const cur = `${window.location.pathname}${window.location.search}${window.location.hash}`;
          if (window.location.pathname !== url.pathname) {
            // Different module route — hard navigate.
            window.location.assign(next);
          } else if (cur !== next && (url.search || url.hash)) {
            // Same pathname: soft-nav only when caller supplies query/hash
            // (e.g. /webcrt?ne_id=...). Bare /webcrt means focus-only — keep
            // current query and in-memory session state (no reload).
            window.history.pushState({}, "", next);
            window.dispatchEvent(new PopStateEvent("popstate"));
          }
        } catch {
          if (window.location.pathname + window.location.search !== targetPath) {
            window.location.assign(targetPath);
          }
        }
      }
      window.focus();
    },
    (data) => data.moduleId === moduleId,
  );
}

export function openOrFocusModule({ moduleId, path }: ModuleWindowSpec): void {
  const targetPath = resolveModulePath(moduleId, path);
  const requestId = createFocusRequestId(`mod-${moduleId}`);
  const name = moduleWindowName(moduleId);

  broadcastMessage<FocusModuleMessage>(MODULE_CHANNEL, {
    type: "focus-module",
    moduleId,
    path: targetPath,
    requestId,
  });

  // Under the user-gesture: reclaim the named window WITHOUT passing a URL.
  // window.open(url, existingName) reloads that tab and kills WebCRT sessions.
  let handledByGesture = false;
  let win: Window | null = null;
  try {
    win = window.open("", name);
  } catch {
    win = null;
  }
  if (win && !win.closed) {
    handledByGesture = true;
    let needsNavigate = false;
    try {
      const href = String(win.location?.href || "");
      needsNavigate = !href || href === "about:blank";
    } catch {
      // Existing cross-context window — only focus; broadcast soft-nav handles path.
      needsNavigate = false;
    }
    if (needsNavigate) {
      try {
        win.location.href = targetPath;
      } catch {
        handledByGesture = false;
      }
    }
    focusWindowSafe(win);
  }

  waitForFocusAck(moduleAckKey(moduleId), requestId, () => {
    if (handledByGesture) return;
    focusWindowSafe(window.open(targetPath, name));
  });
}

/** Always open a fresh browser tab/window; never focus or merge with an existing module tab. */
export function openNewModuleWindow({ moduleId, path }: ModuleWindowSpec): void {
  const targetPath = resolveModulePath(moduleId, path);
  const uniqueName = `${moduleWindowName(moduleId)}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  focusWindowSafe(window.open(targetPath, uniqueName));
}


export { moduleIdFromPath } from "../config/modules";
