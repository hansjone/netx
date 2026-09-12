import { Input, Label, TextField } from "@heroui/react";
import { useI18n } from "../i18n";
import { FieldSelect } from "./ui/FieldSelect";
import {
  HOP_VENDORS,
  defaultHopTemplate,
  expandBastionHopFields,
  isAutoHopTemplate,
  isBastionHopVendor,
  isLinuxHopVendor,
  patchHopVendorChange,
  type HopTargetAuthMode,
  type HopVendor,
} from "../utils/hopProxy";

export type HopProxyFieldsState = {
  hop_vendor: HopVendor;
  hop_host: string;
  hop_port: number;
  hop_protocol: string;
  hop_username: string;
  hop_password: string;
  hop_command_template: string;
  hop_vrf: string;
  hop_target_auth_mode: HopTargetAuthMode;
  hop_enter_system_view: boolean;
};

export const emptyHopProxyFields = (): HopProxyFieldsState => ({
  hop_vendor: "zte",
  hop_host: "",
  hop_port: 22,
  hop_protocol: "ssh",
  hop_username: "",
  hop_password: "",
  hop_command_template: defaultHopTemplate("zte", "ssh", ""),
  hop_vrf: "",
  hop_target_auth_mode: "bastion_managed",
  hop_enter_system_view: false,
});

function applyHopTemplate(
  prev: HopProxyFieldsState,
  protocol: string,
  vrf: string,
  force = false,
): Partial<HopProxyFieldsState> {
  if (!force && !isAutoHopTemplate(prev.hop_command_template, prev.hop_vendor, prev.hop_protocol, prev.hop_vrf)) {
    return {};
  }
  return { hop_command_template: defaultHopTemplate(prev.hop_vendor, protocol, vrf) };
}

function hopHintKey(vendor: string): string {
  const v = String(vendor || "").toLowerCase();
  if (v === "bastion") return "managedNe.hop.bastionHint";
  if (v === "linux") return "managedNe.hop.linuxHint";
  if (v === "huawei") return "managedNe.hop.huaweiHint";
  if (v === "cisco") return "managedNe.hop.ciscoHint";
  return "managedNe.hop.zteHint";
}

function templateHintKey(vendor: string): string {
  const v = String(vendor || "").toLowerCase();
  if (v === "bastion") return "managedNe.hop.templateHintBastion";
  if (v === "huawei") return "managedNe.hop.templateHintHuawei";
  if (v === "cisco") return "managedNe.hop.templateHintCisco";
  return "managedNe.hop.templateHint";
}

function vrfLabelKey(vendor: string): string {
  const v = String(vendor || "").toLowerCase();
  if (v === "huawei") return "managedNe.hop.vpnInstance";
  if (v === "cisco") return "managedNe.hop.vrfCisco";
  return "managedNe.hop.vrf";
}

type Props = {
  value: HopProxyFieldsState;
  onChange: (patch: Partial<HopProxyFieldsState>) => void;
  hopPasswordRequired?: boolean;
  hopPasswordOptional?: boolean;
};

