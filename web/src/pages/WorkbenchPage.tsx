import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useI18n } from "../i18n";
import { WorkbenchCardIcon } from "../components/WorkbenchCardIcon";
import { modulesInSection, type ModuleDefinition, type WorkbenchSection } from "../config/modules";
import { openOrFocusModule } from "../utils/moduleWindows";
import { useAuth } from "../auth/AuthContext";
import { fetchOpsTasks, fetchRuntimeMetrics } from "../services/api";

const SECTIONS: WorkbenchSection[] = ["monitoring", "operations", "system"];

function clampPct(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function ratioPct(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return clampPct((used / limit) * 100);
}

function loadHint(pct: number, t: (k: string) => string): string {
  if (pct >= 75) return t("workbench.gauge.high");
  if (pct >= 40) return t("workbench.gauge.normal");
  return t("workbench.gauge.low");
}

function gaugeTone(pct: number): string {
  if (pct >= 75) return "#f59e0b";
  if (pct >= 40) return "#3b82f6";
  return "#22c55e";
}

function formatBytes(n: number, locale: string): string {
  const v = Math.max(0, Number(n) || 0);
  const gib = 1024 ** 3;
  const mib = 1024 ** 2;
  if (v >= gib) {
    const g = v / gib;
    return locale.startsWith("zh") ? `${g.toFixed(1)} GiB` : `${g.toFixed(1)} GiB`;
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

function Gauge({
  label,
  hint,
  pct,
  valueText,
}: {
  label: string;
  hint: string;
  pct: number;
  valueText?: string;
}) {
  const tone = gaugeTone(pct);
  const safe = clampPct(pct);
  return (
    <div className="wb-gauge">
      <div className="wb-gauge__wrap">
        <div
          className="wb-gauge__ring"
          style={{
            background: `conic-gradient(${tone} ${safe}%, rgba(148, 163, 184, 0.16) 0)`,
          }}
        >
          <span className="wb-gauge__value">{valueText ?? `${safe}%`}</span>
        </div>
      </div>
      <div className="wb-gauge__label">{label}</div>
      <div className="wb-gauge__hint">{hint}</div>
    </div>
  );
}

export function WorkbenchPage() {
  const { t, locale } = useI18n();
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
    // Share AppLayout cache; speed up only while something is active.
    refetchInterval: (q) => ((q.state.data?.active ?? 0) > 0 ? 4000 : 20_000),
    refetchIntervalInBackground: false,
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

  const metrics = metricsQuery.data;
  const host = metrics?.host;
  const activeTasks = opsQuery.data?.active ?? 0;
  const totalTasks = opsQuery.data?.total ?? 0;

  const cpuPct = clampPct(Number(host?.cpu_percent ?? 0));
  const memPct = clampPct(Number(host?.mem_percent ?? 0));
  const memUsed = Number(host?.mem_used_bytes || 0);
  const memTotal = Number(host?.mem_total_bytes || 0);

  const storage = metrics?.db_storage;
  const storageUsed = Number(storage?.used_bytes || 0);
  const storageOk =
    storageUsed > 0 || (storage?.source === "pg_database_size" && !storage?.error);
  const storageHint = storageOk
    ? `${t("workbench.gauge.storageUsed")} · ${formatBytes(storageUsed, locale)}`
    : t("workbench.gauge.storageUnavailable");
  const storageValue = storageOk ? formatBytesCompact(storageUsed) : "—";

  const cliUsed = Number(metrics?.cli_budget?.in_use || 0);
  const cliLimit = Math.max(1, Number(metrics?.cli_budget?.limit || 0));
  const cliPct = ratioPct(cliUsed, cliLimit);

  const dbOut = Number(metrics?.db_pool?.checked_out || 0);
  const dbSize = Math.max(1, Number(metrics?.db_pool?.size || 0));
  const dbPct = ratioPct(dbOut, dbSize);

  const webUsed = Number(metrics?.webcrt?.active_sessions || 0);
  const webMax = Math.max(1, Number(metrics?.webcrt?.max_sessions || 20));
  const webPct = ratioPct(webUsed, webMax);

  const taskCap = Math.max(totalTasks, activeTasks, 8);
  const taskPct = ratioPct(activeTasks, taskCap);

  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const clockLocale = locale === "en" ? "en-US" : "zh-CN";
  const clockDate = useMemo(() => {
    try {
      return new Intl.DateTimeFormat(clockLocale, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        weekday: "short",
      }).format(now);
    } catch {
      return now.toLocaleDateString();
    }
  }, [now, clockLocale]);
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  const colonOn = now.getSeconds() % 2 === 0;

  return (
    <div className="workbench">
      <section className="wb-hero" aria-label={t("workbench.title")}>
        <div className="wb-hero__clock">
          <time className="wb-hero__clock-panel" dateTime={now.toISOString()}>
            <span className="wb-hero__clock-digits" aria-hidden="true">
              <span className="wb-hero__digit">{hh[0]}</span>
              <span className="wb-hero__digit">{hh[1]}</span>
              <span className={`wb-hero__colon${colonOn ? " is-on" : ""}`}>:</span>
              <span className="wb-hero__digit">{mm[0]}</span>
              <span className="wb-hero__digit">{mm[1]}</span>
              <span className={`wb-hero__colon${colonOn ? " is-on" : ""}`}>:</span>
              <span className="wb-hero__digit">{ss[0]}</span>
              <span className="wb-hero__digit">{ss[1]}</span>
            </span>
            <span className="wb-hero__clock-sr">
              {hh}:{mm}:{ss}
            </span>
            <span className="wb-hero__clock-date">{clockDate}</span>
          </time>
        </div>
        <div className="wb-hero__status" aria-label={t("workbench.statusTitle")}>
          <div className="wb-gauges wb-gauges--hero">
            <Gauge
              label={t("workbench.gauge.cpu")}
              hint={`${cpuPct}% · ${loadHint(cpuPct, t)}`}
              pct={cpuPct}
            />
            <Gauge
              label={t("workbench.gauge.mem")}
              hint={
                memTotal > 0
                  ? `${formatBytes(memUsed, locale)} / ${formatBytes(memTotal, locale)}`
                  : loadHint(memPct, t)
              }
              pct={memPct}
            />
            <Gauge
              label={t("workbench.gauge.storage")}
              hint={storageHint}
              pct={0}
              valueText={storageValue}
            />
            <Gauge
              label={t("workbench.gauge.cli")}
              hint={`${cliUsed}/${cliLimit || "—"} · ${loadHint(cliPct, t)}`}
              pct={cliPct}
            />
            <Gauge
              label={t("workbench.gauge.db")}
              hint={`${dbOut}/${dbSize || "—"} · ${loadHint(dbPct, t)}`}
              pct={dbPct}
            />
            <Gauge
              label={t("workbench.gauge.webcrt")}
              hint={`${webUsed}/${webMax} · ${loadHint(webPct, t)}`}
              pct={webPct}
            />
            <Gauge
              label={t("workbench.gauge.tasks")}
              hint={`${activeTasks}/${taskCap} · ${loadHint(taskPct, t)}`}
              pct={taskPct}
            />
          </div>
        </div>
      </section>

      {SECTIONS.map((section) => {
        const mods = visibleBySection[section];
        if (mods.length === 0) return null;
        return (
          <section key={section} className={`wb-section wb-section--${section}`}>
            <div className="wb-section__head">
              <h2 className="wb-section__title">{t(`workbench.${section}`)}</h2>
              <span className="wb-section__count">{mods.length}</span>
            </div>
            <div className="wb-grid">
              {mods.map((mod) => (
                <button
                  key={mod.moduleId}
                  type="button"
                  className={`wb-card wb-card--${mod.iconTone}`}
                  title={t("workbench.openModule")}
                  onClick={() => openOrFocusModule({ moduleId: mod.moduleId, path: mod.path })}
                >
                  <WorkbenchCardIcon tone={mod.iconTone} kind={mod.iconKind} />
                  <span className="wb-card__label">{t(mod.labelKey)}</span>
                </button>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
