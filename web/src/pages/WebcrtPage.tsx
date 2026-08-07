import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import type { WebTerminalHandle } from "../components/WebTerminal";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import {
  ApiRequestError,
  closeWebcrtSession,
  closeWebcrtSessionsKeepalive,
  createWebcrtSession,
  deleteManagedNe,
  fetchCliTargets,
  fetchManagedNeById,
  quickConnectWebcrtSession,
  updateManagedNe,
  webcrtSftpDownload,
  webcrtSftpList,
  webcrtSftpChmod,
  webcrtSftpMkdir,
  webcrtSftpRemove,
  webcrtSftpRename,
  webcrtSftpUpload,
  type WebcrtSftpItem,
} from "../services/api";
import { pageCount } from "../utils/display";
import { writeClipboardText } from "../utils/clipboard";
import type { CliTargetItem, ManagedNeItem } from "../types";
import {
  defaultKeywordHighlightConfig,
  loadKeywordHighlightConfig,
  newKeywordId,
  saveKeywordHighlightConfig,
  type KeywordHighlightConfig,
  type KeywordRule,
} from "../utils/webcrtKeywordHighlight";

/** xterm is large — only pull it when a session pane actually mounts. */
const WebTerminal = lazy(() =>
  import("../components/WebTerminal").then((m) => ({ default: m.WebTerminal })),
);
const PAGE_SIZE = 50;
const SESSION_OPTS_KEY = "netx.webcrt.sessionOptions";
const LEGACY_TERM_PREFS_KEY = "netx.webcrt.termPrefs";
const SFTP_WIDTH_KEY = "netx.webcrt.sftpWidth";
const SFTP_WIDTH_DEFAULT = 480;
const SFTP_WIDTH_MIN = 280;
const SFTP_COL_WIDTHS_KEY = "netx.webcrt.sftpColWidths";

type SftpColKey = "name" | "size" | "mtime" | "owner" | "group" | "mode";
type SftpColWidths = Record<SftpColKey, number>;

const SFTP_COL_KEYS: SftpColKey[] = ["name", "size", "mtime", "owner", "group", "mode"];
const SFTP_COL_WIDTH_DEFAULTS: SftpColWidths = {
  name: 220,
  size: 72,
  mtime: 140,
  owner: 72,
  group: 72,
  mode: 88,
};
const SFTP_COL_WIDTH_MIN: SftpColWidths = {
  name: 120,
  size: 48,
  mtime: 96,
  owner: 48,
  group: 48,
  mode: 64,
};
const ENCODING_OPTIONS = ["utf-8", "gbk", "gb2312", "gb18030"] as const;
const FONT_SIZE_OPTIONS = [12, 13, 14, 16, 18, 20] as const;
const PASTE_DELAY_OPTIONS = [0, 20, 40, 60, 100, 150] as const;
const KEEPALIVE_OPTIONS = [0, 15, 30, 60, 120] as const;
/** Compact recording chunks before they grow unbounded (join + trim). */
const LOG_COMPACT_CHUNKS = 1500;
const LOG_MAX_CHARS = 8 * 1024 * 1024;
/** Keep active + recent tabs mounted; colder tabs detach WS until focused (fresh ticket on remount). */
const WARM_TAB_LIMIT = 8;
/** Legacy key — cleared on load; sessions no longer auto-restore after leaving WebCRT. */
const OPEN_TABS_STORAGE_KEY = "netx.webcrt.openTabs.v1";

type ColorSchemeId = "dark" | "blackWhite" | "whiteBlack" | "greenBlack" | "amberBlack" | "custom";

const COLOR_SCHEMES: Record<Exclude<ColorSchemeId, "custom">, { background: string; foreground: string }> = {
  dark: { background: "#0b1220", foreground: "#e2e8f0" },
  blackWhite: { background: "#ffffff", foreground: "#000000" },
  whiteBlack: { background: "#000000", foreground: "#ffffff" },
  greenBlack: { background: "#000000", foreground: "#33ff33" },
  amberBlack: { background: "#1a1200", foreground: "#ffb000" },
};

const COLOR_SCHEME_IDS = Object.keys(COLOR_SCHEMES) as Array<Exclude<ColorSchemeId, "custom">>;

type SessionOptions = {
  encoding: string;
  fontSize: number;
  colorScheme: ColorSchemeId;
  background: string;
  foreground: string;
  copyOnSelect: boolean;
  pasteDelayMs: number;
  keepaliveSec: number;
};

function normalizeHexColor(hex: string, fallback: string): string {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(String(hex || "").trim());
  return m ? `#${m[1].toLowerCase()}` : fallback;
}

function matchColorScheme(background: string, foreground: string): ColorSchemeId {
  const bg = normalizeHexColor(background, "");
  const fg = normalizeHexColor(foreground, "");
  for (const id of COLOR_SCHEME_IDS) {
    const preset = COLOR_SCHEMES[id];
    if (preset.background === bg && preset.foreground === fg) return id;
  }
  return "custom";
}

type ConnectPhase = "creating" | "authenticating" | "waiting_prompt";

type HostForm = {
  name: string;
  ip_address: string;
  port: number;
  protocol: "ssh" | "telnet";
};

type AuthForm = {
  username: string;
  password: string;
  savePassword: boolean;
};

type AuthDialogState = {
  mode: "quick" | "retry";
  host: HostForm;
  /** When retrying an existing tree target (ne_id known). */
  target?: CliTargetItem;
  /** Claim/promote this ManagedNE (LLDP placeholder) via quick-connect. */
  claimNeId?: string;
  errorHint?: string;
};

type TermTab = {
  key: string;
  sessionId: string;
  wsUrl: string;
  termEpoch: number;
  target: CliTargetItem;
  status: "connecting" | "connected" | "error" | "closed";
  connectPhase?: ConnectPhase;
  errorMessage?: string;
  recording: boolean;
  encoding: string;
  /** From session create — nested CLI hop cannot use SFTP. */
  cliHop?: boolean;
  /** SFTP channel attached on the live SSH transport after connect. */
  sftpReady?: boolean;
};

type TabMenuState = { key: string; x: number; y: number };

type TreeMenuState = { target: CliTargetItem; x: number; y: number };

function connectPhaseLabel(
  phase: ConnectPhase | undefined,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (phase === "waiting_prompt") return t("webcrt.phase.waitingPrompt");
  if (phase === "authenticating") return t("webcrt.phase.authenticating");
  if (phase === "creating") return t("webcrt.phase.creating");
  return t("webcrt.status.connecting");
}

function defaultSessionOptions(): SessionOptions {
  return {
    encoding: "utf-8",
    fontSize: 13,
    colorScheme: "dark",
    background: COLOR_SCHEMES.dark.background,
    foreground: COLOR_SCHEMES.dark.foreground,
    copyOnSelect: true,
    pasteDelayMs: 40,
    keepaliveSec: 0,
  };
}

function loadSessionOptions(): SessionOptions {
  const base = defaultSessionOptions();
  try {
    const legacyRaw = localStorage.getItem(LEGACY_TERM_PREFS_KEY);
    if (legacyRaw) {
      const legacy = JSON.parse(legacyRaw) as Partial<{ copyOnSelect: boolean; pasteDelayMs: number }>;
      if (legacy.copyOnSelect === false) base.copyOnSelect = false;
      if (legacy.pasteDelayMs != null) {
        base.pasteDelayMs = Math.max(0, Math.min(200, Number(legacy.pasteDelayMs) || 40));
      }
    }
    const raw = localStorage.getItem(SESSION_OPTS_KEY);
    if (!raw) return base;
    const j = JSON.parse(raw) as Partial<SessionOptions> & { theme?: string };
    // Migrate legacy theme dark/light.
    let background = normalizeHexColor(String(j.background || ""), "");
    let foreground = normalizeHexColor(String(j.foreground || ""), "");
    let colorScheme = String(j.colorScheme || "") as ColorSchemeId;
    if (!background || !foreground) {
      if (j.theme === "light") {
        background = COLOR_SCHEMES.blackWhite.background;
        foreground = COLOR_SCHEMES.blackWhite.foreground;
        colorScheme = "blackWhite";
      } else {
        background = COLOR_SCHEMES.dark.background;
        foreground = COLOR_SCHEMES.dark.foreground;
        colorScheme = "dark";
      }
    }
    if (colorScheme !== "custom" && colorScheme in COLOR_SCHEMES) {
      const preset = COLOR_SCHEMES[colorScheme as Exclude<ColorSchemeId, "custom">];
      background = preset.background;
      foreground = preset.foreground;
    } else {
      colorScheme = matchColorScheme(background, foreground);
    }
    return {
      encoding: String(j.encoding || base.encoding).trim().toLowerCase() || "utf-8",
      fontSize: Math.max(10, Math.min(28, Number(j.fontSize) || base.fontSize)),
      colorScheme,
      background,
      foreground,
      copyOnSelect: j.copyOnSelect !== false,
      pasteDelayMs: Math.max(0, Math.min(200, Number(j.pasteDelayMs ?? base.pasteDelayMs) || 40)),
      keepaliveSec: Math.max(0, Math.min(600, Number(j.keepaliveSec ?? base.keepaliveSec) || 0)),
    };
  } catch {
    return base;
  }
}

function saveSessionOptions(opts: SessionOptions) {
  localStorage.setItem(SESSION_OPTS_KEY, JSON.stringify(opts));
  localStorage.setItem(
    LEGACY_TERM_PREFS_KEY,
    JSON.stringify({ copyOnSelect: opts.copyOnSelect, pasteDelayMs: opts.pasteDelayMs }),
  );
}

function normalizeSessionOptions(draft: SessionOptions): SessionOptions {
  const background = normalizeHexColor(draft.background, COLOR_SCHEMES.dark.background);
  const foreground = normalizeHexColor(draft.foreground, COLOR_SCHEMES.dark.foreground);
  let colorScheme = draft.colorScheme;
  if (colorScheme !== "custom" && colorScheme in COLOR_SCHEMES) {
    const preset = COLOR_SCHEMES[colorScheme as Exclude<ColorSchemeId, "custom">];
    return {
      encoding: draft.encoding || "utf-8",
      fontSize: Math.max(10, Math.min(28, Number(draft.fontSize) || 13)),
      colorScheme,
      background: preset.background,
      foreground: preset.foreground,
      copyOnSelect: draft.copyOnSelect !== false,
      pasteDelayMs: Math.max(0, Math.min(200, Number(draft.pasteDelayMs) || 0)),
      keepaliveSec: Math.max(0, Math.min(600, Number(draft.keepaliveSec) || 0)),
    };
  }
  return {
    encoding: draft.encoding || "utf-8",
    fontSize: Math.max(10, Math.min(28, Number(draft.fontSize) || 13)),
    colorScheme: matchColorScheme(background, foreground),
    background,
    foreground,
    copyOnSelect: draft.copyOnSelect !== false,
    pasteDelayMs: Math.max(0, Math.min(200, Number(draft.pasteDelayMs) || 0)),
    keepaliveSec: Math.max(0, Math.min(600, Number(draft.keepaliveSec) || 0)),
  };
}

const emptyHostForm = (): HostForm => ({
  name: "",
  ip_address: "",
  port: 22,
  protocol: "ssh",
});

const emptyAuthForm = (username = ""): AuthForm => ({
  username,
  password: "",
  savePassword: true,
});

function FormLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <span className="form-label">
      {children}
      {required ? (
        <span className="form-label__required" title="required" aria-hidden="true">
          {" "}
          *
        </span>
      ) : null}
    </span>
  );
}

function targetKey(t: Pick<CliTargetItem, "source" | "id">): string {
  return `${t.source}:${t.id}`;
}

function deviceLabel(t: Pick<CliTargetItem, "name" | "ip_address">): string {
  return String(t.name || t.ip_address || "").trim() || "-";
}

function ComputerIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden>
      <path d="M4 3h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-5v2h2v2H7v-2h2v-2H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm1 2v9h14V5H5z" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden>
      <path d="M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden>
      <path d="M6 2h8l4 4v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm7 1.5V8h4.5L13 3.5z" />
    </svg>
  );
}

function isSshAuthFailure(err: unknown): boolean {
  const raw = String(err).toLowerCase();
  return (
    raw.includes("auth_rejected") ||
    raw.includes("authentication") ||
    raw.includes("auth_failed") ||
    raw.includes("permission denied") ||
    raw.includes("credentials_incomplete") ||
    raw.includes("password rejected") ||
    raw.includes("incorrect password")
  );
}

/** Inventory SSH (managed / Quick Connect). Telnet authenticates in-terminal; UME uses shared profile. */
function isInventorySsh(target: Pick<CliTargetItem, "source" | "protocol">): boolean {
  const src = String(target.source || "").toLowerCase();
  if (src !== "webcrt" && src !== "managed") return false;
  return String(target.protocol || "ssh").toLowerCase() !== "telnet";
}

/** LLDP / topology placeholders / incomplete inventory rows need New Session (host/IP) before auth. */
function needsSessionSetup(
  target: Pick<CliTargetItem, "source" | "ne_source" | "ip_address">,
): boolean {
  const listSrc = String(target.source || "").toLowerCase();
  if (listSrc === "ume") return false;
  if (listSrc !== "managed" && listSrc !== "webcrt") return false;
  const neSrc = String(target.ne_source || "").trim().toLowerCase();
  if (neSrc === "lldp" || neSrc === "topology") return true;
  return !String(target.ip_address || "").trim();
}

function defaultPortForProtocol(protocol: "ssh" | "telnet", port?: number): number {
  const n = Number(port) || 0;
  if (n > 0) return n;
  return protocol === "telnet" ? 23 : 22;
}

function isSessionGoneError(err: unknown): boolean {
  const raw = String(err).toLowerCase();
  return (
    raw.includes("webcrt_session_not_found") ||
    raw.includes("session_not_found") ||
    raw.includes("4404")
  );
}

function isDeviceClosedMessage(err: unknown): boolean {
  const raw = String(err).toLowerCase();
  return (
    raw.includes("device_closed") ||
    raw.includes("client_close") ||
    raw.includes("client_delete") ||
    raw.includes("cli_hop_return") ||
    raw.includes("idle_timeout") ||
    raw.includes("detach_timeout") ||
    raw.includes("attach_timeout") ||
    raw.includes("session closed")
  );
}

