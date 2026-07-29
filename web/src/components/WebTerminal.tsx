import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

export type WebTerminalHandle = {
  clear: () => void;
  copyAll: () => Promise<string>;
  getText: () => string;
  fit: () => void;
};

type Props = {
  wsUrl: string;
  title?: string;
  recording?: boolean;
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

export const WebTerminal = forwardRef<WebTerminalHandle, Props>(function WebTerminal(
  { wsUrl, title, recording, onStatus, onReady, onStdout },
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

  useEffect(() => {
    onStatusRef.current = onStatus;
    onReadyRef.current = onReady;
    onStdoutRef.current = onStdout;
  }, [onStatus, onReady, onStdout]);

  useEffect(() => {
    recordingRef.current = !!recording;
  }, [recording]);

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
    requestAnimationFrame(() => {
      doFit();
      window.setTimeout(doFit, 50);
    });

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    onStatusRef.current?.("connecting");

    const sendJson = (payload: Record<string, unknown>) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
      }
    };

    const sendResize = () => {
      doFit();
      sendJson({ type: "resize", cols: term.cols, rows: term.rows });
    };

    ws.onopen = () => {
      onStatusRef.current?.("open");
      sendResize();
      onReadyRef.current?.();
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
          term.write(msg.data);
          if (recordingRef.current) onStdoutRef.current?.(msg.data);
          return;
        }
        if (msg.type === "status") {
          onStatusRef.current?.(String(msg.state || ""), msg.message);
          if (msg.state === "connected") return;
          if (msg.state === "closed" || msg.state === "error") {
            const detail = msg.message ? `: ${msg.message}` : "";
            term.writeln(`\r\n\x1b[33m[session ${msg.state}${detail}]\x1b[0m`);
          }
          return;
        }
        if (msg.type === "pong") return;
      } catch {
        const raw = String(ev.data || "");
        term.write(raw);
        if (recordingRef.current) onStdoutRef.current?.(raw);
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

    const dataDisposable = term.onData((data) => {
      sendJson({ type: "stdin", data });
    });

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

  return <div className="webcrt-term" ref={hostRef} />;
});
