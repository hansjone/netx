import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

export type WebTerminalHandle = {
  clear: () => void;
  copyAll: () => Promise<string>;
  getText: () => string;
  fit: () => void;
  focus: () => void;
};

type Props = {
  wsUrl: string;
  title?: string;
  recording?: boolean;
  autoFocus?: boolean;
  onStatus?: (state: string, message?: string) => void;
  onReady?: () => void;
  onStdout?: (data: string) => void;
};

function serializeTerminal(term: Terminal): string {
  const buf = term.buffer.active;
  const lines: string[] = [];
  for (let i = 0; i < buf.length; i += 1) {
    const line = buf.getLine(i);
    if (!line) continue;
    lines.push(line.translateToString(true));
  }
  return lines.join("\n").replace(/\s+$/g, "");
}

function isSidebarSearchTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT") {
    // xterm's hidden textarea must still receive keys normally.
    if (target.classList.contains("xterm-helper-textarea")) return false;
    return true;
  }
  return Boolean(target.closest(".webcrt-sidebar__search"));
}

function isXtermTextarea(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.classList.contains("xterm-helper-textarea");
}

/** Map a browser key event to the bytes xterm/onData would normally emit. */
function keyEventToStdin(e: KeyboardEvent): string | null {
  if (e.ctrlKey || e.altKey || e.metaKey) return null;
  if (e.key === "Backspace") return "\x08"; // BS — common SecureCRT/VT default
  if (e.key === "Enter") return "\r";
  if (e.key === "Tab") return "\t";
  if (e.key === "Escape") return "\x1b";
  if (e.key === "ArrowUp") return "\x1b[A";
  if (e.key === "ArrowDown") return "\x1b[B";
  if (e.key === "ArrowRight") return "\x1b[C";
  if (e.key === "ArrowLeft") return "\x1b[D";
  if (e.key === "Home") return "\x1b[H";
  if (e.key === "End") return "\x1b[F";
  if (e.key === "Delete") return "\x1b[3~";
  if (e.key.length === 1) return e.key;
  return null;
}