/** SFTP is direct SSH only; returns i18n key when unavailable. */
function sftpUnavailableReason(
  tab: Pick<TermTab, "target" | "cliHop" | "sftpReady" | "status">,
):
  | "webcrt.err.sftpSsh"
  | "webcrt.err.sftpHop"
  | "webcrt.err.sftpNeedPassword"
  | "webcrt.err.sftpUnsupported"
  | null {
  const proto = String(tab.target.protocol || "ssh").toLowerCase();
  if (proto === "telnet") return "webcrt.err.sftpSsh";
  if (tab.cliHop || tab.target.hop_enabled) return "webcrt.err.sftpHop";
  // Prefer the live SSH session channel; inventory still needs saved password for pool fallback.
  if (tab.target.source !== "ume" && !tab.target.has_password && tab.sftpReady !== true) {
    return "webcrt.err.sftpNeedPassword";
  }
  if (tab.status === "connected" && tab.sftpReady === false) return "webcrt.err.sftpUnsupported";
  return null;
}

function sftpParentPath(path: string): string {
  const cur = String(path || ".").replace(/\\/g, "/").replace(/\/+$/, "") || ".";
  if (cur === "." || cur === "/") return cur === "/" ? "/" : ".";
  const parts = cur.split("/").filter((p, i) => (i === 0 && cur.startsWith("/")) || Boolean(p));
  if (parts.length <= 1) return cur.startsWith("/") ? "/" : ".";
  parts.pop();
  if (cur.startsWith("/")) {
    return parts.length <= 1 ? "/" : parts.join("/");
  }
  const parent = parts.join("/");
  return parent || ".";
}

function joinSftpPath(base: string, name: string): string {
  const n = String(name || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!n || n === ".") return String(base || ".") || ".";
  if (n === "..") return sftpParentPath(base);
  const b = String(base || ".").replace(/\\/g, "/").replace(/\/+$/, "") || ".";
  if (b === "." || b === "") return n;
  if (b === "/") return `/${n}`;
  return `${b}/${n}`;
}

function loadSftpWidth(): number {
  try {
    const n = Number(localStorage.getItem(SFTP_WIDTH_KEY));
    if (Number.isFinite(n) && n >= SFTP_WIDTH_MIN) return Math.min(Math.round(n), 1200);
  } catch {
    /* ignore */
  }
  return SFTP_WIDTH_DEFAULT;
}

function saveSftpWidth(width: number): void {
  try {
    localStorage.setItem(SFTP_WIDTH_KEY, String(Math.round(width)));
  } catch {
    /* ignore */
  }
}

function clampSftpColWidth(key: SftpColKey, width: number): number {
  const min = SFTP_COL_WIDTH_MIN[key];
  const n = Math.round(Number(width) || 0);
  if (!Number.isFinite(n)) return SFTP_COL_WIDTH_DEFAULTS[key];
  return Math.max(min, Math.min(n, 640));
}

function normalizeSftpColWidths(raw: unknown): SftpColWidths {
  const base = { ...SFTP_COL_WIDTH_DEFAULTS };
  if (!raw || typeof raw !== "object") return base;
  const obj = raw as Record<string, unknown>;
  for (const key of SFTP_COL_KEYS) {
    const v = obj[key];
    if (typeof v === "number" || typeof v === "string") {
      base[key] = clampSftpColWidth(key, Number(v));
    }
  }
  return base;
}

function loadSftpColWidths(): SftpColWidths {
  try {
    const raw = localStorage.getItem(SFTP_COL_WIDTHS_KEY);
    if (!raw) return { ...SFTP_COL_WIDTH_DEFAULTS };
    return normalizeSftpColWidths(JSON.parse(raw));
  } catch {
    return { ...SFTP_COL_WIDTH_DEFAULTS };
  }
}

function saveSftpColWidths(widths: SftpColWidths): void {
  try {
    localStorage.setItem(SFTP_COL_WIDTHS_KEY, JSON.stringify(widths));
  } catch {
    /* ignore */
  }
}

function sumSftpColWidths(widths: SftpColWidths): number {
  return SFTP_COL_KEYS.reduce((acc, key) => acc + widths[key], 0);
}

function formatSftpSizeKb(bytes: number, isDir: boolean): string {
  if (isDir) return "";
  const kb = Number(bytes || 0) / 1024;
  if (!Number.isFinite(kb) || kb <= 0) return "0";
  if (kb < 10) return kb.toFixed(1);
  return String(Math.round(kb));
}

function formatSftpMtime(sec: number): string {
  const n = Number(sec || 0);
  if (!Number.isFinite(n) || n <= 0) return "";
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatSftpTransferPct(loaded: number, total: number): string {
  const t = Number(total || 0);
  if (!Number.isFinite(t) || t <= 0) return "";
  const pct = Math.min(100, Math.max(0, Math.round((Number(loaded || 0) / t) * 100)));
  return `${pct}%`;
}

type SftpSortKey = SftpColKey;

function sortSftpItems(items: WebcrtSftpItem[], key: SftpSortKey, dir: "asc" | "desc"): WebcrtSftpItem[] {
  const mul = dir === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    let cmp = 0;
    if (key === "size" || key === "mtime") {
      cmp = Number(a[key] || 0) - Number(b[key] || 0);
    } else if (key === "name") {
      cmp = String(a.name || "").localeCompare(String(b.name || ""), undefined, { sensitivity: "base" });
    } else {
      cmp = String(a[key] || "").localeCompare(String(b[key] || ""), undefined, { sensitivity: "base" });
    }
    if (cmp !== 0) return cmp * mul;
    return String(a.name || "").localeCompare(String(b.name || ""), undefined, { sensitivity: "base" });
  });
}

function sftpTargetIds(tab: Pick<TermTab, "target">): { ne_id?: string; ume_ne_id?: string } {
  return tab.target.source === "ume"
    ? { ume_ne_id: tab.target.ume_ne_id || tab.target.id }
    : { ne_id: tab.target.id };
}

type SftpUploadItem = { file: File; relativePath: string };

type FsEntryLike = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file: (ok: (f: File) => void, err?: (e: DOMException) => void) => void;
  createReader: () => {
    readEntries: (ok: (entries: FsEntryLike[]) => void, err?: (e: DOMException) => void) => void;
  };
};

async function readAllDirectoryEntries(
  reader: ReturnType<FsEntryLike["createReader"]>,
): Promise<FsEntryLike[]> {
  const all: FsEntryLike[] = [];
  for (;;) {
    const batch = await new Promise<FsEntryLike[]>((resolve, reject) => {
      reader.readEntries(resolve, reject);
    });
    if (!batch.length) break;
    all.push(...batch);
  }
  return all;
}

async function walkFsEntry(entry: FsEntryLike, prefix: string, out: SftpUploadItem[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => {
      entry.file(resolve, reject);
    });
    const relativePath = (prefix ? `${prefix}/${entry.name}` : entry.name).replace(/\\/g, "/");
    out.push({ file, relativePath });
    return;
  }
  if (!entry.isDirectory) return;
  const nextPrefix = prefix ? `${prefix}/${entry.name}` : entry.name;
  const children = await readAllDirectoryEntries(entry.createReader());
  for (const child of children) {
    await walkFsEntry(child, nextPrefix, out);
  }
}

async function collectDroppedSftpUploads(dt: DataTransfer): Promise<SftpUploadItem[]> {
  const out: SftpUploadItem[] = [];
  const items = Array.from(dt.items || []);
  const entries = items
    .map((it) => {
      const getter = (it as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntry | null }).webkitGetAsEntry;
      return typeof getter === "function" ? (getter.call(it) as FsEntryLike | null) : null;
    })
    .filter((e): e is FsEntryLike => Boolean(e));
  if (entries.length) {
    for (const entry of entries) {
      await walkFsEntry(entry, "", out);
    }
    return out;
  }
  for (const file of Array.from(dt.files || [])) {
    const rel = String((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name)
      .replace(/\\/g, "/")
      .replace(/^\/+/, "");
    out.push({ file, relativePath: rel || file.name });
  }
  return out;
}

function connectFailedNe(err: unknown): ManagedNeItem | null {
  if (!(err instanceof ApiRequestError)) return null;
  const detail = err.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const ne = (detail as { ne?: unknown }).ne;
  if (!ne || typeof ne !== "object") return null;
  const id = String((ne as ManagedNeItem).id || "").trim();
  if (!id) return null;
  return ne as ManagedNeItem;
}

function webcrtErrorMessage(err: unknown, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const raw = String(err);
  if (raw.includes("webcrt_session_limit")) return t("webcrt.err.sessionLimit");
  if (raw.includes("managed_ne_not_found") || raw.includes("ume_ne_not_found")) return t("webcrt.err.neNotFound");
  if (raw.includes("credentials_incomplete") || raw.includes("cli_username_required") || raw.includes("password_required"))
    return t("webcrt.err.credsIncomplete");
  if (isSshAuthFailure(raw)) return t("webcrt.err.authRejected");
  if (raw.includes("unsupported_device_type")) return t("webcrt.err.deviceType");
  if (raw.includes("ip_address_conflict_restart_required")) return t("webcrt.err.ipConflictRestart");
  if (raw.includes("ip_address_required")) return t("webcrt.newSession.ipRequired");
  if (raw.includes("cli_connect_profile_not_configured")) return t("webcrt.err.cliProfile");
  if (raw.includes("connect_failed")) {
    const detail = raw.replace(/^.*connect_failed:/, "").split("\n---")[0].trim();
    return t("webcrt.err.connectFailed", { detail: detail.slice(0, 180) });
  }
  if (raw.includes("sftp_hop_not_supported")) return t("webcrt.err.sftpHop");
  if (raw.includes("sftp_requires_ssh")) return t("webcrt.err.sftpSsh");
  if (raw.includes("sftp_file_too_large")) return t("webcrt.err.sftpTooLarge");
  if (raw.includes("sftp_chmod_invalid_mode")) return t("webcrt.err.sftpChmodInvalid");
  if (raw.includes("aborted")) return t("webcrt.err.sftpAborted");
  if (raw.includes("websocket_error")) return t("webcrt.err.websocket");
  return raw;
}

