import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { WebTerminal, type WebTerminalHandle } from "../components/WebTerminal";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import {
  ApiRequestError,
  closeWebcrtSession,
  createWebcrtSession,
  deleteManagedNe,
  fetchCliTargets,
  fetchManagedNeById,
  quickConnectWebcrtSession,
  updateManagedNe,
  webcrtSftpDownload,
  webcrtSftpList,
  webcrtSftpUpload,
  webcrtWsUrl,
} from "../services/api";
import { pageCount } from "../utils/display";
import type { CliTargetItem, ManagedNeItem } from "../types";
import {
  defaultKeywordHighlightConfig,
  loadKeywordHighlightConfig,
  newKeywordId,
  saveKeywordHighlightConfig,
  type KeywordHighlightConfig,
  type KeywordRule,
} from "../utils/webcrtKeywordHighlight";

const PAGE_SIZE = 50;
const SESSION_OPTS_KEY = "netx.webcrt.sessionOptions";
const LEGACY_TERM_PREFS_KEY = "netx.webcrt.termPrefs";
const ENCODING_OPTIONS = ["utf-8", "gbk", "gb2312", "gb18030"] as const;
const FONT_SIZE_OPTIONS = [12, 13, 14, 16, 18, 20] as const;
const PASTE_DELAY_OPTIONS = [0, 20, 40, 60, 100, 150] as const;
const KEEPALIVE_OPTIONS = [0, 15, 30, 60, 120] as const;

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
  const [sftpOpen, setSftpOpen] = useState(false);
  const [sftpPath, setSftpPath] = useState(".");
  const [sftpItems, setSftpItems] = useState<Array<{ name: string; size: number; mtime: number; is_dir: boolean }>>([]);
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
  const [authDialog, setAuthDialog] = useState<AuthDialogState | null>(null);
  const [authForm, setAuthForm] = useState<AuthForm>(() => emptyAuthForm());
  const [sessionBusy, setSessionBusy] = useState(false);
  const connectingKeysRef = useRef<Set<string>>(new Set());
  const tabsRef = useRef<TermTab[]>([]);
  tabsRef.current = tabs;
  const termRefs = useRef<Map<string, WebTerminalHandle>>(new Map());
  const logBuffersRef = useRef<Map<string, string>>(new Map());
  const optionsMenuRef = useRef<HTMLDivElement | null>(null);
  const tabMenuRef = useRef<HTMLDivElement | null>(null);
  const treeMenuRef = useRef<HTMLDivElement | null>(null);
  const sessionOptsRef = useRef(sessionOpts);
  sessionOptsRef.current = sessionOpts;

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
    (target: CliTargetItem, sessionId: string, encoding: string) => {
      const key = targetKey(target);
      const existing = tabsRef.current.find((tab) => tab.key === key);
      const pending: TermTab = {
        key,
        sessionId,
        wsUrl: webcrtWsUrl(sessionId),
        termEpoch: (existing?.termEpoch || 0) + 1,
        target,
        status: "connecting",
        connectPhase: "authenticating",
        recording: existing?.recording || false,
        encoding,
        errorMessage: undefined,
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
        port: proto === "telnet" ? 23 : 22,
        protocol: proto === "telnet" ? "telnet" : "ssh",
      },
      target,
      errorHint,
    });
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

      // WebCRT SSH without saved password → credential popup.
      const isWebcrtSsh =
        target.source === "webcrt" && String(target.protocol || "ssh").toLowerCase() !== "telnet";
      if (isWebcrtSsh && !target.has_password && !opts?.force) {
        openAuthForTarget(target);
        return;
      }

      connectingKeysRef.current.add(key);

      if (opts?.force && existing?.sessionId) {
        try {
          await closeWebcrtSession(existing.sessionId);
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
        termEpoch: (existing?.termEpoch || 0) + (opts?.force ? 1 : 0),
        target,
        status: "connecting",
        connectPhase: "creating",
        recording: existing?.recording || false,
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
        const sess = await createWebcrtSession(body);
        const wsUrl = webcrtWsUrl(sess.session_id);
        updateTab(key, {
          sessionId: sess.session_id,
          wsUrl,
          status: "connecting",
          connectPhase: "authenticating",
          termEpoch: pending.termEpoch + 1,
        });
        showOk(t("webcrt.opened", { name: deviceLabel(target) }));
      } catch (err) {
        const message = webcrtErrorMessage(err, t);
        const needAuth =
          target.source === "webcrt" &&
          String(target.protocol || "ssh").toLowerCase() !== "telnet" &&
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
    [openAuthForTarget, showOk, showError, t, updateTab],
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
            username: result.ne.username || "",
            has_password: false,
            connect_status: result.ne.connect_status || "unknown",
            cli_profile_ready: true,
          };
          attachSessionResult(target, result.session_id, dims.encoding);
          setHostDialogOpen(false);
          setHostForm(emptyHostForm());
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
    setAuthDialog({ mode: "quick", host });
  }, [attachSessionResult, hostForm, queryClient, sessionDims, showError, t]);

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
        if (authForm.savePassword && existing.source === "webcrt") {
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
        username: result.ne.username || username,
        has_password: authForm.savePassword,
        connect_status: result.ne.connect_status || "unknown",
        cli_profile_ready: true,
      };
      attachSessionResult(target, result.session_id, dims.encoding);
      setAuthDialog(null);
      setAuthForm(emptyAuthForm());
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
        await navigator.clipboard.writeText(ip);
        showOk(t("webcrt.tabMenu.copyIpOk", { ip }));
      } catch {
        showError(t("webcrt.actions.copyFailed"));
      }
    },
    [showError, showOk, t],
  );

  const reconnectActive = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab) return;
    await openTarget(tab.target, { force: true });
  }, [activeTabKey, openTarget]);

  const toggleRecording = useCallback(() => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab) return;
    const next = !tab.recording;
    if (next) {
      const seed = termRefs.current.get(tab.key)?.getText() || "";
      logBuffersRef.current.set(tab.key, seed ? `${seed}\n` : "");
      showOk(t("webcrt.actions.recordingOn"));
    } else {
      const body = logBuffersRef.current.get(tab.key) || termRefs.current.get(tab.key)?.getText() || "";
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

  const refreshSftp = useCallback(async () => {
    const tab = tabsRef.current.find((x) => x.key === activeTabKey);
    if (!tab) return;
    try {
      const body =
        tab.target.source === "ume"
          ? { ume_ne_id: tab.target.ume_ne_id || tab.target.id, path: sftpPath }
          : { ne_id: tab.target.id, path: sftpPath };
      const res = await webcrtSftpList(body);
      setSftpItems(res.items || []);
      setSftpPath(res.path || sftpPath);
    } catch (err) {
      showError(webcrtErrorMessage(err, t));
    }
  }, [activeTabKey, showError, sftpPath, t]);

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
            connect_status: row.connect_status,
            cli_profile_ready: true,
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
                  onClick={() => {
                    setSftpOpen((v) => !v);
                    if (!sftpOpen) void refreshSftp();
                  }}
                >
                  SFTP
                </button>
              </div>
            ) : null}
            <div className="webcrt-main__body">
              {tabs.map((tab) => (
                <div
                  key={`${tab.key}:${tab.termEpoch}`}
                  className="webcrt-main__pane"
                  hidden={activeTabKey !== tab.key}
                >
                  {tab.status === "connecting" && !tab.wsUrl ? (
                    <div className="webcrt-main__placeholder">
                      <div>{connectPhaseLabel(tab.connectPhase, t)}…</div>
                      <p className="panel__hint">{t("webcrt.phase.hint")}</p>
                    </div>
                  ) : null}
                  {tab.status === "error" && !tab.wsUrl ? (
                    <div className="webcrt-main__placeholder webcrt-main__placeholder--error">
                      <div>{t("webcrt.status.error")}</div>
                      {tab.errorMessage ? <pre className="webcrt-error-detail">{tab.errorMessage}</pre> : null}
                      <button type="button" className="webcrt-action-btn is-warn" onClick={() => void openTarget(tab.target, { force: true })}>
                        {t("webcrt.actions.reconnect")}
                      </button>
                    </div>
                  ) : null}
                  {tab.wsUrl ? (
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
                            onClick={() => void openTarget(tab.target, { force: true })}
                          >
                            {t("webcrt.actions.reconnect")}
                          </button>
                        </div>
                      ) : null}
                      <WebTerminal
                        ref={(handle) => {
                          if (handle) termRefs.current.set(tab.key, handle);
                          else termRefs.current.delete(tab.key);
                        }}
                        wsUrl={tab.wsUrl}
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
                        autoFocus={activeTabKey === tab.key && tab.status === "connected"}
                        onStdout={(chunk) => {
                          const prev = logBuffersRef.current.get(tab.key) || "";
                          logBuffersRef.current.set(tab.key, prev + chunk);
                        }}
                        onReady={() => {
                          window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 40);
                        }}
                        onStatus={(state, message, phase) => {
                          if (state === "connected") {
                            updateTab(tab.key, {
                              status: "connected",
                              connectPhase: undefined,
                              errorMessage: undefined,
                            });
                            if (activeTabKey === tab.key) {
                              window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 40);
                            }
                          } else if (state === "open" || state === "connecting") {
                            const nextPhase: ConnectPhase =
                              phase === "waiting_prompt" || message === "waiting_prompt"
                                ? "waiting_prompt"
                                : "authenticating";
                            updateTab(tab.key, {
                              status: "connecting",
                              connectPhase: nextPhase,
                            });
                          } else if (state === "error") {
                            const errMsg = message || t("webcrt.disconnectBannerError");
                            const sid = tab.sessionId;
                            updateTab(tab.key, {
                              status: "error",
                              sessionId: "",
                              wsUrl: "",
                              connectPhase: undefined,
                              errorMessage: errMsg,
                            });
                            if (sid) {
                              void closeWebcrtSession(sid).catch(() => undefined);
                            }
                            const tgt = tab.target;
                            const isWebcrtSsh =
                              tgt.source === "webcrt" &&
                              String(tgt.protocol || "ssh").toLowerCase() !== "telnet";
                            if (isWebcrtSsh && isSshAuthFailure(errMsg)) {
                              openAuthForTarget(tgt, webcrtErrorMessage(errMsg, t));
                            }
                          } else if (state === "closed") {
                            // Ignore local socket teardown noise; real device closes send session closed.
                            if (String(message || "").startsWith("websocket_closed:")) return;
                            updateTab(tab.key, {
                              status: "closed",
                              connectPhase: undefined,
                              errorMessage: message || t("webcrt.disconnectBanner"),
                            });
                          }
                        }}
                      />
                    </>
                  ) : null}
                </div>
              ))}
              {sftpOpen && activeTab ? (
                <div className="webcrt-sftp">
                  <div className="webcrt-sftp__bar">
                    <input value={sftpPath} onChange={(e) => setSftpPath(e.target.value)} />
                    <button type="button" onClick={() => void refreshSftp()}>
                      {t("webcrt.sftp.refresh")}
                    </button>
                    <label className="webcrt-sftp__upload">
                      {t("webcrt.sftp.upload")}
                      <input
                        type="file"
                        hidden
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (!file || !activeTab) return;
                          const remote = `${sftpPath.replace(/\/$/, "")}/${file.name}`.replace(/^\.\//, "");
                          void (async () => {
                            try {
                              const body =
                                activeTab.target.source === "ume"
                                  ? {
                                      ume_ne_id: activeTab.target.ume_ne_id || activeTab.target.id,
                                      remote_path: remote,
                                      file,
                                    }
                                  : { ne_id: activeTab.target.id, remote_path: remote, file };
                              await webcrtSftpUpload(body);
                              showOk(t("webcrt.sftp.uploaded"));
                              await refreshSftp();
                            } catch (err) {
                              showError(webcrtErrorMessage(err, t));
                            }
                          })();
                          e.target.value = "";
                        }}
                      />
                    </label>
                  </div>
                  <ul className="webcrt-sftp__list">
                    {sftpItems.map((it) => (
                      <li key={it.name}>
                        <button
                          type="button"
                          onClick={() => {
                            if (it.is_dir) {
                              const next = `${sftpPath.replace(/\/$/, "")}/${it.name}`.replace(/^\.\//, "");
                              setSftpPath(next);
                              window.setTimeout(() => void refreshSftp(), 0);
                              return;
                            }
                            void (async () => {
                              try {
                                const body =
                                  activeTab.target.source === "ume"
                                    ? {
                                        ume_ne_id: activeTab.target.ume_ne_id || activeTab.target.id,
                                        path: `${sftpPath.replace(/\/$/, "")}/${it.name}`,
                                      }
                                    : {
                                        ne_id: activeTab.target.id,
                                        path: `${sftpPath.replace(/\/$/, "")}/${it.name}`,
                                      };
                                const blob = await webcrtSftpDownload(body);
                                const url = URL.createObjectURL(blob);
                                const a = document.createElement("a");
                                a.href = url;
                                a.download = it.name;
                                a.click();
                                URL.revokeObjectURL(url);
                              } catch (err) {
                                showError(webcrtErrorMessage(err, t));
                              }
                            })();
                          }}
                        >
                          {it.is_dir ? "📁" : "📄"} {it.name}
                          {!it.is_dir ? ` (${it.size})` : ""}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
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
            if (!sessionBusy) setHostDialogOpen(false);
          }}
        >
          <div
            className="modal webcrt-new-session-modal"
            role="dialog"
            aria-labelledby="webcrt-new-session-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="webcrt-new-session-title">{t("webcrt.newSession.title")}</h3>
            <p className="form-hint">{t("webcrt.newSession.hint")}</p>
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
              <button type="button" disabled={sessionBusy} onClick={() => setHostDialogOpen(false)}>
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
