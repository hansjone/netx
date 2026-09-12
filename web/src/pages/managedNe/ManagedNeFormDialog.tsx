import { Alert, Button, Checkbox, Input, Label, Modal, TextField } from "@heroui/react";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createManagedNe, fetchManagedNeMeta, updateManagedNe } from "../../services/api";
import { HopProxyFields } from "../../components/HopProxyFields";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
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

  return (
    <AppModalShell open={open} onClose={onClose} dismissible={!saveMutation.isPending} size="lg">
      <Modal.Header>
        <Modal.Heading>
          {editing ? t("managedNe.form.editTitle") : t("managedNe.form.createTitle")}
        </Modal.Heading>
        <Modal.CloseTrigger isDisabled={saveMutation.isPending} />
      </Modal.Header>
      <Modal.Body className="flex flex-col gap-3">
        <p className="text-sm text-muted">{t("managedNe.form.requiredHint")}</p>
        <div className="form-grid">
          <TextField
            fullWidth
            value={form.name}
            onChange={(name) => setForm({ ...form, name })}
          >
            <Label>{t("managedNe.col.name")}</Label>
            <Input />
            <span className="form-field-hint">{t("managedNe.form.nameConnectHint")}</span>
          </TextField>
          <FieldSelect
            label={t("managedNe.col.vendor")}
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
          </FieldSelect>
          <FieldSelect
            label={t("managedNe.col.deviceType")}
            required
            value={form.device_type}
            onChange={(e) => setForm({ ...form, device_type: e.target.value })}
          >
            {deviceTypes.map((dt) => (
              <option key={dt} value={dt}>
                {dt}
              </option>
            ))}
          </FieldSelect>
          <TextField
            fullWidth
            isRequired
            value={form.ip_address}
            onChange={(ip_address) => setForm({ ...form, ip_address })}
          >
            <Label>{t("managedNe.col.ip")}</Label>
            <Input />
          </TextField>
          <TextField
            fullWidth
            type="number"
            value={String(form.port)}
            onChange={(port) => setForm({ ...form, port: Number(port) || 22 })}
          >
            <Label>{t("managedNe.col.port")}</Label>
            <Input />
          </TextField>
          <FieldSelect
            label={t("managedNe.col.protocol")}
            value={form.protocol}
            onChange={(e) => setForm({ ...form, protocol: e.target.value })}
          >
            <option value="ssh">ssh</option>
            <option value="telnet">telnet</option>
          </FieldSelect>
          <TextField
            fullWidth
            isRequired
            value={form.username}
            onChange={(username) => setForm({ ...form, username })}
          >
            <Label>{t("managedNe.col.user")}</Label>
            <Input />
          </TextField>
          <TextField
            fullWidth
            type="password"
            value={form.password}
            onChange={(password) => setForm({ ...form, password })}
          >
            <Label>
              {t("managedNe.col.password")}
              <span className="form-label__optional"> ({t("managedNe.form.passwordOptional")})</span>
            </Label>
            <Input />
          </TextField>
          <TextField fullWidth value={form.tags} onChange={(tags) => setForm({ ...form, tags })}>
            <Label>{t("managedNe.col.tags")}</Label>
            <Input />
          </TextField>
          <TextField
            fullWidth
            className="form-grid__full"
            value={form.remark}
            onChange={(remark) => setForm({ ...form, remark })}
          >
            <Label>{t("managedNe.col.remark")}</Label>
            <Input />
          </TextField>
        </div>

        <fieldset className="form-fieldset form-grid__full">
          <legend>{t("managedNe.hop.sectionTitle")}</legend>
          <Checkbox
            isSelected={form.hop_enabled}
            onChange={(hop_enabled) => {
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
          >
            <Checkbox.Control>
              <Checkbox.Indicator />
            </Checkbox.Control>
            <Checkbox.Content>{t("managedNe.hop.enable")}</Checkbox.Content>
          </Checkbox>
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
        {saveMutation.isError ? (
          <Alert status="danger">
            <Alert.Content>
              <Alert.Description>{String(saveMutation.error)}</Alert.Description>
            </Alert.Content>
          </Alert>
        ) : null}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="tertiary" isDisabled={saveMutation.isPending} onPress={onClose}>
          {t("managedNe.form.cancel")}
        </Button>
        <Button
          variant="primary"
          isDisabled={saveMutation.isPending}
          onPress={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? t("managedNe.form.saving") : t("managedNe.form.save")}
        </Button>
      </Modal.Footer>
    </AppModalShell>
  );
}
