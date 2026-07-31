import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  batchApplyAccountManagedNe,
  batchApplyHopManagedNe,
  batchDeleteManagedNe,
  connectTestManagedNe,
  createManagedNe,
  deleteUmeManagedNe,
  deleteManagedNe,
  fetchIdsByTag,
  fetchManagedNe,
  fetchManagedNeMeta,
  fetchManagedNeStats,
  importManagedNe,
  downloadManagedNeImportTemplate,
  syncUmeManagedNe,
  updateManagedNe,
  type ManagedNeStats,
} from "../services/api";
import { HelpHint } from "../components/HelpHint";
import { HopProxyFields, emptyHopProxyFields, type HopProxyFieldsState } from "../components/HopProxyFields";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { ManagedNeItem } from "../types";
import { pageCount } from "../utils/display";
import { formatSystemTime } from "../utils/time";
import { openOrFocusModule } from "../utils/moduleWindows";
import {
  defaultHopTemplate,
  isAutoHopTemplate,
  patchHopVendorChange,
  type HopVendor,
} from "../utils/hopProxy";

type FormState = {
  name: string;
  vendor: string;
  device_type: string;
  ip_address: string;
  port: number;
  protocol: string;
  username: string;
  password: string;
  tags: string;
  remark: string;
  hop_enabled: boolean;
  hop_vendor: HopVendor;
  hop_host: string;
  hop_port: number;
  hop_protocol: string;
  hop_username: string;
  hop_password: string;
  hop_command_template: string;
  hop_vrf: string;
  hop_target_auth_mode: "bastion_managed" | "manual";
};

type AccountState = {
  username: string;
  password: string;
};

const emptyForm = (): FormState => ({
  name: "",
  vendor: "ZTE",
  device_type: "zte_zxros",
  ip_address: "",
  port: 22,
  protocol: "ssh",
  username: "",
  password: "",
  tags: "",
  remark: "",
  hop_enabled: false,
  hop_vendor: "zte",
  hop_host: "",
  hop_port: 22,
  hop_protocol: "ssh",
  hop_username: "",
  hop_password: "",
  hop_command_template: defaultHopTemplate("zte", "ssh", ""),
  hop_vrf: "",
  hop_target_auth_mode: "bastion_managed",
});

const emptyAccount = (): AccountState => ({
  username: "",
  password: "",
});

function applyHopTemplate(prev: FormState, protocol: string, vrf: string, force = false): Partial<FormState> {
  if (!force && !isAutoHopTemplate(prev.hop_command_template, prev.hop_vendor, prev.hop_protocol, prev.hop_vrf)) {
    return {};
  }
  return { hop_command_template: defaultHopTemplate(prev.hop_vendor, protocol, vrf) };
}

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

function connectPillLevel(status: string): "up" | "down" | "unknown" | "warn" {
  if (status === "pass") return "up";
  if (status === "fail") return "down";
  if (status === "testing") return "warn";
  return "unknown";
}

