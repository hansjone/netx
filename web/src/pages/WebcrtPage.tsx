import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { WebTerminal, type WebTerminalHandle } from "../components/WebTerminal";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import {
  closeWebcrtSession,
  createWebcrtSession,
  fetchCliTargets,
  fetchManagedNeById,
  webcrtWsUrl,
} from "../services/api";
import { pageCount } from "../utils/display";
import type { CliTargetItem } from "../types";

const PAGE_SIZE = 50;

type TermTab = {
  key: string;
  sessionId: string;
  wsUrl: string;
  termEpoch: number;
  target: CliTargetItem;
  status: "connecting" | "connected" | "error" | "closed";
  errorMessage?: string;
  recording: boolean;
};

function targetKey(t: Pick<CliTargetItem, "source" | "id">): string {
  return `${t.source}:${t.id}`;
}

function deviceLabel(t: Pick<CliTargetItem, "name" | "ip_address">): string {
  return String(t.name || t.ip_address || "").trim() || "-";
}

function webcrtErrorMessage(err: unknown, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const raw = String(err);
  if (raw.includes("webcrt_session_limit")) return t("webcrt.err.sessionLimit");
  if (raw.includes("managed_ne_not_found") || raw.includes("ume_ne_not_found")) return t("webcrt.err.neNotFound");
  if (raw.includes("credentials_incomplete") || raw.includes("cli_username_required")) return t("webcrt.err.credsIncomplete");
  if (raw.includes("cli_connect_profile_not_configured")) return t("webcrt.err.cliProfile");
  if (raw.includes("connect_failed")) return t("webcrt.err.connectFailed", { detail: raw.replace(/^.*connect_failed:/, "") });
  return raw;
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
  const [searchParams, setSearchParams] = useSearchParams();
  const presetNeId = String(searchParams.get("ne_id") || "").trim();
  const presetSource = String(searchParams.get("source") || "managed").trim().toLowerCase();

  const [source, setSource] = useState<"all" | "managed" | "ume">("all");
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [tabs, setTabs] = useState<TermTab[]>([]);
  const [activeTabKey, setActiveTabKey] = useState("");
  const connectingKeysRef = useRef<Set<string>>(new Set());
  const tabsRef = useRef<TermTab[]>([]);
  tabsRef.current = tabs;
  const termRefs = useRef<Map<string, WebTerminalHandle>>(new Map());
  const logBuffersRef = useRef<Map<string, string>>(new Map());

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
      connectingKeysRef.current.add(key);

      if (opts?.force && existing?.sessionId) {
        try {
          await closeWebcrtSession(existing.sessionId);
        } catch {
          /* ignore */
        }
      }

      const pending: TermTab = {
        key,
        sessionId: "",
        wsUrl: "",
        termEpoch: (existing?.termEpoch || 0) + (opts?.force ? 1 : 0),
        target,
        status: "connecting",
        recording: existing?.recording || false,
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
            ? { ume_ne_id: target.ume_ne_id || target.id, cols, rows }
            : { ne_id: target.id, cols, rows };
        const sess = await createWebcrtSession(body);
        const wsUrl = webcrtWsUrl(sess.session_id);
        updateTab(key, {
          sessionId: sess.session_id,
          wsUrl,
          status: "connecting",
          termEpoch: pending.termEpoch + 1,
        });
        showOk(t("webcrt.opened", { name: deviceLabel(target) }));
      } catch (err) {
        const message = webcrtErrorMessage(err, t);
        updateTab(key, { status: "error", errorMessage: message });
        showError(message);
      } finally {
        connectingKeysRef.current.delete(key);
      }
    },
    [showOk, showError, t, updateTab],
  );

  const openTargetRef = useRef(openTarget);
  openTargetRef.current = openTarget;

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
      const text = await termRefs.current.get(tab.key)?.copyAll();
      if (text) showOk(t("webcrt.actions.copied"));
      else showError(t("webcrt.actions.copyEmpty"));
    } catch {
      showError(t("webcrt.actions.copyFailed"));
    }
  }, [activeTabKey, showOk, showError, t]);

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

  return (
    <div className="webcrt-shell">
      <aside className="webcrt-sidebar">
        <div className="webcrt-sidebar__header">
          <div className="webcrt-sidebar__title">{t("webcrt.deviceList")}</div>
          <button
            type="button"
            className="webcrt-icon-btn"
            title={t("common.refresh")}
            onClick={() => void targetsQuery.refetch()}
          >
            ↻
          </button>
        </div>
        <div className="webcrt-sidebar__search">
          <input
            type="search"
            value={keywordInput}
            placeholder={t("webcrt.filterKeywordPh")}
            onChange={(e) => setKeywordInput(e.target.value)}
          />
        </div>
        <div className="webcrt-source-tabs">
          {(["all", "managed", "ume"] as const).map((s) => (
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
        <div className="webcrt-tree">
          {targetsQuery.isLoading ? <div className="webcrt-tree__empty">{t("common.refreshing")}</div> : null}
          {!targetsQuery.isLoading && items.length === 0 ? (
            <div className="webcrt-tree__empty">{t("webcrt.empty")}</div>
          ) : null}
          <ul className="webcrt-tree__list">
            {items.map((row) => {
              const key = targetKey(row);
              const tab = tabs.find((x) => x.key === key);
              const isConnecting = tab?.status === "connecting" || connectingKeysRef.current.has(key);
              return (
                <li key={key}>
                  <button
                    type="button"
                    className={`webcrt-tree__item${activeTabKey === key ? " is-active" : ""}`}
                    onMouseDown={(e) => {
                      // Keep focus off the list button so Enter/Backspace go to the CRT.
                      e.preventDefault();
                    }}
                    onClick={() => void openTarget(row)}
                    title={`${deviceLabel(row)}\n${row.ip_address}\n${row.source}`}
                  >
                    <span className="webcrt-tree__icon" aria-hidden>
                      ▣
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
            })}
          </ul>
        </div>
        <div className="webcrt-sidebar__pager">
          <span>{t("common.pagerMeta", { total, page, pages })}</span>
          <div className="webcrt-sidebar__pager-btns">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              ‹
            </button>
            <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              ›
            </button>
          </div>
        </div>
      </aside>

      <main className="webcrt-main">
        {tabs.length > 0 ? (
          <>
            <div className="webcrt-tabs">
              {tabs.map((tab) => (
                <div
                  key={tab.key}
                  className={`webcrt-tabs__item${activeTabKey === tab.key ? " is-active" : ""}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    setActiveTabKey(tab.key);
                    window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 0);
                  }}
                >
                  <span>
                    {deviceLabel(tab.target)}
                    {tab.status === "connecting" ? ` (${t("webcrt.status.connecting")})` : ""}
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
              ))}
            </div>
            {activeTab ? (
              <div className="webcrt-actions">
                <button type="button" className="webcrt-action-btn" onClick={() => void reconnectActive()}>
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
                    <div className="webcrt-main__placeholder">{t("webcrt.status.connecting")}…</div>
                  ) : null}
                  {tab.status === "error" && !tab.wsUrl ? (
                    <div className="webcrt-main__placeholder webcrt-main__placeholder--error">
                      <div>{t("webcrt.status.error")}</div>
                      {tab.errorMessage ? <pre className="webcrt-error-detail">{tab.errorMessage}</pre> : null}
                    </div>
                  ) : null}
                  {tab.wsUrl ? (
                    <WebTerminal
                      ref={(handle) => {
                        if (handle) termRefs.current.set(tab.key, handle);
                        else termRefs.current.delete(tab.key);
                      }}
                      wsUrl={tab.wsUrl}
                      title={deviceLabel(tab.target)}
                      recording={tab.recording}
                      autoFocus={activeTabKey === tab.key}
                      onStdout={(chunk) => {
                        const prev = logBuffersRef.current.get(tab.key) || "";
                        logBuffersRef.current.set(tab.key, prev + chunk);
                      }}
                      onReady={() => {
                        window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 40);
                      }}
                      onStatus={(state) => {
                        if (state === "open" || state === "connected") {
                          updateTab(tab.key, { status: "connected" });
                          if (activeTabKey === tab.key) {
                            window.setTimeout(() => termRefs.current.get(tab.key)?.focus(), 40);
                          }
                        } else if (state === "error") updateTab(tab.key, { status: "error" });
                        else if (state === "closed") updateTab(tab.key, { status: "closed" });
                      }}
                    />
                  ) : null}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="webcrt-main__empty">
            <p>{t("webcrt.termPlaceholder")}</p>
            <p className="panel__hint">{t("webcrt.hintCrt")}</p>
          </div>
        )}
      </main>
    </div>
  );
}