function targetFromManagedNe(ne: ManagedNeItem, fallback?: Partial<CliTargetItem>): CliTargetItem {
  return {
    source: "webcrt",
    id: ne.id,
    name: ne.name || fallback?.name || ne.ip_address || "",
    ip_address: ne.ip_address || fallback?.ip_address || "",
    vendor: ne.vendor || fallback?.vendor,
    device_type: ne.device_type || fallback?.device_type,
    protocol: ne.protocol || fallback?.protocol || "ssh",
    username: ne.username || fallback?.username || "",
    has_password: Boolean(fallback?.has_password),
    connect_status: ne.connect_status || "unknown",
    cli_profile_ready: true,
  };
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function WebcrtPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const presetNeId = String(searchParams.get("ne_id") || "").trim();
  const presetSource = String(searchParams.get("source") || "managed").trim().toLowerCase();

  const [source, setSource] = useState<"all" | "managed" | "webcrt" | "ume">("all");
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [tabs, setTabs] = useState<TermTab[]>([]);
  const [activeTabKey, setActiveTabKey] = useState("");
  /** MRU tab keys for warm-mount (active always mounts via isActive even before this updates). */
  const [warmOrder, setWarmOrder] = useState<string[]>([]);
  const [sftpOpen, setSftpOpen] = useState(false);
  const [sftpPath, setSftpPath] = useState(".");
  const [sftpBusy, setSftpBusy] = useState(false);
  const [sftpItems, setSftpItems] = useState<WebcrtSftpItem[]>([]);
  const [sftpSelected, setSftpSelected] = useState<string[]>([]);
  const [sftpSortKey, setSftpSortKey] = useState<SftpSortKey>("name");
  const [sftpSortDir, setSftpSortDir] = useState<"asc" | "desc">("asc");
  const [sftpWidth, setSftpWidth] = useState(() => loadSftpWidth());
  const [sftpResizing, setSftpResizing] = useState(false);
  const [sftpColWidths, setSftpColWidths] = useState<SftpColWidths>(() => loadSftpColWidths());
  const [sftpDragOver, setSftpDragOver] = useState(false);
  const [sftpStatus, setSftpStatus] = useState("");
  const [sftpTransferring, setSftpTransferring] = useState(false);
  const [sftpListTruncated, setSftpListTruncated] = useState(false);
  const [sftpListMaxEntries, setSftpListMaxEntries] = useState(0);
  const sftpPathRef = useRef(".");
  const sftpPathByTabRef = useRef<Record<string, string>>({});
  const sftpActiveTabRef = useRef("");
  const sftpSelectAnchorRef = useRef("");
  const sftpAbortRef = useRef<AbortController | null>(null);
  const sftpBodyRef = useRef<HTMLDivElement | null>(null);
  const sftpDragRef = useRef<{ startX: number; startW: number } | null>(null);
  const sftpColDragRef = useRef<{ key: SftpColKey; startX: number; startW: number } | null>(null);
  const sftpDragDepthRef = useRef(0);
  sftpPathRef.current = sftpPath;
  const [sessionOpts, setSessionOpts] = useState<SessionOptions>(() => loadSessionOptions());
  const [optionsMenuOpen, setOptionsMenuOpen] = useState(false);
  const [sessionOptsModalOpen, setSessionOptsModalOpen] = useState(false);
  const [sessionOptsDraft, setSessionOptsDraft] = useState<SessionOptions>(() => loadSessionOptions());
  const [keywordHl, setKeywordHl] = useState<KeywordHighlightConfig>(() => loadKeywordHighlightConfig());
  const [keywordHlModalOpen, setKeywordHlModalOpen] = useState(false);
  const [keywordHlDraft, setKeywordHlDraft] = useState<KeywordHighlightConfig>(() =>
    loadKeywordHighlightConfig(),
  );
  const [keywordDraftText, setKeywordDraftText] = useState("");
  const [keywordDraftRegex, setKeywordDraftRegex] = useState(false);
  const [keywordSelectedId, setKeywordSelectedId] = useState("");
  const [tabMenu, setTabMenu] = useState<TabMenuState | null>(null);
  const [treeMenu, setTreeMenu] = useState<TreeMenuState | null>(null);
  const [renameDialog, setRenameDialog] = useState<{ target: CliTargetItem; name: string } | null>(null);
  const [hostDialogOpen, setHostDialogOpen] = useState(false);
  const [hostForm, setHostForm] = useState<HostForm>(() => emptyHostForm());
  /** When set, New Session claims this ManagedNE id instead of creating a duplicate. */
  const [hostDialogClaimNeId, setHostDialogClaimNeId] = useState<string | null>(null);
  const [authDialog, setAuthDialog] = useState<AuthDialogState | null>(null);
  const [authForm, setAuthForm] = useState<AuthForm>(() => emptyAuthForm());
  const [sessionBusy, setSessionBusy] = useState(false);
  const connectingKeysRef = useRef<Set<string>>(new Set());
  const tabsRef = useRef<TermTab[]>([]);
  tabsRef.current = tabs;
  const termRefs = useRef<Map<string, WebTerminalHandle>>(new Map());
  /** Chunk lists avoid O(n²) string append while recording. */
  const logBuffersRef = useRef<Map<string, string[]>>(new Map());
  const lastQueueDropToastAtRef = useRef(0);
  const optionsMenuRef = useRef<HTMLDivElement | null>(null);
  const tabMenuRef = useRef<HTMLDivElement | null>(null);
  const treeMenuRef = useRef<HTMLDivElement | null>(null);
  const sessionOptsRef = useRef(sessionOpts);
  sessionOptsRef.current = sessionOpts;
  /** True while this page instance is mounted (StrictMode-safe leave close). */
  const pageAliveRef = useRef(true);
  const leaveCloseDoneRef = useRef(false);

  useEffect(() => {
    try {
      sessionStorage.removeItem(OPEN_TABS_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const closeThisPageSessionsOnLeave = useCallback((mode: "keepalive" | "async") => {
    if (leaveCloseDoneRef.current) return;
    leaveCloseDoneRef.current = true;
    // Only sessions owned by THIS WebCRT window (tabsRef), never other windows.
    const ids = tabsRef.current
      .map((t) => String(t.sessionId || "").trim())
      .filter(Boolean);
    if (!ids.length) return;
    if (mode === "keepalive") {
      closeWebcrtSessionsKeepalive(ids);
      return;
    }
    for (const id of ids) {
      void closeWebcrtSession(id).catch(() => undefined);
    }
  }, []);

  // Close this window's sessions on leave; other WebCRT windows keep theirs.
  useEffect(() => {
    pageAliveRef.current = true;
    leaveCloseDoneRef.current = false;
    const onUnload = () => closeThisPageSessionsOnLeave("keepalive");
    window.addEventListener("pagehide", onUnload);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      pageAliveRef.current = false;
      window.removeEventListener("pagehide", onUnload);
      window.removeEventListener("beforeunload", onUnload);
      // Defer so React StrictMode remount can cancel; real leave closes after.
      window.setTimeout(() => {
        if (pageAliveRef.current) return;
        closeThisPageSessionsOnLeave("async");
      }, 400);
    };
  }, [closeThisPageSessionsOnLeave]);

  useEffect(() => {
    if (!activeTabKey) return;
    setWarmOrder((prev) => [activeTabKey, ...prev.filter((k) => k !== activeTabKey)].slice(0, 16));
  }, [activeTabKey]);

  const warmTabKeys = useMemo(() => new Set(warmOrder.slice(0, WARM_TAB_LIMIT)), [warmOrder]);

  useEffect(() => {
    if (!optionsMenuOpen) return;
    const onDoc = (e: MouseEvent) => {
      const el = optionsMenuRef.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) {
        setOptionsMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOptionsMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [optionsMenuOpen]);

  useEffect(() => {
    if (!tabMenu) return;
    const onDoc = (e: MouseEvent) => {
      const el = tabMenuRef.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) {
        setTabMenu(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setTabMenu(null);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [tabMenu]);

  useEffect(() => {
    if (!treeMenu) return;
    const onDoc = (e: MouseEvent) => {
      const el = treeMenuRef.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) {
        setTreeMenu(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setTreeMenu(null);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [treeMenu]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setKeyword(keywordInput.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [keywordInput]);

  const targetsQuery = useQuery({
    queryKey: ["webcrtTargets", source, keyword, page],
    queryFn: () => fetchCliTargets({ source, keyword, page, pageSize: PAGE_SIZE }),
    staleTime: 5000,
  });

  const items = targetsQuery.data?.items ?? [];
  const total = targetsQuery.data?.total ?? 0;
  const pages = pageCount(total, PAGE_SIZE);

  const updateTab = useCallback((key: string, patch: Partial<TermTab>) => {
    setTabs((prev) => prev.map((tab) => (tab.key === key ? { ...tab, ...patch } : tab)));
  }, []);

  const attachSessionResult = useCallback(
    (
      target: CliTargetItem,
      sessionId: string,
      encoding: string,
      cliHop?: boolean,
      sftpReady?: boolean,
    ) => {
      const key = targetKey(target);
      const existing = tabsRef.current.find((tab) => tab.key === key);
      // WebTerminal mints its own ws_ticket from sessionId; leave wsUrl empty so a
      // late ticket fill-in cannot remount the terminal mid-connect (Quick Connect).
      const pending: TermTab = {
        key,
        sessionId,
        wsUrl: "",
        termEpoch: (existing?.termEpoch || 0) + 1,
        target,
        status: "connecting",
        connectPhase: "authenticating",
        recording: existing?.recording || false,
        encoding,
        errorMessage: undefined,
        cliHop: Boolean(cliHop),
        sftpReady: sftpReady === true ? true : sftpReady === false ? false : existing?.sftpReady,
      };
      setTabs((prev) => {
        const without = prev.filter((x) => x.key !== key);
        return [...without, pending];
      });
      setActiveTabKey(key);
      showOk(t("webcrt.opened", { name: deviceLabel(target) }));
    },
    [showOk, t],
  );

  const openAuthForTarget = useCallback((target: CliTargetItem, errorHint?: string) => {
    const proto = String(target.protocol || "ssh").toLowerCase() === "telnet" ? "telnet" : "ssh";
    setAuthForm(emptyAuthForm(String(target.username || "")));
    setAuthDialog({
      mode: "retry",
      host: {
        name: target.name || "",
        ip_address: target.ip_address || "",
        port: defaultPortForProtocol(proto, target.port),
        protocol: proto,
      },
      target,
      errorHint,
    });
  }, []);

  const openSessionSetupForTarget = useCallback((target: CliTargetItem) => {
    const proto = String(target.protocol || "ssh").toLowerCase() === "telnet" ? "telnet" : "ssh";
    setHostDialogClaimNeId(target.id);
    setHostForm({
      name: String(target.name || "").trim(),
      ip_address: String(target.ip_address || "").trim(),
      port: defaultPortForProtocol(proto, target.port),
      protocol: proto,
    });
    setHostDialogOpen(true);
  }, []);

  const openTarget = useCallback(
    async (target: CliTargetItem, opts?: { force?: boolean }) => {
      const key = targetKey(target);
      const existing = tabsRef.current.find(
        (tab) => tab.key === key && tab.status !== "closed" && tab.status !== "error",
      );
      if (existing && !opts?.force) {
        setActiveTabKey(existing.key);
        return;
      }
      if (connectingKeysRef.current.has(key)) return;

      // LLDP placeholders / no-IP rows → New Session dialog (host + session name).
      if (needsSessionSetup(target) && !opts?.force) {
        openSessionSetupForTarget(target);
        return;
      }

      // Managed / WebCRT SSH without saved password → credential popup.
      // Telnet stays interactive in the terminal (SecureCRT-style); UME uses shared profile.
      if (isInventorySsh(target) && !target.has_password && !opts?.force) {
        openAuthForTarget(target);
        return;
      }

      connectingKeysRef.current.add(key);

      // force = brand-new device session; drop any prior PTY first.
      const prior = tabsRef.current.find((tab) => tab.key === key);
      if (opts?.force && prior?.sessionId) {
        try {
          await closeWebcrtSession(prior.sessionId);
        } catch {
          /* ignore */
        }
      }

      const optsNow = sessionOptsRef.current;
      const encoding = optsNow.encoding || "utf-8";
      const keepaliveSec = Math.max(0, Math.min(600, Number(optsNow.keepaliveSec) || 0));
      const pending: TermTab = {
        key,
        sessionId: "",
        wsUrl: "",
        termEpoch: (prior?.termEpoch || existing?.termEpoch || 0) + (opts?.force ? 1 : 0),
        target,
        status: "connecting",
        connectPhase: "creating",
        recording: prior?.recording || existing?.recording || false,
        encoding,
        errorMessage: undefined,
      };
      setTabs((prev) => {
        const without = prev.filter((x) => x.key !== key);
        return [...without, pending];
      });
      setActiveTabKey(key);

      try {
        const cols = Math.max(80, Math.floor((window.innerWidth - 360) / 9));
        const rows = Math.max(24, Math.floor((window.innerHeight - 180) / 18));
        const body =
          target.source === "ume"
            ? {
                ume_ne_id: target.ume_ne_id || target.id,
                cols,
                rows,
                encoding,
                keepalive_sec: keepaliveSec,
                async_connect: true,
              }
            : {
                ne_id: target.id,
                cols,
                rows,
                encoding,
                keepalive_sec: keepaliveSec,
                async_connect: true,
              };
        // Prefer sessionId; WebTerminal mints a fresh ticket on mount.
        // Avoid a second updateTab({wsUrl}) — that remounted the terminal mid-connect.
        const sess = await createWebcrtSession(body);
        updateTab(key, {
          sessionId: sess.session_id,
          wsUrl: "",
          status: "connecting",
          connectPhase: "authenticating",
          termEpoch: pending.termEpoch + 1,
          cliHop: Boolean(sess.cli_hop),
          sftpReady: typeof sess.sftp_ready === "boolean" ? sess.sftp_ready : undefined,
        });
        showOk(t("webcrt.opened", { name: deviceLabel(target) }));
      } catch (err) {
        const message = webcrtErrorMessage(err, t);
        const needAuth =
          isInventorySsh(target) &&
          (String(err).includes("credentials_incomplete") ||
            String(err).includes("connect_failed") ||
            isSshAuthFailure(err));
        updateTab(key, { status: "error", connectPhase: undefined, errorMessage: message });
        if (needAuth) {
          openAuthForTarget(target, message);
        } else {
          showError(message);
        }
      } finally {
        connectingKeysRef.current.delete(key);
      }
    },
    [openAuthForTarget, openSessionSetupForTarget, showOk, showError, t, updateTab],
  );

  /** Re-open WS to an existing backend session (within detach grace). */
  const reattachTab = useCallback(
    (tab: TermTab) => {
      if (!tab.sessionId) return false;
      // WebTerminal mints a fresh ticket from sessionId on each mount.
      updateTab(tab.key, {
        status: "connecting",
        connectPhase: "authenticating",
        termEpoch: tab.termEpoch + 1,
        errorMessage: undefined,
      });
      setActiveTabKey(tab.key);
      return true;
    },
    [updateTab],
  );

  const openTargetRef = useRef(openTarget);
  openTargetRef.current = openTarget;

  const sessionDims = useCallback(() => {
    const optsNow = sessionOptsRef.current;
    return {
      encoding: optsNow.encoding || "utf-8",
      keepaliveSec: Math.max(0, Math.min(600, Number(optsNow.keepaliveSec) || 0)),
      cols: Math.max(80, Math.floor((window.innerWidth - 360) / 9)),
      rows: Math.max(24, Math.floor((window.innerHeight - 180) / 18)),
    };
  }, []);

  const submitHostDialog = useCallback(() => {
    const ip = hostForm.ip_address.trim();
    if (!ip) {
      showError(t("webcrt.newSession.ipRequired"));
      return;
    }
    const claimNeId = hostDialogClaimNeId || undefined;
    const host: HostForm = {
      ...hostForm,
      ip_address: ip,
      name: hostForm.name.trim() || ip,
      port: hostForm.port || (hostForm.protocol === "telnet" ? 23 : 22),
    };
    if (host.protocol === "telnet") {
      void (async () => {
        setSessionBusy(true);
        try {
          const dims = sessionDims();
          const result = await quickConnectWebcrtSession({
            name: host.name,
            ip_address: host.ip_address,
            port: host.port,
            protocol: "telnet",
            save_password: false,
            ne_id: claimNeId,
            cols: dims.cols,
            rows: dims.rows,
            encoding: dims.encoding,
            keepalive_sec: dims.keepaliveSec,
            async_connect: true,
          });
          const listSource = result.list_source === "managed" ? "managed" : "webcrt";
          const target: CliTargetItem = {
            source: listSource,
            id: result.ne.id,
            name: result.ne.name || result.ne_name || host.ip_address,
            ip_address: result.ne.ip_address || result.ne_ip || host.ip_address,
            vendor: result.ne.vendor,
            device_type: result.ne.device_type,
            protocol: result.ne.protocol || "telnet",
            port: host.port,
            username: result.ne.username || "",
            has_password: false,
            connect_status: result.ne.connect_status || "unknown",
            cli_profile_ready: true,
            ne_source: "webcrt",
          };
          attachSessionResult(
            target,
            result.session_id,
            dims.encoding,
            Boolean(result.cli_hop),
            result.sftp_ready,
          );
          setHostDialogOpen(false);
          setHostForm(emptyHostForm());
          setHostDialogClaimNeId(null);
          setSource(listSource === "managed" ? "managed" : "webcrt");
          void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
        } catch (err) {
          showError(webcrtErrorMessage(err, t));
        } finally {
          setSessionBusy(false);
        }
      })();
      return;
    }
    setHostDialogOpen(false);
    setAuthForm(emptyAuthForm());
    setAuthDialog({ mode: "quick", host, claimNeId });
  }, [
    attachSessionResult,
    hostDialogClaimNeId,
    hostForm,
    queryClient,
    sessionDims,
    showError,
    t,
  ]);

  const submitAuthDialog = useCallback(async () => {
    if (!authDialog) return;
    const username = authForm.username.trim();
    if (!username) {
      showError(t("webcrt.newSession.userRequired"));
      return;
    }
    if (!authForm.password) {
      showError(t("webcrt.newSession.passwordRequired"));
      return;
    }
    setSessionBusy(true);
    const dims = sessionDims();
    const host = authDialog.host;
    const ip = (authDialog.target?.ip_address || host.ip_address).trim();
    try {
      if (authDialog.mode === "retry" && authDialog.target) {
        const existing = authDialog.target;
        // Persist to ManagedNE for inventory + Quick Connect; UME never uses this dialog.
        if (authForm.savePassword && existing.source !== "ume") {
          try {
            await updateManagedNe(existing.id, { username, password: authForm.password });
          } catch {
            /* connect still proceeds with one-shot creds */
          }
        }
        const sess = await createWebcrtSession({
          ne_id: existing.id,
          cols: dims.cols,
          rows: dims.rows,
          encoding: dims.encoding,
          keepalive_sec: dims.keepaliveSec,
          async_connect: false,
          username,
          password: authForm.password,
        });
        attachSessionResult(
          {
            ...existing,
            username,
            has_password: authForm.savePassword || Boolean(existing.has_password),
          },
          sess.session_id,
          dims.encoding,
          Boolean(sess.cli_hop),
          sess.sftp_ready,
        );
        setAuthDialog(null);
        setAuthForm(emptyAuthForm());
        void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
        return;
      }

      const result = await quickConnectWebcrtSession({
        name: host.name || ip,
        ip_address: ip,
        port: host.port || 22,
        protocol: "ssh",
        username,
        password: authForm.password,
        save_password: authForm.savePassword,
        ne_id: authDialog.claimNeId,
        cols: dims.cols,
        rows: dims.rows,
        encoding: dims.encoding,
        keepalive_sec: dims.keepaliveSec,
        async_connect: false,
      });
      const target: CliTargetItem = {
        source: "webcrt",
        id: result.ne.id,
        name: result.ne.name || result.ne_name || ip,
        ip_address: result.ne.ip_address || result.ne_ip || ip,
        vendor: result.ne.vendor,
        device_type: result.ne.device_type,
        protocol: result.ne.protocol || "ssh",
        port: host.port || 22,
        username: result.ne.username || username,
        has_password: authForm.savePassword,
        connect_status: result.ne.connect_status || "unknown",
        cli_profile_ready: true,
        ne_source: "webcrt",
      };
      attachSessionResult(
        target,
        result.session_id,
        dims.encoding,
        Boolean(result.cli_hop),
        result.sftp_ready,
      );
      setAuthDialog(null);
      setAuthForm(emptyAuthForm());
      setHostDialogClaimNeId(null);
      setSource("webcrt");
      void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
    } catch (err) {
      const message = webcrtErrorMessage(err, t);
      // Keep dialog open; allow fixing username and/or password.
      setAuthForm((prev) => ({ ...prev, password: "" }));
      const failedNe = connectFailedNe(err);
      if (failedNe) {
        const target = targetFromManagedNe(failedNe, {
          name: host.name || ip,
          ip_address: ip,
          username,
          has_password: authForm.savePassword,
          protocol: "ssh",
        });
        setAuthDialog({
          mode: "retry",
          host: {
            name: target.name,
            ip_address: target.ip_address,
            port: host.port || 22,
            protocol: "ssh",
          },
          target: { ...target, username },
          errorHint: message,
        });
        setAuthForm((prev) => ({ ...prev, username, password: "" }));
        setHostDialogClaimNeId(null);
        setSource("webcrt");
        void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
      } else {
        setAuthDialog((prev) => (prev ? { ...prev, errorHint: message } : prev));
      }
    } finally {
      setSessionBusy(false);
    }
  }, [attachSessionResult, authDialog, authForm, queryClient, sessionDims, showError, t]);

  const closeTab = useCallback(
    async (key: string) => {
      const tab = tabsRef.current.find((x) => x.key === key);
      if (tab?.sessionId) {
        try {
          await closeWebcrtSession(tab.sessionId);
        } catch {
          /* ignore */
        }
      }
      logBuffersRef.current.delete(key);
      termRefs.current.delete(key);
      setTabs((prev) => {
        const next = prev.filter((x) => x.key !== key);
        if (activeTabKey === key) {
          setActiveTabKey(next.length ? next[next.length - 1].key : "");
        }
        return next;
      });
    },
    [activeTabKey],
  );

  const closeOtherTabs = useCallback(async (keepKey: string) => {
    const others = tabsRef.current.filter((x) => x.key !== keepKey);
    await Promise.all(
      others.map(async (tab) => {
        if (tab.sessionId) {
          try {
            await closeWebcrtSession(tab.sessionId);
          } catch {
            /* ignore */
          }
        }
        logBuffersRef.current.delete(tab.key);
        termRefs.current.delete(tab.key);
      }),
    );
    setTabs((prev) => prev.filter((x) => x.key === keepKey));
    setActiveTabKey(keepKey);
  }, []);

  const closeAllTabs = useCallback(async () => {
    const all = [...tabsRef.current];
    await Promise.all(
      all.map(async (tab) => {
        if (tab.sessionId) {
          try {
            await closeWebcrtSession(tab.sessionId);
          } catch {
            /* ignore */
          }
        }
        logBuffersRef.current.delete(tab.key);
        termRefs.current.delete(tab.key);
      }),
    );
    setTabs([]);
    setActiveTabKey("");
  }, []);

  const copyTabIp = useCallback(
    async (key: string) => {
      const tab = tabsRef.current.find((x) => x.key === key);
      const ip = String(tab?.target.ip_address || "").trim();
      if (!ip) {
        showError(t("webcrt.tabMenu.copyIpEmpty"));
        return;
      }
      try {
        const ok = await writeClipboardText(ip);
        if (ok) showOk(t("webcrt.tabMenu.copyIpOk", { ip }));
        else showError(t("webcrt.actions.copyFailed"));
      } catch {
        showError(t("webcrt.actions.copyFailed"));
      }
    },
    [showError, showOk, t],
  );

  const reconnectTab = useCallback(
    async (tab: TermTab) => {
      // Manual only: re-attach if backend session still exists, else full reconnect.
      const canReattach =
        Boolean(tab.sessionId) &&
        !isDeviceClosedMessage(tab.errorMessage) &&
        !isSshAuthFailure(tab.errorMessage);
      if (canReattach && reattachTab(tab)) return;
      await openTarget(tab.target, { force: true });
    },
    [openTarget, reattachTab],
  );

  const reconnectActive = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab) return;
    await reconnectTab(tab);
  }, [activeTabKey, reconnectTab]);

  const toggleRecording = useCallback(() => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab) return;
    const next = !tab.recording;
    if (next) {
      const seed = termRefs.current.get(tab.key)?.getText() || "";
      logBuffersRef.current.set(tab.key, seed ? [`${seed}\n`] : []);
      showOk(t("webcrt.actions.recordingOn"));
    } else {
      const chunks = logBuffersRef.current.get(tab.key);
      const body = chunks?.length
        ? chunks.join("")
        : termRefs.current.get(tab.key)?.getText() || "";
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      const name = `${deviceLabel(tab.target) || "session"}-${stamp}.log`;
      downloadText(name, body);
      logBuffersRef.current.delete(tab.key);
      showOk(t("webcrt.actions.recordingSaved"));
    }
    updateTab(tab.key, { recording: next });
  }, [activeTabKey, showOk, t, updateTab]);

  const clearActive = useCallback(() => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab) return;
    termRefs.current.get(tab.key)?.clear();
  }, [activeTabKey]);

  const copyActive = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab) return;
    try {
      const handle = termRefs.current.get(tab.key);
      const sel = await handle?.copySelection();
      const text = sel || (await handle?.copyAll());
      if (text) showOk(t("webcrt.actions.copied"));
      else showError(t("webcrt.actions.copyEmpty"));
    } catch {
      showError(t("webcrt.actions.copyFailed"));
    }
  }, [activeTabKey, showOk, showError, t]);

  const refreshSftp = useCallback(
    async (pathOverride?: string) => {
      const tab = tabsRef.current.find((x) => x.key === activeTabKey);
      if (!tab) return;
      const path = String(pathOverride ?? sftpPathRef.current ?? ".").trim() || ".";
      setSftpBusy(true);
      try {
        const res = await webcrtSftpList({ ...sftpTargetIds(tab), path });
        setSftpItems(res.items || []);
        setSftpListTruncated(Boolean(res.truncated));
        setSftpListMaxEntries(Number(res.max_entries || 0));
        const nextPath = String(res.path || path).trim() || path;
        sftpPathRef.current = nextPath;
        setSftpPath(nextPath);
        if (activeTabKey) sftpPathByTabRef.current[activeTabKey] = nextPath;
        const names = new Set((res.items || []).map((it) => it.name));
        setSftpSelected((cur) => cur.filter((n) => names.has(n)));
      } catch (err) {
        showError(webcrtErrorMessage(err, t));
      } finally {
        setSftpBusy(false);
      }
    },
    [activeTabKey, showError, t],
  );

  const navigateSftp = useCallback(
    (nextPath: string) => {
      const path = String(nextPath || ".").trim() || ".";
      sftpPathRef.current = path;
      setSftpPath(path);
      if (activeTabKey) sftpPathByTabRef.current[activeTabKey] = path;
      setSftpSelected([]);
      sftpSelectAnchorRef.current = "";
      void refreshSftp(path);
    },
    [activeTabKey, refreshSftp],
  );

  const beginSftpTransfer = useCallback(() => {
    sftpAbortRef.current?.abort();
    const ac = new AbortController();
    sftpAbortRef.current = ac;
    setSftpTransferring(true);
    setSftpBusy(true);
    return ac;
  }, []);

  const endSftpTransfer = useCallback(() => {
    sftpAbortRef.current = null;
    setSftpTransferring(false);
    setSftpBusy(false);
    setSftpStatus("");
  }, []);

  const cancelSftpTransfer = useCallback(() => {
    sftpAbortRef.current?.abort();
  }, []);

  const selectSftpRow = useCallback(
    (name: string, e: { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean }, orderedNames: string[]) => {
      const multi = Boolean(e.ctrlKey || e.metaKey);
      const range = Boolean(e.shiftKey);
      if (range && sftpSelectAnchorRef.current) {
        const a = orderedNames.indexOf(sftpSelectAnchorRef.current);
        const b = orderedNames.indexOf(name);
        if (a >= 0 && b >= 0) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          setSftpSelected(orderedNames.slice(lo, hi + 1));
          return;
        }
      }
      if (multi) {
        setSftpSelected((prev) =>
          prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
        );
        sftpSelectAnchorRef.current = name;
        return;
      }
      setSftpSelected([name]);
      sftpSelectAnchorRef.current = name;
    },
    [],
  );

  const downloadSftpItems = useCallback(
    async (items: WebcrtSftpItem[]) => {
      const tab = tabsRef.current.find((x) => x.key === activeTabKey);
      const files = items.filter((it) => !it.is_dir);
      if (!tab || !files.length || sftpBusy) return;
      const ac = beginSftpTransfer();
      let done = 0;
      let failed = 0;
      try {
        for (const it of files) {
          if (ac.signal.aborted) throw new Error("aborted");
          done += 1;
          setSftpStatus(
            t("webcrt.sftp.downloading", {
              name: it.name,
              pct: "0%",
              done,
              total: files.length,
            }),
          );
          try {
            const remote = joinSftpPath(sftpPathRef.current, it.name);
            const blob = await webcrtSftpDownload(
              { ...sftpTargetIds(tab), path: remote },
              {
                signal: ac.signal,
                retries: 2,
                onRetry: (attempt) => {
                  setSftpStatus(
                    t("webcrt.sftp.retrying", { name: it.name, attempt, max: 3 }),
                  );
                },
                onProgress: (p) => {
                  const pct = formatSftpTransferPct(p.loaded, p.total || it.size);
                  setSftpStatus(
                    t("webcrt.sftp.downloading", {
                      name: it.name,
                      pct: pct || "…",
                      done,
                      total: files.length,
                    }),
                  );
                },
              },
            );
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = it.name;
            a.click();
            URL.revokeObjectURL(url);
          } catch (err) {
            if (String(err).includes("aborted")) throw err;
            failed += 1;
            if (files.length === 1) throw err;
          }
        }
        if (failed > 0) showError(t("webcrt.sftp.downloadPartial", { ok: files.length - failed, failed }));
        else if (files.length > 1) showOk(t("webcrt.sftp.downloaded", { count: files.length }));
      } catch (err) {
        if (!String(err).includes("aborted")) showError(webcrtErrorMessage(err, t));
        else showError(t("webcrt.err.sftpAborted"));
      } finally {
        endSftpTransfer();
      }
    },
    [activeTabKey, beginSftpTransfer, endSftpTransfer, showError, showOk, sftpBusy, t],
  );

  const downloadSftpItem = useCallback(
    async (it: WebcrtSftpItem) => {
      await downloadSftpItems([it]);
    },
    [downloadSftpItems],
  );

  const mkdirSftp = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab || sftpBusy) return;
    const name = window.prompt(t("webcrt.sftp.mkdirPrompt"), "");
    if (name == null) return;
    const trimmed = String(name).trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!trimmed || trimmed.includes("..") || trimmed.includes("/")) {
      showError(t("webcrt.sftp.mkdirInvalid"));
      return;
    }
    setSftpBusy(true);
    try {
      const remote = joinSftpPath(sftpPathRef.current, trimmed);
      await webcrtSftpMkdir({ ...sftpTargetIds(tab), path: remote });
      showOk(t("webcrt.sftp.mkdirOk"));
      await refreshSftp(sftpPathRef.current);
      setSftpSelected([trimmed]);
      sftpSelectAnchorRef.current = trimmed;
    } catch (err) {
      showError(webcrtErrorMessage(err, t));
    } finally {
      setSftpBusy(false);
    }
  }, [activeTabKey, refreshSftp, showError, showOk, sftpBusy, t]);

  const removeSftpSelected = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab || !sftpSelected.length || sftpBusy) return;
    const items = sftpItems.filter((x) => sftpSelected.includes(x.name));
    if (!items.length) return;
    const label =
      items.length === 1 ? items[0].name : t("webcrt.sftp.selectedCount", { count: items.length });
    if (!window.confirm(t("webcrt.sftp.deleteConfirm", { name: label }))) return;
    const hasDir = items.some((x) => x.is_dir);
    let recursive = false;
    if (hasDir) {
      recursive = window.confirm(t("webcrt.sftp.deleteRecursiveConfirm", { name: label }));
    }
    setSftpBusy(true);
    let failed = 0;
    try {
      for (const item of items) {
        const remote = joinSftpPath(sftpPathRef.current, item.name);
        try {
          try {
            await webcrtSftpRemove({
              ...sftpTargetIds(tab),
              path: remote,
              recursive: false,
            });
          } catch (err) {
            const raw = String(err);
            if (item.is_dir && raw.includes("sftp_dir_not_empty")) {
              if (!recursive) throw err;
              await webcrtSftpRemove({ ...sftpTargetIds(tab), path: remote, recursive: true });
            } else {
              throw err;
            }
          }
        } catch {
          failed += 1;
        }
      }
      setSftpSelected([]);
      await refreshSftp(sftpPathRef.current);
      if (failed > 0) showError(t("webcrt.sftp.deletePartial", { ok: items.length - failed, failed }));
      else showOk(t("webcrt.sftp.deleted"));
    } catch (err) {
      showError(webcrtErrorMessage(err, t));
    } finally {
      setSftpBusy(false);
    }
  }, [activeTabKey, refreshSftp, showError, showOk, sftpBusy, sftpItems, sftpSelected, t]);

  const renameSftpSelected = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab || sftpSelected.length !== 1 || sftpBusy) return;
    const name = sftpSelected[0];
    const next = window.prompt(t("webcrt.sftp.renamePrompt"), name);
    if (next == null) return;
    const trimmed = String(next).trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!trimmed || trimmed.includes("..") || trimmed.includes("/")) {
      showError(t("webcrt.sftp.renameInvalid"));
      return;
    }
    if (trimmed === name) return;
    const oldPath = joinSftpPath(sftpPathRef.current, name);
    const newPath = joinSftpPath(sftpPathRef.current, trimmed);
    setSftpBusy(true);
    try {
      await webcrtSftpRename({ ...sftpTargetIds(tab), old_path: oldPath, new_path: newPath });
      showOk(t("webcrt.sftp.renamed"));
      setSftpSelected([trimmed]);
      sftpSelectAnchorRef.current = trimmed;
      await refreshSftp(sftpPathRef.current);
    } catch (err) {
      showError(webcrtErrorMessage(err, t));
    } finally {
      setSftpBusy(false);
    }
  }, [activeTabKey, refreshSftp, showError, showOk, sftpBusy, sftpSelected, t]);

  const chmodSftpSelected = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab || !sftpSelected.length || sftpBusy) return;
    const sample = sftpItems.find((x) => x.name === sftpSelected[0]);
    const preset = sample?.mode ? sample.mode.replace(/^./, "").replace(/[^rwx-]/g, "") : "755";
    const mode = window.prompt(t("webcrt.sftp.chmodPrompt"), preset.length === 9 ? preset : "755");
    if (mode == null) return;
    const trimmed = String(mode).trim();
    if (!trimmed) return;
    setSftpBusy(true);
    let failed = 0;
    try {
      for (const name of sftpSelected) {
        const remote = joinSftpPath(sftpPathRef.current, name);
        try {
          await webcrtSftpChmod({ ...sftpTargetIds(tab), path: remote, mode: trimmed });
        } catch {
          failed += 1;
        }
      }
      await refreshSftp(sftpPathRef.current);
      if (failed > 0) showError(t("webcrt.sftp.chmodPartial", { ok: sftpSelected.length - failed, failed }));
      else showOk(t("webcrt.sftp.chmodOk"));
    } catch (err) {
      showError(webcrtErrorMessage(err, t));
    } finally {
      setSftpBusy(false);
    }
  }, [activeTabKey, refreshSftp, showError, showOk, sftpBusy, sftpItems, sftpSelected, t]);

  const toggleSftpSort = useCallback((key: SftpSortKey) => {
    setSftpSortKey((prev) => {
      if (prev === key) {
        setSftpSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return prev;
      }
      setSftpSortDir(key === "mtime" || key === "size" ? "desc" : "asc");
      return key;
    });
  }, []);

  const uploadSftpItems = useCallback(
    async (items: SftpUploadItem[]) => {
      const tab = tabsRef.current.find((x) => x.key === activeTabKey);
      if (!tab || !items.length || sftpBusy) return;
      const ac = beginSftpTransfer();
      setSftpDragOver(false);
      sftpDragDepthRef.current = 0;
      let done = 0;
      let failed = 0;
      try {
        for (const item of items) {
          if (ac.signal.aborted) throw new Error("aborted");
          done += 1;
          const shortName = String(item.relativePath || item.file.name).split("/").pop() || item.file.name;
          setSftpStatus(
            t("webcrt.sftp.uploading", { done, total: items.length, name: shortName, pct: "0%" }),
          );
          const rel = String(item.relativePath || item.file.name)
            .replace(/\\/g, "/")
            .replace(/^\/+/, "")
            .replace(/\/+/g, "/");
          if (!rel || rel.includes("..")) {
            failed += 1;
            continue;
          }
          const remote = joinSftpPath(sftpPathRef.current, rel);
          try {
            await webcrtSftpUpload(
              { ...sftpTargetIds(tab), remote_path: remote, file: item.file },
              {
                signal: ac.signal,
                retries: 2,
                onRetry: (attempt) => {
                  setSftpStatus(t("webcrt.sftp.retrying", { name: shortName, attempt, max: 3 }));
                },
                onProgress: (p) => {
                  const pct = formatSftpTransferPct(p.loaded, p.total || item.file.size);
                  setSftpStatus(
                    t("webcrt.sftp.uploading", {
                      done,
                      total: items.length,
                      name: shortName,
                      pct: pct || "…",
                    }),
                  );
                },
              },
            );
          } catch (err) {
            if (String(err).includes("aborted")) throw err;
            failed += 1;
            if (items.length === 1) throw err;
          }
        }
        await refreshSftp(sftpPathRef.current);
        if (failed > 0) {
          showError(t("webcrt.sftp.uploadPartial", { ok: items.length - failed, failed }));
        } else {
          showOk(t("webcrt.sftp.uploaded"));
        }
      } catch (err) {
        if (String(err).includes("aborted")) showError(t("webcrt.err.sftpAborted"));
        else showError(webcrtErrorMessage(err, t));
      } finally {
        endSftpTransfer();
      }
    },
    [activeTabKey, beginSftpTransfer, endSftpTransfer, refreshSftp, showError, showOk, sftpBusy, t],
  );

  const onSftpDragEnter = useCallback((e: ReactDragEvent<HTMLElement>) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    e.stopPropagation();
    sftpDragDepthRef.current += 1;
    setSftpDragOver(true);
  }, []);

  const onSftpDragOver = useCallback((e: ReactDragEvent<HTMLElement>) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  const onSftpDragLeave = useCallback((e: ReactDragEvent<HTMLElement>) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    e.stopPropagation();
    sftpDragDepthRef.current = Math.max(0, sftpDragDepthRef.current - 1);
    if (sftpDragDepthRef.current === 0) setSftpDragOver(false);
  }, []);

  const onSftpDrop = useCallback(
    (e: ReactDragEvent<HTMLElement>) => {
      e.preventDefault();
      e.stopPropagation();
      sftpDragDepthRef.current = 0;
      setSftpDragOver(false);
      if (sftpBusy) return;
      void (async () => {
        try {
          const items = await collectDroppedSftpUploads(e.dataTransfer);
          if (!items.length) {
            showError(t("webcrt.sftp.dropEmpty"));
            return;
          }
          await uploadSftpItems(items);
        } catch (err) {
          showError(webcrtErrorMessage(err, t));
        }
      })();
    },
    [showError, sftpBusy, t, uploadSftpItems],
  );

  const onSftpSplitDown = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    sftpDragRef.current = { startX: e.clientX, startW: sftpWidth };
    setSftpResizing(true);
    const onMove = (ev: MouseEvent) => {
      const drag = sftpDragRef.current;
      if (!drag) return;
      const bodyW = sftpBodyRef.current?.clientWidth || window.innerWidth;
      const maxW = Math.max(SFTP_WIDTH_MIN, Math.floor(bodyW * 0.75));
      const next = Math.max(SFTP_WIDTH_MIN, Math.min(maxW, drag.startW + (drag.startX - ev.clientX)));
      setSftpWidth(next);
    };
    const onUp = () => {
      sftpDragRef.current = null;
      setSftpResizing(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      setSftpWidth((w) => {
        saveSftpWidth(w);
        return w;
      });
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [sftpWidth]);

  const onSftpColResizeDown = useCallback((key: SftpColKey, e: ReactMouseEvent<HTMLSpanElement>) => {
    e.preventDefault();
    e.stopPropagation();
    sftpColDragRef.current = { key, startX: e.clientX, startW: sftpColWidths[key] };
    const onMove = (ev: MouseEvent) => {
      const drag = sftpColDragRef.current;
      if (!drag) return;
      const next = clampSftpColWidth(drag.key, drag.startW + (ev.clientX - drag.startX));
      setSftpColWidths((prev) => (prev[drag.key] === next ? prev : { ...prev, [drag.key]: next }));
    };
    const onUp = () => {
      sftpColDragRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      setSftpColWidths((prev) => {
        saveSftpColWidths(prev);
        return prev;
      });
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [sftpColWidths]);

  // Auto-connect from /webcrt?ne_id=...
  useEffect(() => {
    if (!presetNeId) return;
    const neId = presetNeId;
    const sourceHint = presetSource === "ume" ? "ume" : "managed";
    let alive = true;

    (async () => {
      try {
        if (sourceHint === "ume") {
          await openTargetRef.current({
            source: "ume",
            id: neId,
            ume_ne_id: neId,
            name: neId,
            ip_address: "",
            connect_status: "unknown",
          });
        } else {
          const row = await fetchManagedNeById(neId);
          await openTargetRef.current({
            source: "managed",
            id: row.id,
            name: row.name || row.ip_address,
            ip_address: row.ip_address,
            vendor: row.vendor,
            device_type: row.device_type,
            protocol: row.protocol || "ssh",
            port: row.port,
            username: row.username || "",
            has_password: Boolean(row.has_password),
            hop_enabled: Boolean(row.hop_enabled),
            connect_status: row.connect_status,
            cli_profile_ready: true,
            ne_source: row.source || "",
          });
        }
      } catch (err) {
        if (alive) showError(webcrtErrorMessage(err, t));
      } finally {
        if (!alive) return;
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (next.get("ne_id") === neId) {
              next.delete("ne_id");
              next.delete("source");
            }
            return next;
          },
          { replace: true },
        );
      }
    })();

    return () => {
      alive = false;
    };
  }, [presetNeId, presetSource, showError, t, setSearchParams]);

  const activeTab = tabs.find((x) => x.key === activeTabKey) || null;
  const sftpBlockedKey = activeTab ? sftpUnavailableReason(activeTab) : "webcrt.err.sftpSsh";
  const sftpAllowed = Boolean(activeTab && !sftpBlockedKey);
  const sftpSortedItems = sortSftpItems(sftpItems, sftpSortKey, sftpSortDir);
  const sftpOrderedNames = sftpSortedItems.map((it) => it.name);
  const sftpSelectedItems = sftpSortedItems.filter((it) => sftpSelected.includes(it.name));
  const sftpSelectedFiles = sftpSelectedItems.filter((it) => !it.is_dir);

  useEffect(() => {
    if (sftpOpen && !sftpAllowed) setSftpOpen(false);
  }, [sftpOpen, sftpAllowed]);

  useEffect(() => {
    const prev = sftpActiveTabRef.current;
    if (prev === activeTabKey) return;
    if (prev) sftpPathByTabRef.current[prev] = sftpPathRef.current;
    sftpActiveTabRef.current = activeTabKey;
    if (!sftpOpen || !activeTabKey || !sftpAllowed) return;
    const saved = sftpPathByTabRef.current[activeTabKey] || ".";
    sftpPathRef.current = saved;
    setSftpPath(saved);
    setSftpSelected([]);
    sftpSelectAnchorRef.current = "";
    void refreshSftp(saved);
  }, [activeTabKey, refreshSftp, sftpAllowed, sftpOpen]);

  const renameWebcrtSession = useCallback(
    async (target: CliTargetItem, nextName: string) => {
      const name = nextName.trim();
      if (!name) {
        showError(t("webcrt.treeMenu.renameRequired"));
        return;
      }
      if (target.source !== "webcrt") return;
      try {
        await updateManagedNe(target.id, { name });
        const key = targetKey(target);
        setTabs((prev) =>
          prev.map((tab) => (tab.key === key ? { ...tab, target: { ...tab.target, name } } : tab)),
        );
        void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
        showOk(t("webcrt.treeMenu.renamed"));
        setRenameDialog(null);
      } catch (err) {
        showError(webcrtErrorMessage(err, t));
      }
    },
    [queryClient, showError, showOk, t],
  );

  const deleteWebcrtSession = useCallback(
    async (target: CliTargetItem) => {
      if (target.source !== "webcrt") return;
      const label = deviceLabel(target);
      if (!window.confirm(t("webcrt.treeMenu.deleteConfirm", { name: label }))) return;
      try {
        const key = targetKey(target);
        await closeTab(key);
        await deleteManagedNe(target.id);
        void queryClient.invalidateQueries({ queryKey: ["webcrtTargets"] });
        showOk(t("webcrt.treeMenu.deleted"));
      } catch (err) {
        showError(webcrtErrorMessage(err, t));
      }
    },
    [closeTab, queryClient, showError, showOk, t],
  );

  const renderDeviceRow = (row: CliTargetItem) => {
    const key = targetKey(row);
    const tab = tabs.find((x) => x.key === key);
    const isConnecting = tab?.status === "connecting" || connectingKeysRef.current.has(key);
    const isWebcrtSession = row.source === "webcrt";
    return (
      <li key={key}>
        <button
          type="button"
          className={`webcrt-tree__item${activeTabKey === key ? " is-active" : ""}`}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => void openTarget(row)}
          onContextMenu={(e) => {
            if (!isWebcrtSession) return;
            e.preventDefault();
            e.stopPropagation();
            setTabMenu(null);
            setTreeMenu({ target: row, x: e.clientX, y: e.clientY });
          }}
          title={`${deviceLabel(row)}\n${row.ip_address}\n${row.source}`}
        >
          <span className="webcrt-tree__icon" aria-hidden>
            <ComputerIcon />
          </span>
          <span className="webcrt-tree__label">
            <span className="webcrt-tree__name">{deviceLabel(row)}</span>
            <span className="webcrt-tree__meta">
              {row.ip_address || row.source}
              {isConnecting ? ` · ${t("webcrt.status.connecting")}` : ""}
              {tab?.status === "connected" ? ` · ${t("webcrt.status.connected")}` : ""}
            </span>
          </span>
        </button>
      </li>
    );
  };

  return (
    <div className="webcrt-shell">
      <aside className="webcrt-sidebar">
        <div className="webcrt-sidebar__chrome">
          <div className="webcrt-sidebar__actions">
            <button
              type="button"
              className="webcrt-sidebar__new-btn"
              onClick={() => {
                setHostDialogClaimNeId(null);
                setHostForm(emptyHostForm());
                setHostDialogOpen(true);
              }}
            >
              {t("webcrt.newSession.title")}
            </button>
            <div className="webcrt-menubar__item" ref={optionsMenuRef}>
              <button
                type="button"
                className={`webcrt-menubar__btn${optionsMenuOpen ? " is-open" : ""}`}
                aria-haspopup="menu"
                aria-expanded={optionsMenuOpen}
                onClick={() => setOptionsMenuOpen((v) => !v)}
              >
                {t("webcrt.options")}
              </button>
              {optionsMenuOpen ? (
                <div className="webcrt-menubar__menu webcrt-menubar__menu--end" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    className="webcrt-menubar__menu-item"
                    onClick={() => {
                      setOptionsMenuOpen(false);
                      setSessionOptsDraft(sessionOpts);
                      setSessionOptsModalOpen(true);
                    }}
                  >
                    {t("webcrt.globalSessionOptions")}…
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="webcrt-menubar__menu-item"
                    onClick={() => {
                      setOptionsMenuOpen(false);
                      // If keywords exist but Enable was left off, open as enabled so Save works.
                      setKeywordHlDraft({
                        ...keywordHl,
                        enabled: keywordHl.keywords.length > 0 ? true : keywordHl.enabled,
                      });
                      setKeywordDraftText("");
                      setKeywordDraftRegex(false);
                      setKeywordSelectedId(keywordHl.keywords[0]?.id || "");
                      setKeywordHlModalOpen(true);
                    }}
                  >
                    {t("webcrt.keywordHl.title")}…
                  </button>
                </div>
              ) : null}
            </div>
          </div>
          <div className="webcrt-sidebar__search">
            <input
              type="search"
              value={keywordInput}
              placeholder={t("webcrt.filterKeywordPh")}
              onChange={(e) => setKeywordInput(e.target.value)}
            />
          </div>
          <div className="webcrt-source-tabs webcrt-source-tabs--4">
            {(["all", "managed", "webcrt", "ume"] as const).map((s) => (
              <button
                key={s}
                type="button"
                className={`webcrt-source-tabs__btn${source === s ? " is-active" : ""}`}
                onClick={() => {
                  setSource(s);
                  setPage(1);
                }}
              >
                {t(`webcrt.source.${s}`)}
              </button>
            ))}
          </div>
        </div>
        <div className="webcrt-tree">
          {targetsQuery.isLoading ? <div className="webcrt-tree__empty">{t("common.refreshing")}</div> : null}
          {!targetsQuery.isLoading && items.length === 0 ? (
            <div className="webcrt-tree__empty">{t("webcrt.empty")}</div>
          ) : null}
          <ul className="webcrt-tree__list">{items.map((row) => renderDeviceRow(row))}</ul>
        </div>
        <div className="webcrt-sidebar__pager">
          <span>{t("common.pagerMeta", { total, page, pages })}</span>
          <div className="webcrt-sidebar__pager-btns">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))} aria-label="prev">
              ‹
            </button>
            <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} aria-label="next">
              ›
            </button>
          </div>
        </div>
      </aside>

      <main className="webcrt-main">
        {tabs.length > 0 ? (
          <>
            <div className="webcrt-tabs">
              {tabs.map((tab) => {
                const dead = tab.status === "closed" || tab.status === "error";
                return (
                  <div
                    key={tab.key}
                    className={`webcrt-tabs__item${activeTabKey === tab.key ? " is-active" : ""}${
                      dead ? " is-dead" : ""
                    }${tab.status === "connecting" ? " is-connecting" : ""}`}
                    onMouseDown={(e) => {
                      if (e.button === 0) e.preventDefault();
                    }}
                    onClick={() => {
                      setActiveTabKey(tab.key);
                      setTabMenu(null);
                      const cur = tabsRef.current.find((x) => x.key === tab.key);
                      // Soft-closed tabs still hold a live backend session — re-attach immediately.
                      if (
                        cur?.sessionId &&
                        (cur.status === "closed" || cur.status === "error") &&
                        !isDeviceClosedMessage(cur.errorMessage) &&
                        !isSshAuthFailure(cur.errorMessage) &&
                        !isSessionGoneError(cur.errorMessage)
                      ) {
                        reattachTab(cur);
                        return;
                      }
                      window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 0);
                    }}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      setActiveTabKey(tab.key);
                      setTabMenu({ key: tab.key, x: e.clientX, y: e.clientY });
                    }}
                  >
                    <span>
                      {deviceLabel(tab.target)}
                      {tab.status === "connecting"
                        ? ` (${connectPhaseLabel(tab.connectPhase, t)})`
                        : ""}
                      {tab.status === "closed" ? ` (${t("webcrt.status.closed")})` : ""}
                      {tab.status === "error" ? ` (${t("webcrt.status.error")})` : ""}
                      <span
                        role="button"
                        tabIndex={0}
                        className="webcrt-tabs__close"
                        aria-label={t("webcrt.disconnect")}
                        onClick={(e) => {
                          e.stopPropagation();
                          void closeTab(tab.key);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            e.stopPropagation();
                            void closeTab(tab.key);
                          }
                        }}
                      >
                        ×
                      </span>
                    </span>
                  </div>
                );
              })}
            </div>
            {activeTab ? (
              <div className="webcrt-actions">
                <button
                  type="button"
                  className={`webcrt-action-btn${
                    activeTab.status === "closed" || activeTab.status === "error" ? " is-warn" : ""
                  }`}
                  onClick={() => void reconnectActive()}
                >
                  <span aria-hidden>↻</span>
                  {t("webcrt.actions.reconnect")}
                </button>
                <button
                  type="button"
                  className={`webcrt-action-btn${activeTab.recording ? " is-active" : ""}`}
                  onClick={toggleRecording}
                >
                  <span aria-hidden>☐</span>
                  {t("webcrt.actions.recordLog")}
                </button>
                <button type="button" className="webcrt-action-btn" onClick={clearActive}>
                  <span aria-hidden>⌫</span>
                  {t("webcrt.actions.clear")}
                </button>
                <button type="button" className="webcrt-action-btn" onClick={() => void copyActive()}>
                  <span aria-hidden>⧉</span>
                  {t("webcrt.actions.copy")}
                </button>
                <button
                  type="button"
                  className={`webcrt-action-btn${sftpOpen ? " is-active" : ""}`}
                  disabled={!sftpAllowed}
                  title={sftpAllowed ? t("webcrt.sftp.title") : t(sftpBlockedKey || "webcrt.err.sftpSsh")}
                  onClick={() => {
                    if (!sftpAllowed) {
                      showError(t(sftpBlockedKey || "webcrt.err.sftpSsh"));
                      return;
                    }
                    setSftpOpen((v) => {
                      const next = !v;
                      if (next) {
                        const saved =
                          sftpPathByTabRef.current[activeTabKey] || sftpPathRef.current || ".";
                        sftpPathRef.current = saved;
                        setSftpPath(saved);
                        void refreshSftp(saved);
                      }
                      return next;
                    });
                  }}
                >
                  {t("webcrt.sftp.title")}
                </button>
              </div>
            ) : null}
            <div
              ref={sftpBodyRef}
              className={`webcrt-main__body${sftpOpen && activeTab && sftpAllowed ? " has-sftp" : ""}`}
            >
              <div className="webcrt-main__terms">
              {tabs.map((tab) => {
                const isActive = activeTabKey === tab.key;
                // Cap concurrent xterm+WS: active always mounts; keep recent tabs warm.
                // Do not mount while disconnected — reconnect is manual (no silent remount).
                const mountTerminal = Boolean(
                  tab.sessionId &&
                    tab.status !== "closed" &&
                    tab.status !== "error" &&
                    (isActive || warmTabKeys.has(tab.key)),
                );
                return (
                <div
                  key={tab.key}
                  className="webcrt-main__pane"
                  hidden={!isActive}
                >
                  {tab.status === "connecting" && !tab.sessionId ? (
                    <div className="webcrt-main__placeholder">
                      <div>{connectPhaseLabel(tab.connectPhase, t)}…</div>
                      <p className="panel__hint">{t("webcrt.phase.hint")}</p>
                    </div>
                  ) : null}
                  {tab.status === "error" && !tab.sessionId ? (
                    <div className="webcrt-main__placeholder webcrt-main__placeholder--error">
                      <div>{t("webcrt.status.error")}</div>
                      {tab.errorMessage ? <pre className="webcrt-error-detail">{tab.errorMessage}</pre> : null}
                      <button type="button" className="webcrt-action-btn is-warn" onClick={() => void reconnectTab(tab)}>
                        {t("webcrt.actions.reconnect")}
                      </button>
                    </div>
                  ) : null}
                  {tab.sessionId ? (
                    <>
                      {tab.status === "connecting" ? (
                        <div className="webcrt-connect-banner" role="status">
                          <div className="webcrt-connect-banner__text">
                            <strong>{connectPhaseLabel(tab.connectPhase, t)}…</strong>
                            <span>{t("webcrt.phase.hint")}</span>
                          </div>
                        </div>
                      ) : null}
                      {tab.status === "closed" || tab.status === "error" ? (
                        <div className="webcrt-disconnect-banner" role="status">
                          <div className="webcrt-disconnect-banner__text">
                            <strong>
                              {tab.status === "error"
                                ? t("webcrt.disconnectBannerError")
                                : t("webcrt.disconnectBanner")}
                            </strong>
                            {tab.errorMessage ? <span>{tab.errorMessage}</span> : null}
                          </div>
                          <button
                            type="button"
                            className="webcrt-action-btn is-warn"
                            onClick={() => void reconnectTab(tab)}
                          >
                            {t("webcrt.actions.reconnect")}
                          </button>
                        </div>
                      ) : null}
                      {mountTerminal ? (
                      <Suspense
                        fallback={
                          <div className="webcrt-main__placeholder" role="status">
                            {t("common.refreshing")}
                          </div>
                        }
                      >
                      <WebTerminal
                        key={`${tab.key}:${tab.termEpoch}`}
                        ref={(handle) => {
                          if (handle) termRefs.current.set(tab.key, handle);
                          else termRefs.current.delete(tab.key);
                        }}
                        sessionId={tab.sessionId || undefined}
                        wsUrl={tab.wsUrl || undefined}
                        title={deviceLabel(tab.target)}
                        recording={tab.recording}
                        encoding={tab.encoding}
                        fontSize={sessionOpts.fontSize}
                        termColors={{
                          background: sessionOpts.background,
                          foreground: sessionOpts.foreground,
                        }}
                        copyOnSelect={sessionOpts.copyOnSelect}
                        pasteDelayMs={sessionOpts.pasteDelayMs}
                        keywordHighlight={keywordHl}
                        autoFocus={tab.status === "connected"}
                        onStdout={(chunk) => {
                          if (!chunk) return;
                          let parts = logBuffersRef.current.get(tab.key);
                          if (!parts) {
                            parts = [];
                            logBuffersRef.current.set(tab.key, parts);
                          }
                          parts.push(chunk);
                          if (parts.length >= LOG_COMPACT_CHUNKS) {
                            const joined = parts.join("");
                            logBuffersRef.current.set(
                              tab.key,
                              [joined.length > LOG_MAX_CHARS ? joined.slice(-LOG_MAX_CHARS) : joined],
                            );
                          }
                        }}
                        onReady={() => {
                          window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 40);
                        }}
                        onStatus={(state, message, phase, meta) => {
                          if (state === "connected") {
                            updateTab(tab.key, {
                              status: "connected",
                              connectPhase: undefined,
                              errorMessage: undefined,
                              ...(typeof meta?.sftpReady === "boolean" ? { sftpReady: meta.sftpReady } : {}),
                              ...(typeof meta?.cliHop === "boolean" ? { cliHop: meta.cliHop } : {}),
                            });
                            window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 40);
                          } else if (state === "open" || state === "connecting") {
                            const nextPhase: ConnectPhase =
                              phase === "waiting_prompt" || message === "waiting_prompt"
                                ? "waiting_prompt"
                                : "authenticating";
                            updateTab(tab.key, {
                              status: "connecting",
                              connectPhase: nextPhase,
                              ...(typeof meta?.sftpReady === "boolean" ? { sftpReady: meta.sftpReady } : {}),
                              ...(typeof meta?.cliHop === "boolean" ? { cliHop: meta.cliHop } : {}),
                            });
                          } else if (state === "warning") {
                            const m = String(message || "");
                            const dropMatch = /^queue_dropped:(\d+)/i.exec(m);
                            if (dropMatch) {
                              const now = Date.now();
                              if (now - lastQueueDropToastAtRef.current > 3000) {
                                lastQueueDropToastAtRef.current = now;
                                showError(
                                  t("webcrt.err.queueDropped", {
                                    count: Number(dropMatch[1]) || dropMatch[1],
                                  }),
                                );
                              }
                            }
                          } else if (state === "error") {
                            const errMsg = message || t("webcrt.disconnectBannerError");
                            const sid = tab.sessionId;
                            // Session gone / auth failure: stop here — user must click Reconnect.
                            if (sid && isSessionGoneError(errMsg)) {
                              updateTab(tab.key, {
                                status: "closed",
                                sessionId: "",
                                wsUrl: "",
                                connectPhase: undefined,
                                errorMessage: webcrtErrorMessage(errMsg, t),
                              });
                              return;
                            }
                            updateTab(tab.key, {
                              status: "error",
                              sessionId: "",
                              wsUrl: "",
                              connectPhase: undefined,
                              errorMessage: webcrtErrorMessage(errMsg, t),
                            });
                            if (sid) {
                              void closeWebcrtSession(sid).catch(() => undefined);
                            }
                            if (isInventorySsh(tab.target) && isSshAuthFailure(errMsg)) {
                              openAuthForTarget(tab.target, webcrtErrorMessage(errMsg, t));
                            }
                          } else if (state === "closed") {
                            const msg = String(message || "");
                            // No silent remount / auto re-login — show banner; user clicks Reconnect.
                            const deviceGone = isDeviceClosedMessage(msg);
                            updateTab(tab.key, {
                              status: "closed",
                              sessionId: deviceGone ? "" : tab.sessionId,
                              wsUrl: deviceGone ? "" : tab.wsUrl,
                              connectPhase: undefined,
                              errorMessage: msg || t("webcrt.disconnectBanner"),
                            });
                          }
                        }}
                      />
                      </Suspense>
                      ) : null}
                    </>
                  ) : null}
                </div>
                );
              })}
              </div>
              {sftpOpen && activeTab && sftpAllowed ? (
                <>
                  <div
                    className={`webcrt-sftp-split${sftpResizing ? " is-dragging" : ""}`}
                    role="separator"
                    aria-orientation="vertical"
                    aria-label={t("webcrt.sftp.resizeHint")}
                    title={t("webcrt.sftp.resizeHint")}
                    onMouseDown={onSftpSplitDown}
                  />
                  <aside
                    className={`webcrt-sftp${sftpBusy ? " is-busy" : ""}${sftpDragOver ? " is-drop-target" : ""}`}
                    style={{ width: sftpWidth }}
                    onDragEnter={onSftpDragEnter}
                    onDragOver={onSftpDragOver}
                    onDragLeave={onSftpDragLeave}
                    onDrop={onSftpDrop}
                  >
                    <div className="webcrt-sftp__bar">
                      <button
                        type="button"
                        title={t("webcrt.sftp.up")}
                        disabled={sftpBusy}
                        onClick={() => navigateSftp(sftpParentPath(sftpPathRef.current))}
                      >
                        {t("webcrt.sftp.up")}
                      </button>
                      <input
                        value={sftpPath}
                        disabled={sftpBusy}
                        onChange={(e) => {
                          const v = e.target.value;
                          sftpPathRef.current = v;
                          setSftpPath(v);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void refreshSftp(sftpPathRef.current);
                          }
                        }}
                      />
                      <button type="button" disabled={sftpBusy} onClick={() => void refreshSftp()}>
                        {t("webcrt.sftp.refresh")}
                      </button>
                      <button type="button" disabled={sftpBusy} onClick={() => void mkdirSftp()}>
                        {t("webcrt.sftp.mkdir")}
                      </button>
                      <button
                        type="button"
                        disabled={sftpBusy || sftpSelected.length !== 1}
                        onClick={() => void renameSftpSelected()}
                      >
                        {t("webcrt.sftp.rename")}
                      </button>
                      <button
                        type="button"
                        disabled={sftpBusy || sftpSelectedFiles.length === 0}
                        onClick={() => void downloadSftpItems(sftpSelectedFiles)}
                      >
                        {t("webcrt.sftp.download")}
                      </button>
                      <button
                        type="button"
                        disabled={sftpBusy || sftpSelected.length === 0}
                        onClick={() => void chmodSftpSelected()}
                      >
                        {t("webcrt.sftp.chmod")}
                      </button>
                      <button
                        type="button"
                        disabled={sftpBusy || sftpSelected.length === 0}
                        onClick={() => void removeSftpSelected()}
                      >
                        {t("webcrt.sftp.delete")}
                      </button>
                      <label className={`webcrt-sftp__upload${sftpBusy ? " is-disabled" : ""}`}>
                        {t("webcrt.sftp.upload")}
                        <input
                          type="file"
                          hidden
                          multiple
                          disabled={sftpBusy}
                          onChange={(e) => {
                            const files = Array.from(e.target.files || []);
                            e.target.value = "";
                            if (!files.length || sftpBusy) return;
                            void uploadSftpItems(files.map((file) => ({ file, relativePath: file.name })));
                          }}
                        />
                      </label>
                    </div>
                    {sftpBusy || sftpStatus ? (
                      <div className="webcrt-sftp__status" role="status">
                        <span>{sftpStatus || t("webcrt.sftp.loading")}</span>
                        {sftpTransferring ? (
                          <button
                            type="button"
                            className="webcrt-sftp__cancel"
                            onClick={cancelSftpTransfer}
                          >
                            {t("webcrt.sftp.cancel")}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    {!sftpBusy && sftpListTruncated ? (
                      <div className="webcrt-sftp__status webcrt-sftp__status--warn" role="status">
                        {t("webcrt.sftp.listTruncated", {
                          count: sftpItems.length,
                          max: sftpListMaxEntries || sftpItems.length,
                        })}
                      </div>
                    ) : null}
                    {sftpDragOver ? (
                      <div className="webcrt-sftp__drop-overlay" aria-hidden>
                        {t("webcrt.sftp.dropHint")}
                      </div>
                    ) : null}
                    <div className="webcrt-sftp__table-wrap">
                      <table
                        className="webcrt-sftp__table"
                        style={{
                          width: sumSftpColWidths(sftpColWidths),
                          minWidth: sumSftpColWidths(sftpColWidths),
                        }}
                      >
                        <colgroup>
                          {SFTP_COL_KEYS.map((key) => (
                            <col key={key} style={{ width: sftpColWidths[key] }} />
                          ))}
                        </colgroup>
                        <thead>
                          <tr>
                            {(
                              [
                                ["name", "webcrt.sftp.colName"],
                                ["size", "webcrt.sftp.colSize"],
                                ["mtime", "webcrt.sftp.colMtime"],
                                ["owner", "webcrt.sftp.colOwner"],
                                ["group", "webcrt.sftp.colGroup"],
                                ["mode", "webcrt.sftp.colMode"],
                              ] as const
                            ).map(([key, labelKey]) => (
                              <th
                                key={key}
                                className={`webcrt-sftp__col-${key}${sftpSortKey === key ? " is-sorted" : ""}`}
                                style={{ width: sftpColWidths[key] }}
                              >
                                <button
                                  type="button"
                                  className="webcrt-sftp__th-label"
                                  onClick={() => toggleSftpSort(key)}
                                >
                                  {t(labelKey)}
                                  {sftpSortKey === key ? (sftpSortDir === "asc" ? " ▲" : " ▼") : ""}
                                </button>
                                <span
                                  className="webcrt-sftp__col-resizer"
                                  title={t("webcrt.sftp.colResizeHint")}
                                  onMouseDown={(e) => onSftpColResizeDown(key, e)}
                                />
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {!sftpBusy && sftpSortedItems.length === 0 ? (
                            <tr className="webcrt-sftp__empty-row">
                              <td colSpan={6}>{t("webcrt.sftp.empty")}</td>
                            </tr>
                          ) : null}
                          {sftpSortedItems.map((it) => (
                            <tr
                              key={`${sftpPath}:${it.is_dir ? "d" : "f"}:${it.name}`}
                              className={`${sftpBusy ? "is-busy" : ""}${
                                sftpSelected.includes(it.name) ? " is-selected" : ""
                              }`}
                              title={it.is_dir ? t("webcrt.sftp.enterHint") : t("webcrt.sftp.downloadHint")}
                              onClick={(e) => {
                                if (sftpBusy) return;
                                selectSftpRow(it.name, e, sftpOrderedNames);
                              }}
                              onDoubleClick={() => {
                                if (sftpBusy) return;
                                if (it.is_dir) {
                                  navigateSftp(joinSftpPath(sftpPathRef.current, it.name));
                                  return;
                                }
                                void downloadSftpItem(it);
                              }}
                            >
                              <td className="webcrt-sftp__col-name" style={{ width: sftpColWidths.name }}>
                                <span className="webcrt-sftp__name-cell">
                                  <span className={`webcrt-sftp__icon${it.is_dir ? " is-dir" : ""}`}>
                                    {it.is_dir ? <FolderIcon /> : <FileIcon />}
                                  </span>
                                  <span className="webcrt-sftp__name">{it.name}</span>
                                </span>
                              </td>
                              <td className="webcrt-sftp__col-size" style={{ width: sftpColWidths.size }}>
                                {formatSftpSizeKb(it.size, it.is_dir)}
                              </td>
                              <td className="webcrt-sftp__col-mtime" style={{ width: sftpColWidths.mtime }}>
                                {formatSftpMtime(it.mtime)}
                              </td>
                              <td className="webcrt-sftp__col-owner" style={{ width: sftpColWidths.owner }}>
                                {it.owner || ""}
                              </td>
                              <td className="webcrt-sftp__col-group" style={{ width: sftpColWidths.group }}>
                                {it.group || ""}
                              </td>
                              <td className="webcrt-sftp__col-mode" style={{ width: sftpColWidths.mode }}>
                                {it.mode || ""}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </aside>
                </>
              ) : null}
            </div>
          </>
        ) : (
          <div className="webcrt-main__empty">
            <p>{t("webcrt.termPlaceholder")}</p>
            <p className="panel__hint">{t("webcrt.hintCrt")}</p>
          </div>
        )}
      </main>

      {treeMenu ? (
        <div
          ref={treeMenuRef}
          className="webcrt-ctx webcrt-tab-ctx"
          style={{ left: treeMenu.x, top: treeMenu.y }}
          role="menu"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              const target = treeMenu.target;
              setTreeMenu(null);
              setRenameDialog({ target, name: target.name || target.ip_address || "" });
            }}
          >
            {t("webcrt.treeMenu.rename")}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              const target = treeMenu.target;
              setTreeMenu(null);
              void deleteWebcrtSession(target);
            }}
          >
            {t("webcrt.treeMenu.delete")}
          </button>
        </div>
      ) : null}

      {renameDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setRenameDialog(null)}
        >
          <div
            className="modal webcrt-auth-modal"
            role="dialog"
            aria-labelledby="webcrt-rename-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="webcrt-rename-title">{t("webcrt.treeMenu.rename")}</h3>
            <p className="form-hint">
              {renameDialog.target.ip_address}
              {renameDialog.target.protocol ? ` · ${String(renameDialog.target.protocol).toUpperCase()}` : ""}
            </p>
            <div className="form-grid">
              <label>
                <FormLabel required>{t("webcrt.treeMenu.renameLabel")}</FormLabel>
                <input
                  autoFocus
                  value={renameDialog.name}
                  onChange={(e) => setRenameDialog({ ...renameDialog, name: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void renameWebcrtSession(renameDialog.target, renameDialog.name);
                  }}
                />
              </label>
            </div>
            <div className="modal__actions">
              <button type="button" onClick={() => setRenameDialog(null)}>
                {t("webcrt.sessionOptionsCancel")}
              </button>
              <button
                type="button"
                onClick={() => void renameWebcrtSession(renameDialog.target, renameDialog.name)}
              >
                {t("webcrt.treeMenu.renameSave")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {tabMenu ? (
        <div
          ref={tabMenuRef}
          className="webcrt-ctx webcrt-tab-ctx"
          style={{ left: tabMenu.x, top: tabMenu.y }}
          role="menu"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              const key = tabMenu.key;
              setTabMenu(null);
              void closeTab(key);
            }}
          >
            {t("webcrt.tabMenu.close")}
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={tabs.length <= 1}
            onClick={() => {
              const key = tabMenu.key;
              setTabMenu(null);
              void closeOtherTabs(key);
            }}
          >
            {t("webcrt.tabMenu.closeOthers")}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setTabMenu(null);
              void closeAllTabs();
            }}
          >
            {t("webcrt.tabMenu.closeAll")}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              const key = tabMenu.key;
              setTabMenu(null);
              void copyTabIp(key);
            }}
          >
            {t("webcrt.tabMenu.copyIp")}
          </button>
        </div>
      ) : null}

      {keywordHlModalOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setKeywordHlModalOpen(false)}
        >
          <div
            className="modal webcrt-keyword-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="webcrt-keyword-hl-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="webcrt-keyword-hl-title">{t("webcrt.keywordHl.title")}</h3>
            <label className="webcrt-session-opts__field webcrt-session-opts__field--check">
              <span>{t("webcrt.keywordHl.enabled")}</span>
              <input
                type="checkbox"
                checked={keywordHlDraft.enabled}
                onChange={(e) =>
                  setKeywordHlDraft((prev) => ({ ...prev, enabled: e.target.checked }))
                }
              />
            </label>
            <div className="webcrt-keyword-add">
              <label className="webcrt-session-opts__field">
                <span>{t("webcrt.keywordHl.newKeyword")}</span>
                <input
                  type="text"
                  value={keywordDraftText}
                  placeholder={t("webcrt.keywordHl.newKeywordPh")}
                  onChange={(e) => setKeywordDraftText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return;
                    e.preventDefault();
                    const pattern = keywordDraftText.trim();
                    if (!pattern) return;
                    const row: KeywordRule = {
                      id: newKeywordId(),
                      pattern,
                      regex: keywordDraftRegex,
                    };
                    setKeywordHlDraft((prev) => ({
                      ...prev,
                      enabled: true,
                      keywords: [...prev.keywords, row],
                    }));
                    setKeywordSelectedId(row.id);
                    setKeywordDraftText("");
                  }}
                />
              </label>
              <label className="webcrt-keyword-add__regex">
                <input
                  type="checkbox"
                  checked={keywordDraftRegex}
                  onChange={(e) => setKeywordDraftRegex(e.target.checked)}
                />
                <span>{t("webcrt.keywordHl.regex")}</span>
              </label>
              <button
                type="button"
                onClick={() => {
                  const pattern = keywordDraftText.trim();
                  if (!pattern) return;
                  const row: KeywordRule = {
                    id: newKeywordId(),
                    pattern,
                    regex: keywordDraftRegex,
                  };
                  setKeywordHlDraft((prev) => ({
                    ...prev,
                    enabled: true,
                    keywords: [...prev.keywords, row],
                  }));
                  setKeywordSelectedId(row.id);
                  setKeywordDraftText("");
                }}
              >
                {t("webcrt.keywordHl.add")}
              </button>
            </div>
            <div className="webcrt-keyword-list-wrap">
              <div className="webcrt-keyword-list-head">
                <span>{t("webcrt.keywordHl.colKeyword")}</span>
                <span>{t("webcrt.keywordHl.colRegex")}</span>
              </div>
              <ul className="webcrt-keyword-list">
                {keywordHlDraft.keywords.length === 0 ? (
                  <li className="webcrt-keyword-list__empty">{t("webcrt.keywordHl.empty")}</li>
                ) : (
                  keywordHlDraft.keywords.map((kw) => (
                    <li key={kw.id}>
                      <button
                        type="button"
                        className={`webcrt-keyword-list__row${
                          keywordSelectedId === kw.id ? " is-selected" : ""
                        }`}
                        onClick={() => setKeywordSelectedId(kw.id)}
                      >
                        <span className="webcrt-keyword-list__pattern" title={kw.pattern}>
                          {kw.pattern}
                        </span>
                        <span>{kw.regex ? "✓" : ""}</span>
                      </button>
                    </li>
                  ))
                )}
              </ul>
              <div className="webcrt-keyword-list-actions">
                <button
                  type="button"
                  disabled={!keywordSelectedId}
                  title={t("webcrt.keywordHl.moveUp")}
                  onClick={() => {
                    setKeywordHlDraft((prev) => {
                      const idx = prev.keywords.findIndex((k) => k.id === keywordSelectedId);
                      if (idx <= 0) return prev;
                      const next = [...prev.keywords];
                      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                      return { ...prev, keywords: next };
                    });
                  }}
                >
                  ↑
                </button>
                <button
                  type="button"
                  disabled={!keywordSelectedId}
                  title={t("webcrt.keywordHl.moveDown")}
                  onClick={() => {
                    setKeywordHlDraft((prev) => {
                      const idx = prev.keywords.findIndex((k) => k.id === keywordSelectedId);
                      if (idx < 0 || idx >= prev.keywords.length - 1) return prev;
                      const next = [...prev.keywords];
                      [next[idx + 1], next[idx]] = [next[idx], next[idx + 1]];
                      return { ...prev, keywords: next };
                    });
                  }}
                >
                  ↓
                </button>
                <button
                  type="button"
                  disabled={!keywordSelectedId}
                  title={t("webcrt.keywordHl.remove")}
                  onClick={() => {
                    setKeywordHlDraft((prev) => {
                      const next = prev.keywords.filter((k) => k.id !== keywordSelectedId);
                      setKeywordSelectedId(next[0]?.id || "");
                      return { ...prev, keywords: next };
                    });
                  }}
                >
                  ×
                </button>
              </div>
            </div>
            <div className="webcrt-keyword-footer">
              <label className="webcrt-session-opts__field webcrt-session-opts__field--check">
                <span>{t("webcrt.keywordHl.caseSensitive")}</span>
                <input
                  type="checkbox"
                  checked={keywordHlDraft.caseSensitive}
                  onChange={(e) =>
                    setKeywordHlDraft((prev) => ({ ...prev, caseSensitive: e.target.checked }))
                  }
                />
              </label>
              <label className="webcrt-session-opts__field webcrt-keyword-color">
                <span>{t("webcrt.keywordHl.color")}</span>
                <input
                  type="color"
                  value={keywordHlDraft.color || "#ffff00"}
                  onChange={(e) =>
                    setKeywordHlDraft((prev) => ({ ...prev, color: e.target.value }))
                  }
                />
              </label>
            </div>
            <div className="modal__actions">
              <button type="button" onClick={() => setKeywordHlModalOpen(false)}>
                {t("webcrt.sessionOptionsCancel")}
              </button>
              <button
                type="button"
                onClick={() => {
                  const keywords = keywordHlDraft.keywords
                    .map((k) => ({
                      id: k.id || newKeywordId(),
                      pattern: String(k.pattern || "").trim(),
                      regex: Boolean(k.regex),
                    }))
                    .filter((k) => k.pattern);
                  const next: KeywordHighlightConfig = {
                    ...defaultKeywordHighlightConfig(),
                    enabled: keywords.length > 0,
                    caseSensitive: Boolean(keywordHlDraft.caseSensitive),
                    color: keywordHlDraft.color || "#ffff00",
                    keywords,
                  };
                  saveKeywordHighlightConfig(next);
                  setKeywordHl(next);
                  setKeywordHlDraft(next);
                  setKeywordHlModalOpen(false);
                  showOk(t("webcrt.keywordHl.saved"));
                }}
              >
                {t("webcrt.sessionOptionsSave")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {sessionOptsModalOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setSessionOptsModalOpen(false)}
        >
          <div
            className="modal webcrt-session-opts-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="webcrt-session-opts-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="webcrt-session-opts-title">{t("webcrt.globalSessionOptions")}</h3>
            <div className="webcrt-session-opts">
              <label className="webcrt-session-opts__field">
                <span>{t("webcrt.encoding")}</span>
                <select
                  value={sessionOptsDraft.encoding}
                  onChange={(e) =>
                    setSessionOptsDraft((prev) => ({ ...prev, encoding: e.target.value }))
                  }
                >
                  {ENCODING_OPTIONS.map((enc) => (
                    <option key={enc} value={enc}>
                      {enc}
                    </option>
                  ))}
                </select>
              </label>
              <label className="webcrt-session-opts__field">
                <span>{t("webcrt.fontSize")}</span>
                <select
                  value={sessionOptsDraft.fontSize}
                  onChange={(e) =>
                    setSessionOptsDraft((prev) => ({
                      ...prev,
                      fontSize: Number(e.target.value) || 13,
                    }))
                  }
                >
                  {FONT_SIZE_OPTIONS.map((size) => (
                    <option key={size} value={size}>
                      {size}px
                    </option>
                  ))}
                </select>
              </label>
              <label className="webcrt-session-opts__field">
                <span>{t("webcrt.colorScheme")}</span>
                <select
                  value={sessionOptsDraft.colorScheme}
                  onChange={(e) => {
                    const id = e.target.value as ColorSchemeId;
                    if (id !== "custom" && id in COLOR_SCHEMES) {
                      const preset = COLOR_SCHEMES[id as Exclude<ColorSchemeId, "custom">];
                      setSessionOptsDraft((prev) => ({
                        ...prev,
                        colorScheme: id,
                        background: preset.background,
                        foreground: preset.foreground,
                      }));
                      return;
                    }
                    setSessionOptsDraft((prev) => ({ ...prev, colorScheme: "custom" }));
                  }}
                >
                  <option value="dark">{t("webcrt.scheme.dark")}</option>
                  <option value="blackWhite">{t("webcrt.scheme.blackWhite")}</option>
                  <option value="whiteBlack">{t("webcrt.scheme.whiteBlack")}</option>
                  <option value="greenBlack">{t("webcrt.scheme.greenBlack")}</option>
                  <option value="amberBlack">{t("webcrt.scheme.amberBlack")}</option>
                  <option value="custom">{t("webcrt.scheme.custom")}</option>
                </select>
              </label>
              <div className="webcrt-session-opts__colors">
                <label className="webcrt-session-opts__field webcrt-session-opts__color">
                  <span>{t("webcrt.backgroundColor")}</span>
                  <input
                    type="color"
                    value={sessionOptsDraft.background || "#0b1220"}
                    onChange={(e) =>
                      setSessionOptsDraft((prev) => ({
                        ...prev,
                        colorScheme: "custom",
                        background: e.target.value,
                      }))
                    }
                  />
                </label>
                <label className="webcrt-session-opts__field webcrt-session-opts__color">
                  <span>{t("webcrt.foregroundColor")}</span>
                  <input
                    type="color"
                    value={sessionOptsDraft.foreground || "#e2e8f0"}
                    onChange={(e) =>
                      setSessionOptsDraft((prev) => ({
                        ...prev,
                        colorScheme: "custom",
                        foreground: e.target.value,
                      }))
                    }
                  />
                </label>
                <div
                  className="webcrt-session-opts__preview"
                  style={{
                    background: sessionOptsDraft.background,
                    color: sessionOptsDraft.foreground,
                  }}
                  aria-hidden
                >
                  Aa 192.168.0.1
                </div>
              </div>
              <label className="webcrt-session-opts__field webcrt-session-opts__field--check">
                <span>{t("webcrt.copyOnSelect")}</span>
                <input
                  type="checkbox"
                  checked={sessionOptsDraft.copyOnSelect}
                  onChange={(e) =>
                    setSessionOptsDraft((prev) => ({ ...prev, copyOnSelect: e.target.checked }))
                  }
                />
              </label>
              <label className="webcrt-session-opts__field">
                <span>{t("webcrt.pasteDelay")}</span>
                <select
                  value={sessionOptsDraft.pasteDelayMs}
                  onChange={(e) =>
                    setSessionOptsDraft((prev) => ({
                      ...prev,
                      pasteDelayMs: Number(e.target.value) || 0,
                    }))
                  }
                >
                  {PASTE_DELAY_OPTIONS.map((ms) => (
                    <option key={ms} value={ms}>
                      {ms === 0 ? t("webcrt.pasteDelayOff") : `${ms} ms`}
                    </option>
                  ))}
                </select>
              </label>
              <label className="webcrt-session-opts__field">
                <span>{t("webcrt.keepalive")}</span>
                <select
                  value={sessionOptsDraft.keepaliveSec}
                  onChange={(e) =>
                    setSessionOptsDraft((prev) => ({
                      ...prev,
                      keepaliveSec: Number(e.target.value) || 0,
                    }))
                  }
                >
                  {KEEPALIVE_OPTIONS.map((sec) => (
                    <option key={sec} value={sec}>
                      {sec === 0 ? t("webcrt.keepaliveOff") : t("webcrt.keepaliveSec", { n: sec })}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="modal__actions">
              <button type="button" onClick={() => setSessionOptsModalOpen(false)}>
                {t("webcrt.sessionOptionsCancel")}
              </button>
              <button
                type="button"
                onClick={() => {
                  const next = normalizeSessionOptions(sessionOptsDraft);
                  saveSessionOptions(next);
                  setSessionOpts(next);
                  setSessionOptsModalOpen(false);
                  showOk(t("webcrt.sessionOptionsSaved"));
                }}
              >
                {t("webcrt.sessionOptionsSave")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {authDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => {
            if (!sessionBusy) setAuthDialog(null);
          }}
        >
          <div
            className="modal webcrt-auth-modal"
            role="dialog"
            aria-labelledby="webcrt-auth-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="webcrt-auth-title">{t("webcrt.newSession.authTitle")}</h3>
            <p className="form-hint">
              {authDialog.host.ip_address}
              {authDialog.host.port ? `:${authDialog.host.port}` : ""}
              {" · SSH"}
            </p>
            {authDialog.errorHint ? (
              <p className="form-hint webcrt-auth-error">{authDialog.errorHint}</p>
            ) : null}
            <div className="form-grid">
              <label>
                <FormLabel required>{t("managedNe.col.user")}</FormLabel>
                <input
                  required
                  autoFocus
                  value={authForm.username}
                  onChange={(e) => setAuthForm({ ...authForm, username: e.target.value })}
                />
              </label>
              <label>
                <FormLabel required>{t("managedNe.col.password")}</FormLabel>
                <input
                  type="password"
                  required
                  value={authForm.password}
                  onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitAuthDialog();
                  }}
                />
              </label>
              <label className="form-check form-grid__full">
                <input
                  type="checkbox"
                  checked={authForm.savePassword}
                  onChange={(e) => setAuthForm({ ...authForm, savePassword: e.target.checked })}
                />
                <span className="form-check__text">{t("webcrt.newSession.savePassword")}</span>
              </label>
            </div>
            <div className="modal__actions">
              <button type="button" disabled={sessionBusy} onClick={() => setAuthDialog(null)}>
                {t("webcrt.sessionOptionsCancel")}
              </button>
              <button type="button" disabled={sessionBusy} onClick={() => void submitAuthDialog()}>
                {sessionBusy ? t("webcrt.newSession.connecting") : t("webcrt.newSession.connect")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {hostDialogOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => {
            if (!sessionBusy) {
              setHostDialogOpen(false);
              setHostDialogClaimNeId(null);
            }
          }}
        >
          <div
            className="modal webcrt-new-session-modal"
            role="dialog"
            aria-labelledby="webcrt-new-session-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="webcrt-new-session-title">
              {hostDialogClaimNeId
                ? t("webcrt.newSession.claimTitle")
                : t("webcrt.newSession.title")}
            </h3>
            {hostDialogClaimNeId ? (
              <p className="form-hint">{t("webcrt.newSession.claimHint")}</p>
            ) : null}
            <div className="form-grid">
              <label>
                <FormLabel required>{t("webcrt.newSession.protocol")}</FormLabel>
                <select
                  value={hostForm.protocol}
                  onChange={(e) => {
                    const protocol = e.target.value === "telnet" ? "telnet" : "ssh";
                    setHostForm((prev) => ({
                      ...prev,
                      protocol,
                      port: protocol === "telnet" ? 23 : 22,
                    }));
                  }}
                >
                  <option value="ssh">SSH</option>
                  <option value="telnet">Telnet</option>
                </select>
              </label>
              <label>
                <FormLabel required>{t("webcrt.newSession.host")}</FormLabel>
                <input
                  required
                  autoFocus
                  value={hostForm.ip_address}
                  placeholder="192.168.1.1"
                  onChange={(e) => setHostForm({ ...hostForm, ip_address: e.target.value })}
                />
              </label>
              <label>
                <FormLabel>{t("webcrt.newSession.port")}</FormLabel>
                <input
                  type="number"
                  value={hostForm.port}
                  onChange={(e) =>
                    setHostForm({
                      ...hostForm,
                      port: Number(e.target.value) || (hostForm.protocol === "telnet" ? 23 : 22),
                    })
                  }
                />
              </label>
              <label>
                <FormLabel>{t("webcrt.newSession.sessionName")}</FormLabel>
                <input
                  value={hostForm.name}
                  placeholder={t("webcrt.newSession.sessionNamePh")}
                  onChange={(e) => setHostForm({ ...hostForm, name: e.target.value })}
                />
              </label>
            </div>
            <div className="modal__actions">
              <button
                type="button"
                disabled={sessionBusy}
                onClick={() => {
                  setHostDialogOpen(false);
                  setHostDialogClaimNeId(null);
                }}
              >
                {t("webcrt.sessionOptionsCancel")}
              </button>
              <button type="button" disabled={sessionBusy} onClick={() => submitHostDialog()}>
                {sessionBusy ? t("webcrt.newSession.connecting") : t("webcrt.newSession.connect")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

    </div>
  );
}
