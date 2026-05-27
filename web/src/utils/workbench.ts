import {
  broadcastMessage,
  createFocusRequestId,
  focusWindowSafe,
  registerBroadcastListener,
  waitForFocusAck,
} from "./tabChannel";

export const WORKBENCH_WINDOW_NAME = "netx-workbench";

const CHANNEL_NAME = "netx-workbench-nav";
const FOCUS_ACK_KEY = "netx-wb-focus-ack";

type FocusWorkbenchMessage = {
  type: "focus-workbench";
  requestId: string;
};

export function registerWorkbenchWindow(): () => void {
  try {
    window.name = WORKBENCH_WINDOW_NAME;
  } catch {
    /* ignore */
  }

  return registerBroadcastListener<FocusWorkbenchMessage>(CHANNEL_NAME, FOCUS_ACK_KEY, () => {
    window.focus();
  });
}

export function returnToWorkbench(): void {
  const requestId = createFocusRequestId("wb");

  broadcastMessage<FocusWorkbenchMessage>(CHANNEL_NAME, {
    type: "focus-workbench",
    requestId,
  });

  try {
    if (window.opener && !window.opener.closed) {
      window.opener.focus();
    }
  } catch {
    /* ignore */
  }

  waitForFocusAck(FOCUS_ACK_KEY, requestId, () => {
    focusWindowSafe(window.open("/", WORKBENCH_WINDOW_NAME));
  });
}
