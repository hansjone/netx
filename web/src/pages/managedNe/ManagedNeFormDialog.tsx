import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createManagedNe, fetchManagedNeMeta, updateManagedNe } from "../../services/api";
import { HopProxyFields } from "../../components/HopProxyFields";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { patchHopVendorChange } from "../../utils/hopProxy";
import type { ManagedNeItem } from "../../types";
import {
  applyHopTemplate,
  buildManagedNeSaveBody,
  emptyManagedNeForm,
  formFromManagedNe,
  type ManagedNeFormState,
} from "./formState";

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

export type ManagedNeFormDialogProps = {
  open: boolean;
  /** null = create mode */
  editing: ManagedNeItem | null;
  /** Prefill when creating (e.g. topology deep-link fields). Ignored when editing. */
  initialValues?: Partial<ManagedNeFormState>;
  onClose: () => void;
  onSaved: (item: ManagedNeItem) => void;
};

export function ManagedNeFormDialog({
  open,
  editing,
  initialValues,
  onClose,
  onSaved,
}: ManagedNeFormDialogProps) {
  const { t } = useI18n();
  const [form, setForm] = useState<ManagedNeFormState>(emptyManagedNeForm);

  const metaQuery = useQuery({
    queryKey: queryKeys.managedNeMeta,
    queryFn: fetchManagedNeMeta,
    staleTime: 60_000,
    enabled: open,
  });

  // Reset only when the dialog opens or the edited row changes — avoid clobbering
  // in-progress edits if the parent re-renders with a new initialValues object identity.
  const editingId = editing?.id ?? "";
  useEffect(() => {
    if (!open) return;
    if (editing) {
      setForm(formFromManagedNe(editing));
      return;
    }
    setForm({ ...emptyManagedNeForm(), ...(initialValues || {}) });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- seed once per open/create
  }, [open, editingId]);

  const vendors = metaQuery.data?.vendors ?? [];
  const deviceTypes = useMemo(() => {
    const base = metaQuery.data?.device_types ?? [];
    const cur = String(form.device_type || "").trim();
    if (cur && !base.includes(cur)) return [cur, ...base];
    return base;
  }, [metaQuery.data?.device_types, form.device_type]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const body = buildManagedNeSaveBody(form, {
        editing: Boolean(editing),
        hopHostRequired: t("managedNe.hop.hostRequired"),
        hopUserRequired: t("managedNe.hop.userRequired"),
        hopPasswordRequired: t("managedNe.hop.passwordRequired"),
      });
      if (editing) {
        return updateManagedNe(editing.id, body);
      }
      return createManagedNe(body);
    },
    onSuccess: (item) => {
      onSaved(item);
    },
  });

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={() => {
        if (saveMutation.isPending) return;
        onClose();
      }}
    >
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
            <select
              required
              value={form.vendor}
              onChange={(e) => {
                const vendor = e.target.value;
                setForm((prev) => {
                  const next = { ...prev, vendor };
                  const dt = String(prev.device_type || "").trim().toLowerCase();
                  if (!dt || dt === "generic" || dt === "other" || dt === "linux") {
                    if (vendor === "ZTE") next.device_type = "zte_zxros";
                    else if (vendor === "Huawei") next.device_type = "huawei";
                    else if (vendor === "Cisco") next.device_type = "cisco_ios";
                    else if (vendor === "Juniper") next.device_type = "juniper_junos";
                    else if (vendor === "Nokia") next.device_type = "nokia_sros";
                  }
                  return next;
                });
              }}
            >
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
                hop_enter_system_view: form.hop_enter_system_view,
              }}
              onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
              hopPasswordRequired={!editing}
              hopPasswordOptional={Boolean(editing)}
            />
          ) : null}
        </fieldset>
        <div className="modal__actions">
          <button type="button" disabled={saveMutation.isPending} onClick={onClose}>
            {t("managedNe.form.cancel")}
          </button>
          <button
            type="button"
            disabled={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? t("managedNe.form.saving") : t("managedNe.form.save")}
          </button>
        </div>
        {saveMutation.isError ? (
          <p className="form-hint" role="alert">
            {String(saveMutation.error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
