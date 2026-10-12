import { Alert, Button, Checkbox, Input, Label, Modal, TextField } from "@heroui/react";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createManagedNe, fetchManagedNeMeta, updateManagedNe } from "../../../services/api";
import { HopProxyFields } from "./HopProxyFields";
import { AppModalShell } from "../../../components/ui/AppModalShell";
import { FieldSelect } from "../../../components/ui/FieldSelect";
import { queryKeys } from "../../../constants/queryKeys";
import { useI18n } from "../../../i18n";
import { patchHopVendorChange } from "../lib/hopProxy";
import { managedNeErrorText } from "../lib/errors";
import type { ManagedNeItem } from "../../../types";
import {
  applyHopTemplate,
  buildManagedNeSaveBody,
  deviceTypeForVendor,
  isValidMgmtAddress,
  emptyManagedNeForm,
  formFromManagedNe,
  allowsOpenExecPolicy,
  type ManagedNeFormState,
} from "../lib/formState";

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
  // Port is edited as text so the field can be cleared while typing.
  const [portDraft, setPortDraft] = useState("22");
  const [submitted, setSubmitted] = useState(false);

  const metaQuery = useQuery({
    queryKey: queryKeys.managedNeMeta,
    queryFn: fetchManagedNeMeta,
    staleTime: 60_000,
    enabled: open,
  });


  const vendors = metaQuery.data?.vendors ?? [];
  const execPolicyEnabled = Boolean(metaQuery.data?.exec_policy_enabled);
  const deviceTypes = useMemo(() => {
    const base = metaQuery.data?.device_types ?? [];
    const cur = String(form.device_type || "").trim();
    if (cur && !base.includes(cur)) return [cur, ...base];
    return base;
  }, [metaQuery.data?.device_types, form.device_type]);

  const ipText = form.ip_address.trim();
  const portNum = /^\d+$/.test(portDraft.trim()) ? Number(portDraft.trim()) : NaN;
  const fieldErrors = {
    ip: !ipText ? t("managedNe.form.ipRequired") : !isValidMgmtAddress(ipText) ? t("managedNe.form.ipInvalid") : "",
    username: !form.username.trim() ? t("managedNe.form.usernameRequired") : "",
    port: portNum >= 1 && portNum <= 65535 ? "" : t("managedNe.form.portInvalid"),
  };
  const hasFieldErrors = Object.values(fieldErrors).some(Boolean);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const body = buildManagedNeSaveBody(form, {
        editing: Boolean(editing),
        hopHostRequired: t("managedNe.hop.hostRequired"),
        hopUserRequired: t("managedNe.hop.userRequired"),
        hopPasswordRequired: t("managedNe.hop.passwordRequired"),
        execPolicyEnabled: Boolean(metaQuery.data?.exec_policy_enabled),
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

  // Re-seed the form once each time the dialog opens (or switches row). Done
  // during render — React's pattern for resetting state when inputs change.
  const editingId = editing?.id ?? "";
  const seedKey = open ? `open:${editingId}` : "";
  const [seededKey, setSeededKey] = useState("");
  if (seedKey !== seededKey) {
    setSeededKey(seedKey);
    if (open) {
      const seed = editing ? formFromManagedNe(editing) : { ...emptyManagedNeForm(), ...(initialValues || {}) };
      setForm(seed);
      setPortDraft(String(seed.port));
      setSubmitted(false);
    }
  }
  // The mutation's error/result belongs to the previous opening.
  const resetSave = saveMutation.reset;
  useEffect(() => {
    if (open) resetSave();
  }, [open, editingId, resetSave]);

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
                const suggested = deviceTypeForVendor(vendor);
                // Follow the vendor while the type is a placeholder or still the old vendor's default.
                const followVendor = !dt || dt === deviceTypeForVendor(prev.vendor)
                  || ((dt === "generic" || dt === "other" || dt === "linux") && suggested !== "generic");
                if (followVendor) {
                  next.device_type = suggested;
                  if (!allowsOpenExecPolicy(suggested)) next.exec_policy = "readonly";
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
            onChange={(e) => {
              const device_type = e.target.value;
              setForm((prev) => ({
                ...prev,
                device_type,
                exec_policy: allowsOpenExecPolicy(device_type) ? prev.exec_policy : "readonly",
              }));
            }}
          >
            {deviceTypes.map((dt) => (
              <option key={dt} value={dt}>
                {dt}
              </option>
            ))}
          </FieldSelect>
          {execPolicyEnabled ? (
            <>
              <FieldSelect
                label={t("managedNe.col.execPolicy")}
                value={form.exec_policy}
                onChange={(e) =>
                  setForm({
                    ...form,
                    exec_policy: e.target.value as ManagedNeFormState["exec_policy"],
                  })
                }
              >
                <option value="readonly">{t("managedNe.execPolicy.readonly")}</option>
                {allowsOpenExecPolicy(form.device_type) ? (
                  <>
                    <option value="linux_shell">{t("managedNe.execPolicy.linuxShell")}</option>
                    <option value="unrestricted">{t("managedNe.execPolicy.unrestricted")}</option>
                  </>
                ) : null}
              </FieldSelect>
              <p className="form-field-hint form-grid__full">{t("managedNe.execPolicy.hint")}</p>
            </>
          ) : null}
          <TextField
            fullWidth
            isRequired
            isInvalid={submitted && Boolean(fieldErrors.ip)}
            value={form.ip_address}
            onChange={(ip_address) => setForm({ ...form, ip_address })}
          >
            <Label>{t("managedNe.col.ip")}</Label>
            <Input placeholder="192.0.2.10 / pe-01.example.net" />
            {submitted && fieldErrors.ip ? <span className="form-field-hint error-text">{fieldErrors.ip}</span> : null}
          </TextField>
          <TextField
            fullWidth
            isRequired
            inputMode="numeric"
            isInvalid={submitted && Boolean(fieldErrors.port)}
            value={portDraft}
            onChange={(value) => {
              setPortDraft(value);
              if (/^\d+$/.test(value.trim())) setForm((prev) => ({ ...prev, port: Number(value.trim()) }));
            }}
          >
            <Label>{t("managedNe.col.port")}</Label>
            <Input />
            {submitted && fieldErrors.port ? <span className="form-field-hint error-text">{fieldErrors.port}</span> : null}
          </TextField>
          <FieldSelect
            label={t("managedNe.col.protocol")}
            value={form.protocol}
            onChange={(e) => {
              const protocol = e.target.value;
              // Keep the well-known port in step with the protocol unless it was customised.
              const swapPort = protocol === "telnet" && portNum === 22 ? 23 : protocol === "ssh" && portNum === 23 ? 22 : 0;
              setForm((prev) => ({ ...prev, protocol, ...(swapPort ? { port: swapPort } : {}) }));
              if (swapPort) setPortDraft(String(swapPort));
            }}
          >
            <option value="ssh">ssh</option>
            <option value="telnet">telnet</option>
          </FieldSelect>
          <TextField
            fullWidth
            isRequired
            isInvalid={submitted && Boolean(fieldErrors.username)}
            value={form.username}
            onChange={(username) => setForm({ ...form, username })}
          >
            <Label>{t("managedNe.col.user")}</Label>
            <Input />
            {submitted && fieldErrors.username ? <span className="form-field-hint error-text">{fieldErrors.username}</span> : null}
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
              <Alert.Description>{managedNeErrorText(saveMutation.error, t)}</Alert.Description>
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
          onPress={() => {
            setSubmitted(true);
            if (hasFieldErrors) return;
            saveMutation.mutate();
          }}
        >
          {saveMutation.isPending ? t("managedNe.form.saving") : t("managedNe.form.save")}
        </Button>
      </Modal.Footer>
    </AppModalShell>
  );
}
