import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useI18n } from "../i18n";
import { webcrtWsUrl } from "../services/api";
import {
  applyKeywordHighlight,
  type KeywordHighlightConfig,
} from "../utils/webcrtKeywordHighlight";
import { readClipboardText, writeClipboardText } from "../utils/clipboard";

export type WebTerminalHandle = {
  clear: () => void;
  copyAll: () => Promise<string>;
  copySelection: () => Promise<string>;
  getText: () => string;
  fit: () => void;
  focus: () => void;
  sendBreak: () => void;
  sendText: (data: string, opts?: { throttle?: boolean }) => void;
  findNext: (term: string) => void;
  findPrevious: (term: string) => void;
};

export type TermColors = {
  background: string;
  foreground: string;
};

const DEFAULT_TERM_COLORS: TermColors = {
  background: "#0b1220",
  foreground: "#e2e8f0",
};

function normalizeHex(hex: string, fallback: string): string {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(String(hex || "").trim());
  return m ? `#${m[1].toLowerCase()}` : fallback;
}

function hexLuminance(hex: string): number {
  const n = normalizeHex(hex, "#000000").slice(1);
  const r = parseInt(n.slice(0, 2), 16) / 255;
  const g = parseInt(n.slice(2, 4), 16) / 255;
  const b = parseInt(n.slice(4, 6), 16) / 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function xtermThemeFromColors(colors: TermColors) {
  const background = normalizeHex(colors.background, DEFAULT_TERM_COLORS.background);
  const foreground = normalizeHex(colors.foreground, DEFAULT_TERM_COLORS.foreground);
  return {
    background,
    foreground,
    cursor: foreground,
    selectionBackground: hexLuminance(background) < 0.5 ? "#334155" : "#93c5fd",
  };
}

type Props = {
  /** Mint a fresh ws_ticket on every mount when set (preferred). */
  sessionId?: string;
  /** Fallback URL; prefer sessionId so tab focus does not reuse a spent ticket. */
  wsUrl?: string;
  title?: string;
  recording?: boolean;
  autoFocus?: boolean;
  encoding?: string;
  fontSize?: number;
  /** Terminal background / foreground (overrides legacy themeName). */
  termColors?: TermColors;
  /** @deprecated use termColors */
  themeName?: "dark" | "light";
  pasteDelayMs?: number;
  copyOnSelect?: boolean;
  keywordHighlight?: KeywordHighlightConfig;
  onStatus?: (
    state: string,
    message?: string,
    phase?: string,
    meta?: { sftpReady?: boolean; cliHop?: boolean },
  ) => void;
  onReady?: () => void;
  onStdout?: (data: string) => void;
  /** Seed text when mounting (frozen view after disconnect, or pre-WS buffer). */
  initialOutput?: string;
};

function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*(\x07|$)|\x1b./g, "");
}

/** Visible xterm row on Enter — keep device prompt prefix, drop ANSI only. */
export function normalizeAuditLine(line: string): string {
  return stripAnsi(line).replace(/\r/g, "").replace(/\s+$/, "");
}

