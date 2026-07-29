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
        // Compare pathname only for hard navigation. Same route + new query
        // must soft-navigate so React state (open tabs) survives.
        try {
          const url = new URL(targetPath, window.location.origin);
          const next = `${url.pathname}${url.search}${url.hash}`;
          const cur = `${window.location.pathname}${window.location.search}${window.location.hash}`;
          if (window.location.pathname !== url.pathname) {
            window.location.assign(next);
          } else if (cur !== next) {
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
  const mod = getModuleById(moduleId);
  const base = mod?.path ?? "/";
  // Prefer caller path so query strings like /webcrt?ne_id=... are preserved.
  const targetPath = path && (path === base || path.startsWith(`${base}?`) || path.startsWith(`${base}/`))
    ? path
    : base;
  const requestId = createFocusRequestId(`mod-${moduleId}`);
  const name = moduleWindowName(moduleId);

  broadcastMessage<FocusModuleMessage>(MODULE_CHANNEL, {
    type: "focus-module",
    moduleId,
    path: targetPath,
    requestId,
  });

  focusWindowSafe(window.open(targetPath, name));

  waitForFocusAck(moduleAckKey(moduleId), requestId, () => {
    focusWindowSafe(window.open(targetPath, name));
  });
}

/** Always open a fresh browser tab/window; never focus or merge with an existing module tab. */
export function openNewModuleWindow({ moduleId, path }: ModuleWindowSpec): void {
  const mod = getModuleById(moduleId);
  const base = mod?.path ?? "/";
  const targetPath = path && (path === base || path.startsWith(`${base}?`) || path.startsWith(`${base}/`))
    ? path
    : base;
  const uniqueName = `${moduleWindowName(moduleId)}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  focusWindowSafe(window.open(targetPath, uniqueName));
}


export { moduleIdFromPath } from "../config/modules";
