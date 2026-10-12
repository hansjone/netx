import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Label, Modal, TextField } from "@heroui/react";
import {
  batchApplyAccountManagedNe,
  batchApplyHopManagedNe,
  batchDeleteManagedNe,
  connectTestManagedNe,
  deleteUmeManagedNe,
  deleteManagedNe,
  fetchIdsByTag,
  fetchManagedNe,
  fetchManagedNeById,
  fetchManagedNeMeta,
  fetchManagedNeStats,
  importManagedNe,
  downloadManagedNeImportTemplate,
  syncUmeManagedNe,
  type ManagedNeStats,
} from "../../services/api";
import { HelpHint } from "../../components/HelpHint";
import { ListPager } from "../../components/ListPager";
import { HopProxyFields, emptyHopProxyFields, type HopProxyFieldsState } from "./components/HopProxyFields";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { queryKeys } from "../../constants/queryKeys";
import { useTableColumnFilters } from "../../hooks/useTableColumnFilters";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import { useAuth } from "../auth/AuthContext";
import type { ManagedNeImportResult, ManagedNeItem } from "../../types";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";
import { openOrFocusModule } from "../../utils/moduleWindows";
import { ManagedNeFormDialog } from "./components/ManagedNeFormDialog";
import { ManagedNeConnectDetailDialog } from "./components/ManagedNeConnectDetailDialog";
import { connectStatusClass } from "./lib/connectStatus";
import { managedNeErrorText } from "./lib/errors";
import {
  deviceTypeForVendor,
  emptyManagedNeForm,
  managedSourceKey,
  type ManagedNeFormState,
} from "./lib/formState";

/** Hide UME→managed sync/delete controls until needed again. APIs remain available. */
const SHOW_UME_MANAGED_SYNC = false;

const MANAGED_NE_ENDPOINT = "/v1/managed-ne";
const CONNECT_STATUSES = ["pass", "fail", "testing", "unknown"] as const;

type AccountState = {
  username: string;
  password: string;
};

type BulkTagAction = "proxy" | "test" | "account";

const emptyAccount = (): AccountState => ({
  username: "",
  password: "",
});

function hopPayload(hop: HopProxyFieldsState) {
  return {
    hop_vendor: hop.hop_vendor,
    hop_host: hop.hop_host.trim(),
    hop_port: hop.hop_port,
    hop_protocol: hop.hop_protocol,
    hop_username: hop.hop_username.trim(),
    hop_password: hop.hop_password,
    hop_command_template: hop.hop_command_template.trim(),
    hop_vrf: hop.hop_vrf.trim(),
    hop_target_auth_mode: hop.hop_target_auth_mode,
    hop_enter_system_view: hop.hop_enter_system_view,
  };
}

