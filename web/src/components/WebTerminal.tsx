import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

type Props = {
  wsUrl: string;
  title?: string;
  onStatus?: (state: string, message?: string) => void;
  onReady?: () => void;
};

export function WebTerminal({ wsUrl, title, onStatus, onReady }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const onStatusRef = useRef(onStatus);
  const onReadyRef = useRef(onReady);

  useEffect(() => {
    onStatusRef.current = onStatus;
    onReadyRef.current = onReady;
  }, [onStatus, onReady]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Consolas, "Courier New", monospace',
      theme: {
        background: "#0f172a",
        foreground: "#e2e8f0",
        cursor: "#93c5fd",
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
    // Fit after layout; hidden/zero-size parents need a deferred pass.
    requestAnimationFrame(() => {
      doFit();
      window.setTimeout(doFit, 50);
    });

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    onStatusRef.current?.("connecting");
    term.writeln(`\x1b[90mConnecting${title ? ` ${title}` : ""}…\x1b[0m`);

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
          ne_name?: string;
          ne_ip?: string;
          protocol?: string;
        };
        if (msg.type === "stdout" && typeof msg.data === "string") {
          term.write(msg.data);
          return;
        }
        if (msg.type === "status") {
          onStatusRef.current?.(String(msg.state || ""), msg.message);
          if (msg.state === "connected") {
            const where = [msg.ne_name || title, msg.ne_ip, msg.protocol].filter(Boolean).join(" · ");
            term.writeln(`\x1b[90m--- session ready${where ? `: ${where}` : ""} ---\x1b[0m`);
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
        term.write(String(ev.data || ""));
      }
    };

    ws.onerror = () => {
      onStatusRef.current?.("error", "websocket_error");
      term.writeln("\r\n\x1b[31m[websocket error]\x1b[0m");
      term.writeln("\x1b[33mHint: ensure API has package 'websockets' and is restarted; Vite UI should reach ws://127.0.0.1:8890\x1b[0m");
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
      // Do not send {type:"close"} here — React StrictMode remounts and needs reconnect.
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
}
