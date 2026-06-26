import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiDelete,
  apiPatch,
  apiPost,
  fetchCliMeta,
  fetchCliProfiles,
  fetchManagedNeMeta,
  postUmeConnectTest,
} from "../services/api";
import { HopProxyFields, emptyHopProxyFields, type HopProxyFieldsState } from "./HopProxyFields";
import { HelpHint } from "./HelpHint";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { CliConnectProfileItem } from "../types";

type ProfileForm = {
  id: string;
  name: string;
  username: string;
  password: string;
  port: number;
  protocol: string;
  device_type_default: string;
  vendor_default: string;
  is_default: boolean;
  hop: HopProxyFieldsState;
};

const emptyForm = (): ProfileForm => ({
  id: "",
  name: "default",
  username: "",
  password: "",
  port: 22,
  protocol: "ssh",
  device_type_default: "zte_zxros",
  vendor_default: "ZTE",
  is_default: true,
  hop: { ...emptyHopProxyFields(), hop_vendor: "bastion", hop_port: 22 },
});

function profileToForm(row: CliConnectProfileItem): ProfileForm {
  return {
    id: row.id,
    name: row.name,
    username: row.username,
    password: "",
    port: row.port,
    protocol: row.protocol,
    device_type_default: row.device_type_default,
    vendor_default: row.vendor_default,
    is_default: row.is_default,
    hop: {
      hop_vendor: (row.hop_vendor || "bastion") as HopProxyFieldsState["hop_vendor"],
      hop_host: row.hop_host ?? "",
      hop_port: row.hop_port ?? 22,
      hop_protocol: row.hop_protocol ?? "ssh",
      hop_username: row.hop_username ?? "",
      hop_password: "",
      hop_command_template: row.hop_command_template ?? "",
      hop_vrf: row.hop_vrf ?? "",
      hop_target_auth_mode: (row.hop_target_auth_mode || "bastion_managed") as HopProxyFieldsState["hop_target_auth_mode"],
    },
  };
}

