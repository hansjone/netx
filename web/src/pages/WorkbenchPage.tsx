import { useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useI18n } from "../i18n";
import { WorkbenchCardIcon } from "../components/WorkbenchCardIcon";
import { modulesInSection, type ModuleDefinition, type WorkbenchSection } from "../config/modules";
import { openOrFocusModule } from "../utils/moduleWindows";
import { useAuth } from "../auth/AuthContext";
import { fetchFabricSummary, fetchManagedNeStats, fetchOpsTasks, fetchRuntimeMetrics, fetchTopologyTree, type OpsTaskItem } from "../services/api";
import { formatSystemTime } from "../utils/time";
import { isUmeWorldContainer, isWorldDrillFolder } from "./topology/treeUtils";
import type { TopologyTreeFolderItem } from "../types";

const SECTIONS: WorkbenchSection[] = ["monitoring", "operations", "system"];

type DisplaySection = {
  key: "operations" | "system";
  mods: ModuleDefinition[];
};

function formatCount(n: number | string): string {
  if (typeof n === "string") return n;
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 10000) {
    return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  return String(n);
}

function formatAgeSec(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const n = Math.max(0, sec);
  if (n < 60) return `${Math.round(n)}s`;
  if (n < 3600) return `${Math.max(1, Math.round(n / 60))}m`;
  return `${(n / 3600).toFixed(n >= 10 ? 0 : 1)}h`;
}

function formatCompactTime(value: string | null | undefined): string {
  if (!value) return "—";
  const full = formatSystemTime(value);
  if (!full) return "—";
  // Keep date+time but drop seconds when present: "2026/9/12 15:42:01" → "9/12 15:42"
  const m = full.match(/(\d{1,4}[/-]\d{1,2}[/-]\d{1,2})\s+(\d{1,2}:\d{2})(?::\d{2})?/);
  if (m) {
    const date = m[1].replace(/^\d{4}[/-]/, "");
    return `${date} ${m[2]}`;
  }
  return full;
}

const FLEET_TONE_COLOR: Record<"ok" | "mid" | "warn" | "bad" | "muted", string> = {
  ok: "#22c55e",
  mid: "#38bdf8",
  warn: "#f59e0b",
  bad: "#ef4444",
  muted: "#64748b",
};

const REGION_PALETTE = [
  "#38bdf8",
  "#22d3ee",
  "#818cf8",
  "#34d399",
  "#fbbf24",
  "#fb7185",
  "#a78bfa",
  "#2dd4bf",
];

type FleetSlice = {
  key: string;
  n: number;
  label?: string;
  tone?: "ok" | "mid" | "warn" | "bad" | "muted";
  color?: string;
};

function sliceColor(p: FleetSlice): string {
  if (p.color) return p.color;
  if (p.tone) return FLEET_TONE_COLOR[p.tone];
  return FLEET_TONE_COLOR.muted;
}

function buildDonutGradient(parts: FleetSlice[]): string {
  const raw = parts.map((p) => Math.max(0, p.n));
  const total = raw.reduce((s, n) => s + n, 0);
  if (total <= 0) {
    return `conic-gradient(${FLEET_TONE_COLOR.muted} 0deg 360deg)`;
  }

  // Tiny non-zero slices get a minimum arc so they stay glanceable on skewed fleets.
  const MIN_SHARE = 0.022;
  const shares = raw.map((n) => {
    if (n <= 0) return 0;
    return Math.max(n / total, MIN_SHARE);
  });
  const shareSum = shares.reduce((s, n) => s + n, 0);
  let cursor = 0;
  const stops: string[] = [];
  parts.forEach((p, i) => {
    const share = shares[i] / shareSum;
    if (share <= 0) return;
    const start = cursor * 360;
    cursor += share;
    const end = cursor * 360;
    stops.push(`${sliceColor(p)} ${start.toFixed(2)}deg ${end.toFixed(2)}deg`);
  });
  return `conic-gradient(${stops.join(", ")})`;
}