function currentCommandLine(term: Terminal): string {
  const buf = term.buffer.active;
  for (let y = buf.cursorY; y >= Math.max(0, buf.cursorY - 3); y -= 1) {
    const line = buf.getLine(y);
    if (!line) continue;
    const text = normalizeAuditLine(line.translateToString(true));
    if (/[#>\]]\s*\S/.test(text) || /#[^\s]/.test(text)) return text;
  }
  const line = buf.getLine(buf.cursorY);
  if (!line) return "";
  return normalizeAuditLine(line.translateToString(true));
}

function auditLineForEnter(term: Terminal | null, explicitLine?: string): string | undefined {
  if (explicitLine != null && explicitLine.trim()) {
    const cmd = normalizeAuditLine(explicitLine);
    return cmd.trim() ? cmd : undefined;
  }
  if (!term) return undefined;
  const cmd = currentCommandLine(term);
  return cmd.trim() ? cmd : undefined;
}

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

/** Line-wise search — avoids serializing the full scrollback on every Find. */
function findBufferLine(
  term: Terminal,
  query: string,
  startLine: number,
  direction: 1 | -1,
): number {
  const needle = String(query || "").toLowerCase();
  if (!needle) return -1;
  const buf = term.buffer.active;
  const len = buf.length;
  if (len <= 0) return -1;
  const start = ((Math.trunc(startLine) % len) + len) % len;
  for (let step = 0; step < len; step += 1) {
    const i = direction > 0 ? (start + step) % len : (start - step + len) % len;
    const line = buf.getLine(i);
    if (!line) continue;
    if (line.translateToString(true).toLowerCase().includes(needle)) return i;
  }
  return -1;
}

function isSidebarSearchTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT") {
    if (target.classList.contains("xterm-helper-textarea")) return false;
    return true;
  }
  return Boolean(target.closest(".webcrt-sidebar__search") || target.closest(".webcrt-ctx") || target.closest(".webcrt-find"));
}

function isXtermTextarea(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.classList.contains("xterm-helper-textarea");
}

/** Map a browser key event to the bytes xterm/onData would normally emit. */
function keyEventToStdin(e: KeyboardEvent): string | null {
  if (e.ctrlKey || e.altKey || e.metaKey) return null;
  if (e.key === "Backspace") return "\x08";
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

function decodeBytes(buf: ArrayBuffer, _encoding: string): string {
  // Server normalizes device encodings (e.g. GBK) to UTF-8 on the wire.
  try {
    return new TextDecoder("utf-8").decode(buf);
  } catch {
    return "";
  }
}

const PREF_KEY = "netx.webcrt.termPrefs";

function loadPrefs(): { copyOnSelect: boolean; pasteDelayMs: number } {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (!raw) return { copyOnSelect: true, pasteDelayMs: 40 };
    const j = JSON.parse(raw) as Partial<{ copyOnSelect: boolean; pasteDelayMs: number }>;
    return {
      copyOnSelect: j.copyOnSelect !== false,
      pasteDelayMs: Math.max(0, Math.min(200, Number(j.pasteDelayMs) || 40)),
    };
  } catch {
    return { copyOnSelect: true, pasteDelayMs: 40 };
  }
}

