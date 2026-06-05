import type { ReactNode } from "react";
import { useI18n } from "../i18n";
import {
  HOP_VENDORS,
  defaultHopTemplate,
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
});

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
      <label className="form-grid__full">
        <FormLabel required>{t("managedNe.hop.type")}</FormLabel>
        <select
          value={value.hop_vendor}
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
        </select>
        <span className="form-field-hint">{t(hopHintKey(value.hop_vendor))}</span>
      </label>
      <label>
        <FormLabel required>{t("managedNe.hop.host")}</FormLabel>
        <input required value={value.hop_host} onChange={(e) => set({ hop_host: e.target.value })} />
      </label>
      <label>
        <FormLabel>{t("managedNe.hop.port")}</FormLabel>
        <input
          type="number"
          value={value.hop_port}
          onChange={(e) => set({ hop_port: Number(e.target.value) || 22 })}
        />
      </label>
      {bastion ? (
        <label className="form-grid__full">
          <FormLabel>{t("managedNe.hop.targetAuthMode")}</FormLabel>
          <select
            value={value.hop_target_auth_mode}
            onChange={(e) => set({ hop_target_auth_mode: e.target.value as HopTargetAuthMode })}
          >
            <option value="bastion_managed">{t("managedNe.hop.targetAuthBastionManaged")}</option>
            <option value="manual">{t("managedNe.hop.targetAuthManual")}</option>
          </select>
          <span className="form-field-hint">{t("managedNe.hop.targetAuthHint")}</span>
        </label>
      ) : null}
      {cliHop ? (
        <label>
          <FormLabel>{t("managedNe.hop.protocol")}</FormLabel>
          <select
            value={value.hop_protocol}
            onChange={(e) => {
              const hop_protocol = e.target.value;
              set({ hop_protocol, ...applyHopTemplate(value, hop_protocol, value.hop_vrf) });
            }}
          >
            <option value="ssh">{huawei ? t("managedNe.hop.protocolSshStelnet") : "ssh"}</option>
            <option value="telnet">telnet</option>
          </select>
        </label>
      ) : null}
      <label>
        <FormLabel required>{t("managedNe.hop.username")}</FormLabel>
        <input required value={value.hop_username} onChange={(e) => set({ hop_username: e.target.value })} />
      </label>
      <label>
        <FormLabel required={hopPasswordRequired}>
          {t("managedNe.hop.password")}
          {hopPasswordOptional ? (
            <span className="form-label__optional"> ({t("managedNe.form.passwordOptional")})</span>
          ) : null}
        </FormLabel>
        <input
          type="password"
          required={hopPasswordRequired}
          value={value.hop_password}
          onChange={(e) => set({ hop_password: e.target.value })}
        />
      </label>
      {bastion ? (
        <label className="form-grid__full">
          <FormLabel>{t("managedNe.hop.usernameTemplate")}</FormLabel>
          <input
            value={value.hop_command_template}
            onChange={(e) => set({ hop_command_template: e.target.value })}
            placeholder={defaultHopTemplate(value.hop_vendor, value.hop_protocol, value.hop_vrf)}
          />
          <span className="form-field-hint">{t(templateHintKey(value.hop_vendor))}</span>
        </label>
      ) : null}
      {cliHop ? (
        <>
          <label>
            <FormLabel>{t(vrfLabelKey(value.hop_vendor))}</FormLabel>
            <input
              value={value.hop_vrf}
              onChange={(e) => {
                const hop_vrf = e.target.value;
                set({ hop_vrf, ...applyHopTemplate(value, value.hop_protocol, hop_vrf) });
              }}
            />
          </label>
          <label className="form-grid__full">
            <FormLabel>{t("managedNe.hop.commandTemplate")}</FormLabel>
            <input
              value={value.hop_command_template}
              onChange={(e) => set({ hop_command_template: e.target.value })}
              placeholder={defaultHopTemplate(value.hop_vendor, value.hop_protocol, value.hop_vrf)}
            />
            <span className="form-field-hint">{t(templateHintKey(value.hop_vendor))}</span>
          </label>
        </>
      ) : null}
    </div>
  );
}