function FleetDonut({
  parts,
  center,
  unit,
}: {
  parts: FleetSlice[];
  center: ReactNode;
  unit?: ReactNode;
}) {
  return (
    <div
      className="wb-fleet__donut"
      style={{ background: buildDonutGradient(parts) }}
      aria-hidden="true"
    >
      <div className="wb-fleet__donut-hole">
        <div className="wb-fleet__donut-value">{center}</div>
        {unit ? <div className="wb-fleet__donut-unit">{unit}</div> : null}
      </div>
    </div>
  );
}

const SCHED_DISC_COLORS = ["#38bdf8", "#22d3ee", "#818cf8", "#34d399"];

function polarPoint(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function donutSlicePath(
  cx: number,
  cy: number,
  rOuter: number,
  rInner: number,
  startDeg: number,
  endDeg: number,
): string {
  const sweep = endDeg - startDeg;
  const large = sweep > 180 ? 1 : 0;
  const [x1, y1] = polarPoint(cx, cy, rOuter, startDeg);
  const [x2, y2] = polarPoint(cx, cy, rOuter, endDeg);
  const [x3, y3] = polarPoint(cx, cy, rInner, endDeg);
  const [x4, y4] = polarPoint(cx, cy, rInner, startDeg);
  return [
    `M ${x1.toFixed(2)} ${y1.toFixed(2)}`,
    `A ${rOuter} ${rOuter} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`,
    `L ${x3.toFixed(2)} ${y3.toFixed(2)}`,
    `A ${rInner} ${rInner} 0 ${large} 0 ${x4.toFixed(2)} ${y4.toFixed(2)}`,
    "Z",
  ].join(" ");
}

type SchedDiscItem = {
  key: string;
  label: string;
  on: boolean;
  age: string;
  moduleId: string;
  path: string;
};

function SchedDartDisc({
  items,
  center,
  unit,
  onSelect,
}: {
  items: SchedDiscItem[];
  center: ReactNode;
  unit?: ReactNode;
  onSelect: (item: SchedDiscItem) => void;
}) {
  // Match FleetDonut footprint (84px) so the three FLEET cards share one visual rhythm.
  const size = 84;
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = 39;
  const rInner = 25;
  const gap = items.length > 1 ? 2.5 : 0;
  const slice = items.length > 0 ? 360 / items.length : 360;

  return (
    <div className="wb-fleet__donut wb-fleet__donut--dart">
      <svg
        className="wb-fleet__dart"
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="group"
        aria-label={typeof unit === "string" ? unit : "schedulers"}
      >
        {items.map((item, i) => {
          const start = i * slice + gap / 2;
          const end = (i + 1) * slice - gap / 2;
          const color = item.on
            ? SCHED_DISC_COLORS[i % SCHED_DISC_COLORS.length]
            : "#475569";
          return (
            <path
              key={item.key}
              className={`wb-fleet__dart-slice${item.on ? " is-on" : ""}`}
              d={donutSlicePath(cx, cy, rOuter, rInner, start, end)}
              fill={color}
              role="button"
              tabIndex={0}
              aria-label={item.label}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(item);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  e.stopPropagation();
                  onSelect(item);
                }
              }}
            >
              <title>{`${item.label} · ${item.on ? "ON" : "OFF"} · ${item.age}`}</title>
            </path>
          );
        })}
      </svg>
      <div className="wb-fleet__donut-hole wb-fleet__donut-hole--dart">
        <div className="wb-fleet__donut-value">{center}</div>
        {unit ? <div className="wb-fleet__donut-unit">{unit}</div> : null}
      </div>
    </div>
  );
}

function topLevelRegions(root: TopologyTreeFolderItem | null | undefined): TopologyTreeFolderItem[] {
  if (!root) return [];
  return (root.children || []).filter((c) => {
    if (String(c.kind) !== "region") return false;
    if (isUmeWorldContainer(c) || isWorldDrillFolder(c)) return false;
    return true;
  });
}