export function UmeCliConnectPanel() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [sampleUmeNeId, setSampleUmeNeId] = useState("");

  const metaQuery = useQuery({ queryKey: queryKeys.cliMeta, queryFn: fetchCliMeta });
  const neMetaQuery = useQuery({ queryKey: queryKeys.managedNeMeta, queryFn: fetchManagedNeMeta });
  const profilesQuery = useQuery({ queryKey: queryKeys.cliProfiles, queryFn: fetchCliProfiles });

  useEffect(() => {
    const items = profilesQuery.data?.items || [];
    const current = items.find((x) => x.id === form.id);
    const picked = current || items.find((x) => x.is_default) || items[0];
    if (picked && (!form.id || !current)) {
      setForm(profileToForm(picked));
    }
  }, [profilesQuery.data, form.id]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const body = {
        name: form.name.trim(),
        username: form.username.trim(),
        password: form.password || undefined,
        port: form.port,
        protocol: form.protocol,
        device_type_default: form.device_type_default,
        vendor_default: form.vendor_default,
        is_default: form.is_default,
        hop_enabled:
          form.hop.hop_vendor !== "linux" ? Boolean((form.hop.hop_host ?? "").trim()) : true,
        hop_vendor: form.hop.hop_vendor,
        hop_host: form.hop.hop_host,
        hop_port: form.hop.hop_port,
        hop_protocol: form.hop.hop_protocol,
        hop_username: form.hop.hop_username,
        hop_password: form.hop.hop_password || undefined,
        hop_command_template: form.hop.hop_command_template,
        hop_vrf: form.hop.hop_vrf,
        hop_target_auth_mode: form.hop.hop_target_auth_mode,
      };
      if (form.id) {
        return apiPatch<CliConnectProfileItem>(`/v1/cli/profiles/${form.id}`, body);
      }
      return apiPost<CliConnectProfileItem>("/v1/cli/profiles", { ...body, password: form.password });
    },
    onSuccess: async (row) => {
      setForm(profileToForm(row));
      await queryClient.invalidateQueries({ queryKey: queryKeys.cliProfiles });
      await queryClient.invalidateQueries({ queryKey: queryKeys.cliMeta });
      showOk(t("ume.cli.saved"));
    },
    onError: (e: Error) => showError(e.message),
  });

  const connectTestMutation = useMutation({
    mutationFn: async () => {
      const id = sampleUmeNeId.trim();
      if (!id) throw new Error(t("ume.cli.sampleNeRequired"));
      return postUmeConnectTest([id]);
    },
    onSuccess: () => {
      showOk(t("ume.cli.connectTestSubmitted"));
    },
    onError: (e: Error) => showError(e.message),
  });

  const deviceTypes = neMetaQuery.data?.device_types ?? [];
  const vendors = neMetaQuery.data?.vendors ?? [];
  const cliReady = Boolean(metaQuery.data?.cli_profile_ready);

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("ume.cli.title")}</h2>
        <HelpHint text={t("ume.cli.hint")} />
      </div>
      <p className="form-field-hint" style={{ marginBottom: 12 }}>
        {cliReady ? t("ume.cli.statusReady") : t("ume.cli.statusNotReady")}
      </p>
      <div className="form-grid" style={{ maxWidth: 960 }}>
        <label>
          <span className="form-label">{t("ume.cli.profileName")}</span>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label>
          <span className="form-label">{t("ume.cli.targetUsername")}</span>
          <input
            required
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            placeholder="ca-oper"
          />
        </label>
        <label>
          <span className="form-label">{t("ume.cli.targetPassword")}</span>
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            placeholder={form.id ? t("ume.cli.passwordKeep") : ""}
          />
        </label>
        <label>
          <span className="form-label">{t("managedNe.form.deviceType")}</span>
          <select
            value={form.device_type_default}
            onChange={(e) => setForm({ ...form, device_type_default: e.target.value })}
          >
            {deviceTypes.map((dt) => (
              <option key={dt} value={dt}>
                {dt}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="form-label">{t("managedNe.form.vendor")}</span>
          <select value={form.vendor_default} onChange={(e) => setForm({ ...form, vendor_default: e.target.value })}>
            {vendors.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      <HopProxyFields
        value={form.hop}
        onChange={(patch) => setForm((prev) => ({ ...prev, hop: { ...prev.hop, ...patch } }))}
        hopPasswordRequired={!form.id}
        hopPasswordOptional={Boolean(form.id)}
      />
      <div className="filter-inline" style={{ marginTop: 16 }}>
        <button type="button" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? t("managedNe.form.saving") : t("managedNe.form.save")}
        </button>
        <button
          type="button"
          onClick={() => {
            setForm(emptyForm());
          }}
        >
          {t("ume.cli.newProfile")}
        </button>
        {form.id ? (
          <button
            type="button"
            className="danger-btn"
            onClick={async () => {
              if (!window.confirm(t("ume.cli.deleteConfirm"))) return;
              try {
                await apiDelete(`/v1/cli/profiles/${form.id}`);
                setForm(emptyForm());
                await queryClient.invalidateQueries({ queryKey: queryKeys.cliProfiles });
                showOk(t("ume.cli.deleted"));
              } catch (e) {
                showError(e instanceof Error ? e.message : String(e));
              }
            }}
          >
            {t("managedNe.delete")}
          </button>
        ) : null}
      </div>
      <div className="panel" style={{ marginTop: 20 }}>
        <h3>{t("ume.cli.connectTestTitle")}</h3>
        <div className="filter-inline">
          <input
            value={sampleUmeNeId}
            onChange={(e) => setSampleUmeNeId(e.target.value)}
            placeholder={t("ume.cli.sampleNePh")}
            style={{ minWidth: 320 }}
          />
          <button type="button" onClick={() => connectTestMutation.mutate()} disabled={connectTestMutation.isPending}>
            {t("managedNe.connect.run")}
          </button>
        </div>
      </div>
      {(profilesQuery.data?.items || []).length > 1 ? (
        <div style={{ marginTop: 16 }}>
          <span className="form-label">{t("ume.cli.existingProfiles")}</span>
          <div className="filter-inline">
            {(profilesQuery.data?.items || []).map((p) => (
              <button key={p.id} type="button" className="link-btn" onClick={() => setForm(profileToForm(p))}>
                {p.name}
                {p.is_default ? ` (${t("ume.cli.default")})` : ""}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
