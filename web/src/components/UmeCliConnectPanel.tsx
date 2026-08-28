import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiDelete,
  apiPatch,
  apiPost,
  fetchCliMeta,
  fetchCliProfiles,
  fetchManagedNeMeta,
  fetchUmeCliOverride,
  postUmeConnectTest,
} from "../services/api";
import { HopProxyFields, emptyHopProxyFields, type HopProxyFieldsState } from "./HopProxyFields";
import { HelpHint } from "./HelpHint";
import { queryKeys } from "../constants/queryKeys";
import { useI18n } from "../i18n";
import { useToast } from "../hooks/useToast";
import type { CliConnectProfileItem, UmeCliOverrideItem } from "../types";
import { defaultHopTemplate, isAutoHopTemplate, patchHopVendorChange } from "../utils/hopProxy";
import { formatSystemTime } from "../utils/time";

function connectPillLevel(status: string): "up" | "down" | "unknown" | "warn" {
  const s = String(status || "").toLowerCase();
  if (s === "pass" || s === "ok") return "up";
  if (s === "fail" || s === "error") return "down";
  if (s === "testing") return "warn";
  return "unknown";
}

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
  hop_enabled: boolean;
  hop: HopProxyFieldsState;
};

function applyHopTemplate(hop: HopProxyFieldsState, protocol: string, vrf: string, force = false): Partial<HopProxyFieldsState> {
  if (!force && !isAutoHopTemplate(hop.hop_command_template, hop.hop_vendor, hop.hop_protocol, hop.hop_vrf)) {
    return {};
  }
  return { hop_command_template: defaultHopTemplate(hop.hop_vendor, protocol, vrf) };
}

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
  hop_enabled: false,
  hop: emptyHopProxyFields(),
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
    hop_enabled: row.hop_enabled,
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
      hop_enter_system_view: Boolean(row.hop_enter_system_view),
    },
  };
}

