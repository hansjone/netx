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
  try {
    window.name = moduleWindowName(moduleId);
  } catch {
    /* ignore */
  }

  return registerBroadcastListener<FocusModuleMessage>(
    MODULE_CHANNEL,
    moduleAckKey(moduleId),
    (data) => {
      const targetPath = String(data.path || "").trim();
      if (targetPath && window.location.pathname !== targetPath) {
        window.location.assign(targetPath);
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

export { moduleIdFromPath } from "../config/modules";
