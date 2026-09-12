import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Checkbox, Input, Label, Modal, TextField } from "@heroui/react";
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
} from "../services/api";
import { HelpHint } from "../components/HelpHint";
import { HopProxyFields, emptyHopProxyFields, type HopProxyFieldsState } from "../components/HopProxyFields";
import { AppModalShell } from "../components/ui/AppModalShell";
import { FieldSelect } from "../components/ui/FieldSelect";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import { useAuth } from "../auth/AuthContext";
import type { ManagedNeItem } from "../types";
import { pageCount } from "../utils/display";
import { formatSystemTime } from "../utils/time";
import { openOrFocusModule } from "../utils/moduleWindows";
import { ManagedNeFormDialog } from "./managedNe/ManagedNeFormDialog";
import { ManagedNeConnectDetailDialog } from "./managedNe/ManagedNeConnectDetailDialog";
import { connectStatusClass } from "./managedNe/connectStatus";
import {
  deviceTypeForVendor,
  emptyManagedNeForm,
  managedSourceKey,
  type ManagedNeFormState,
} from "./managedNe/formState";

/** Hide UME→managed sync/delete controls until needed again. APIs remain available. */
const SHOW_UME_MANAGED_SYNC = false;

type AccountState = {
  username: string;
  password: string;
};

const emptyAccount = (): AccountState => ({
  username: "",
  password: "",
});

