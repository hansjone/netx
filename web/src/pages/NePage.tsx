import { useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  batchApplyHopManagedNe,
  connectTestManagedNe,
  createManagedNe,
  deleteManagedNe,
  fetchManagedNe,
  fetchManagedNeMeta,
  importManagedNe,
  managedNeImportTemplateUrl,
  updateManagedNe,
} from "../services/api";
import { HopProxyFields, emptyHopProxyFields, type HopProxyFieldsState } from "../components/HopProxyFields";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { ManagedNeItem } from "../types";
import { pageCount } from "../utils/display";
import { formatSystemTime } from "../utils/time";
import { isAutoHopTemplate, patchHopVendorChange, zteHopTemplate } from "../utils/hopProxy";
import type { HopVendor } from "../utils/hopProxy";

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
  hop_command_template: zteHopTemplate("ssh", ""),
  hop_vrf: "",
});

function applyHopTemplate(prev: FormState, protocol: string, vrf: string, force = false): Partial<FormState> {
  if (!force && !isAutoHopTemplate(prev.hop_command_template, prev.hop_protocol, prev.hop_vrf)) {
    return {};
  }
  return { hop_command_template: zteHopTemplate(protocol, vrf) };
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
  const [editing, setEditing] = useState<ManagedNeItem | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [batchHop, setBatchHop] = useState<HopProxyFieldsState>(emptyHopProxyFields);

  const metaQuery = useQuery({
    queryKey: queryKeys.managedNeMeta,
    queryFn: fetchManagedNeMeta,
    staleTime: 60_000,
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
      if (!form.password) throw new Error(t("managedNe.form.passwordRequired"));
      return createManagedNe({ ...body, password: form.password });
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
      }),
    onSuccess: async (res) => {
      setBatchHopOpen(false);
      setBatchHop(emptyHopProxyFields());
      showOk(t("managedNe.hop.batchDone", { n: res.updated }));
      await invalidateList();
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
      hop_vendor: (row.hop_vendor === "linux" ? "linux" : "zte") as HopVendor,
      hop_host: row.hop_host,
      hop_port: row.hop_port,
      hop_protocol: row.hop_protocol,
      hop_username: row.hop_username,
      hop_password: "",
      hop_command_template: isAutoHopTemplate(
        row.hop_command_template,
        row.hop_protocol,
        row.hop_vrf,
      )
        ? zteHopTemplate(row.hop_protocol, row.hop_vrf)
        : row.hop_command_template || zteHopTemplate(row.hop_protocol, row.hop_vrf),
      hop_vrf: row.hop_vrf,
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

  return (
    <div className="page-stack">
      {!credsOk ? (
        <section className="panel panel--warn">
          <p>{t("managedNe.credsNotConfigured")}</p>
        </section>
      ) : null}

      <section className="panel">
        <div className="panel__toolbar">
          <h2>{t("managedNe.title")}</h2>
          <div className="panel__actions">
            <button type="button" onClick={openCreate} disabled={!credsOk}>
              {t("managedNe.add")}
            </button>
            <button
              type="button"
              onClick={() => window.location.assign(managedNeImportTemplateUrl("xlsx"))}
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
              disabled={!credsOk || selected.length === 0 || batchHopMutation.isPending}
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
            <button type="button" onClick={() => invalidateList()}>
              {t("common.refresh")}
            </button>
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
                  {row.ip_address}:{row.port}/{row.protocol}
                  {row.hop_enabled ? (
                    <span
                      className="table-tag"
                      title={`${row.hop_host}:${row.hop_port} (${row.hop_vendor})`}
                    >
                      {t(`managedNe.hop.badge.${row.hop_vendor === "linux" ? "linux" : "zte"}`)}
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
                  <button type="button" className="link-btn" onClick={() => openEdit(row)}>
                    {t("managedNe.edit")}
                  </button>
                  <button
                    type="button"
                    className="link-btn"
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
                <FormLabel required={!editing}>
                  {t("managedNe.col.password")}
                  {editing ? (
                    <span className="form-label__optional"> ({t("managedNe.form.passwordOptional")})</span>
                  ) : null}
                </FormLabel>
                <input
                  type="password"
                  required={!editing}
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
                {t("managedNe.hop.enable")}
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
                  if (!batchHop.hop_password) {
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
    </div>
  );
}
