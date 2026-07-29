import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { WebTerminal } from "../components/WebTerminal";
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
  target: CliTargetItem;
  status: "connecting" | "connected" | "error" | "closed";
  errorMessage?: string;
};

function targetKey(t: Pick<CliTargetItem, "source" | "id">): string {
  return `${t.source}:${t.id}`;
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
  const [selectedKey, setSelectedKey] = useState("");
  const [tabs, setTabs] = useState<TermTab[]>([]);
  const [activeTabKey, setActiveTabKey] = useState("");
  const connectingKeysRef = useRef<Set<string>>(new Set());
  const presetDoneRef = useRef(false);

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

  const selected = useMemo(
    () => items.find((x) => targetKey(x) === selectedKey) || null,
    [items, selectedKey],
  );

  const updateTab = useCallback((key: string, patch: Partial<TermTab>) => {
    setTabs((prev) => prev.map((tab) => (tab.key === key ? { ...tab, ...patch } : tab)));
  }, []);

  const openTarget = useCallback(
    async (target: CliTargetItem) => {
      const key = targetKey(target);
      const existing = tabs.find((tab) => tab.key === key && tab.status !== "closed" && tab.status !== "error");
      if (existing) {
        setActiveTabKey(existing.key);
        setSelectedKey(key);
        return;
      }
      if (connectingKeysRef.current.has(key)) return;
      connectingKeysRef.current.add(key);
      setSelectedKey(key);

      const pending: TermTab = {
        key,
        sessionId: "",
        wsUrl: "",
        target,
        status: "connecting",
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
        updateTab(key, { sessionId: sess.session_id, wsUrl, status: "connecting" });
        showOk(t("webcrt.opened", { name: target.name || target.ip_address }));
      } catch (err) {
        const message = webcrtErrorMessage(err, t);
        updateTab(key, { status: "error", errorMessage: message });
        showError(message);
      } finally {
        connectingKeysRef.current.delete(key);
      }
    },
    [tabs, showOk, showError, t, updateTab],
  );

  const closeTab = useCallback(
    async (key: string) => {
      const tab = tabs.find((x) => x.key === key);
      if (tab?.sessionId) {
        try {
          await closeWebcrtSession(tab.sessionId);
        } catch {
          /* ignore */
        }
      }
      setTabs((prev) => {
        const next = prev.filter((x) => x.key !== key);
        if (activeTabKey === key) {
          setActiveTabKey(next.length ? next[next.length - 1].key : "");
        }
        return next;
      });
    },
    [tabs, activeTabKey],
  );

  // One-shot open from /webcrt?ne_id=...
  useEffect(() => {
    if (!presetNeId || presetDoneRef.current) return;
    let cancelled = false;
    (async () => {
      presetDoneRef.current = true;
      try {
        if (presetSource === "ume") {
          await openTarget({
            source: "ume",
            id: presetNeId,
            ume_ne_id: presetNeId,
            name: presetNeId,
            ip_address: "",
            connect_status: "unknown",
          });
        } else {
          const hit = items.find((x) => x.source === "managed" && x.id === presetNeId);
          if (hit) {
            await openTarget(hit);
          } else {
            const row = await fetchManagedNeById(presetNeId);
            if (cancelled) return;
            await openTarget({
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
        }
      } catch (err) {
        if (!cancelled) showError(webcrtErrorMessage(err, t));
      } finally {
        setSearchParams({}, { replace: true });
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetNeId]);

  const activeTab = tabs.find((x) => x.key === activeTabKey) || null;
  const perPage = (n: number) => t("common.perPage", { n });

  return (
    <div className="webcrt-shell">
      <aside className="webcrt-sidebar">
        <div className="webcrt-sidebar__title">{t("webcrt.sessionManager")}</div>
        <div className="webcrt-sidebar__toolbar">
          <button
            type="button"
            className="webcrt-icon-btn"
            title={t("webcrt.connect")}
            disabled={!selected}
            onClick={() => selected && void openTarget(selected)}
          >
            ⚡
          </button>
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
          <div className="webcrt-tree__folder">Sessions</div>
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
                    className={`webcrt-tree__item${selectedKey === key ? " is-selected" : ""}${activeTabKey === key ? " is-active" : ""}`}
                    onClick={() => setSelectedKey(key)}
                    onDoubleClick={() => void openTarget(row)}
                    title={`${row.name}\n${row.ip_address}\n${row.source}`}
                  >
                    <span className="webcrt-tree__icon" aria-hidden>
                      ▣
                    </span>
                    <span className="webcrt-tree__label">
                      <span className="webcrt-tree__name">{row.ip_address || row.name}</span>
                      <span className="webcrt-tree__meta">
                        {row.source === "ume" ? "UME" : "NE"}
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
          <span>
            {t("common.pagerMeta", { total, page, pages })}
          </span>
          <div className="webcrt-sidebar__pager-btns">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              ‹
            </button>
            <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              ›
            </button>
          </div>
          <span>{perPage(PAGE_SIZE)}</span>
        </div>
        <div className="webcrt-sidebar__footer">
          <button type="button" className="is-active">
            {t("webcrt.sessionManager")}
          </button>
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
                  onClick={() => setActiveTabKey(tab.key)}
                >
                  <span>
                    {tab.target.ip_address || tab.target.name}
                    {tab.status === "connecting" ? ` (${t("webcrt.status.connecting")})` : ""}
                  </span>
                  <button
                    type="button"
                    className="webcrt-tabs__close"
                    aria-label={t("webcrt.disconnect")}
                    onClick={(e) => {
                      e.stopPropagation();
                      void closeTab(tab.key);
                    }}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
            <div className="webcrt-main__body">
              {tabs.map((tab) => (
                <div
                  key={tab.key}
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
                      wsUrl={tab.wsUrl}
                      title={tab.target.ip_address || tab.target.name}
                      onStatus={(state) => {
                        if (state === "open" || state === "connected") updateTab(tab.key, { status: "connected" });
                        else if (state === "error") updateTab(tab.key, { status: "error" });
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
        {activeTab ? (
          <div className="webcrt-statusline">
            {activeTab.target.name} · {activeTab.target.ip_address} · {activeTab.target.source} ·{" "}
            {t(`webcrt.status.${activeTab.status}`)}
          </div>
        ) : null}
      </main>
    </div>
  );
}