export function NePage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const importRef = useRef<HTMLInputElement>(null);

  const [keyword, setKeyword] = useState("");
  const [vendorFilter, setVendorFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selected, setSelected] = useState<string[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [batchHopOpen, setBatchHopOpen] = useState(false);
  const [batchAccountOpen, setBatchAccountOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedNeItem | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
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

  const saveMutation = useMutation({
    mutationFn: async () => {
      const body = {
        name: form.name,
        vendor: form.vendor,
        device_type: form.device_type,
        ip_address: form.ip_address,
        port: form.port,
        protocol: form.protocol,
        username: form.username,
        tags: form.tags,
        remark: form.remark,
        hop_enabled: form.hop_enabled,
        hop_vendor: form.hop_vendor,
        hop_host: form.hop_host,
        hop_port: form.hop_port,
        hop_protocol: form.hop_protocol,
        hop_username: form.hop_username,
        hop_command_template: form.hop_command_template,
        hop_vrf: form.hop_vrf,
        hop_target_auth_mode: form.hop_target_auth_mode,
        ...(form.password ? { password: form.password } : {}),
        ...(form.hop_password ? { hop_password: form.hop_password } : {}),
      };
      if (form.hop_enabled) {
        if (!form.hop_host.trim()) throw new Error(t("managedNe.hop.hostRequired"));
        if (!form.hop_username.trim()) throw new Error(t("managedNe.hop.userRequired"));
        if (!editing && !form.hop_password) throw new Error(t("managedNe.hop.passwordRequired"));
      }
      if (editing) {
        if (!form.password) delete (body as { password?: string }).password;
        if (!form.hop_password) delete (body as { hop_password?: string }).hop_password;
        return updateManagedNe(editing.id, body);
      }
      return createManagedNe({ ...body, password: form.password || "" });
    },
    onSuccess: async () => {
      setModalOpen(false);
      setEditing(null);
      setForm(emptyForm());
      showOk(editing ? t("managedNe.form.updated") : t("managedNe.form.created"));
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

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
  const deviceTypes = metaQuery.data?.device_types ?? [];
  const credsOk = metaQuery.data?.credentials_configured ?? false;

  const allSelected = useMemo(() => {
    const items = listQuery.data?.items ?? [];
    return items.length > 0 && items.every((x) => selected.includes(x.id));
  }, [listQuery.data?.items, selected]);

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm());
    setModalOpen(true);
  };

  const openEdit = (row: ManagedNeItem) => {
    setEditing(row);
    setForm({
      name: row.name,
      vendor: row.vendor,
      device_type: row.device_type,
      ip_address: row.ip_address,
      port: row.port,
      protocol: row.protocol,
      username: row.username,
      password: "",
      tags: row.tags,
      remark: row.remark,
      hop_enabled: row.hop_enabled,
      hop_vendor: (["linux", "huawei", "cisco", "zte", "bastion"].includes(row.hop_vendor)
        ? row.hop_vendor
        : "zte") as HopVendor,
      hop_host: row.hop_host,
      hop_port: row.hop_port,
      hop_protocol: row.hop_protocol,
      hop_username: row.hop_username,
      hop_password: "",
      hop_command_template: isAutoHopTemplate(
        row.hop_command_template,
        row.hop_vendor,
        row.hop_protocol,
        row.hop_vrf,
      )
        ? defaultHopTemplate(row.hop_vendor, row.hop_protocol, row.hop_vrf)
        : row.hop_command_template || defaultHopTemplate(row.hop_vendor, row.hop_protocol, row.hop_vrf),
      hop_vrf: row.hop_vrf,
      hop_target_auth_mode:
        row.hop_target_auth_mode === "manual" ? "manual" : "bastion_managed",
    });
    setModalOpen(true);
  };

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

  return (
    <div className="page-stack">
      {!credsOk ? (
        <section className="panel panel--warn">
          <p>{t("managedNe.credsNotConfigured")}</p>
        </section>
      ) : null}

      {/* ── statistics card ── */}
      <section className="card ne-stats-card">
        <div className="ne-stats-card__header">
          <div className="ne-stats-card__header-left">
            <span className="ne-stats-card__title">{t("managedNe.stats.title")}</span>
          </div>
          <div className="ne-stats-card__header-actions">
            <button
              type="button"
              disabled={bulkByTagMutation.isPending}
              onClick={() => {
                setBulkTagAction("proxy");
                setBulkHop(emptyHopProxyFields());
                setBulkTagModalOpen(true);
              }}
            >
              {t("managedNe.stats.batchProxy")}
            </button>
            <button
              type="button"
              disabled={bulkByTagMutation.isPending}
              onClick={() => {
                setBulkTagAction("account");
                setBulkAccount(emptyAccount());
                setBulkTagModalOpen(true);
              }}
            >
              {t("managedNe.account.batchByTag")}
            </button>
            <button
              type="button"
              disabled={bulkByTagMutation.isPending}
              onClick={() => {
                setBulkTagAction("test");
                setBulkTagModalOpen(true);
              }}
            >
              {t("managedNe.stats.batchTest")}
            </button>
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
                    <span key={s} className={`ne-stats-pill ne-stats-pill--${s}`}>
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
              <button type="button" onClick={openCreate} disabled={!credsOk}>
                {t("managedNe.add")}
              </button>
              <button
                type="button"
                onClick={() => umeSyncMutation.mutate()}
                disabled={umeSyncMutation.isPending}
              >
                {umeSyncMutation.isPending ? t("managedNe.umeSync.syncing") : t("managedNe.umeSync.sync")}
              </button>
              <button
                type="button"
                className="btn btn--danger"
                onClick={() => {
                  if (!window.confirm(t("managedNe.umeSync.deleteConfirm"))) return;
                  umeDeleteMutation.mutate();
                }}
                disabled={umeDeleteMutation.isPending}
              >
                {umeDeleteMutation.isPending ? t("managedNe.umeSync.deleting") : t("managedNe.umeSync.delete")}
              </button>
              <button
                type="button"
                onClick={() => {
                  void downloadManagedNeImportTemplate("xlsx").catch((err) => showError(String(err)));
                }}
              >
                {t("managedNe.downloadTemplate")}
              </button>
              <button
                type="button"
                onClick={() => importRef.current?.click()}
                disabled={!credsOk || importMutation.isPending}
              >
                {importMutation.isPending ? t("managedNe.importing") : t("managedNe.importBtn")}
              </button>
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
              <button
                type="button"
                disabled={selected.length === 0 || connectMutation.isPending}
                onClick={() => connectMutation.mutate(selected)}
              >
                {connectMutation.isPending ? t("managedNe.connect.running") : t("managedNe.connect.run")}
              </button>
              <button
                type="button"
                disabled={selected.length === 0 || batchHopMutation.isPending}
                onClick={() => {
                  if (selected.length === 0) {
                    showError(t("managedNe.hop.selectRequired"));
                    return;
                  }
                  setBatchHop(emptyHopProxyFields());
                  setBatchHopOpen(true);
                }}
              >
                {batchHopMutation.isPending ? t("managedNe.hop.applying") : t("managedNe.hop.batchAdd")}
              </button>
              <button
                type="button"
                disabled={selected.length === 0 || batchAccountMutation.isPending}
                onClick={() => {
                  if (selected.length === 0) {
                    showError(t("managedNe.account.selectRequired"));
                    return;
                  }
                  setBatchAccount(emptyAccount());
                  setBatchAccountOpen(true);
                }}
              >
                {batchAccountMutation.isPending ? t("managedNe.account.applying") : t("managedNe.account.batchAdd")}
              </button>
              <button
                type="button"
                className="btn btn--danger"
                disabled={selected.length === 0 || batchDeleteMutation.isPending}
                onClick={() => {
                  if (selected.length === 0) {
                    showError(t("managedNe.batchDeleteSelectRequired"));
                    return;
                  }
                  if (!window.confirm(t("managedNe.batchDeleteConfirm", { n: selected.length }))) return;
                  batchDeleteMutation.mutate(selected);
                }}
              >
                {batchDeleteMutation.isPending ? t("managedNe.batchDeleting") : t("managedNe.batchDelete")}
              </button>
              <button type="button" onClick={() => invalidateList()}>
                {t("common.refresh")}
              </button>
            </div>
            <HelpHint text={t("managedNe.help")} ariaLabel={t("common.help")} align="end" />
          </div>
        </div>

        <div className="filter-inline">
          <input
            value={keyword}
            placeholder={t("managedNe.keywordPh")}
            onChange={(e) => setKeyword(e.target.value)}
          />
          <select value={vendorFilter} onChange={(e) => setVendorFilter(e.target.value)}>
            <option value="">{t("managedNe.allVendors")}</option>
            {vendors.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">{t("managedNe.allConnectStatus")}</option>
            <option value="unknown">unknown</option>
            <option value="testing">testing</option>
            <option value="pass">pass</option>
            <option value="fail">fail</option>
          </select>
          <button type="button" onClick={() => setPage(1)}>
            {t("common.query")}
          </button>
          <button
            type="button"
            onClick={() => {
              setKeyword("");
              setVendorFilter("");
              setStatusFilter("");
              setPage(1);
            }}
          >
            {t("common.clearFilters")}
          </button>
        </div>

        <table>
          <thead>
            <tr>
              <th>
                <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} aria-label="select all" />
              </th>
              <th>{t("managedNe.col.name")}</th>
              <th>{t("managedNe.col.vendor")}</th>
              <th>{t("managedNe.col.deviceType")}</th>
              <th>{t("managedNe.col.tags")}</th>
              <th>{t("managedNe.col.ip")}</th>
              <th>{t("managedNe.col.user")}</th>
              <th>{t("managedNe.col.connect")}</th>
              <th>{t("managedNe.col.testedAt")}</th>
              <th>{t("managedNe.col.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {(listQuery.data?.items || []).map((row) => (
              <tr key={row.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.includes(row.id)}
                    onChange={() => toggleSelect(row.id)}
                  />
                </td>
                <td>{row.name || row.ip_address}</td>
                <td>{row.vendor}</td>
                <td>{row.device_type}</td>
                <td>
                  {row.tags
                    ? row.tags.split(/\s+/).map((tag) => (
                        <span key={tag} className="table-tag">
                          {tag}
                        </span>
                      ))
                    : t("common.empty")}
                </td>
                <td>
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
                    className={`conn-pill conn-pill--${connectPillLevel(row.connect_status)}`}
                    title={row.connect_message || undefined}
                  >
                    {row.connect_status}
                  </span>
                </td>
                <td>
                  {row.connect_tested_at
                    ? formatSystemTime(row.connect_tested_at, { assumeUtcNaive: true })
                    : t("common.empty")}
                </td>
                <td className="table-actions">
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() =>
                      openOrFocusModule({
                        moduleId: "network",
                        path: `/network/webcrt?ne_id=${encodeURIComponent(row.id)}`,
                      })
                    }
                    title={t("managedNe.openTerminal")}
                  >
                    {t("managedNe.openTerminal")}
                  </button>
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => setConnectDetailRow(row)}
                    disabled={!row.connect_tested_at && !row.connect_message && !row.connect_detail}
                  >
                    {t("managedNe.connectDetail")}
                  </button>
                  <button type="button" className="link-btn" onClick={() => openEdit(row)}>
                    {t("managedNe.edit")}
                  </button>
                  <button
                    type="button"
                    className="link-btn link-btn--danger"
                    onClick={() => {
                      if (window.confirm(t("managedNe.confirmDelete"))) deleteMutation.mutate(row.id);
                    }}
                  >
                    {t("managedNe.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="pager">
          <div className="pager__meta">{t("common.pagerMeta", { total, page, pages })}</div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1}>
              {t("common.prevPage")}
            </button>
            <button className="pager__btn" onClick={() => setPage(page + 1)} disabled={page >= pages}>
              {t("common.nextPage")}
            </button>
            <select
              className="pager__size"
              value={String(pageSize)}
              onChange={(e) => {
                setPageSize(Number(e.target.value) || 50);
                setPage(1);
              }}
            >
              <option value="20">{perPage(20)}</option>
              <option value="50">{perPage(50)}</option>
              <option value="100">{perPage(100)}</option>
            </select>
          </div>
        </div>
      </section>

      {modalOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setModalOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>{editing ? t("managedNe.form.editTitle") : t("managedNe.form.createTitle")}</h3>
            <p className="form-hint">{t("managedNe.form.requiredHint")}</p>
            <div className="form-grid">
              <label>
                <FormLabel>{t("managedNe.col.name")}</FormLabel>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                <span className="form-field-hint">{t("managedNe.form.nameConnectHint")}</span>
              </label>
              <label>
                <FormLabel required>{t("managedNe.col.vendor")}</FormLabel>
                <select required value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })}>
                  {vendors.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <FormLabel required>{t("managedNe.col.deviceType")}</FormLabel>
                <select
                  required
                  value={form.device_type}
                  onChange={(e) => setForm({ ...form, device_type: e.target.value })}
                >
                  {deviceTypes.map((dt) => (
                    <option key={dt} value={dt}>
                      {dt}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <FormLabel required>{t("managedNe.col.ip")}</FormLabel>
                <input
                  required
                  value={form.ip_address}
                  onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                  disabled={Boolean(editing)}
                />
              </label>
              <label>
                <FormLabel>{t("managedNe.col.port")}</FormLabel>
                <input
                  type="number"
                  value={form.port}
                  onChange={(e) => setForm({ ...form, port: Number(e.target.value) || 22 })}
                />
              </label>
              <label>
                <FormLabel>{t("managedNe.col.protocol")}</FormLabel>
                <select value={form.protocol} onChange={(e) => setForm({ ...form, protocol: e.target.value })}>
                  <option value="ssh">ssh</option>
                  <option value="telnet">telnet</option>
                </select>
              </label>
              <label>
                <FormLabel required>{t("managedNe.col.user")}</FormLabel>
                <input
                  required
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                />
              </label>
              <label>
                <FormLabel>
                  {t("managedNe.col.password")}
                  <span className="form-label__optional"> ({t("managedNe.form.passwordOptional")})</span>
                </FormLabel>
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                />
              </label>
              <label>
                <FormLabel>{t("managedNe.col.tags")}</FormLabel>
                <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} />
              </label>
              <label className="form-grid__full">
                <FormLabel>{t("managedNe.col.remark")}</FormLabel>
                <input value={form.remark} onChange={(e) => setForm({ ...form, remark: e.target.value })} />
              </label>
            </div>

            <fieldset className="form-fieldset form-grid__full">
              <legend>{t("managedNe.hop.sectionTitle")}</legend>
              <label className="form-check">
                <input
                  type="checkbox"
                  checked={form.hop_enabled}
                  onChange={(e) => {
                    const hop_enabled = e.target.checked;
                    setForm((prev) => ({
                      ...prev,
                      hop_enabled,
                      ...(hop_enabled
                        ? {
                            ...patchHopVendorChange(prev.hop_vendor, prev),
                            ...applyHopTemplate(prev, prev.hop_protocol, prev.hop_vrf, true),
                          }
                        : {}),
                    }));
                  }}
                />
                <span className="form-check__text">{t("managedNe.hop.enable")}</span>
              </label>
              {form.hop_enabled ? (
                <HopProxyFields
                  value={{
                    hop_vendor: form.hop_vendor,
                    hop_host: form.hop_host,
                    hop_port: form.hop_port,
                    hop_protocol: form.hop_protocol,
                    hop_username: form.hop_username,
                    hop_password: form.hop_password,
                    hop_command_template: form.hop_command_template,
                    hop_vrf: form.hop_vrf,
                    hop_target_auth_mode: form.hop_target_auth_mode,
                  }}
                  onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
                  hopPasswordRequired={!editing}
                  hopPasswordOptional={Boolean(editing)}
                />
              ) : null}
            </fieldset>
            <div className="modal__actions">
              <button type="button" onClick={() => setModalOpen(false)}>
                {t("managedNe.form.cancel")}
              </button>
              <button type="button" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                {saveMutation.isPending ? t("managedNe.form.saving") : t("managedNe.form.save")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {batchHopOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setBatchHopOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>{t("managedNe.hop.batchTitle")}</h3>
            <p className="form-hint">{t("managedNe.hop.batchHint", { n: selected.length })}</p>
            <HopProxyFields value={batchHop} onChange={(patch) => setBatchHop((prev) => ({ ...prev, ...patch }))} />
            <div className="modal__actions">
              <button type="button" onClick={() => setBatchHopOpen(false)}>
                {t("managedNe.form.cancel")}
              </button>
              <button
                type="button"
                disabled={batchHopMutation.isPending}
                onClick={() => {
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
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {batchAccountOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setBatchAccountOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>{t("managedNe.account.batchTitle")}</h3>
            <p className="form-hint">{t("managedNe.account.batchHint", { n: selected.length })}</p>
            <div className="form-grid">
              <label>
                <FormLabel>{t("managedNe.col.user")}</FormLabel>
                <input
                  value={batchAccount.username}
                  onChange={(e) => setBatchAccount((prev) => ({ ...prev, username: e.target.value }))}
                />
              </label>
              <label>
                <FormLabel>
                  {t("managedNe.col.password")}
                  <span className="form-label__optional"> ({t("managedNe.account.passwordOptionalBatch")})</span>
                </FormLabel>
                <input
                  type="password"
                  value={batchAccount.password}
                  onChange={(e) => setBatchAccount((prev) => ({ ...prev, password: e.target.value }))}
                />
              </label>
            </div>
            <div className="modal__actions">
              <button type="button" onClick={() => setBatchAccountOpen(false)}>
                {t("managedNe.form.cancel")}
              </button>
              <button
                type="button"
                disabled={batchAccountMutation.isPending}
                onClick={() => {
                  if (!batchAccount.username.trim() && !batchAccount.password) {
                    showError(t("managedNe.account.usernameOrPasswordRequired"));
                    return;
                  }
                  batchAccountMutation.mutate();
                }}
              >
                {batchAccountMutation.isPending ? t("managedNe.account.applying") : t("managedNe.account.apply")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {bulkTagModalOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setBulkTagModalOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>
              {bulkTagAction === "proxy"
                ? t("managedNe.stats.batchProxy")
                : bulkTagAction === "account"
                  ? t("managedNe.account.batchByTag")
                  : t("managedNe.stats.batchTest")}
              {bulkTagSelected && bulkTagSelected !== "__no_tag__" ? ` · ${bulkTagSelected}` : ""}
              {bulkTagSelected === "__no_tag__" ? ` · ${t("managedNe.stats.noTag")}` : ""}
              {bulkTagSelected === "" ? ` · ${t("managedNe.stats.allTag")}` : ""}
            </h3>
            <label>
              <FormLabel>{t("managedNe.stats.tagFilter")}</FormLabel>
              <select
                className="ne-stats-card__tag-select"
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
              </select>
            </label>
            {bulkTagAction === "proxy" ? (
              <>
                <p className="form-hint">{t("managedNe.hop.batchHint", { n: "?" })}</p>
                <HopProxyFields value={bulkHop} onChange={(patch) => setBulkHop((prev) => ({ ...prev, ...patch }))} />
              </>
            ) : bulkTagAction === "account" ? (
              <>
                <p className="form-hint">{t("managedNe.account.batchByTagHint")}</p>
                <div className="form-grid">
                  <label>
                    <FormLabel>{t("managedNe.col.user")}</FormLabel>
                    <input
                      value={bulkAccount.username}
                      onChange={(e) => setBulkAccount((prev) => ({ ...prev, username: e.target.value }))}
                    />
                  </label>
                  <label>
                    <FormLabel>
                      {t("managedNe.col.password")}
                      <span className="form-label__optional"> ({t("managedNe.account.passwordOptionalBatch")})</span>
                    </FormLabel>
                    <input
                      type="password"
                      value={bulkAccount.password}
                      onChange={(e) => setBulkAccount((prev) => ({ ...prev, password: e.target.value }))}
                    />
                  </label>
                </div>
              </>
            ) : (
              <p className="form-hint">{t("managedNe.stats.confirm", { n: "?" })}</p>
            )}
            <div className="modal__actions">
              <button type="button" onClick={() => setBulkTagModalOpen(false)}>
                {t("managedNe.form.cancel")}
              </button>
              <button
                type="button"
                disabled={bulkByTagMutation.isPending}
                onClick={() => {
                  if (bulkTagAction === "proxy") {
                    if (!bulkHop.hop_host.trim()) { showError(t("managedNe.hop.hostRequired")); return; }
                    if (!bulkHop.hop_username.trim()) { showError(t("managedNe.hop.userRequired")); return; }
                    if (!bulkHop.hop_password && bulkHop.hop_target_auth_mode !== "bastion_managed") {
                      showError(t("managedNe.hop.passwordRequired")); return;
                    }
                  }
                  if (bulkTagAction === "account") {
                    if (!bulkAccount.username.trim() && !bulkAccount.password) {
                      showError(t("managedNe.account.usernameOrPasswordRequired")); return;
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
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {connectDetailRow ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setConnectDetailRow(null)}>
          <div className="modal modal--wide" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>{t("managedNe.connectDetailTitle")}</h3>
            <p className="form-hint">
              {connectDetailRow.name || connectDetailRow.ip_address} · {connectDetailRow.ip_address}:
              {connectDetailRow.port}/{connectDetailRow.protocol}
              {connectDetailRow.connect_tested_at
                ? ` · ${formatSystemTime(connectDetailRow.connect_tested_at, { assumeUtcNaive: true })}`
                : ""}
            </p>
            <p>
              <span className={`conn-pill conn-pill--${connectPillLevel(connectDetailRow.connect_status)}`}>
                {connectDetailRow.connect_status}
              </span>
              {connectDetailRow.connect_message ? (
                <span className="connect-detail-summary"> — {connectDetailRow.connect_message}</span>
              ) : null}
            </p>
            <pre className="connect-log">
              {connectDetailRow.connect_detail?.trim() ||
                connectDetailRow.connect_message?.trim() ||
                t("managedNe.connectDetailEmpty")}
            </pre>
            <div className="modal__actions">
              <button
                type="button"
                disabled={connectMutation.isPending}
                onClick={() => {
                  connectMutation.mutate([connectDetailRow.id]);
                }}
              >
                {connectMutation.isPending ? t("managedNe.connect.running") : t("managedNe.connect.retest")}
              </button>
              <button type="button" onClick={() => setConnectDetailRow(null)}>
                {t("managedNe.form.cancel")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