export function NePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const { hasScope, isAdmin } = useAuth();
  const canWriteNe = isAdmin || hasScope("ne:write");
  const queryClient = useQueryClient();
  const importRef = useRef<HTMLInputElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkHandled = useRef("");
  const errText = (err: unknown) => managedNeErrorText(err, t);
  const fail = (err: unknown) => showError(errText(err));

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selected, setSelected] = useState<string[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [batchHopOpen, setBatchHopOpen] = useState(false);
  const [batchAccountOpen, setBatchAccountOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedNeItem | null>(null);
  const [formSeed, setFormSeed] = useState<Partial<ManagedNeFormState> | undefined>();
  const [batchHop, setBatchHop] = useState<HopProxyFieldsState>(emptyHopProxyFields);
  const [batchAccount, setBatchAccount] = useState<AccountState>(emptyAccount);
  const [connectDetailRow, setConnectDetailRow] = useState<ManagedNeItem | null>(null);
  const [importResult, setImportResult] = useState<ManagedNeImportResult | null>(null);

  // --- bulk-by-tag dialog ---
  const [bulkTagModalOpen, setBulkTagModalOpen] = useState(false);
  const [bulkTagAction, setBulkTagAction] = useState<BulkTagAction>("proxy");
  const [bulkTagSelected, setBulkTagSelected] = useState<string>("");   // "" = all, "__no_tag__" = no-tag NEs
  const [bulkAccount, setBulkAccount] = useState<AccountState>(emptyAccount);
  const [bulkHop, setBulkHop] = useState<HopProxyFieldsState>(() => emptyHopProxyFields());
  const [resolvingIds, setResolvingIds] = useState(false);

  // A filter change hides rows, so drop the selection rather than act on rows the user cannot see.
  const columns = useTableColumnFilters(MANAGED_NE_ENDPOINT, () => {
    setPage(1);
    setSelected([]);
  });

  const metaQuery = useQuery({
    queryKey: queryKeys.managedNeMeta,
    queryFn: fetchManagedNeMeta,
    staleTime: 60_000,
  });

  const statsQuery = useQuery({
    queryKey: queryKeys.managedNeStats,
    queryFn: fetchManagedNeStats,
    staleTime: 10_000,
  });

  const listQuery = useQuery({
    queryKey: queryKeys.managedNe(columns.key, page, pageSize),
    queryFn: () =>
      fetchManagedNe({
        keyword: "",
        vendor: "",
        connectStatus: "",
        page,
        pageSize,
        columnFilters: columns.filters,
      }),
    refetchInterval: (q) => {
      const items = q.state.data?.items || [];
      return items.some((x) => x.connect_status === "testing") ? 2000 : false;
    },
  });

  useEffect(() => {
    if (!connectDetailRow) return;
    const updated = listQuery.data?.items?.find((x) => x.id === connectDetailRow.id);
    if (!updated) return;
    if (
      updated.connect_status !== connectDetailRow.connect_status ||
      updated.connect_message !== connectDetailRow.connect_message ||
      updated.connect_detail !== connectDetailRow.connect_detail ||
      updated.connect_tested_at !== connectDetailRow.connect_tested_at
    ) {
      setConnectDetailRow(updated);
    }
  }, [listQuery.data, connectDetailRow]);

  const total = listQuery.data?.total ?? 0;
  const pages = pageCount(total, pageSize);
  // Deleting the last rows of the last page must not strand the user on an empty page.
  if (listQuery.data && page > pages) setPage(pages);

  /** List, stats, column filter options and the Network devices view all derive from managed_ne. */
  const invalidateList = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.managedNeAll }),
      queryClient.invalidateQueries({ queryKey: queryKeys.managedNeStats }),
      queryClient.invalidateQueries({ queryKey: ["tableFilterOptions", MANAGED_NE_ENDPOINT] }),
      queryClient.invalidateQueries({ queryKey: queryKeys.cliTargetsAll }),
    ]);

  const deleteMutation = useMutation({
    mutationFn: deleteManagedNe,
    onSuccess: async (_res, id) => {
      setSelected((prev) => prev.filter((x) => x !== id));
      showOk(t("managedNe.form.deleted"));
      await invalidateList();
    },
    onError: fail,
  });

  const connectMutation = useMutation({
    mutationFn: connectTestManagedNe,
    onSuccess: async (res) => {
      showOk(t("managedNe.connect.submitted", { n: res.submitted }));
      await invalidateList();
    },
    onError: fail,
  });

  const batchDeleteMutation = useMutation({
    mutationFn: batchDeleteManagedNe,
    onSuccess: async (res) => {
      setSelected([]);
      showOk(t("managedNe.batchDeleteDone", { n: res.deleted }));
      await invalidateList();
    },
    onError: fail,
  });

  const batchHopMutation = useMutation({
    mutationFn: () => batchApplyHopManagedNe(selected, hopPayload(batchHop)),
    onSuccess: async (res) => {
      setBatchHopOpen(false);
      setBatchHop(emptyHopProxyFields());
      showOk(t("managedNe.hop.batchDone", { n: res.updated }));
      await invalidateList();
    },
    onError: fail,
  });

  const batchAccountMutation = useMutation({
    mutationFn: () =>
      batchApplyAccountManagedNe(selected, {
        username: batchAccount.username.trim(),
        password: batchAccount.password,
      }),
    onSuccess: async (res) => {
      setBatchAccountOpen(false);
      setBatchAccount(emptyAccount());
      showOk(t("managedNe.account.batchDone", { n: res.updated }));
      await invalidateList();
    },
    onError: fail,
  });

  const umeSyncMutation = useMutation({
    mutationFn: syncUmeManagedNe,
    onSuccess: async (res) => {
      showOk(
        t("managedNe.umeSync.done", {
          inserted: res.inserted,
          updated: res.updated,
          deleted: res.deleted,
          total: res.total_inventory,
        }),
      );
      await invalidateList();
    },
    onError: fail,
  });

  const umeDeleteMutation = useMutation({
    mutationFn: deleteUmeManagedNe,
    onSuccess: async (res) => {
      showOk(t("managedNe.umeSync.deletedDone", { n: res.deleted }));
      await invalidateList();
    },
    onError: fail,
  });

  const importMutation = useMutation({
    mutationFn: importManagedNe,
    onSuccess: async (res) => {
      showOk(
        t("managedNe.importResult.done", {
          inserted: res.inserted,
          updated: res.updated,
          failed: res.failed.length,
        }),
      );
      if (res.failed.length) setImportResult(res);
      await invalidateList();
    },
    onError: fail,
  });

  const bulkByTagMutation = useMutation({
    mutationFn: async (params: { action: BulkTagAction; ids: string[] }) => {
      const { action, ids } = params;
      if (action === "test") {
        const res = await connectTestManagedNe(ids);
        return { type: action, n: res.submitted };
      }
      if (action === "account") {
        const res = await batchApplyAccountManagedNe(ids, {
          username: bulkAccount.username.trim(),
          password: bulkAccount.password,
        });
        return { type: action, n: res.updated };
      }
      const res = await batchApplyHopManagedNe(ids, hopPayload(bulkHop));
      return { type: action, n: res.updated };
    },
    onSuccess: async (res) => {
      setBulkTagModalOpen(false);
      if (res.type === "test") showOk(t("managedNe.stats.testDone", { n: res.n }));
      else if (res.type === "account") showOk(t("managedNe.account.batchDone", { n: res.n }));
      else showOk(t("managedNe.stats.proxyDone", { n: res.n }));
      await invalidateList();
    },
    onError: fail,
  });

  const credsOk = metaQuery.data?.credentials_configured ?? false;
  const listItems = useMemo(() => listQuery.data?.items ?? [], [listQuery.data?.items]);
  const pageIds = listItems.map((x) => x.id);
  const selectedOnPage = pageIds.filter((id) => selected.includes(id)).length;
  const allSelected = pageIds.length > 0 && selectedOnPage === pageIds.length;
  const someSelected = selectedOnPage > 0 && !allSelected;

  const openCreate = () => {
    setEditing(null);
    setFormSeed(undefined);
    setModalOpen(true);
  };

  const openEdit = (row: ManagedNeItem) => {
    setEditing(row);
    setFormSeed(undefined);
    setModalOpen(true);
  };

  // Deep links from topology: /ne?ne_id=… (edit) or /ne?create=1&name=&ip_address=&vendor=
  useEffect(() => {
    const neId = String(searchParams.get("ne_id") || "").trim();
    const wantCreate = searchParams.get("create") === "1";
    if (!neId && !wantCreate) {
      deepLinkHandled.current = "";
      return;
    }

    const handleKey = neId ? `edit:${neId}` : `create:${searchParams.toString()}`;
    if (deepLinkHandled.current === handleKey) return;
    deepLinkHandled.current = handleKey;

    const clearDeepLink = () => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const key of ["ne_id", "create", "name", "ip_address", "vendor"]) {
            next.delete(key);
          }
          return next;
        },
        { replace: true },
      );
    };

    if (neId) {
      void (async () => {
        try {
          const row = await fetchManagedNeById(neId);
          openEdit(row);
        } catch (err) {
          showError(managedNeErrorText(err, t));
        } finally {
          clearDeepLink();
        }
      })();
      return;
    }

    const name = String(searchParams.get("name") || "").trim();
    const ip = String(searchParams.get("ip_address") || "").trim();
    const vendorRaw = String(searchParams.get("vendor") || "").trim();
    const base = emptyManagedNeForm();
    const vendor = vendorRaw || base.vendor;
    setEditing(null);
    setFormSeed({
      name,
      vendor,
      device_type: deviceTypeForVendor(vendor),
      ip_address: ip,
    });
    setModalOpen(true);
    clearDeepLink();
  }, [searchParams, setSearchParams, showError, t]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const toggleSelectAll = () => {
    if (allSelected) {
      const ids = new Set(pageIds);
      setSelected((prev) => prev.filter((id) => !ids.has(id)));
    } else {
      setSelected((prev) => [...new Set([...prev, ...pageIds])]);
    }
  };

  const stats: ManagedNeStats | undefined = statsQuery.data;
  const statTags: string[] = stats?.tags ?? [];
  const perTag = stats?.per_tag ?? {};
  const tagCardItems = useMemo(() => {
    if (!stats) return [];
    const items: Array<{ key: string; title: string; total: number; by_status: Record<string, number> }> = [];
    items.push({ key: "__all__", title: t("managedNe.stats.all"), total: stats.total, by_status: stats.by_status });
    // no-tag first (if any)
    if ((stats.no_tag_count ?? 0) > 0 || perTag["__no_tag__"]) {
      items.push({
        key: "__no_tag__",
        title: t("managedNe.stats.noTag"),
        total: perTag["__no_tag__"]?.total ?? stats.no_tag_count ?? 0,
        by_status: perTag["__no_tag__"]?.by_status ?? {},
      });
    }
    for (const tag of stats.tags || []) {
      const x = perTag[tag];
      items.push({
        key: tag,
        title: tag,
        total: x?.total ?? 0,
        by_status: x?.by_status ?? {},
      });
    }
    return items;
  }, [stats, perTag, t]);

  // Tag cards double as a quick filter on the tags column ("" = untagged).
  const tagsFilter = columns.filters.tags ?? null;
  const activeTagCard =
    tagsFilter === null
      ? "__all__"
      : tagsFilter.mode === "include" && tagsFilter.values.length === 1
        ? tagsFilter.values[0] || "__no_tag__"
        : "";
  const applyTagCard = (key: string) => {
    if (key === "__all__") columns.setFilter("tags", null);
    else columns.setFilter("tags", { mode: "include", values: [key === "__no_tag__" ? "" : key] });
  };

  const bulkScopeCount =
    bulkTagSelected === ""
      ? stats?.total
      : perTag[bulkTagSelected]?.total ?? (bulkTagSelected === "__no_tag__" ? stats?.no_tag_count : 0);

  const sourceLabel = (source: string) => {
    const key = managedSourceKey(source);
    return key ? t(`managedNe.source.${key}`) : String(source || "").trim() || t("managedNe.source.manual");
  };
  const statusLabel = (status: string) =>
    (CONNECT_STATUSES as readonly string[]).includes(status) ? t(`managedNe.stats.${status}`) : status;

  const runBulkByTag = async () => {
    if (bulkTagAction === "proxy") {
      if (!bulkHop.hop_host.trim()) {
        showError(t("managedNe.hop.hostRequired"));
        return;
      }
      if (!bulkHop.hop_username.trim()) {
        showError(t("managedNe.hop.userRequired"));
        return;
      }
    }
    if (bulkTagAction === "account" && !bulkAccount.username.trim() && !bulkAccount.password) {
      showError(t("managedNe.account.usernameOrPasswordRequired"));
      return;
    }
    setResolvingIds(true);
    let ids: string[];
    try {
      ids = (await fetchIdsByTag(bulkTagSelected === "" ? null : bulkTagSelected)).ids;
    } catch (err) {
      fail(err);
      return;
    } finally {
      setResolvingIds(false);
    }
    if (ids.length === 0) {
      showError(t("managedNe.stats.noMatchingNe"));
      return;
    }
    if (!window.confirm(t("managedNe.stats.confirm", { n: ids.length }))) return;
    bulkByTagMutation.mutate({ action: bulkTagAction, ids });
  };

  const bulkBusy = resolvingIds || bulkByTagMutation.isPending;

  return (
    <div className="page-stack ne-page">
      {!credsOk && metaQuery.isSuccess ? (
        <section className="panel panel--warn">
          <p>{t("managedNe.credsNotConfigured")}</p>
        </section>
      ) : null}

      <section className="panel ne-stats-panel">
        <div className="panel__toolbar">
          <h2>{t("managedNe.stats.title")}</h2>
          <div className="panel__toolbar-end">
            <div className="panel__actions">
              <Button
                size="sm"
                variant="secondary"
                isDisabled={bulkBusy || !canWriteNe}
                onPress={() => {
                  setBulkTagAction("proxy");
                  setBulkHop(emptyHopProxyFields());
                  setBulkTagModalOpen(true);
                }}
              >
                {t("managedNe.stats.batchProxy")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={bulkBusy || !canWriteNe}
                onPress={() => {
                  setBulkTagAction("account");
                  setBulkAccount(emptyAccount());
                  setBulkTagModalOpen(true);
                }}
              >
                {t("managedNe.account.batchByTag")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={bulkBusy || !canWriteNe}
                onPress={() => {
                  setBulkTagAction("test");
                  setBulkTagModalOpen(true);
                }}
              >
                {t("managedNe.stats.batchTest")}
              </Button>
            </div>
          </div>
        </div>
        {stats ? (
          <>
            <p className="muted text-sm">{t("managedNe.stats.cardFilterHint")}</p>
            <div className="ne-stats-card__tag-cards">
              {tagCardItems.map((x) => (
                <button
                  type="button"
                  key={x.key}
                  className={`ne-tag-card${activeTagCard === x.key ? " is-active" : ""}`}
                  aria-pressed={activeTagCard === x.key}
                  onClick={() => applyTagCard(x.key)}
                >
                  <span className="ne-tag-card__title">{x.title}</span>
                  <span className="ne-tag-card__total">{t("managedNe.stats.total", { n: x.total })}</span>
                  <span className="ne-tag-card__pills">
                    {CONNECT_STATUSES.map((s) => (
                      <span key={s} className={`pt-list-status ${connectStatusClass(s)}`}>
                        {t(`managedNe.stats.${s}`)} {x.by_status[s] ?? 0}
                      </span>
                    ))}
                  </span>
                </button>
              ))}
            </div>
          </>
        ) : statsQuery.isError ? (
          <p className="error-text">{errText(statsQuery.error)}</p>
        ) : (
          <p className="ne-stats-card__loading">{t("managedNe.stats.loadingIds")}</p>
        )}
      </section>

      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("managedNe.title")}</h2>
          <div className="panel__toolbar-end">
            <div className="panel__actions">
              <Button size="sm" variant="primary" onPress={openCreate} isDisabled={!credsOk || !canWriteNe}>
                {t("managedNe.add")}
              </Button>
              {SHOW_UME_MANAGED_SYNC ? (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    onPress={() => umeSyncMutation.mutate()}
                    isDisabled={umeSyncMutation.isPending}
                  >
                    {umeSyncMutation.isPending
                      ? t("managedNe.umeSync.syncing")
                      : t("managedNe.umeSync.sync")}
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onPress={() => {
                      if (!window.confirm(t("managedNe.umeSync.deleteConfirm"))) return;
                      umeDeleteMutation.mutate();
                    }}
                    isDisabled={umeDeleteMutation.isPending}
                  >
                    {umeDeleteMutation.isPending
                      ? t("managedNe.umeSync.deleting")
                      : t("managedNe.umeSync.delete")}
                  </Button>
                </>
              ) : null}
              <Button
                size="sm"
                variant="secondary"
                onPress={() => {
                  void downloadManagedNeImportTemplate("xlsx").catch(fail);
                }}
              >
                {t("managedNe.downloadTemplate")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onPress={() => importRef.current?.click()}
                isDisabled={!credsOk || importMutation.isPending || !canWriteNe}
              >
                {importMutation.isPending ? t("managedNe.importing") : t("managedNe.importBtn")}
              </Button>
              <input
                ref={importRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) importMutation.mutate(file);
                }}
              />
              <Button
                size="sm"
                variant="secondary"
                isDisabled={selected.length === 0 || connectMutation.isPending || !canWriteNe}
                onPress={() => connectMutation.mutate(selected)}
              >
                {connectMutation.isPending ? t("managedNe.connect.running") : t("managedNe.connect.run")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={selected.length === 0 || batchHopMutation.isPending || !canWriteNe}
                onPress={() => {
                  setBatchHop(emptyHopProxyFields());
                  setBatchHopOpen(true);
                }}
              >
                {batchHopMutation.isPending ? t("managedNe.hop.applying") : t("managedNe.hop.batchAdd")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={selected.length === 0 || batchAccountMutation.isPending || !canWriteNe}
                onPress={() => {
                  setBatchAccount(emptyAccount());
                  setBatchAccountOpen(true);
                }}
              >
                {batchAccountMutation.isPending ? t("managedNe.account.applying") : t("managedNe.account.batchAdd")}
              </Button>
              <Button
                size="sm"
                variant="danger"
                isDisabled={selected.length === 0 || batchDeleteMutation.isPending || !canWriteNe}
                onPress={() => {
                  if (!window.confirm(t("managedNe.batchDeleteConfirm", { n: selected.length }))) return;
                  batchDeleteMutation.mutate(selected);
                }}
              >
                {batchDeleteMutation.isPending ? t("managedNe.batchDeleting") : t("managedNe.batchDelete")}
              </Button>
              <Button size="sm" variant="tertiary" isDisabled={!columns.active} onPress={columns.reset}>
                {t("common.clearFilters")}
              </Button>
              <Button size="sm" variant="tertiary" onPress={() => void invalidateList()}>
                {t("common.refresh")}
              </Button>
            </div>
            <HelpHint text={t("managedNe.help")} ariaLabel={t("common.help")} align="end" />
          </div>
        </div>

        <div className="pt-list">
          {listQuery.isError ? (
            <p className="error-text">{t("managedNe.loadFailed", { error: errText(listQuery.error) })}</p>
          ) : null}

          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      aria-label="select all"
                      ref={(input) => { if (input) input.indeterminate = someSelected; }}
                      checked={allSelected}
                      disabled={!pageIds.length}
                      onChange={toggleSelectAll}
                    />
                  </th>
                  <th>{columns.column("name", t("managedNe.col.name"))}</th>
                  <th>{columns.column("vendor", t("managedNe.col.vendor"))}</th>
                  <th>{columns.column("deviceType", t("managedNe.col.deviceType"))}</th>
                  <th>{columns.column("source", t("managedNe.col.source"), sourceLabel)}</th>
                  <th>{columns.column("tags", t("managedNe.col.tags"))}</th>
                  <th>{columns.column("ip", t("managedNe.col.ip"))}</th>
                  <th>{columns.column("username", t("managedNe.col.user"))}</th>
                  <th>{columns.column("connectStatus", t("managedNe.col.connect"), statusLabel)}</th>
                  <th>{t("managedNe.col.testedAt")}</th>
                  <th>{t("managedNe.col.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {listItems.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={row.name || row.ip_address}
                        checked={selected.includes(row.id)}
                        onChange={() => toggleSelect(row.id)}
                      />
                    </td>
                    <td className="pt-list-task-name">{row.name || row.ip_address}</td>
                    <td>{row.vendor}</td>
                    <td>{row.device_type}</td>
                    <td title={row.source_ref || undefined}>
                      <span className="table-tag">{sourceLabel(row.source || "")}</span>
                    </td>
                    <td>
                      {row.tags
                        ? row.tags.split(/\s+/).filter(Boolean).map((tag) => (
                            <span key={tag} className="table-tag">
                              {tag}
                            </span>
                          ))
                        : "—"}
                    </td>
                    <td className="pt-list-num">
                      {row.ip_address}:{row.port}/{row.protocol}
                      {row.hop_enabled ? (
                        <span
                          className="table-tag"
                          title={`${row.hop_host}:${row.hop_port} (${row.hop_vendor})`}
                        >
                          {t(
                            `managedNe.hop.badge.${["linux", "huawei", "cisco", "zte", "bastion"].includes(row.hop_vendor) ? row.hop_vendor : "zte"}`,
                          )}
                        </span>
                      ) : null}
                    </td>
                    <td>{row.username || "—"}</td>
                    <td>
                      <span
                        className={`pt-list-status ${connectStatusClass(row.connect_status)}`}
                        title={row.connect_message || undefined}
                      >
                        {statusLabel(row.connect_status)}
                      </span>
                    </td>
                    <td className="pt-list-time">
                      {row.connect_tested_at
                        ? formatSystemTime(row.connect_tested_at, { assumeUtcNaive: true })
                        : "—"}
                    </td>
                    <td>
                      <div className="btn-row pt-list-actions table-actions">
                        <Button
                          size="sm"
                          variant="secondary"
                          onPress={() =>
                            openOrFocusModule({
                              moduleId: "webcrt",
                              path: `/webcrt?ne_id=${encodeURIComponent(row.id)}`,
                            })
                          }
                        >
                          {t("managedNe.openTerminal")}
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          isDisabled={!row.connect_tested_at && !row.connect_message && !row.connect_detail}
                          onPress={() => setConnectDetailRow(row)}
                        >
                          {t("managedNe.connectDetail")}
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          isDisabled={!canWriteNe}
                          onPress={() => openEdit(row)}
                        >
                          {t("managedNe.edit")}
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          isDisabled={!canWriteNe || deleteMutation.isPending}
                          onPress={() => {
                            if (window.confirm(t("managedNe.confirmDelete"))) deleteMutation.mutate(row.id);
                          }}
                        >
                          {t("managedNe.delete")}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!listItems.length && !listQuery.isFetching && !listQuery.isError ? (
                  <tr>
                    <td colSpan={11}>
                      <div className="pt-list-empty">
                        <p>{columns.active ? t("managedNe.emptyFiltered") : t("common.empty")}</p>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <ListPager
            page={page}
            pages={pages}
            total={total}
            pageSize={pageSize}
            pageSizeOptions={[10, 20, 50, 100, 200]}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
            disabled={listQuery.isLoading}
            meta={
              <>
                {selected.length ? (
                  <span className="ne-selection-meta">
                    · {t("managedNe.selectedCount", { n: selected.length })}
                    <button type="button" className="ne-selection-meta__clear" onClick={() => setSelected([])}>
                      {t("managedNe.clearSelection")}
                    </button>
                  </span>
                ) : null}
                {listQuery.isFetching ? <span className="ne-selection-meta"> · {t("common.refreshing")}</span> : null}
              </>
            }
          />
        </div>
      </section>

      <ManagedNeFormDialog
        open={modalOpen}
        editing={editing}
        initialValues={formSeed}
        onClose={() => {
          setModalOpen(false);
          setEditing(null);
          setFormSeed(undefined);
        }}
        onSaved={async () => {
          const wasEdit = Boolean(editing);
          setModalOpen(false);
          setEditing(null);
          setFormSeed(undefined);
          showOk(wasEdit ? t("managedNe.form.updated") : t("managedNe.form.created"));
          await invalidateList();
        }}
      />

      <AppModalShell
        open={batchHopOpen}
        onClose={() => setBatchHopOpen(false)}
        dismissible={!batchHopMutation.isPending}
        size="lg"
      >
        <Modal.Header>
          <Modal.Heading>{t("managedNe.hop.batchTitle")}</Modal.Heading>
          <Modal.CloseTrigger isDisabled={batchHopMutation.isPending} />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="text-sm text-muted">{t("managedNe.hop.batchHint", { n: selected.length })}</p>
          <p className="text-sm text-muted">{t("managedNe.hop.batchPasswordHint")}</p>
          <HopProxyFields
            value={batchHop}
            onChange={(patch) => setBatchHop((prev) => ({ ...prev, ...patch }))}
            hopPasswordRequired={false}
            hopPasswordOptional
          />
        </Modal.Body>
        <Modal.Footer>
          <Button variant="tertiary" onPress={() => setBatchHopOpen(false)}>
            {t("managedNe.form.cancel")}
          </Button>
          <Button
            variant="primary"
            isDisabled={batchHopMutation.isPending}
            onPress={() => {
              if (!batchHop.hop_host.trim()) {
                showError(t("managedNe.hop.hostRequired"));
                return;
              }
              if (!batchHop.hop_username.trim()) {
                showError(t("managedNe.hop.userRequired"));
                return;
              }
              batchHopMutation.mutate();
            }}
          >
            {batchHopMutation.isPending ? t("managedNe.hop.applying") : t("managedNe.hop.apply")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      <AppModalShell
        open={batchAccountOpen}
        onClose={() => setBatchAccountOpen(false)}
        dismissible={!batchAccountMutation.isPending}
        size="md"
      >
        <Modal.Header>
          <Modal.Heading>{t("managedNe.account.batchTitle")}</Modal.Heading>
          <Modal.CloseTrigger isDisabled={batchAccountMutation.isPending} />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="text-sm text-muted">{t("managedNe.account.batchHint", { n: selected.length })}</p>
          <div className="form-grid">
            <TextField
              fullWidth
              value={batchAccount.username}
              onChange={(username) => setBatchAccount((prev) => ({ ...prev, username }))}
            >
              <Label>{t("managedNe.col.user")}</Label>
              <Input />
            </TextField>
            <TextField
              fullWidth
              type="password"
              value={batchAccount.password}
              onChange={(password) => setBatchAccount((prev) => ({ ...prev, password }))}
            >
              <Label>
                {t("managedNe.col.password")}
                <span className="form-label__optional"> ({t("managedNe.account.passwordOptionalBatch")})</span>
              </Label>
              <Input />
            </TextField>
          </div>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="tertiary" onPress={() => setBatchAccountOpen(false)}>
            {t("managedNe.form.cancel")}
          </Button>
          <Button
            variant="primary"
            isDisabled={batchAccountMutation.isPending}
            onPress={() => {
              if (!batchAccount.username.trim() && !batchAccount.password) {
                showError(t("managedNe.account.usernameOrPasswordRequired"));
                return;
              }
              batchAccountMutation.mutate();
            }}
          >
            {batchAccountMutation.isPending ? t("managedNe.account.applying") : t("managedNe.account.apply")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      <AppModalShell
        open={bulkTagModalOpen}
        onClose={() => setBulkTagModalOpen(false)}
        dismissible={!bulkBusy}
        size="lg"
      >
        <Modal.Header>
          <Modal.Heading>
            {bulkTagAction === "proxy"
              ? t("managedNe.stats.batchProxy")
              : bulkTagAction === "account"
                ? t("managedNe.account.batchByTag")
                : t("managedNe.stats.batchTest")}
            {bulkTagSelected && bulkTagSelected !== "__no_tag__" ? ` · ${bulkTagSelected}` : ""}
            {bulkTagSelected === "__no_tag__" ? ` · ${t("managedNe.stats.noTag")}` : ""}
            {bulkTagSelected === "" ? ` · ${t("managedNe.stats.allTag")}` : ""}
          </Modal.Heading>
          <Modal.CloseTrigger isDisabled={bulkBusy} />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <FieldSelect
            label={t("managedNe.stats.tagFilter")}
            fullWidth
            value={bulkTagSelected}
            onChange={(e) => setBulkTagSelected(e.target.value)}
          >
            <option value="">{t("managedNe.stats.allTag")}</option>
            <option value="__no_tag__">{t("managedNe.stats.noTag")}</option>
            {statTags.map((tag) => (
              <option key={tag} value={tag}>
                {tag}
              </option>
            ))}
          </FieldSelect>
          {bulkScopeCount !== undefined ? (
            <p className="text-sm text-muted">{t("managedNe.stats.scopeCount", { n: bulkScopeCount })}</p>
          ) : null}
          {bulkTagAction === "proxy" ? (
            <>
              <p className="text-sm text-muted">{t("managedNe.hop.batchPasswordHint")}</p>
              <HopProxyFields
                value={bulkHop}
                onChange={(patch) => setBulkHop((prev) => ({ ...prev, ...patch }))}
                hopPasswordRequired={false}
                hopPasswordOptional
              />
            </>
          ) : bulkTagAction === "account" ? (
            <>
              <p className="text-sm text-muted">{t("managedNe.account.batchByTagHint")}</p>
              <div className="form-grid">
                <TextField
                  fullWidth
                  value={bulkAccount.username}
                  onChange={(username) => setBulkAccount((prev) => ({ ...prev, username }))}
                >
                  <Label>{t("managedNe.col.user")}</Label>
                  <Input />
                </TextField>
                <TextField
                  fullWidth
                  type="password"
                  value={bulkAccount.password}
                  onChange={(password) => setBulkAccount((prev) => ({ ...prev, password }))}
                >
                  <Label>
                    {t("managedNe.col.password")}
                    <span className="form-label__optional"> ({t("managedNe.account.passwordOptionalBatch")})</span>
                  </Label>
                  <Input />
                </TextField>
              </div>
            </>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="tertiary" isDisabled={bulkBusy} onPress={() => setBulkTagModalOpen(false)}>
            {t("managedNe.form.cancel")}
          </Button>
          <Button
            variant="primary"
            isDisabled={bulkBusy || bulkScopeCount === 0}
            onPress={() => void runBulkByTag()}
          >
            {bulkBusy
              ? t("managedNe.stats.loadingIds")
              : bulkTagAction === "proxy"
                ? t("managedNe.hop.apply")
                : bulkTagAction === "account"
                  ? t("managedNe.account.apply")
                  : t("managedNe.connect.run")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      <AppModalShell open={importResult !== null} onClose={() => setImportResult(null)} size="md">
        <Modal.Header>
          <Modal.Heading>{t("managedNe.importResult.title")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {importResult ? (
            <>
              <p className="text-sm">
                {t("managedNe.importResult.done", {
                  inserted: importResult.inserted,
                  updated: importResult.updated,
                  failed: importResult.failed.length,
                })}
              </p>
              <h3 className="text-sm">{t("managedNe.importResult.failedTitle", { n: importResult.failed.length })}</h3>
              <div className="ne-import-failures">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t("managedNe.importResult.row")}</th>
                      <th>{t("managedNe.importResult.reason")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importResult.failed.map((item) => (
                      <tr key={`${item.row}:${item.reason}`}>
                        <td className="pt-list-num">{item.row}</td>
                        <td>{errText(new Error(item.reason))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="primary" onPress={() => setImportResult(null)}>
            {t("managedNe.importResult.close")}
          </Button>
        </Modal.Footer>
      </AppModalShell>

      <ManagedNeConnectDetailDialog
        row={connectDetailRow}
        onClose={() => setConnectDetailRow(null)}
        onRetestSubmitted={() => {
          void invalidateList();
        }}
      />
    </div>
  );
}
