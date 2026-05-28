/** Shared cross-tab focus helpers (BroadcastChannel + sessionStorage ack). */

export const TAB_FOCUS_DELAY_MS = 200;

export function createFocusRequestId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function registerBroadcastListener<T extends { requestId: string }>(
  channelName: string,
  ackStorageKey: string,
  onMessage: (data: T) => void,
  shouldHandle?: (data: T) => boolean,
): () => void {
  if (typeof BroadcastChannel === "undefined") {
    return () => undefined;
  }

  const channel = new BroadcastChannel(channelName);
  channel.onmessage = (ev: MessageEvent<T>) => {
    const data = ev.data;
    if (!data?.requestId) return;
    if (shouldHandle && !shouldHandle(data)) return;
    try {
      sessionStorage.setItem(ackStorageKey, data.requestId);
    } catch {
      /* ignore */
    }
    onMessage(data);
  };
  return () => channel.close();
}

export function broadcastMessage<T>(channelName: string, message: T): void {
  if (typeof BroadcastChannel === "undefined") return;
  const channel = new BroadcastChannel(channelName);
  channel.postMessage(message);
  channel.close();
}

export function waitForFocusAck(
  ackStorageKey: string,
  requestId: string,
  fallback: () => void,
  delayMs: number = TAB_FOCUS_DELAY_MS,
): void {
  window.setTimeout(() => {
    try {
      if (sessionStorage.getItem(ackStorageKey) === requestId) {
        sessionStorage.removeItem(ackStorageKey);
        return;
      }
    } catch {
      /* ignore */
    }
    fallback();
  }, delayMs);
}

export function focusWindowSafe(win: Window | null): void {
  try {
    win?.focus();
  } catch {
    /* ignore */
  }
}
