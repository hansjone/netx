/** Map backend runtime status codes (rt:* / ws:* / fwd:*) and legacy Chinese to i18n keys. */

type TFunc = (key: string) => string;

const LEGACY_ZH_RUNTIME_ERROR: Record<string, string> = {
  "启动：正在同步当前告警（完成后连接 WSS）…": "ume.tasks.runtimeError.startup_alarm_sync_before_ws",
  "启动 REST 全量同步未完成，定时 REST 与 WSS 均待命": "ume.tasks.runtimeError.startup_gate_waiting",
  "WSS 实时接收中，已跳过 REST 同步": "ume.tasks.runtimeError.wss_active_skip_rest",
  "正在拉取 UME 当前告警…": "ume.tasks.runtimeError.pulling_alarms_current",
  "另一条当前告警 REST 同步进行中，已跳过": "ume.tasks.runtimeError.alarms_sync_in_progress_skip",
  "正在拉取 UME 网元清单…": "ume.tasks.runtimeError.pulling_inventory",
  "未启用或未配置 UME_BASE_URL": "ume.tasks.runtimeError.ume_ws_disabled_no_base_url",
  "未启用或未配置 NETX_OCLAW_ALARM_WS / token / url": "ume.tasks.runtimeError.oclaw_fwd_disabled",
  "已恢复：将跳过本轮周期等待并尽快同步": "ume.tasks.runtimeError.resumed_sync_soon",
  "已恢复：将尽快重连 WSS": "ume.tasks.runtimeError.resumed_wss_reconnect",
  "已恢复：将尽快重连 OClaw WSS": "ume.tasks.runtimeError.resumed_oclaw_wss_reconnect",
  已恢复: "ume.tasks.runtimeError.resumed",
  初始化: "ume.tasks.wsState.init",
  已连接: "ume.tasks.wsState.connected",
  连接中: "ume.tasks.wsState.connecting",
  已断开: "ume.tasks.wsState.disconnected",
  无订阅: "ume.tasks.wsState.no_subscription",
  "等待 token": "ume.tasks.wsState.waiting_token",
  已暂停: "ume.tasks.wsState.paused",
  连接异常: "ume.tasks.wsState.error",
  重连等待: "ume.tasks.wsState.reconnecting",
  UME订阅已丢失: "ume.tasks.wsState.subscription_lost",
};

function translatePrefixed(code: string, prefix: string, i18nSection: string, t: TFunc): string | null {
  if (!code.startsWith(prefix)) return null;
  const slug = code.slice(prefix.length);
  if (!slug) return null;
  const key = `${i18nSection}.${slug}`;
  const tr = t(key);
  return tr !== key ? tr : null;
}

export function runtimeLastError(raw: string | null | undefined, t: TFunc): string {
  const s = String(raw ?? "").trim();
  if (!s) return "";

  const rt = translatePrefixed(s, "rt:", "ume.tasks.runtimeError", t);
  if (rt) return rt;
  const ws = translatePrefixed(s, "ws:", "ume.tasks.wsState", t);
  if (ws) return ws;
  const fwd = translatePrefixed(s, "fwd:", "ume.tasks.fwdState", t);
  if (fwd) return fwd;

  if (s === "keepalive_failed") {
    const tr = t("ume.tasks.runtimeError.keepalive_failed");
    if (tr !== "ume.tasks.runtimeError.keepalive_failed") return tr;
  }

  const legacyKey = LEGACY_ZH_RUNTIME_ERROR[s];
  if (legacyKey) {
    const tr = t(legacyKey);
    if (tr !== legacyKey) return tr;
  }

  return s;
}

export function wsConnectionLabel(
  state: string | null | undefined,
  label: string | null | undefined,
  t: TFunc,
): string {
  const fromLabel = runtimeLastError(label, t);
  if (fromLabel && fromLabel !== String(label ?? "").trim()) return fromLabel;
  const st = String(state ?? "").trim();
  if (st) {
    const tr = runtimeLastError(`ws:${st}`, t);
    if (tr) return tr;
  }
  return fromLabel || st || "";
}