function clampPct(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function ratioPct(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return clampPct((used / limit) * 100);
}

function toneClass(pct: number, muted?: boolean): string {
  if (muted) return "is-muted";
  if (pct >= 75) return "is-high";
  if (pct >= 40) return "is-mid";
  return "is-ok";
}

function formatBytes(n: number): string {
  const v = Math.max(0, Number(n) || 0);
  const gib = 1024 ** 3;
  const mib = 1024 ** 2;
  if (v >= gib) {
    const g = v / gib;
    return `${g.toFixed(g >= 10 ? 0 : 1)} GiB`;
  }
  return `${(v / mib).toFixed(0)} MiB`;
}

function formatBytesCompact(n: number): string {
  const v = Math.max(0, Number(n) || 0);
  const gib = 1024 ** 3;
  const mib = 1024 ** 2;
  if (v >= gib) return `${(v / gib).toFixed(v >= 10 * gib ? 0 : 1)}G`;
  if (v >= mib) return `${(v / mib).toFixed(0)}M`;
  return `${Math.max(1, Math.round(v / 1024))}K`;
}

function isActiveStatus(status: string): boolean {
  const s = status.toLowerCase();
  return (
    s === "collecting" ||
    s === "running" ||
    s === "connecting" ||
    s === "testing" ||
    s === "paused" ||
    s === "pending" ||
    s === "detached"
  );
}

function kindLabel(kind: string, t: (k: string) => string): string {
  const key = `audit.tasks.kind.${kind}`;
  const tr = t(key);
  return tr === key ? kind : tr;
}

function statusLabel(status: string, t: (k: string) => string): string {
  const key = `audit.tasks.status.${status}`;
  const tr = t(key);
  return tr === key ? status : tr;
}

function statusTone(status: string): "running" | "ok" | "paused" | "stopped" | "other" {
  const s = status.toLowerCase();
  if (s === "collecting" || s === "running" || s === "connecting" || s === "testing") return "running";
  if (s === "ready" || s === "idle" || s === "succeeded" || s === "success") return "ok";
  if (s === "paused" || s === "pending" || s === "detached") return "paused";
  if (s === "failed" || s === "error" || s === "stopped") return "stopped";
  return "other";
}

function taskTitle(row: OpsTaskItem, t: (k: string) => string): string {
  const kind = kindLabel(row.kind, t);
  const subject = String(row.title || row.id || "").trim();
  if (!subject) return kind;
  if (subject === kind || subject.startsWith(`${kind} · `) || subject.startsWith(`${kind}·`)) {
    return subject;
  }
  return `${kind} · ${subject}`;
}

function openTaskHref(href: string) {
  const path = String(href || "").trim() || "/audit/tasks";
  if (path.startsWith("/ume")) {
    void openOrFocusModule({ moduleId: "ume", path });
    return;
  }
  if (
    path.startsWith("/network") ||
    path.startsWith("/lldp") ||
    path.startsWith("/config-sync") ||
    path.startsWith("/port-traffic")
  ) {
    void openOrFocusModule({ moduleId: "network", path });
    return;
  }
  if (path.startsWith("/ne")) {
    void openOrFocusModule({ moduleId: "ne", path });
    return;
  }
  if (path.startsWith("/webcrt")) {
    void openOrFocusModule({ moduleId: "webcrt", path });
    return;
  }
  void openOrFocusModule({
    moduleId: "audit",
    path: path.startsWith("/audit") ? path : "/audit/tasks",
  });
}

function StatLine({
  label,
  value,
  pct,
  muted,
}: {
  label: string;
  value: string;
  pct?: number;
  muted?: boolean;
}) {
  return (
    <div className={`wb-stat ${toneClass(pct ?? 0, muted)}`}>
      <span className="wb-stat__label">{label}</span>
      <span className="wb-stat__value">{value}</span>
      {pct == null ? null : (
        <span className="wb-stat__track" aria-hidden="true">
          <span className="wb-stat__fill" style={{ width: `${clampPct(pct)}%` }} />
        </span>
      )}
    </div>
  );
}

export function WorkbenchPage() {
  const { t } = useI18n();
  const { isAdmin, hasScope } = useAuth();

  const metricsQuery = useQuery({
    queryKey: ["runtimeMetrics"],
    queryFn: fetchRuntimeMetrics,
    staleTime: 10_000,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });

  const opsQuery = useQuery({
    queryKey: ["opsTasks"],
    queryFn: fetchOpsTasks,
    staleTime: 10_000,
    refetchInterval: (q) => ((q.state.data?.active ?? 0) > 0 ? 4000 : 20_000),
    refetchIntervalInBackground: false,
  });

  const neStatsQuery = useQuery({
    queryKey: ["managedNeStats"],
    queryFn: fetchManagedNeStats,
    staleTime: 30_000,
    refetchInterval: 45_000,
    refetchIntervalInBackground: false,
    enabled: isAdmin || hasScope("ne:read"),
  });

  const fabricQuery = useQuery({
    queryKey: ["fabricSummary"],
    queryFn: fetchFabricSummary,
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled: isAdmin || hasScope("ne:read"),
  });

  const topoTreeQuery = useQuery({
    queryKey: ["topologyTree"],
    queryFn: fetchTopologyTree,
    staleTime: 45_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled: isAdmin || hasScope("ne:read"),
  });

  const visibleBySection = useMemo(() => {
    const canSee = (mod: ModuleDefinition) =>
      !mod.workbenchHidden &&
      (!mod.adminOnly || isAdmin) &&
      (!mod.requiredScope || hasScope(mod.requiredScope) || isAdmin);

    return Object.fromEntries(
      SECTIONS.map((section) => [section, modulesInSection(section).filter(canSee)]),
    ) as Record<WorkbenchSection, ModuleDefinition[]>;
  }, [isAdmin, hasScope]);

  const displaySections = useMemo((): DisplaySection[] => {
    const ops = [...visibleBySection.monitoring, ...visibleBySection.operations];
    const system = visibleBySection.system;
    const sections: DisplaySection[] = [
      { key: "operations", mods: ops },
      { key: "system", mods: system },
    ];
    return sections.filter((s) => s.mods.length > 0);
  }, [visibleBySection]);

  const railTasks = useMemo(() => {
    const items = opsQuery.data?.items ?? [];
    const active = items.filter((row) => isActiveStatus(row.status));
    return (active.length ? active : items).slice(0, 8);
  }, [opsQuery.data]);

  const metrics = metricsQuery.data;
  const host = metrics?.host;
  const activeTasks = opsQuery.data?.active ?? 0;

  const cpuPct = clampPct(Number(host?.cpu_percent ?? 0));
  const memPct = clampPct(Number(host?.mem_percent ?? 0));
  const memUsed = Number(host?.mem_used_bytes || 0);
  const memTotal = Number(host?.mem_total_bytes || 0);

  const storage = metrics?.db_storage;
  const storageUsed = Number(storage?.used_bytes || 0);
  const storageOk =
    storageUsed > 0 || (storage?.source === "pg_database_size" && !storage?.error);

  const cliUsed = Number(metrics?.cli_budget?.in_use || 0);
  const cliLimit = Math.max(1, Number(metrics?.cli_budget?.limit || 0));
  const cliPct = ratioPct(cliUsed, cliLimit);

  const dbOut = Number(metrics?.db_pool?.checked_out || 0);
  const dbSize = Math.max(1, Number(metrics?.db_pool?.size || 0));
  const dbPct = ratioPct(dbOut, dbSize);

  const webUsed = Number(metrics?.webcrt?.active_sessions || 0);
  const webMax = Math.max(1, Number(metrics?.webcrt?.max_sessions || 20));
  const webPct = ratioPct(webUsed, webMax);

  const hostBusy = cpuPct >= 75 || memPct >= 75;
  const platformBusy = cliPct >= 75 || dbPct >= 75 || webPct >= 75;
  const schedulers = metrics?.device_schedulers;
  const schedConfigOn = Boolean(schedulers?.config_sync?.running ?? metrics?.config_sync?.running);
  const schedLldpOn = Boolean(schedulers?.lldp_collect?.running ?? metrics?.lldp_collect?.running);
  const schedPortOn = Boolean(schedulers?.port_traffic?.running ?? metrics?.port_traffic?.running);
  const schedNeOn = Boolean(schedulers?.ne_collect?.running);
  const schedStale = Boolean(schedulers?.stale);
  const schedMode = String(schedulers?.mode || "");
  const schedAge = typeof schedulers?.age_sec === "number" ? schedulers.age_sec : null;
  const healthBusy = hostBusy || platformBusy || schedStale;
  const canReadFleet = isAdmin || hasScope("ne:read");

  const neTotal = neStatsQuery.data?.total ?? 0;
  const neByStatus = neStatsQuery.data?.by_status ?? {};
  const nePass = Number(neByStatus.pass || 0);
  const neFail = Number(neByStatus.fail || 0);
  const neTesting = Number(neByStatus.testing || 0);
  const neUnknown = Number(neByStatus.unknown || 0);
  const neNoTag = Number(neStatsQuery.data?.no_tag_count || 0);
  const neKnownPct = neTotal > 0 ? clampPct(((neTotal - neUnknown) / neTotal) * 100) : 0;
  const neTopTags = Object.entries(neStatsQuery.data?.tag_counts ?? {})
    .filter(([tag, n]) => tag && tag !== "__no_tag__" && Number(n) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 2)
    .map(([tag, n]) => ({ tag, n: Number(n) }));
  const neValue = !canReadFleet
    ? "—"
    : neStatsQuery.isLoading
      ? "…"
      : neStatsQuery.isError
        ? "—"
        : neTotal;

  const fabricNodes = Number(fabricQuery.data?.node_count || 0);
  const fabricEdges = Number(fabricQuery.data?.edge_count || 0);
  const fabricDiscover = formatCompactTime(fabricQuery.data?.last_discover_at ?? null);
  const fabricUpdated = formatCompactTime(fabricQuery.data?.updated_at ?? null);

  const regionSlices = useMemo((): FleetSlice[] => {
    const regions = topLevelRegions(topoTreeQuery.data?.root)
      .map((r) => ({
        id: r.id,
        name: String(r.name || "").trim() || r.id,
        n: Number(r.ne_count || 0),
      }))
      .filter((r) => r.n > 0)
      .sort((a, b) => b.n - a.n);

    // Cap at 4 legend rows to match 网元 / 调度 denseness.
    const TOP_N = 3;
    const head = regions.slice(0, TOP_N);
    const rest = regions.slice(TOP_N);
    const restSum = rest.reduce((s, r) => s + r.n, 0);
    const assigned = regions.reduce((s, r) => s + r.n, 0);
    const unassigned = Math.max(0, fabricNodes - assigned);
    const other = restSum + unassigned;

    const slices: FleetSlice[] = head.map((r, i) => ({
      key: r.id,
      n: r.n,
      label: r.name,
      color: REGION_PALETTE[i % REGION_PALETTE.length],
    }));
    if (other > 0) {
      slices.push({
        key: unassigned > 0 && restSum === 0 ? "__none__" : "__other__",
        n: other,
        label: "",
        color: unassigned > 0 && restSum === 0 ? FLEET_TONE_COLOR.muted : "#94a3b8",
      });
    }
    return slices;
  }, [topoTreeQuery.data, fabricNodes]);

  const memValue =
    memTotal > 0 ? `${memPct}% · ${formatBytes(memUsed)}` : `${memPct}%`;
  const storageValue = storageOk ? formatBytesCompact(storageUsed) : "—";

  const dash = (v: number | string) => (canReadFleet ? v : "—");
  const tickAge = (sec?: number | null) => formatAgeSec(sec ?? null);

  const schedRows: SchedDiscItem[] = [
    {
      key: "config",
      on: schedConfigOn,
      label: t("workbench.fleetSchedConfig"),
      age: tickAge(schedulers?.config_sync?.last_tick_age_sec),
      moduleId: "network",
      path: "/network/tasks/config-sync",
    },
    {
      key: "lldp",
      on: schedLldpOn,
      label: t("workbench.fleetSchedLldp"),
      age: tickAge(schedulers?.lldp_collect?.last_tick_age_sec),
      moduleId: "network",
      path: "/network/topology/lldp",
    },
    {
      key: "port",
      on: schedPortOn,
      label: t("workbench.fleetSchedPort"),
      age: tickAge(schedulers?.port_traffic?.last_tick_age_sec),
      moduleId: "network",
      path: "/network/tasks/port-traffic",
    },
    {
      key: "ne",
      on: schedNeOn,
      label: t("workbench.fleetSchedNeCollect"),
      age: tickAge(schedulers?.ne_collect?.last_tick_age_sec),
      moduleId: "network",
      path: "/network/tasks/collect",
    },
  ];
  const schedOnCount = schedRows.filter((r) => r.on).length;

  return (
    <div className="workbench workbench--console">
      <header className="wb-head">
        <div className="wb-head__title-row">
          <h1 className="wb-head__title">{t("workbench.title")}</h1>
          <span className={`wb-head__health${healthBusy ? " is-busy" : " is-ok"}`}>
            <span className="wb-head__health-dot" aria-hidden="true" />
            {healthBusy
              ? t("workbench.kpi.healthWarn")
              : metrics
                ? t("workbench.kpi.healthOk")
                : t("workbench.kpi.healthUnknown")}
          </span>
        </div>
      </header>

      <section className="wb-strip wb-panel" aria-label={t("workbench.statusTitle")}>
        <div className="wb-strip__row">
          <span className="wb-strip__tag">{t("workbench.hostHealth")}</span>
          <StatLine label={t("workbench.gauge.cpu")} value={`${cpuPct}%`} pct={cpuPct} />
          <StatLine label={t("workbench.gauge.mem")} value={memValue} pct={memPct} />
          <StatLine
            label={t("workbench.gauge.storage")}
            value={storageValue}
            muted={!storageOk}
          />
          <span className="wb-strip__divider" aria-hidden="true" />
          <span className="wb-strip__tag wb-strip__tag--soft">{t("workbench.channels")}</span>
          <StatLine
            label={t("workbench.gauge.cli")}
            value={`${cliUsed}/${cliLimit}`}
            pct={cliPct}
          />
          <StatLine
            label={t("workbench.gauge.db")}
            value={`${dbOut}/${dbSize}`}
            pct={dbPct}
          />
          <StatLine
            label={t("workbench.gauge.webcrt")}
            value={`${webUsed}/${webMax}`}
            pct={webPct}
          />
        </div>
      </section>

      <div className="wb-body">
        <div className="wb-main">
          <section className="wb-modules wb-panel" aria-label={t("workbench.modules")}>
            {displaySections.map((section) => (
              <div key={section.key} className={`wb-section wb-section--${section.key}`}>
                <h2 className="wb-section__title">{t(`workbench.${section.key}`)}</h2>
                <div className="wb-tiles">
                  {section.mods.map((mod) => (
                    <Button
                      key={mod.moduleId}
                      variant="ghost"
                      className={`wb-tile wb-tile--${mod.iconTone}`}
                      aria-label={t("workbench.openModule")}
                      onPress={() => openOrFocusModule({ moduleId: mod.moduleId, path: mod.path })}
                    >
                      <WorkbenchCardIcon tone={mod.iconTone} kind={mod.iconKind} />
                      <span className="wb-tile__label">{t(mod.labelKey)}</span>
                    </Button>
                  ))}
                </div>
              </div>
            ))}
          </section>

          <section className="wb-fleet wb-panel" aria-label={t("workbench.fleet")}>
            <div className="wb-fleet__head">
              <h2 className="wb-fleet__title">{t("workbench.fleet")}</h2>
            </div>
            <div className="wb-fleet__grid">
              <button
                type="button"
                className="wb-fleet__card"
                disabled={!canReadFleet}
                onClick={() => openOrFocusModule({ moduleId: "ne", path: "/ne" })}
              >
                <div className="wb-fleet__card-top">
                  <div className="wb-fleet__card-label">{t("workbench.fleetNe")}</div>
                  <span className="wb-fleet__card-badge">
                    {canReadFleet ? `${t("workbench.fleetNeKnown")} ${neKnownPct}%` : "—"}
                  </span>
                </div>
                <div className="wb-fleet__viz">
                  <FleetDonut
                    parts={[
                      { key: "pass", n: canReadFleet ? nePass : 0, tone: "ok" },
                      { key: "fail", n: canReadFleet ? neFail : 0, tone: "bad" },
                      { key: "testing", n: canReadFleet ? neTesting : 0, tone: "mid" },
                      { key: "unknown", n: canReadFleet ? neUnknown : 0, tone: "muted" },
                    ]}
                    center={typeof neValue === "number" ? formatCount(neValue) : neValue}
                    unit={
                      canReadFleet && typeof neValue === "number"
                        ? t("workbench.fleetNeUnit")
                        : undefined
                    }
                  />
                  <div className="wb-fleet__rows">
                    <div className="wb-fleet__row">
                      <span className="wb-fleet__legend">
                        <i className="wb-fleet__swatch is-ok" aria-hidden="true" />
                        {t("workbench.fleetNePass")}
                      </span>
                      <strong className="is-ok">{dash(nePass)}</strong>
                    </div>
                    <div className="wb-fleet__row">
                      <span className="wb-fleet__legend">
                        <i className="wb-fleet__swatch is-bad" aria-hidden="true" />
                        {t("workbench.fleetNeFail")}
                      </span>
                      <strong className="is-bad">{dash(neFail)}</strong>
                    </div>
                    <div className="wb-fleet__row">
                      <span className="wb-fleet__legend">
                        <i className="wb-fleet__swatch is-mid" aria-hidden="true" />
                        {t("workbench.fleetNeTesting")}
                      </span>
                      <strong className="is-mid">{dash(neTesting)}</strong>
                    </div>
                    <div className="wb-fleet__row">
                      <span className="wb-fleet__legend">
                        <i className="wb-fleet__swatch is-muted" aria-hidden="true" />
                        {t("workbench.fleetNeUnknown")}
                      </span>
                      <strong className="is-muted">{dash(neUnknown)}</strong>
                    </div>
                  </div>
                </div>
                <div className="wb-fleet__foot">
                  <span>
                    {t("workbench.fleetNeTags")}{" "}
                    {canReadFleet && neTopTags.length > 0 ? (
                      neTopTags.map((x, i) => (
                        <em key={x.tag}>
                          {i > 0 ? " · " : ""}
                          {x.tag} {formatCount(x.n)}
                        </em>
                      ))
                    ) : (
                      <em>
                        {t("workbench.fleetNeNoTag")} {canReadFleet ? neNoTag : "—"}
                      </em>
                    )}
                  </span>
                </div>
              </button>

              <button
                type="button"
                className="wb-fleet__card"
                disabled={!canReadFleet}
                onClick={() => openOrFocusModule({ moduleId: "topology", path: "/topology" })}
              >
                <div className="wb-fleet__card-top">
                  <div className="wb-fleet__card-label">{t("workbench.fleetTopo")}</div>
                  <span className="wb-fleet__card-badge">
                    {canReadFleet
                      ? `${formatCount(fabricEdges)} ${t("workbench.fleetTopoEdges")}`
                      : "—"}
                  </span>
                </div>
                <div className="wb-fleet__viz">
                  <FleetDonut
                    parts={canReadFleet ? regionSlices : []}
                    center={
                      !canReadFleet
                        ? "—"
                        : fabricQuery.isLoading || topoTreeQuery.isLoading
                          ? "…"
                          : formatCount(fabricNodes)
                    }
                    unit={t("workbench.fleetTopoNodes")}
                  />
                  <div className="wb-fleet__rows">
                    {(canReadFleet ? regionSlices : []).length === 0 ? (
                      <div className="wb-fleet__row">
                        <span>{t("workbench.fleetTopoNoRegion")}</span>
                        <strong>{dash(formatCount(fabricNodes))}</strong>
                      </div>
                    ) : (
                      (canReadFleet ? regionSlices : []).map((s) => (
                        <div key={s.key} className="wb-fleet__row">
                          <span className="wb-fleet__legend">
                            <i
                              className="wb-fleet__swatch"
                              style={{ background: sliceColor(s), boxShadow: "none" }}
                              aria-hidden="true"
                            />
                            <span className="wb-fleet__legend-text">
                              {s.key === "__other__"
                                ? t("workbench.fleetTopoOther")
                                : s.key === "__none__"
                                  ? t("workbench.fleetTopoUnassigned")
                                  : s.label || s.key}
                            </span>
                          </span>
                          <strong>{formatCount(s.n)}</strong>
                        </div>
                      ))
                    )}
                  </div>
                </div>
                <div className="wb-fleet__foot">
                  <span>
                    {t("workbench.fleetTopoDiscover")}{" "}
                    <em>{canReadFleet ? fabricDiscover : "—"}</em>
                  </span>
                  <span>
                    {t("workbench.fleetTopoUpdated")}{" "}
                    <em>{canReadFleet ? fabricUpdated : "—"}</em>
                  </span>
                </div>
              </button>

              <div
                className="wb-fleet__card wb-fleet__card--sched"
                role="button"
                tabIndex={0}
                onClick={() =>
                  openOrFocusModule({
                    moduleId: "network",
                    path: "/network/tasks/port-traffic/wall",
                  })
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openOrFocusModule({
                      moduleId: "network",
                      path: "/network/tasks/port-traffic/wall",
                    });
                  }
                }}
              >
                <div className="wb-fleet__card-top">
                  <div className="wb-fleet__card-label">{t("workbench.fleetSched")}</div>
                  <span className={`wb-fleet__card-badge${schedStale ? " is-warn" : " is-ok"}`}>
                    {schedStale ? t("workbench.fleetSchedStale") : t("workbench.fleetSchedFresh")}
                  </span>
                </div>
                <div className="wb-fleet__viz">
                  <SchedDartDisc
                    items={schedRows}
                    center={`${schedOnCount}/${schedRows.length}`}
                    unit={t("workbench.fleetSchedOn")}
                    onSelect={(row) =>
                      openOrFocusModule({ moduleId: row.moduleId, path: row.path })
                    }
                  />
                  <div className="wb-fleet__rows">
                    {schedRows.map((row, i) => (
                      <button
                        key={row.key}
                        type="button"
                        className={`wb-fleet__row wb-fleet__row--sched${row.on ? " is-on" : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          openOrFocusModule({ moduleId: row.moduleId, path: row.path });
                        }}
                      >
                        <span className="wb-fleet__legend">
                          <i
                            className="wb-fleet__swatch"
                            style={{
                              background: row.on
                                ? SCHED_DISC_COLORS[i % SCHED_DISC_COLORS.length]
                                : "#475569",
                              boxShadow: "none",
                            }}
                            aria-hidden="true"
                          />
                          <span className="wb-fleet__legend-text">{row.label}</span>
                        </span>
                        <span className={`wb-fleet__sched-state${row.on ? " is-ok" : " is-muted"}`}>
                          {row.on ? "ON" : "OFF"}
                        </span>
                        <span className="wb-fleet__sched-age">{row.age}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="wb-fleet__foot">
                  <span>
                    {t("workbench.fleetSchedMode")} <em>{schedMode || "—"}</em>
                  </span>
                  <span>
                    {t("workbench.fleetSchedHeartbeat")}{" "}
                    <em className={schedStale ? "is-warn" : "is-ok"}>{formatAgeSec(schedAge)}</em>
                  </span>
                </div>
              </div>
            </div>
          </section>
        </div>

        <aside className="wb-side" aria-label={t("workbench.sidePanel")}>
          <section className="wb-rail wb-panel wb-rail--tasks">
            <div className="wb-rail__head">
              <h2 className="wb-rail__title">
                {t("workbench.taskRail")}
                <span className="wb-rail__count">{activeTasks}</span>
              </h2>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="wb-rail__all"
                onPress={() => openOrFocusModule({ moduleId: "audit", path: "/audit/tasks" })}
              >
                {t("workbench.taskRailAll")}
              </Button>
            </div>

            {railTasks.length === 0 ? (
              <p className="wb-rail__empty">{t("workbench.taskRailEmpty")}</p>
            ) : (
            <ul className="wb-rail__list">
              {railTasks.map((row) => (
                <li key={`${row.kind}:${row.id}`}>
                  <button
                    type="button"
                    className="wb-rail__item"
                    onClick={() => openTaskHref(row.href)}
                  >
                    <span className="wb-rail__item-title">{taskTitle(row, t)}</span>
                    <span className="wb-rail__item-meta">
                      <span className={`wb-rail__status wb-rail__status--${statusTone(row.status)}`}>
                        {statusLabel(row.status, t)}
                      </span>
                      {row.progress ? (
                        <span className="wb-rail__progress"> · {row.progress}</span>
                      ) : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