export function UmeCliConnectPanel({ enabled = true, embedded = false }: { enabled?: boolean; embedded?: boolean }) {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [sampleUmeNeId, setSampleUmeNeId] = useState("");
  const [testPending, setTestPending] = useState(false);

  const testNeId = sampleUmeNeId.trim();

  useEffect(() => {
    setTestPending(false);
  }, [testNeId]);

  const metaQuery = useQuery({ queryKey: queryKeys.cliMeta, queryFn: fetchCliMeta, enabled });
  const neMetaQuery = useQuery({ queryKey: queryKeys.managedNeMeta, queryFn: fetchManagedNeMeta, enabled });
  const profilesQuery = useQuery({ queryKey: queryKeys.cliProfiles, queryFn: fetchCliProfiles, enabled });
  const overrideQuery = useQuery({
    queryKey: queryKeys.umeCliOverride(testNeId),
    queryFn: () => fetchUmeCliOverride(testNeId),
    enabled: Boolean(testNeId),
    refetchInterval: (q) => {
      const status = String(q.state.data?.connect_status || "").toLowerCase();
      return status === "testing" || testPending ? 2000 : false;
    },
  });
  const overrideResult: UmeCliOverrideItem | null = overrideQuery.data ?? null;
  const overrideStatus = String(overrideResult?.connect_status || "").toLowerCase();
  const overrideTesting = overrideStatus === "testing" || (testPending && !overrideResult?.connect_tested_at);

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
      if (form.hop_enabled) {
        if (!form.hop.hop_host.trim()) throw new Error(t("managedNe.hop.hostRequired"));
        if (!form.hop.hop_username.trim()) throw new Error(t("managedNe.hop.userRequired"));
        if (!form.id && !form.hop.hop_password) throw new Error(t("managedNe.hop.passwordRequired"));
      }
      const body = {
        name: form.name.trim(),
        username: form.username.trim(),
        password: form.password || undefined,
        port: form.port,
        protocol: form.protocol,
        device_type_default: form.device_type_default,
        vendor_default: form.vendor_default,
        is_default: form.is_default,
        hop_enabled: form.hop_enabled,
        hop_vendor: form.hop.hop_vendor,
        hop_host: form.hop.hop_host,
        hop_port: form.hop.hop_port,
        hop_protocol: form.hop.hop_protocol,
        hop_username: form.hop.hop_username,
        hop_password: form.hop_enabled ? form.hop.hop_password || undefined : undefined,
        hop_command_template: form.hop.hop_command_template,
        hop_vrf: form.hop.hop_vrf,
        hop_target_auth_mode: form.hop.hop_target_auth_mode,
        hop_enter_system_view: form.hop.hop_enter_system_view,
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
    onSuccess: async () => {
      const id = sampleUmeNeId.trim();
      setTestPending(true);
      showOk(t("ume.cli.connectTestSubmitted"));
      if (id) {
        await queryClient.invalidateQueries({ queryKey: queryKeys.umeCliOverride(id) });
      }
    },
    onError: (e: Error) => {
      setTestPending(false);
      showError(e.message);
    },
  });

  useEffect(() => {
    if (!testPending) return;
    if (!overrideResult) return;
    if (overrideStatus === "testing") return;
    setTestPending(false);
    void queryClient.invalidateQueries({ queryKey: queryKeys.cliTargetsAll });
  }, [testPending, overrideResult, overrideStatus, queryClient]);

  const deviceTypes = neMetaQuery.data?.device_types ?? [];
  const vendors = neMetaQuery.data?.vendors ?? [];
  const cliReady = Boolean(metaQuery.data?.cli_profile_ready);

  const body = (
    <div className={embedded ? "cli-connect-panel" : undefined}>
      {!embedded ? (
        <div className="panel__toolbar">
          <h2>{t("ume.cli.title")}</h2>
          <HelpHint text={t("ume.cli.hint")} ariaLabel={t("common.help")} />
        </div>
      ) : null}
      <p className="form-field-hint" style={{ marginBottom: 12 }}>
        {cliReady ? t("ume.cli.statusReady") : t("ume.cli.statusNotReady")}
      </p>
      <div className="form-grid">
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
      <div className="form-fieldset" style={{ marginTop: 16 }}>
        <div className="form-fieldset__title">{t("managedNe.hop.sectionTitle")}</div>
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
                      hop: {
                        ...prev.hop,
                        ...patchHopVendorChange(prev.hop.hop_vendor, prev.hop),
                        ...applyHopTemplate(prev.hop, prev.hop.hop_protocol, prev.hop.hop_vrf, true),
                      },
                    }
                  : {}),
              }));
            }}
          />
          <span className="form-check__text">{t("managedNe.hop.enable")}</span>
        </label>
        {form.hop_enabled ? (
          <div className="hop-proxy-fields">
            <HopProxyFields
              value={form.hop}
              onChange={(patch) => setForm((prev) => ({ ...prev, hop: { ...prev.hop, ...patch } }))}
              hopPasswordRequired={!form.id}
              hopPasswordOptional={Boolean(form.id)}
            />
          </div>
        ) : null}
      </div>
      <div className="actions-row actions-row--inline" style={{ marginTop: 16 }}>
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
      <div className={embedded ? undefined : "panel"} style={{ marginTop: 20 }}>
        <h3 className={embedded ? "card__section-title" : undefined}>{t("ume.cli.connectTestTitle")}</h3>
        <div className="cli-connect-test-row">
          <input
            value={sampleUmeNeId}
            onChange={(e) => setSampleUmeNeId(e.target.value)}
            placeholder={t("ume.cli.sampleNePh")}
          />
          <button type="button" onClick={() => connectTestMutation.mutate()} disabled={connectTestMutation.isPending}>
            {connectTestMutation.isPending || overrideTesting
              ? t("managedNe.connect.running")
              : t("managedNe.connect.run")}
          </button>
        </div>
        {testNeId ? (
          <div style={{ marginTop: 12 }}>
            <div className="form-label">{t("ume.cli.connectTestResult")}</div>
            {overrideQuery.isLoading && !overrideResult ? (
              <p className="form-field-hint">{t("ume.cli.connectTestWaiting")}</p>
            ) : overrideTesting ? (
              <p className="form-field-hint">
                <span className="inline-spinner" aria-hidden /> {t("ume.cli.connectTestRunning")}
              </p>
            ) : overrideResult ? (
              <>
                <p style={{ marginTop: 8 }}>
                  <span className={`conn-pill conn-pill--${connectPillLevel(overrideResult.connect_status)}`}>
                    {overrideResult.connect_status}
                  </span>
                  {overrideResult.connect_message ? (
                    <span className="connect-detail-summary"> — {overrideResult.connect_message}</span>
                  ) : null}
                  {overrideResult.connect_tested_at ? (
                    <span className="muted" style={{ marginLeft: 8 }}>
                      {formatSystemTime(overrideResult.connect_tested_at, { assumeUtcNaive: true })}
                    </span>
                  ) : null}
                </p>
                <pre className="connect-log">
                  {overrideResult.connect_detail?.trim() ||
                    overrideResult.connect_message?.trim() ||
                    t("managedNe.connectDetailEmpty")}
                </pre>
              </>
            ) : testPending ? (
              <p className="form-field-hint">{t("ume.cli.connectTestWaiting")}</p>
            ) : (
              <p className="form-field-hint">{t("managedNe.connectDetailEmpty")}</p>
            )}
          </div>
        ) : null}
      </div>
      {(profilesQuery.data?.items || []).length > 1 ? (
        <div style={{ marginTop: 16 }}>
          <span className="form-label">{t("ume.cli.existingProfiles")}</span>
          <div className="actions-row actions-row--inline">
            {(profilesQuery.data?.items || []).map((p) => (
              <button key={p.id} type="button" className="link-btn" onClick={() => setForm(profileToForm(p))}>
                {p.name}
                {p.is_default ? ` (${t("ume.cli.default")})` : ""}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );

  if (embedded) return body;
  return <section className="panel">{body}</section>;
}