export const WebTerminal = forwardRef<WebTerminalHandle, Props>(function WebTerminal(
  { wsUrl, title, recording, autoFocus = true, onStatus, onReady, onStdout },
  ref,
) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const onStatusRef = useRef(onStatus);
  const onReadyRef = useRef(onReady);
  const onStdoutRef = useRef(onStdout);
  const recordingRef = useRef(!!recording);
  const autoFocusRef = useRef(autoFocus);

  useEffect(() => {
    onStatusRef.current = onStatus;
    onReadyRef.current = onReady;
    onStdoutRef.current = onStdout;
  }, [onStatus, onReady, onStdout]);

  useEffect(() => {
    recordingRef.current = !!recording;
  }, [recording]);

  useEffect(() => {
    autoFocusRef.current = autoFocus;
  }, [autoFocus]);

  const focusTerminal = () => {
    try {
      termRef.current?.focus();
    } catch {
      /* ignore */
    }
  };

  useImperativeHandle(ref, () => ({
    clear: () => {
      termRef.current?.clear();
      termRef.current?.write("\x1b[H\x1b[2J");
    },
    copyAll: async () => {
      const text = termRef.current ? serializeTerminal(termRef.current) : "";
      if (text && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      }
      return text;
    },
    getText: () => (termRef.current ? serializeTerminal(termRef.current) : ""),
    fit: () => {
      try {
        fitRef.current?.fit();
      } catch {
        /* ignore */
      }
    },
    focus: focusTerminal,
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Consolas, "Courier New", monospace',
      theme: {
        background: "#0b1220",
        foreground: "#e2e8f0",
        cursor: "#e2e8f0",
      },
      convertEol: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    termRef.current = term;
    fitRef.current = fit;

    const doFit = () => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
    };
    const maybeFocus = () => {
      if (!autoFocusRef.current) return;
      focusTerminal();
    };
    // Focus immediately so the first keystroke is not lost to the sidebar/body.
    maybeFocus();
    requestAnimationFrame(() => {
      doFit();
      maybeFocus();
      window.setTimeout(() => {
        doFit();
        maybeFocus();
      }, 50);
    });

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    onStatusRef.current?.("connecting");

    const sendJson = (payload: Record<string, unknown>) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
      }
    };

    const sendStdin = (data: string) => {
      if (!data) return;
      sendJson({ type: "stdin", data });
    };

    // Display only what the device echoes (chars, Tab completion, BS erase, etc.).
    // No local echo / local erase — those desync on Tab complete and prompt redraw.
    const writeStdout = (raw: string) => {
      if (raw) term.write(raw);
      if (recordingRef.current) onStdoutRef.current?.(raw);
    };

    const sendResize = () => {
      doFit();
      sendJson({ type: "resize", cols: term.cols, rows: term.rows });
    };

    ws.onopen = () => {
      onStatusRef.current?.("open");
      sendResize();
      onReadyRef.current?.();
      window.setTimeout(maybeFocus, 30);
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(String(ev.data || "{}")) as {
          type?: string;
          data?: string;
          state?: string;
          message?: string;
        };
        if (msg.type === "stdout" && typeof msg.data === "string") {
          writeStdout(msg.data);
          maybeFocus();
          return;
        }
        if (msg.type === "status") {
          onStatusRef.current?.(String(msg.state || ""), msg.message);
          if (msg.state === "connected") {
            maybeFocus();
            return;
          }
          if (msg.state === "closed" || msg.state === "error") {
            const detail = msg.message ? `: ${msg.message}` : "";
            term.writeln(`\r\n\x1b[33m[session ${msg.state}${detail}]\x1b[0m`);
          }
          return;
        }
        if (msg.type === "pong") return;
      } catch {
        writeStdout(String(ev.data || ""));
      }
    };

    ws.onerror = () => {
      onStatusRef.current?.("error", "websocket_error");
      term.writeln("\r\n\x1b[31m[websocket error]\x1b[0m");
    };

    ws.onclose = (ev) => {
      onStatusRef.current?.("closed", `websocket_closed:${ev.code}`);
      if (!ev.wasClean) {
        term.writeln(`\r\n\x1b[33m[websocket closed code=${ev.code}]\x1b[0m`);
      }
    };

    // When focused, xterm onData sends keystrokes.
    const dataDisposable = term.onData((data) => {
      // Normalize Backspace: xterm emits DEL(0x7f); devices expect BS(0x08) like default SecureCRT.
      const normalized = data.replace(/\x7f/g, "\x08");
      sendStdin(normalized);
    });

    // When NOT focused (sidebar still focused after click), capture keys and forward
    // so the first character / Backspace are not lost to the browser.
    const onKeyDownCapture = (e: KeyboardEvent) => {
      if (!autoFocusRef.current) return;
      if (host.closest("[hidden]")) return;
      if (isSidebarSearchTarget(e.target)) return;

      // Always block browser "Backspace = history back" while this session pane is active.
      if (e.key === "Backspace") {
        e.preventDefault();
      }

      if (isXtermTextarea(e.target)) {
        // Let xterm onData handle it; we only prevented browser back above.
        maybeFocus();
        return;
      }

      const data = keyEventToStdin(e);
      if (data == null) return;

      e.preventDefault();
      e.stopPropagation();
      maybeFocus();
      sendStdin(data);
    };
    window.addEventListener("keydown", onKeyDownCapture, true);

    const pingTimer = window.setInterval(() => {
      sendJson({ type: "ping" });
    }, 25000);

    const onWinResize = () => sendResize();
    window.addEventListener("resize", onWinResize);

    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            sendResize();
          })
        : null;
    ro?.observe(host);

    return () => {
      window.clearInterval(pingTimer);
      window.removeEventListener("resize", onWinResize);
      window.removeEventListener("keydown", onKeyDownCapture, true);
      ro?.disconnect();
      dataDisposable.dispose();
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [wsUrl, title]);

  return (
    <div
      className="webcrt-term"
      ref={hostRef}
      onMouseDown={() => {
        focusTerminal();
      }}
    />
  );
});