export function HopProxyFields({
  value,
  onChange,
  hopPasswordRequired = true,
  hopPasswordOptional = false,
}: Props) {
  const { t } = useI18n();
  const linux = isLinuxHopVendor(value.hop_vendor);
  const bastion = isBastionHopVendor(value.hop_vendor);
  const huawei = value.hop_vendor === "huawei";
  const cliHop = !linux && !bastion;

  const set = (patch: Partial<HopProxyFieldsState>) => onChange(patch);

  return (
    <div className="form-grid">
      <FieldSelect
        fullWidth
        className="form-grid__full"
        label={t("managedNe.hop.type")}
        required
        value={value.hop_vendor}
        hint={t(hopHintKey(value.hop_vendor))}
        onChange={(e) => {
          const hop_vendor = e.target.value as HopVendor;
          set(patchHopVendorChange(hop_vendor, value));
        }}
      >
        {HOP_VENDORS.map((v) => (
          <option key={v} value={v}>
            {t(`managedNe.hop.vendor.${v}`)}
          </option>
        ))}
      </FieldSelect>
      <TextField
        fullWidth
        isRequired
        value={value.hop_host}
        onChange={(hop_host) => set({ hop_host })}
        onBlur={() => {
          if (!bastion) return;
          const raw = String(value.hop_host || "").trim();
          if (!raw.includes("@")) return;
          const parsed = expandBastionHopFields(raw, value.hop_username);
          if (!parsed.hop_host || parsed.hop_host === raw) return;
          set({
            hop_host: parsed.hop_host,
            hop_username: parsed.hop_username || value.hop_username,
          });
        }}
      >
        <Label>{t("managedNe.hop.host")}</Label>
        <Input placeholder={bastion ? t("managedNe.hop.hostPlaceholderBastion") : undefined} />
        {bastion ? <span className="form-field-hint">{t("managedNe.hop.hostHintBastion")}</span> : null}
      </TextField>
      <TextField
        fullWidth
        type="number"
        value={String(value.hop_port)}
        onChange={(hop_port) => set({ hop_port: Number(hop_port) || 22 })}
      >
        <Label>{t("managedNe.hop.port")}</Label>
        <Input />
      </TextField>
      {bastion ? (
        <FieldSelect
          fullWidth
          className="form-grid__full"
          label={t("managedNe.hop.targetAuthMode")}
          value={value.hop_target_auth_mode}
          hint={t("managedNe.hop.targetAuthHint")}
          onChange={(e) => set({ hop_target_auth_mode: e.target.value as HopTargetAuthMode })}
        >
          <option value="bastion_managed">{t("managedNe.hop.targetAuthBastionManaged")}</option>
          <option value="manual">{t("managedNe.hop.targetAuthManual")}</option>
        </FieldSelect>
      ) : null}
      {cliHop ? (
        <FieldSelect
          label={t("managedNe.hop.protocol")}
          value={value.hop_protocol}
          onChange={(e) => {
            const hop_protocol = e.target.value;
            set({ hop_protocol, ...applyHopTemplate(value, hop_protocol, value.hop_vrf) });
          }}
        >
          <option value="ssh">{huawei ? t("managedNe.hop.protocolSshStelnet") : "ssh"}</option>
          <option value="telnet">telnet</option>
        </FieldSelect>
      ) : null}
      <TextField
        fullWidth
        isRequired
        value={value.hop_username}
        onChange={(hop_username) => set({ hop_username })}
      >
        <Label>{t("managedNe.hop.username")}</Label>
        <Input />
      </TextField>
      <TextField
        fullWidth
        type="password"
        isRequired={hopPasswordRequired}
        value={value.hop_password}
        onChange={(hop_password) => set({ hop_password })}
      >
        <Label>
          {t("managedNe.hop.password")}
          {hopPasswordOptional ? (
            <span className="form-label__optional"> ({t("managedNe.form.passwordOptional")})</span>
          ) : null}
        </Label>
        <Input />
      </TextField>
      {bastion ? (
        <TextField
          fullWidth
          className="form-grid__full"
          value={value.hop_command_template}
          onChange={(hop_command_template) => set({ hop_command_template })}
        >
          <Label>{t("managedNe.hop.usernameTemplate")}</Label>
          <Input placeholder={defaultHopTemplate(value.hop_vendor, value.hop_protocol, value.hop_vrf)} />
          <span className="form-field-hint">{t(templateHintKey(value.hop_vendor))}</span>
        </TextField>
      ) : null}
      {cliHop ? (
        <>
          <TextField
            fullWidth
            value={value.hop_vrf}
            onChange={(hop_vrf) => {
              set({ hop_vrf, ...applyHopTemplate(value, value.hop_protocol, hop_vrf) });
            }}
          >
            <Label>{t(vrfLabelKey(value.hop_vendor))}</Label>
            <Input />
          </TextField>
          {huawei ? (
            <FieldSelect
              fullWidth
              className="form-grid__full"
              label={t("managedNe.hop.enterSystemView")}
              value={value.hop_enter_system_view ? "yes" : "no"}
              hint={t("managedNe.hop.enterSystemViewHint")}
              onChange={(e) => set({ hop_enter_system_view: e.target.value === "yes" })}
            >
              <option value="no">{t("managedNe.hop.enterSystemViewNo")}</option>
              <option value="yes">{t("managedNe.hop.enterSystemViewYes")}</option>
            </FieldSelect>
          ) : null}
          <TextField
            fullWidth
            className="form-grid__full"
            value={value.hop_command_template}
            onChange={(hop_command_template) => set({ hop_command_template })}
          >
            <Label>{t("managedNe.hop.commandTemplate")}</Label>
            <Input placeholder={defaultHopTemplate(value.hop_vendor, value.hop_protocol, value.hop_vrf)} />
            <span className="form-field-hint">{t(templateHintKey(value.hop_vendor))}</span>
          </TextField>
        </>
      ) : null}
    </div>
  );
}
