import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import {
  applyKeywordHighlight,
  type KeywordHighlightConfig,
} from "../utils/webcrtKeywordHighlight";

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
  wsUrl: string;
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
  onStatus?: (state: string, message?: string, phase?: string) => void;
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
    title,
    recording,
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
  },
  ref,
) {
  const resolvedColors: TermColors = termColors ||
    (themeName === "light"
      ? { background: "#ffffff", foreground: "#000000" }
      : DEFAULT_TERM_COLORS);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const findIndexRef = useRef(0);
  const onStatusRef = useRef(onStatus);
  const onReadyRef = useRef(onReady);
  const onStdoutRef = useRef(onStdout);
  const recordingRef = useRef(!!recording);
  const autoFocusRef = useRef(autoFocus);
  const encodingRef = useRef(encoding);
  const fontSizeRef = useRef(Math.max(10, Math.min(28, Number(fontSize) || 13)));
  const termColorsRef = useRef<TermColors>(resolvedColors);
  const pasteDelayRef = useRef(pasteDelayMs ?? loadPrefs().pasteDelayMs);
  const copyOnSelectRef = useRef(copyOnSelect ?? loadPrefs().copyOnSelect);
  const pasteQueueRef = useRef<Promise<void>>(Promise.resolve());
  /** Updated whenever device stdout arrives; used to pace paste by echo, not fixed sleep. */
  const lastStdoutAtRef = useRef(0);
  const keywordHighlightRef = useRef(keywordHighlight);
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number } | null>(null);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [pasteStatus, setPasteStatus] = useState<{ done: number; total: number } | null>(null);
  const findInputRef = useRef<HTMLInputElement | null>(null);

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

  const sendStdinImmediate = (data: string) => {
    if (!data) return;
    sendJson({ type: "stdin", data });
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
  const sendStdinThrottled = (data: string) => {
    if (!data) return;
    const maxDelay = pasteDelayRef.current;
    if (maxDelay <= 0 || data.length < 8) {
      sendStdinImmediate(data);
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
          if (chunk) sendStdinImmediate(chunk);
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
      if (text && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      }
      return text;
    },
    copySelection: async () => {
      const text = termRef.current?.getSelection() || "";
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
    sendBreak: () => sendJson({ type: "break" }),
    sendText: (data: string, opts?: { throttle?: boolean }) => {
      if (opts?.throttle) sendStdinThrottled(data);
      else sendStdinImmediate(data);
    },
    findNext: (q: string) => {
      const term = termRef.current;
      if (!term || !q) return;
      const text = serializeTerminal(term).toLowerCase();
      const needle = q.toLowerCase();
      let idx = text.indexOf(needle, findIndexRef.current + 1);
      if (idx < 0) idx = text.indexOf(needle);
      if (idx >= 0) {
        findIndexRef.current = idx;
        // Approximate scroll: each buffer line ~1 row.
        const line = text.slice(0, idx).split("\n").length - 1;
        term.scrollToLine(Math.max(0, line - 2));
      }
    },
    findPrevious: (q: string) => {
      const term = termRef.current;
      if (!term || !q) return;
      const text = serializeTerminal(term).toLowerCase();
      const needle = q.toLowerCase();
      const before = text.slice(0, Math.max(0, findIndexRef.current));
      let idx = before.lastIndexOf(needle);
      if (idx < 0) idx = text.lastIndexOf(needle);
      if (idx >= 0) {
        findIndexRef.current = idx;
        const line = text.slice(0, idx).split("\n").length - 1;
        term.scrollToLine(Math.max(0, line - 2));
      }
    },
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: fontSizeRef.current,
      fontFamily: 'Consolas, "Courier New", monospace',
      scrollback: 10000,
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

    // Guard against React StrictMode remount: the first WS teardown must not
    // report closed/error after a newer socket owns the terminal.
    let cancelled = false;
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    onStatusRef.current?.("connecting");

    const isActiveSocket = () => !cancelled && wsRef.current === ws;

    const writeStdout = (raw: string) => {
      if (!raw || !isActiveSocket()) return;
      lastStdoutAtRef.current = performance.now();
      if (recordingRef.current) onStdoutRef.current?.(raw);
      term.write(applyKeywordHighlight(raw, keywordHighlightRef.current));
    };

    const sendResize = () => {
      if (!isActiveSocket()) return;
      doFit();
      sendJson({ type: "resize", cols: term.cols, rows: term.rows });
    };

    ws.onopen = () => {
      if (!isActiveSocket()) return;
      onStatusRef.current?.("open");
      sendResize();
      onReadyRef.current?.();
      window.setTimeout(maybeFocus, 30);
    };

    ws.onmessage = (ev) => {
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
        };
        if (msg.type === "stdout" && typeof msg.data === "string") {
          writeStdout(msg.data);
          maybeFocus();
          return;
        }
        if (msg.type === "status") {
          if (!isActiveSocket()) return;
          const phase = typeof msg.phase === "string" ? msg.phase : undefined;
          onStatusRef.current?.(String(msg.state || ""), msg.message, phase);
          if (msg.state === "connected" || msg.state === "connecting") {
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
      if (!isActiveSocket()) return;
      onStatusRef.current?.("error", "websocket_error");
      term.writeln("\r\n\x1b[31m[websocket error]\x1b[0m");
    };

    ws.onclose = (ev) => {
      // Intentional unmount/remount closes the socket; do not flip UI to "closed".
      if (!isActiveSocket()) return;
      onStatusRef.current?.("closed", `websocket_closed:${ev.code}`);
      if (!ev.wasClean) {
        term.writeln(`\r\n\x1b[33m[websocket closed code=${ev.code}]\x1b[0m`);
      }
    };

    const dataDisposable = term.onData((data) => {
      const normalized = data.replace(/\x7f/g, "\x08");
      // Large pastes from xterm arrive as one onData blob.
      if (normalized.length > 32 || normalized.includes("\r") || normalized.includes("\n")) {
        sendStdinThrottled(normalized);
      } else {
        sendStdinImmediate(normalized);
      }
    });

    const selDisposable = term.onSelectionChange(() => {
      if (!copyOnSelectRef.current) return;
      const sel = term.getSelection();
      if (sel && navigator.clipboard?.writeText) {
        void navigator.clipboard.writeText(sel).catch(() => undefined);
      }
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
      sendStdinImmediate(data);
    };
    window.addEventListener("keydown", onKeyDownCapture, true);

    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      setCtxMenu({ x: e.clientX, y: e.clientY });
    };
    host.addEventListener("contextmenu", onContextMenu);

    const onPasteCapture = (e: ClipboardEvent) => {
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
        ws.close();
      } catch {
        /* ignore */
      }
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
    // title is display-only; remounting on title change tears down a live WS.
  }, [wsUrl]);

  const pasteFromClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      sendStdinThrottled(text);
    } catch {
      /* ignore */
    }
    setCtxMenu(null);
    focusTerminal();
  };

  const copySelection = async () => {
    const text = termRef.current?.getSelection() || "";
    if (text && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    }
    setCtxMenu(null);
  };

  return (
    <div className="webcrt-term-wrap">
      {pasteStatus ? (
        <div className="webcrt-paste-status" role="status" aria-live="polite">
          粘贴中 {pasteStatus.done}/{pasteStatus.total} 行…
        </div>
      ) : null}
      {findOpen ? (
        <div className="webcrt-find">
          <input
            ref={findInputRef}
            type="search"
            value={findQuery}
            placeholder="Find…"
            onChange={(e) => setFindQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                const term = termRef.current;
                if (!term || !findQuery) return;
                const text = serializeTerminal(term).toLowerCase();
                const needle = findQuery.toLowerCase();
                if (e.shiftKey) {
                  const before = text.slice(0, Math.max(0, findIndexRef.current));
                  let idx = before.lastIndexOf(needle);
                  if (idx < 0) idx = text.lastIndexOf(needle);
                  if (idx >= 0) {
                    findIndexRef.current = idx;
                    term.scrollToLine(Math.max(0, text.slice(0, idx).split("\n").length - 3));
                  }
                } else {
                  let idx = text.indexOf(needle, findIndexRef.current + 1);
                  if (idx < 0) idx = text.indexOf(needle);
                  if (idx >= 0) {
                    findIndexRef.current = idx;
                    term.scrollToLine(Math.max(0, text.slice(0, idx).split("\n").length - 3));
                  }
                }
              } else if (e.key === "Escape") {
                setFindOpen(false);
                focusTerminal();
              }
            }}
          />
          <button
            type="button"
            onClick={() => {
              const term = termRef.current;
              if (!term || !findQuery) return;
              const text = serializeTerminal(term).toLowerCase();
              const needle = findQuery.toLowerCase();
              const before = text.slice(0, Math.max(0, findIndexRef.current));
              let idx = before.lastIndexOf(needle);
              if (idx < 0) idx = text.lastIndexOf(needle);
              if (idx >= 0) {
                findIndexRef.current = idx;
                term.scrollToLine(Math.max(0, text.slice(0, idx).split("\n").length - 3));
              }
            }}
          >
            ↑
          </button>
          <button
            type="button"
            onClick={() => {
              const term = termRef.current;
              if (!term || !findQuery) return;
              const text = serializeTerminal(term).toLowerCase();
              const needle = findQuery.toLowerCase();
              let idx = text.indexOf(needle, findIndexRef.current + 1);
              if (idx < 0) idx = text.indexOf(needle);
              if (idx >= 0) {
                findIndexRef.current = idx;
                term.scrollToLine(Math.max(0, text.slice(0, idx).split("\n").length - 3));
              }
            }}
          >
            ↓
          </button>
          <button
            type="button"
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
            Copy
          </button>
          <button type="button" onClick={() => void pasteFromClipboard()}>
            Paste
          </button>
          <button
            type="button"
            onClick={() => {
              termRef.current?.clear();
              termRef.current?.write("\x1b[H\x1b[2J");
              setCtxMenu(null);
            }}
          >
            Clear
          </button>
        </div>
      ) : null}
    </div>
  );
});