export function NePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const { hasScope, isAdmin } = useAuth();
  const canWriteNe = isAdmin || hasScope("ne:write");
  const queryClient = useQueryClient();
  const importRef = useRef<HTMLInputElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkHandled = useRef("");

  const [keyword, setKeyword] = useState("");
  const [vendorFilter, setVendorFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
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

  // --- bulk-by-tag dialog ---
  const [bulkTagModalOpen, setBulkTagModalOpen] = useState(false);
  const [bulkTagAction, setBulkTagAction] = useState<"proxy" | "test" | "account">("proxy");
  const [bulkTagSelected, setBulkTagSelected] = useState<string>("");   // "" = all, "__no_tag__" = no-tag NEs
  const [bulkAccount, setBulkAccount] = useState<AccountState>(emptyAccount);

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
    queryKey: queryKeys.managedNe(keyword, vendorFilter, statusFilter, page, pageSize),
    queryFn: () =>
      fetchManagedNe({
        keyword,
        vendor: vendorFilter,
        connectStatus: statusFilter,
        page,
        pageSize,
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
  const perPage = (n: number) => t("common.perPage", { n });

  const invalidateList = () => queryClient.invalidateQueries({ queryKey: queryKeys.managedNeAll });

  const deleteMutation = useMutation({
    mutationFn: deleteManagedNe,
    onSuccess: async () => {
      showOk(t("managedNe.form.deleted"));
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  const connectMutation = useMutation({
    mutationFn: connectTestManagedNe,
    onSuccess: async (res) => {
      showOk(t("managedNe.connect.submitted", { n: res.submitted }));
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  const batchDeleteMutation = useMutation({
    mutationFn: batchDeleteManagedNe,
    onSuccess: async (res) => {
      setSelected([]);
      showOk(t("managedNe.batchDeleteDone", { n: res.deleted }));
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  const batchHopMutation = useMutation({
    mutationFn: () =>
      batchApplyHopManagedNe(selected, {
        hop_vendor: batchHop.hop_vendor,
        hop_host: batchHop.hop_host.trim(),
        hop_port: batchHop.hop_port,
        hop_protocol: batchHop.hop_protocol,
        hop_username: batchHop.hop_username.trim(),
        hop_password: batchHop.hop_password,
        hop_command_template: batchHop.hop_command_template.trim(),
        hop_vrf: batchHop.hop_vrf.trim(),
        hop_target_auth_mode: batchHop.hop_target_auth_mode,
        hop_enter_system_view: batchHop.hop_enter_system_view,
      }),
    onSuccess: async (res) => {
      setBatchHopOpen(false);
      setBatchHop(emptyHopProxyFields());
      showOk(t("managedNe.hop.batchDone", { n: res.updated }));
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
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
    onError: (err) => showError(String(err)),
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
      await Promise.all([
        invalidateList(),
        queryClient.invalidateQueries({ queryKey: queryKeys.managedNeStats }),
      ]);
    },
    onError: (err) => showError(String(err)),
  });

  const umeDeleteMutation = useMutation({
    mutationFn: deleteUmeManagedNe,
    onSuccess: async (res) => {
      showOk(t("managedNe.umeSync.deletedDone", { n: res.deleted }));
      await Promise.all([
        invalidateList(),
        queryClient.invalidateQueries({ queryKey: queryKeys.managedNeStats }),
      ]);
    },
    onError: (err) => showError(String(err)),
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
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  // Bulk-by-tag: fetch ids then run proxy/test
  const [bulkHop, setBulkHop] = useState<HopProxyFieldsState>(() => emptyHopProxyFields());
  const bulkByTagMutation = useMutation({
    mutationFn: async (params: { action: "proxy" | "test" | "account"; tag: string }) => {
      const apiTag = params.tag === "" ? null : params.tag;
      const { ids } = await fetchIdsByTag(apiTag);
      if (ids.length === 0) throw new Error(t("managedNe.stats.loadingIds"));
      if (!window.confirm(t("managedNe.stats.confirm", { n: ids.length }))) return null;
      if (params.action === "test") {
        const res = await connectTestManagedNe(ids);
        return { type: "test" as const, n: res.submitted };
      }
      if (params.action === "account") {
        const res = await batchApplyAccountManagedNe(ids, {
          username: bulkAccount.username.trim(),
          password: bulkAccount.password,
        });
        return { type: "account" as const, n: res.updated };
      }
      const res = await batchApplyHopManagedNe(ids, {
        hop_vendor: bulkHop.hop_vendor,
        hop_host: bulkHop.hop_host.trim(),
        hop_port: bulkHop.hop_port,
        hop_protocol: bulkHop.hop_protocol,
        hop_username: bulkHop.hop_username.trim(),
        hop_password: bulkHop.hop_password,
        hop_command_template: bulkHop.hop_command_template.trim(),
        hop_vrf: bulkHop.hop_vrf.trim(),
        hop_target_auth_mode: bulkHop.hop_target_auth_mode,
        hop_enter_system_view: bulkHop.hop_enter_system_view,
      });
      return { type: "proxy" as const, n: res.updated };
    },
    onSuccess: async (res) => {
      if (!res) return;
      setBulkTagModalOpen(false);
      if (res.type === "test") showOk(t("managedNe.stats.testDone", { n: res.n }));
      else if (res.type === "account") showOk(t("managedNe.account.batchDone", { n: res.n }));
      else showOk(t("managedNe.stats.proxyDone", { n: res.n }));
      await Promise.all([
        invalidateList(),
        queryClient.invalidateQueries({ queryKey: queryKeys.managedNeStats }),
      ]);
    },
    onError: (err) => showError(String(err)),
  });

  const vendors = metaQuery.data?.vendors ?? [];
  const credsOk = metaQuery.data?.credentials_configured ?? false;

  const allSelected = useMemo(() => {
    const items = listQuery.data?.items ?? [];
    return items.length > 0 && items.every((x) => selected.includes(x.id));
  }, [listQuery.data?.items, selected]);

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
          showError(String(err));
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
  }, [searchParams, setSearchParams, showError]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const toggleSelectAll = () => {
    const items = listQuery.data?.items ?? [];
    if (allSelected) {
      const pageIds = new Set(items.map((x) => x.id));
      setSelected((prev) => prev.filter((id) => !pageIds.has(id)));
    } else {
      const ids = items.map((x) => x.id);
      setSelected((prev) => [...new Set([...prev, ...ids])]);
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

  const listItems = listQuery.data?.items || [];
  const hasFilters = Boolean(keyword || vendorFilter || statusFilter);

  return (
    <div className="page-stack ne-page">
      {!credsOk ? (
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
                isDisabled={bulkByTagMutation.isPending}
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
                isDisabled={bulkByTagMutation.isPending}
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
                isDisabled={bulkByTagMutation.isPending}
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
          <div className="ne-stats-card__tag-cards">
            {tagCardItems.map((x) => (
              <div key={x.key} className="ne-tag-card">
                <div className="ne-tag-card__title">{x.title}</div>
                <div className="ne-tag-card__total">{t("managedNe.stats.total", { n: x.total })}</div>
                <div className="ne-tag-card__pills">
                  {(["pass", "fail", "testing", "unknown"] as const).map((s) => (
                    <span key={s} className={`pt-list-status ${connectStatusClass(s)}`}>
                      {t(`managedNe.stats.${s}`)} {x.by_status[s] ?? 0}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
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
                  void downloadManagedNeImportTemplate("xlsx").catch((err) => showError(String(err)));
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
                isDisabled={selected.length === 0 || connectMutation.isPending}
                onPress={() => connectMutation.mutate(selected)}
              >
                {connectMutation.isPending ? t("managedNe.connect.running") : t("managedNe.connect.run")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={selected.length === 0 || batchHopMutation.isPending || !canWriteNe}
                onPress={() => {
                  if (selected.length === 0) {
                    showError(t("managedNe.hop.selectRequired"));
                    return;
                  }
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
                  if (selected.length === 0) {
                    showError(t("managedNe.account.selectRequired"));
                    return;
                  }
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
                  if (selected.length === 0) {
                    showError(t("managedNe.batchDeleteSelectRequired"));
                    return;
                  }
                  if (!window.confirm(t("managedNe.batchDeleteConfirm", { n: selected.length }))) return;
                  batchDeleteMutation.mutate(selected);
                }}
              >
                {batchDeleteMutation.isPending ? t("managedNe.batchDeleting") : t("managedNe.batchDelete")}
              </Button>
              <Button size="sm" variant="tertiary" onPress={() => invalidateList()}>
                {t("common.refresh")}
              </Button>
            </div>
            <HelpHint text={t("managedNe.help")} ariaLabel={t("common.help")} align="end" />
          </div>
        </div>

        <div className="pt-list">
          <div className="filter-inline">
            <Input
              value={keyword}
              placeholder={t("managedNe.keywordPh")}
              onChange={(e) => {
                setKeyword(e.target.value);
                setPage(1);
              }}
            />
            <FieldSelect
              value={vendorFilter}
              onChange={(e) => {
                setVendorFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">{t("managedNe.allVendors")}</option>
              {vendors.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </FieldSelect>
            <FieldSelect
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">{t("managedNe.allConnectStatus")}</option>
              <option value="unknown">unknown</option>
              <option value="testing">testing</option>
              <option value="pass">pass</option>
              <option value="fail">fail</option>
            </FieldSelect>
            <Button
              size="sm"
              variant="tertiary"
              isDisabled={!hasFilters}
              onPress={() => {
                setKeyword("");
                setVendorFilter("");
                setStatusFilter("");
                setPage(1);
              }}
            >
              {t("common.clearFilters")}
            </Button>
          </div>

          {listQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

          {!listItems.length && !listQuery.isLoading ? (
            <div className="pt-list-empty">
              <p>{t("common.empty")}</p>
            </div>
          ) : (
            <div className="pt-list-table-wrap">
              <table className="data-table pt-list-table">
                <thead>
                  <tr>
                    <th>
                      <Checkbox
                        isSelected={allSelected}
                        onChange={toggleSelectAll}
                        aria-label="select all"
                      >
                        <Checkbox.Control>
                          <Checkbox.Indicator />
                        </Checkbox.Control>
                      </Checkbox>
                    </th>
                    <th>{t("managedNe.col.name")}</th>
                    <th>{t("managedNe.col.vendor")}</th>
                    <th>{t("managedNe.col.deviceType")}</th>
                    <th>{t("managedNe.col.source")}</th>
                    <th>{t("managedNe.col.tags")}</th>
                    <th>{t("managedNe.col.ip")}</th>
                    <th>{t("managedNe.col.user")}</th>
                    <th>{t("managedNe.col.connect")}</th>
                    <th>{t("managedNe.col.testedAt")}</th>
                    <th>{t("managedNe.col.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {listItems.map((row) => {
                    const srcKey = managedSourceKey(row.source);
                    const srcLabel = srcKey
                      ? t(`managedNe.source.${srcKey}`)
                      : String(row.source || "").trim() || t("managedNe.source.manual");
                    return (
                    <tr key={row.id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.includes(row.id)}
                          onChange={() => toggleSelect(row.id)}
                        />
                      </td>
                      <td className="pt-list-task-name">{row.name || row.ip_address}</td>
                      <td>{row.vendor}</td>
                      <td>{row.device_type}</td>
                      <td title={row.source_ref || undefined}>
                        <span className="table-tag">{srcLabel}</span>
                      </td>
                      <td>
                        {row.tags
                          ? row.tags.split(/\s+/).map((tag) => (
                              <span key={tag} className="table-tag">
                                {tag}
                              </span>
                            ))
                          : t("common.empty")}
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
                      <td>{row.username}</td>
                      <td>
                        <span
                          className={`pt-list-status ${connectStatusClass(row.connect_status)}`}
                          title={row.connect_message || undefined}
                        >
                          {row.connect_status}
                        </span>
                      </td>
                      <td className="pt-list-time">
                        {row.connect_tested_at
                          ? formatSystemTime(row.connect_tested_at, { assumeUtcNaive: true })
                          : t("common.empty")}
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
                            isDisabled={!canWriteNe}
                            onPress={() => {
                              if (window.confirm(t("managedNe.confirmDelete"))) deleteMutation.mutate(row.id);
                            }}
                          >
                            {t("managedNe.delete")}
                          </Button>
                        </div>
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="pager pt-list-pager">
            <span className="muted">
              {t("common.pagerMeta", {
                total: String(total),
                page: String(page),
                pages: String(pages),
              })}
            </span>
            <div className="btn-row">
              <Button
                size="sm"
                variant="secondary"
                isDisabled={page <= 1}
                onPress={() => setPage(Math.max(1, page - 1))}
              >
                {t("common.prevPage")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isDisabled={page >= pages}
                onPress={() => setPage(page + 1)}
              >
                {t("common.nextPage")}
              </Button>
              <FieldSelect
                value={String(pageSize)}
                onChange={(e) => {
                  setPageSize(Number(e.target.value) || 10);
                  setPage(1);
                }}
              >
                <option value="10">{perPage(10)}</option>
                <option value="20">{perPage(20)}</option>
                <option value="50">{perPage(50)}</option>
                <option value="100">{perPage(100)}</option>
              </FieldSelect>
            </div>
          </div>
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
          <HopProxyFields value={batchHop} onChange={(patch) => setBatchHop((prev) => ({ ...prev, ...patch }))} />
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
              if (!batchHop.hop_password && batchHop.hop_target_auth_mode !== "bastion_managed") {
                showError(t("managedNe.hop.passwordRequired"));
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
        dismissible={!bulkByTagMutation.isPending}
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
          <Modal.CloseTrigger isDisabled={bulkByTagMutation.isPending} />
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
          {bulkTagAction === "proxy" ? (
            <>
              <p className="text-sm text-muted">{t("managedNe.hop.batchHint", { n: "?" })}</p>
              <HopProxyFields value={bulkHop} onChange={(patch) => setBulkHop((prev) => ({ ...prev, ...patch }))} />
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
          ) : (
            <p className="text-sm text-muted">{t("managedNe.stats.confirm", { n: "?" })}</p>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="tertiary" onPress={() => setBulkTagModalOpen(false)}>
            {t("managedNe.form.cancel")}
          </Button>
          <Button
            variant="primary"
            isDisabled={bulkByTagMutation.isPending}
            onPress={() => {
              if (bulkTagAction === "proxy") {
                if (!bulkHop.hop_host.trim()) {
                  showError(t("managedNe.hop.hostRequired"));
                  return;
                }
                if (!bulkHop.hop_username.trim()) {
                  showError(t("managedNe.hop.userRequired"));
                  return;
                }
                if (!bulkHop.hop_password && bulkHop.hop_target_auth_mode !== "bastion_managed") {
                  showError(t("managedNe.hop.passwordRequired"));
                  return;
                }
              }
              if (bulkTagAction === "account") {
                if (!bulkAccount.username.trim() && !bulkAccount.password) {
                  showError(t("managedNe.account.usernameOrPasswordRequired"));
                  return;
                }
              }
              bulkByTagMutation.mutate({ action: bulkTagAction, tag: bulkTagSelected });
            }}
          >
            {bulkByTagMutation.isPending
              ? t("managedNe.stats.loadingIds")
              : bulkTagAction === "proxy"
                ? t("managedNe.hop.apply")
                : bulkTagAction === "account"
                  ? t("managedNe.account.apply")
                  : t("managedNe.connect.run")}
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