export const WebTerminal = forwardRef<WebTerminalHandle, Props>(function WebTerminal(
  {
    wsUrl,
    sessionId,
    title: _title,
    recording: _recording,
    autoFocus = true,
    encoding = "utf-8",
    fontSize = 13,
    termColors,
    themeName = "dark",
    pasteDelayMs,
    copyOnSelect,
    keywordHighlight,
    onStatus,
    onReady,
    onStdout,
    initialOutput,
  },
  ref,
) {
  const { t } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
  const resolvedColors: TermColors = termColors ||
    (themeName === "light"
      ? { background: "#ffffff", foreground: "#000000" }
      : DEFAULT_TERM_COLORS);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const findLineRef = useRef(-1);
  const onStatusRef = useRef(onStatus);
  const onReadyRef = useRef(onReady);
  const onStdoutRef = useRef(onStdout);
  const autoFocusRef = useRef(autoFocus);
  const encodingRef = useRef(encoding);
  const fontSizeRef = useRef(Math.max(10, Math.min(28, Number(fontSize) || 13)));
  const termColorsRef = useRef<TermColors>(resolvedColors);
  const pasteDelayRef = useRef(pasteDelayMs ?? loadPrefs().pasteDelayMs);
  const copyOnSelectRef = useRef(copyOnSelect ?? loadPrefs().copyOnSelect);
  const pasteQueueRef = useRef<Promise<void>>(Promise.resolve());
  /** Updated whenever device stdout arrives; used to pace paste by echo, not fixed sleep. */
  const lastStdoutAtRef = useRef(0);
  /** Snapshot selection when opening the context menu — click on menu clears xterm selection. */
  const menuSelectionRef = useRef("");
  const keywordHighlightRef = useRef(keywordHighlight);
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number } | null>(null);
  const [clipStatus, setClipStatus] = useState<string | null>(null);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [pasteStatus, setPasteStatus] = useState<{ done: number; total: number } | null>(null);
  const [pasteBridgeOpen, setPasteBridgeOpen] = useState(false);
  const pasteBridgeRef = useRef<HTMLTextAreaElement | null>(null);
  const pasteBridgeOpenRef = useRef(false);
  pasteBridgeOpenRef.current = pasteBridgeOpen;
  const findInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    onStatusRef.current = onStatus;
    onReadyRef.current = onReady;
    onStdoutRef.current = onStdout;
  }, [onStatus, onReady, onStdout]);

  useEffect(() => {
    autoFocusRef.current = autoFocus;
  }, [autoFocus]);

  useEffect(() => {
    encodingRef.current = encoding;
  }, [encoding]);

  useEffect(() => {
    fontSizeRef.current = Math.max(10, Math.min(28, Number(fontSize) || 13));
    const term = termRef.current;
    if (!term) return;
    term.options.fontSize = fontSizeRef.current;
    try {
      fitRef.current?.fit();
    } catch {
      /* ignore */
    }
  }, [fontSize]);

  useEffect(() => {
    termColorsRef.current = resolvedColors;
    const term = termRef.current;
    if (!term) return;
    term.options.theme = xtermThemeFromColors(resolvedColors);
  }, [resolvedColors.background, resolvedColors.foreground]);

  useEffect(() => {
    if (pasteDelayMs != null) pasteDelayRef.current = pasteDelayMs;
  }, [pasteDelayMs]);

  useEffect(() => {
    if (copyOnSelect != null) copyOnSelectRef.current = copyOnSelect;
  }, [copyOnSelect]);

  keywordHighlightRef.current = keywordHighlight;

  const focusTerminal = () => {
    try {
      termRef.current?.focus();
    } catch {
      /* ignore */
    }
  };

  const sendJson = (payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  };

  const sendStdinWithAudit = (data: string, explicitAuditLine?: string) => {
    if (!data) return;
    const payload: Record<string, unknown> = { type: "stdin", data };
    if (data.includes("\r") || data.includes("\n")) {
      const auditLine = auditLineForEnter(termRef.current, explicitAuditLine);
      if (auditLine) payload.audit_line = auditLine.slice(0, 512);
    }
    sendJson(payload);
  };

  const sendStdinImmediate = (data: string) => {
    sendStdinWithAudit(data);
  };

  /** Wait until device echoes (stdout after sentAt) or maxMs elapses — whichever first. */
  const waitForEchoOrTimeout = (maxMs: number, sentAt: number) =>
    new Promise<void>((resolve) => {
      if (maxMs <= 0) {
        resolve();
        return;
      }
      const started = performance.now();
      const tick = () => {
        if (lastStdoutAtRef.current >= sentAt) {
          // Brief settle so multi-chunk echoes finish before the next line.
          window.setTimeout(resolve, 2);
          return;
        }
        if (performance.now() - started >= maxMs) {
          resolve();
          return;
        }
        window.setTimeout(tick, 2);
      };
      tick();
    });

  /**
   * Line-by-line paste paced by device echo.
   * pasteDelayMs is a *maximum* wait per line; fast responses advance immediately.
   */
  const sendStdinThrottled = (data: string, explicitAuditLine?: string) => {
    if (!data) return;
    const maxDelay = pasteDelayRef.current;
    if (maxDelay <= 0 || data.length < 8) {
      sendStdinWithAudit(data, explicitAuditLine);
      return;
    }
    pasteQueueRef.current = pasteQueueRef.current.then(async () => {
      const lines = data.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
      const total = Math.max(1, lines.length);
      setPasteStatus({ done: 0, total });
      try {
        for (let i = 0; i < lines.length; i += 1) {
          const line = lines[i];
          const chunk = i < lines.length - 1 ? `${line}\r` : line;
          const sentAt = performance.now();
          if (chunk) sendStdinWithAudit(chunk, line.trim() || undefined);
          setPasteStatus({ done: i + 1, total });
          if (i < lines.length - 1) {
            await waitForEchoOrTimeout(maxDelay, sentAt);
          }
        }
      } finally {
        setPasteStatus(null);
      }
    });
  };

  useImperativeHandle(ref, () => ({
    clear: () => {
      termRef.current?.clear();
      termRef.current?.write("\x1b[H\x1b[2J");
    },
    copyAll: async () => {
      const text = termRef.current ? serializeTerminal(termRef.current) : "";
      if (text) await writeClipboardText(text);
      return text;
    },
    copySelection: async () => {
      const text = termRef.current?.getSelection() || "";
      if (text) await writeClipboardText(text);
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
    sendBreak: () => sendJson({ type: "break" }),
    sendText: (data: string, opts?: { throttle?: boolean }) => {
      if (opts?.throttle) sendStdinThrottled(data);
      else sendStdinImmediate(data);
    },
    findNext: (q: string) => {
      const term = termRef.current;
      if (!term || !q) return;
      const hit = findBufferLine(term, q, findLineRef.current + 1, 1);
      if (hit >= 0) {
        findLineRef.current = hit;
        term.scrollToLine(Math.max(0, hit - 2));
      }
    },
    findPrevious: (q: string) => {
      const term = termRef.current;
      if (!term || !q) return;
      const hit = findBufferLine(term, q, Math.max(0, findLineRef.current) - 1, -1);
      if (hit >= 0) {
        findLineRef.current = hit;
        term.scrollToLine(Math.max(0, hit - 2));
      }
    },
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const live = Boolean(sessionId || wsUrl);
    const seed = String(initialOutput || "");
    if (!live && !seed.trim()) return;

    const term = new Terminal({
      cursorBlink: live,
      disableStdin: !live,
      fontSize: fontSizeRef.current,
      fontFamily: 'Consolas, "Courier New", monospace',
      // Lower than CRT-style 10k: remounts + multi-tab stay lighter; server replays log tail on attach.
      scrollback: 4000,
      theme: xtermThemeFromColors(termColorsRef.current),
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
    maybeFocus();
    requestAnimationFrame(() => {
      doFit();
      maybeFocus();
      window.setTimeout(() => {
        doFit();
        maybeFocus();
      }, 50);
    });

    if (seed) {
      term.write(applyKeywordHighlight(seed.endsWith("\n") ? seed : `${seed}\n`, keywordHighlightRef.current));
    }

    // Frozen transcript after disconnect / login failure — no WebSocket.
    // Do not emit onStatus("closed"/"error"): parent already owns that state;
    // re-emitting would bump termEpoch and remount in a loop.
    if (!live) {
      onReadyRef.current?.();
      return () => {
        try {
          term.dispose();
        } catch {
          /* ignore */
        }
        termRef.current = null;
        fitRef.current = null;
      };
    }

    // Guard against React StrictMode remount: the first WS teardown must not
    // report closed/error after a newer socket owns the terminal.
    let cancelled = false;
    let ws: WebSocket | null = null;
    // Server sends status:error then close(4502); ignore the close code so UI keeps the real reason.
    let terminalFailureReported = false;
    onStatusRef.current?.("connecting");

    const isActiveSocket = () => !cancelled && !!ws && wsRef.current === ws;

    // Coalesce high-rate stdout into one paint frame to cut xterm write churn.
    let writeBuf = "";
    let writeRaf = 0;
    const flushWriteBuf = () => {
      writeRaf = 0;
      const chunk = writeBuf;
      writeBuf = "";
      if (!chunk || cancelled) return;
      term.write(applyKeywordHighlight(chunk, keywordHighlightRef.current));
    };
    const writeStdout = (raw: string) => {
      if (!raw || !isActiveSocket()) return;
      lastStdoutAtRef.current = performance.now();
      // Always forward to parent (log buffer / frozen failure transcript); recording flag only gates download.
      onStdoutRef.current?.(raw);
      writeBuf += raw;
      if (writeBuf.length >= 16384) {
        if (writeRaf) {
          cancelAnimationFrame(writeRaf);
          writeRaf = 0;
        }
        flushWriteBuf();
        return;
      }
      if (!writeRaf) {
        writeRaf = requestAnimationFrame(flushWriteBuf);
      }
    };

    const sendResize = () => {
      if (!isActiveSocket()) return;
      doFit();
      sendJson({ type: "resize", cols: term.cols, rows: term.rows });
    };

    const attachHandlers = (socket: WebSocket) => {
      socket.onopen = () => {
        if (!isActiveSocket()) return;
        onStatusRef.current?.("open");
        sendResize();
        onReadyRef.current?.();
        window.setTimeout(maybeFocus, 30);
      };

      socket.onmessage = (ev) => {
        if (!isActiveSocket()) return;
        if (ev.data instanceof ArrayBuffer) {
          writeStdout(decodeBytes(ev.data, encodingRef.current));
          maybeFocus();
          return;
        }
        if (typeof Blob !== "undefined" && ev.data instanceof Blob) {
          void ev.data.arrayBuffer().then((buf) => {
            if (!isActiveSocket()) return;
            writeStdout(decodeBytes(buf, encodingRef.current));
            maybeFocus();
          });
          return;
        }
        try {
          const msg = JSON.parse(String(ev.data || "{}")) as {
            type?: string;
            data?: string;
            state?: string;
            message?: string;
            phase?: string;
            sftp_ready?: boolean;
            cli_hop?: boolean;
          };
          if (msg.type === "stdout" && typeof msg.data === "string") {
            writeStdout(msg.data);
            maybeFocus();
            return;
          }
          if (msg.type === "status") {
            if (!isActiveSocket()) return;
            const phase = typeof msg.phase === "string" ? msg.phase : undefined;
            const meta =
              typeof msg.sftp_ready === "boolean" || typeof msg.cli_hop === "boolean"
                ? {
                    sftpReady: typeof msg.sftp_ready === "boolean" ? msg.sftp_ready : undefined,
                    cliHop: typeof msg.cli_hop === "boolean" ? msg.cli_hop : undefined,
                  }
                : undefined;
            const state = String(msg.state || "");
            if (state === "error" || state === "closed") {
              terminalFailureReported = true;
            }
            onStatusRef.current?.(state, msg.message, phase, meta);
            if (state === "connected" || state === "connecting") {
              maybeFocus();
              return;
            }
            if (state === "warning") {
              const m = String(msg.message || "");
              const dropMatch = /^queue_dropped:(\d+)/i.exec(m);
              if (dropMatch) {
                term.writeln(
                  `\r\n\x1b[33m${tRef.current("webcrt.term.outputTruncated", { count: dropMatch[1] })}\x1b[0m`,
                );
              }
              return;
            }
            if (state === "closed" || state === "error") {
              const rawMsg = String(msg.message || "");
              // Device login failures are already live-echoed into the terminal
              // (incl. Netmiko detail). Avoid dumping a second toast-sized blob.
              let detail = "";
              if (rawMsg) {
                if (/connect_failed|NetmikoTimeoutException|TCP connection to device failed/i.test(rawMsg)) {
                  const one = rawMsg
                    .replace(/^.*connect_failed:/i, "")
                    .split("\n---")[0]
                    .split("\n")[0]
                    .trim()
                    .slice(0, 240);
                  detail = one ? `: ${one}` : "";
                } else {
                  detail = `: ${rawMsg}`;
                }
              }
              term.writeln(
                `\r\n\x1b[33m${tRef.current("webcrt.term.sessionStatus", {
                  state,
                  detail,
                })}\x1b[0m`,
              );
            }
            return;
          }
          if (msg.type === "pong") return;
        } catch {
          writeStdout(String(ev.data || ""));
        }
      };

      socket.onerror = () => {
        if (!isActiveSocket()) return;
        terminalFailureReported = true;
        onStatusRef.current?.("error", "websocket_error");
      };

      socket.onclose = (ev) => {
        // Intentional unmount/remount closes the socket; do not flip UI to "closed".
        if (!isActiveSocket()) return;
        // 45xx = app close after status:error (e.g. connect_timeout → 4502).
        if (terminalFailureReported && ev.code >= 4000 && ev.code < 5000) {
          return;
        }
        onStatusRef.current?.("closed", `websocket_closed:${ev.code}`);
      };

    };

    void (async () => {
      let url = String(wsUrl || "").trim();
      if (sessionId) {
        try {
          url = await webcrtWsUrl(sessionId);
        } catch {
          if (!cancelled) onStatusRef.current?.("error", "websocket_error");
          return;
        }
      }
      if (cancelled || !url) {
        if (!cancelled) onStatusRef.current?.("error", "websocket_error");
        return;
      }
      const socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";
      if (cancelled) {
        try {
          socket.close();
        } catch {
          /* ignore */
        }
        return;
      }
      ws = socket;
      wsRef.current = socket;
      attachHandlers(socket);
    })();

    const dataDisposable = term.onData((data) => {
      const normalized = data.replace(/\x7f/g, "\x08");
      // Capture visible line before Enter moves the cursor to the next row.
      const auditLine =
        normalized.includes("\r") || normalized.includes("\n")
          ? auditLineForEnter(term)
          : undefined;
      // Large pastes from xterm arrive as one onData blob.
      if (normalized.length > 32 || normalized.includes("\r") || normalized.includes("\n")) {
        sendStdinThrottled(normalized, auditLine);
      } else {
        sendStdinWithAudit(normalized, auditLine);
      }
    });

    const selDisposable = term.onSelectionChange(() => {
      if (!copyOnSelectRef.current) return;
      const sel = term.getSelection();
      if (sel) void writeClipboardText(sel).catch(() => undefined);
    });

    const onKeyDownCapture = (e: KeyboardEvent) => {
      if (!autoFocusRef.current) return;
      if (host.closest("[hidden]")) return;
      if (isSidebarSearchTarget(e.target)) return;

      if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) {
        e.preventDefault();
        setFindOpen(true);
        window.setTimeout(() => findInputRef.current?.focus(), 0);
        return;
      }

      // CRT-style clipboard: Ctrl+Shift+C / Ctrl+Insert copy; Ctrl+Shift+V / Shift+Insert paste.
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.shiftKey && (e.key === "C" || e.key === "c")) {
        e.preventDefault();
        e.stopPropagation();
        const text = term.getSelection() || "";
        if (text) void writeClipboardText(text).catch(() => undefined);
        return;
      }
      if ((mod && e.shiftKey && (e.key === "V" || e.key === "v")) || (e.shiftKey && e.key === "Insert")) {
        e.preventDefault();
        e.stopPropagation();
        void (async () => {
          const text = await readClipboardText();
          if (text) {
            sendStdinThrottled(text);
            return;
          }
          // http://LAN: Clipboard API blocked — open paste bridge (paste event still works).
          setPasteBridgeOpen(true);
        })();
        return;
      }
      if (mod && e.key === "Insert") {
        e.preventDefault();
        e.stopPropagation();
        const text = term.getSelection() || "";
        if (text) void writeClipboardText(text).catch(() => undefined);
        return;
      }

      if (e.key === "Backspace") {
        e.preventDefault();
      }

      if (isXtermTextarea(e.target)) {
        maybeFocus();
        return;
      }

      const data = keyEventToStdin(e);
      if (data == null) return;

      e.preventDefault();
      e.stopPropagation();
      maybeFocus();
      if (e.key === "Enter") {
        sendStdinWithAudit("\r", auditLineForEnter(term));
        return;
      }
      sendStdinWithAudit(data);
    };
    window.addEventListener("keydown", onKeyDownCapture, true);

    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      menuSelectionRef.current = term.getSelection() || "";
      setCtxMenu({ x: e.clientX, y: e.clientY });
    };
    host.addEventListener("contextmenu", onContextMenu);

    const onPasteCapture = (e: ClipboardEvent) => {
      if (pasteBridgeOpenRef.current) return;
      if (!autoFocusRef.current) return;
      if (host.closest("[hidden]")) return;
      if (isSidebarSearchTarget(e.target)) return;
      const text = e.clipboardData?.getData("text") || "";
      if (!text) return;
      // Intercept paste so we always apply delay (even if focus is odd).
      if (document.activeElement === term.textarea || host.contains(document.activeElement) || autoFocusRef.current) {
        e.preventDefault();
        sendStdinThrottled(text);
        maybeFocus();
      }
    };
    window.addEventListener("paste", onPasteCapture, true);

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
      cancelled = true;
      if (writeRaf) {
        cancelAnimationFrame(writeRaf);
        writeRaf = 0;
      }
      writeBuf = "";
      window.clearInterval(pingTimer);
      window.removeEventListener("resize", onWinResize);
      window.removeEventListener("keydown", onKeyDownCapture, true);
      window.removeEventListener("paste", onPasteCapture, true);
      host.removeEventListener("contextmenu", onContextMenu);
      ro?.disconnect();
      dataDisposable.dispose();
      selDisposable.dispose();
      if (wsRef.current === ws) wsRef.current = null;
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
    // Fresh ticket per mount when sessionId is set. Do not re-run when a late
    // parent wsUrl fill-in arrives — that remount dropped the wait-loop WS and
    // left Quick Connect stuck on "authenticating".
  }, [sessionId || wsUrl, initialOutput]);

  const pasteFromClipboard = async () => {
    setCtxMenu(null);
    const text = await readClipboardText();
    if (text) {
      sendStdinThrottled(text);
      focusTerminal();
      return;
    }
    // Insecure http://IP cannot read clipboard via API — bridge via paste event.
    setPasteBridgeOpen(true);
  };

  const copySelection = async () => {
    const text = menuSelectionRef.current || termRef.current?.getSelection() || "";
    setCtxMenu(null);
    if (!text) {
      setClipStatus(t("webcrt.actions.copyEmpty"));
      window.setTimeout(() => setClipStatus(null), 2200);
      return;
    }
    try {
      const ok = await writeClipboardText(text);
      setClipStatus(ok ? t("webcrt.actions.copied") : t("webcrt.actions.copyFailed"));
    } catch {
      setClipStatus(t("webcrt.actions.copyFailed"));
    }
    window.setTimeout(() => setClipStatus(null), 2200);
  };

  useEffect(() => {
    if (!pasteBridgeOpen) return;
    window.setTimeout(() => pasteBridgeRef.current?.focus(), 0);
  }, [pasteBridgeOpen]);

  return (
    <div className="webcrt-term-wrap">
      {pasteStatus ? (
        <div className="webcrt-paste-status" role="status" aria-live="polite">
          {t("webcrt.term.pasting", { done: pasteStatus.done, total: pasteStatus.total })}
        </div>
      ) : null}
      {clipStatus ? (
        <div className="webcrt-paste-status" role="status" aria-live="polite">
          {clipStatus}
        </div>
      ) : null}
      {pasteBridgeOpen ? (
        <div className="webcrt-paste-bridge" role="dialog" aria-label={t("webcrt.term.paste")}>
          <div className="webcrt-paste-bridge__card">
            <p className="webcrt-paste-bridge__hint">{t("webcrt.term.pasteBridgeHint")}</p>
            <textarea
              ref={pasteBridgeRef}
              className="webcrt-paste-bridge__input"
              rows={4}
              placeholder={t("webcrt.term.pasteBridgePh")}
              onPaste={(e) => {
                const text = e.clipboardData?.getData("text") || "";
                if (!text) return;
                e.preventDefault();
                setPasteBridgeOpen(false);
                sendStdinThrottled(text);
                focusTerminal();
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  setPasteBridgeOpen(false);
                  focusTerminal();
                }
              }}
            />
            <div className="webcrt-paste-bridge__actions">
              <button
                type="button"
                onClick={() => {
                  const text = pasteBridgeRef.current?.value || "";
                  setPasteBridgeOpen(false);
                  if (text) sendStdinThrottled(text);
                  focusTerminal();
                }}
              >
                {t("webcrt.term.paste")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setPasteBridgeOpen(false);
                  focusTerminal();
                }}
              >
                {t("webcrt.term.pasteBridgeCancel")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {findOpen ? (
        <div className="webcrt-find">
          <input
            ref={findInputRef}
            type="search"
            value={findQuery}
            placeholder={t("webcrt.term.findPh")}
            aria-label={t("webcrt.term.findPh")}
            onChange={(e) => {
              setFindQuery(e.target.value);
              findLineRef.current = -1;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                const term = termRef.current;
                if (!term || !findQuery) return;
                const hit = findBufferLine(
                  term,
                  findQuery,
                  e.shiftKey ? Math.max(0, findLineRef.current) - 1 : findLineRef.current + 1,
                  e.shiftKey ? -1 : 1,
                );
                if (hit >= 0) {
                  findLineRef.current = hit;
                  term.scrollToLine(Math.max(0, hit - 2));
                }
              } else if (e.key === "Escape") {
                setFindOpen(false);
                focusTerminal();
              }
            }}
          />
          <button
            type="button"
            aria-label={t("webcrt.term.findPrev")}
            title={t("webcrt.term.findPrev")}
            onClick={() => {
              const term = termRef.current;
              if (!term || !findQuery) return;
              const hit = findBufferLine(term, findQuery, Math.max(0, findLineRef.current) - 1, -1);
              if (hit >= 0) {
                findLineRef.current = hit;
                term.scrollToLine(Math.max(0, hit - 2));
              }
            }}
          >
            ↑
          </button>
          <button
            type="button"
            aria-label={t("webcrt.term.findNext")}
            title={t("webcrt.term.findNext")}
            onClick={() => {
              const term = termRef.current;
              if (!term || !findQuery) return;
              const hit = findBufferLine(term, findQuery, findLineRef.current + 1, 1);
              if (hit >= 0) {
                findLineRef.current = hit;
                term.scrollToLine(Math.max(0, hit - 2));
              }
            }}
          >
            ↓
          </button>
          <button
            type="button"
            aria-label={t("webcrt.term.findClose")}
            title={t("webcrt.term.findClose")}
            onClick={() => {
              setFindOpen(false);
              focusTerminal();
            }}
          >
            ×
          </button>
        </div>
      ) : null}
      <div
        className="webcrt-term"
        ref={hostRef}
        onMouseDown={() => {
          focusTerminal();
          setCtxMenu(null);
        }}
      />
      {ctxMenu ? (
        <div
          className="webcrt-ctx"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <button type="button" onClick={() => void copySelection()}>
            {t("webcrt.term.copy")}
          </button>
          <button type="button" onClick={() => void pasteFromClipboard()}>
            {t("webcrt.term.paste")}
          </button>
          <button
            type="button"
            onClick={() => {
              termRef.current?.clear();
              termRef.current?.write("\x1b[H\x1b[2J");
              setCtxMenu(null);
            }}
          >
            {t("webcrt.term.clear")}
          </button>
        </div>
      ) : null}
    </div>
  );
});
