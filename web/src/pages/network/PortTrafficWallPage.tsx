import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  collectPortTrafficNow,
  fetchPortTrafficCompare,
  fetchPortTrafficDevices,
  fetchPortTrafficTargets,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import type { PortTrafficDevice, PortTrafficSamplePoint } from "../../types";
import { PortTrafficWall, type WallYMode } from "./PortTrafficWall";

const POLL_MS = 5000;
const EMPTY_COMPARE_POINTS: PortTrafficSamplePoint[] = [];

type BaselineMode = "off" | "shift" | "day" | "week" | "custom";

function deviceLabel(d: PortTrafficDevice) {
  return d.ne_name || d.ne_ip || d.ne_id || "—";
}

function FullscreenIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M7 14H5v5h5v-2H7v-3zm0-9h3V3H5v5h2V5zm12 9h-2v3h-3v2h5v-5zm-2-9V3h-3v2h3v3h2V5h-2z"
      />
    </svg>
  );
}

export function PortTrafficWallPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkAppliedRef = useRef(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const [wallDeviceId, setWallDeviceId] = useState(() => (searchParams.get("device_id") || "").trim());
  const [wallTargetId, setWallTargetId] = useState(() => (searchParams.get("target_id") || "").trim());
  const [mapBaselineDeviceId, setMapBaselineDeviceId] = useState("");
  const [mapBaselineTargetId, setMapBaselineTargetId] = useState("");
  const [rangeHours, setRangeHours] = useState(24);
  const [baseline, setBaseline] = useState<BaselineMode>("off");
  const [customOffsetHours, setCustomOffsetHours] = useState(48);
  const [yMode, setYMode] = useState<WallYMode>("auto");

  const devicesQuery = useQuery({
    queryKey: queryKeys.portTrafficDevices(0),
    queryFn: () => fetchPortTrafficDevices({ page: 1, pageSize: 100 }),
    staleTime: 5000,
    refetchInterval: POLL_MS,
  });

  const wallTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(wallDeviceId),
    queryFn: () => fetchPortTrafficTargets(wallDeviceId),
    enabled: Boolean(wallDeviceId),
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const mapBaselineTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(mapBaselineDeviceId),
    queryFn: () => fetchPortTrafficTargets(mapBaselineDeviceId),
    enabled: Boolean(mapBaselineDeviceId),
    staleTime: 2000,
    refetchInterval: POLL_MS,
  });

  const compareQuery = useQuery({
    queryKey: queryKeys.portTrafficCompare(
      wallTargetId,
      rangeHours,
      baseline,
      baseline === "custom" ? customOffsetHours : 0,
      mapBaselineTargetId,
    ),
    queryFn: () =>
      fetchPortTrafficCompare({
        targetId: wallTargetId,
        rangeHours,
        baseline,
        offsetHours: baseline === "custom" ? customOffsetHours : undefined,
        baselineTargetId: mapBaselineTargetId || undefined,
      }),
    enabled: Boolean(wallTargetId),
    staleTime: 1000,
    placeholderData: keepPreviousData,
    refetchInterval: (q) => {
      const n = q.state.data?.current?.length ?? 0;
      return n === 0 ? 2500 : POLL_MS;
    },
  });

  const devices = devicesQuery.data?.items || [];
  const wallTargets = useMemo(
    () => (wallTargetsQuery.data?.items || []).filter((x) => x.status === "active"),
    [wallTargetsQuery.data?.items],
  );
  const selectedWallTarget = wallTargets.find((x) => x.id === wallTargetId) || null;
  const mapBaselineOptions = useMemo(
    () =>
      (mapBaselineTargetsQuery.data?.items || []).filter(
        (x) => x.status === "active" && x.id !== wallTargetId,
      ),
    [mapBaselineTargetsQuery.data?.items, wallTargetId],
  );

  const wallPoints = compareQuery.data?.current ?? EMPTY_COMPARE_POINTS;
  const wallBaselinePoints =
    baseline === "off" && !mapBaselineTargetId
      ? EMPTY_COMPARE_POINTS
      : (compareQuery.data?.baseline ?? EMPTY_COMPARE_POINTS);

  useEffect(() => {
    if (deepLinkAppliedRef.current) return;
    const did = (searchParams.get("device_id") || "").trim();
    const tid = (searchParams.get("target_id") || "").trim();
    deepLinkAppliedRef.current = true;
    if (did) setWallDeviceId(did);
    if (tid) setWallTargetId(tid);
    if (did || tid) setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    if (wallDeviceId || !devices.length) return;
    setWallDeviceId(devices[0].id);
  }, [wallDeviceId, devices]);

  useEffect(() => {
    if (!wallDeviceId || wallTargetId || !wallTargets.length) return;
    setWallTargetId(wallTargets[0].id);
  }, [wallDeviceId, wallTargetId, wallTargets]);

  useEffect(() => {
    if (!mapBaselineTargetId) return;
    if (mapBaselineTargetId === wallTargetId) {
      setMapBaselineTargetId("");
      return;
    }
    if (
      mapBaselineDeviceId &&
      mapBaselineTargetsQuery.isFetched &&
      !mapBaselineOptions.some((x) => x.id === mapBaselineTargetId)
    ) {
      setMapBaselineTargetId("");
    }
  }, [
    mapBaselineTargetId,
    wallTargetId,
    mapBaselineDeviceId,
    mapBaselineOptions,
    mapBaselineTargetsQuery.isFetched,
  ]);

  useEffect(() => {
    const syncFs = () => {
      const el = stageRef.current;
      setFullscreen(Boolean(el && document.fullscreenElement === el));
      window.setTimeout(() => window.dispatchEvent(new Event("resize")), 80);
    };
    document.addEventListener("fullscreenchange", syncFs);
    return () => document.removeEventListener("fullscreenchange", syncFs);
  }, []);

  const toggleFullscreen = async () => {
    const el = stageRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement === el) {
        await document.exitFullscreen();
      } else {
        await el.requestFullscreen();
      }
    } catch (err) {
      showError(String(err));
    }
  };

  const collectMut = useMutation({
    mutationFn: collectPortTrafficNow,
    onSuccess: (res) => {
      showOk(res.started ? t("portTraffic.collectStarted") : t("portTraffic.collectBusy"));
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficDevicesAll });
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficTargets(wallDeviceId) });
      void queryClient.invalidateQueries({ queryKey: ["portTrafficCompare"] });
    },
    onError: (e: Error) => showError(e.message),
  });

  return (
    <section className="pt-wall-shell">
      {!devices.length && !devicesQuery.isLoading ? (
        <div className="pt-list-empty pt-wall-shell__empty">
          <p>{t("portTraffic.wallEmpty")}</p>
          <Link to="/network/tasks/port-traffic" className="btn-primary" style={{ textDecoration: "none" }}>
            {t("portTraffic.create")}
          </Link>
        </div>
      ) : (
        <div className="pt-wall-page">
          <div className="pt-wall-page__toolbar">
            <div className="pt-wall-page__row pt-wall-page__row--primary">
              <label className="pt-wall-page__field pt-wall-page__field--task">
                {t("portTraffic.wallDevice")}
                <select
                  value={wallDeviceId}
                  onChange={(e) => {
                    setWallDeviceId(e.target.value);
                    setWallTargetId("");
                  }}
                >
                  {devices.map((d) => (
                    <option key={d.id} value={d.id}>
                      {deviceLabel(d)}
                      {d.ne_ip ? ` (${d.ne_ip})` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="pt-wall-page__field pt-wall-page__field--port">
                {t("portTraffic.wallPort")}
                <select value={wallTargetId} onChange={(e) => setWallTargetId(e.target.value)}>
                  <option value="">{t("portTraffic.pickPort")}</option>
                  {wallTargets.map((tgt) => (
                    <option key={tgt.id} value={tgt.id}>
                      {tgt.ifname}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn-primary pt-wall-page__collect"
                disabled={!wallDeviceId || collectMut.isPending}
                onClick={() => collectMut.mutate(wallDeviceId)}
              >
                {t("portTraffic.collectNow")}
              </button>
              <button
                type="button"
                className="pt-wall-page__fs-btn pt-wall-page__fs-btn--toolbar"
                onClick={() => void toggleFullscreen()}
                title={t("portTraffic.fullscreen")}
                aria-label={t("portTraffic.fullscreen")}
              >
                <FullscreenIcon />
                <span>{t("portTraffic.fullscreen")}</span>
              </button>
            </div>

            <div className="pt-wall-page__row pt-wall-page__row--secondary">
              <div className="pt-wall-page__cluster">
                <span className="pt-wall-page__cluster-label">{t("portTraffic.filterView")}</span>
                <div className="pt-wall-page__cluster-fields">
                  <label className="pt-wall-page__field pt-wall-page__field--sm">
                    {t("portTraffic.range")}
                    <select value={rangeHours} onChange={(e) => setRangeHours(Number(e.target.value))}>
                      <option value={1}>1h</option>
                      <option value={6}>6h</option>
                      <option value={24}>24h</option>
                    </select>
                  </label>
                  <label className="pt-wall-page__field pt-wall-page__field--md">
                    {t("portTraffic.compare")}
                    <select value={baseline} onChange={(e) => setBaseline(e.target.value as BaselineMode)}>
                      <option value="off">{t("portTraffic.compareOff")}</option>
                      <option value="day">{t("portTraffic.compareDay")}</option>
                      <option value="week">{t("portTraffic.compareWeek")}</option>
                      <option value="shift">{t("portTraffic.compareShift")}</option>
                      <option value="custom">{t("portTraffic.compareCustom")}</option>
                    </select>
                  </label>
                  {baseline === "custom" ? (
                    <label className="pt-wall-page__field pt-wall-page__field--sm">
                      {t("portTraffic.offsetHours")}
                      <input
                        type="number"
                        min={1}
                        max={24 * 90}
                        value={customOffsetHours}
                        onChange={(e) => setCustomOffsetHours(Number(e.target.value) || 24)}
                      />
                    </label>
                  ) : null}
                  <label className="pt-wall-page__field pt-wall-page__field--md">
                    {t("portTraffic.yMode")}
                    <select value={yMode} onChange={(e) => setYMode(e.target.value as WallYMode)}>
                      <option value="auto">{t("portTraffic.yModeAuto")}</option>
                      <option value="current">{t("portTraffic.yModeCurrent")}</option>
                      <option value="util">{t("portTraffic.yModeUtil")}</option>
                    </select>
                  </label>
                </div>
              </div>

              <div className="pt-wall-page__cluster-sep" aria-hidden />

              <div className="pt-wall-page__cluster pt-wall-page__cluster--map">
                <span className="pt-wall-page__cluster-label">{t("portTraffic.filterMap")}</span>
                <div className="pt-wall-page__cluster-fields">
                  <label className="pt-wall-page__field pt-wall-page__field--task">
                    {t("portTraffic.wallDevice")}
                    <select
                      value={mapBaselineDeviceId}
                      onChange={(e) => {
                        setMapBaselineDeviceId(e.target.value);
                        setMapBaselineTargetId("");
                      }}
                      disabled={!wallTargetId}
                    >
                      <option value="">{t("portTraffic.mapBaselineNone")}</option>
                      {devices.map((d) => (
                        <option key={d.id} value={d.id}>
                          {deviceLabel(d)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="pt-wall-page__field pt-wall-page__field--port">
                    {t("portTraffic.wallPort")}
                    <select
                      value={mapBaselineTargetId}
                      onChange={(e) => setMapBaselineTargetId(e.target.value)}
                      disabled={!mapBaselineDeviceId}
                    >
                      <option value="">{t("portTraffic.pickPort")}</option>
                      {mapBaselineOptions.map((tgt) => (
                        <option key={tgt.id} value={tgt.id}>
                          {tgt.ifname}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
            </div>
          </div>

          {baseline === "week" ? (
            <p className="muted pt-wall-page__hint">{t("portTraffic.retentionHint")}</p>
          ) : null}

          <div
            ref={stageRef}
            className={`pt-wall-page__stage${fullscreen ? " is-fullscreen" : ""}`}
          >
            <PortTrafficWall
              target={selectedWallTarget}
              baselineTarget={
                compareQuery.data?.meta?.baseline_target ||
                (baseline !== "off" && wallBaselinePoints.length ? selectedWallTarget : null)
              }
              points={wallPoints}
              baselinePoints={wallBaselinePoints}
              yMode={yMode}
              rangeLabel={`${rangeHours}h${
                baseline === "off"
                  ? ""
                  : baseline === "day"
                    ? ` · ${t("portTraffic.compareDay")}`
                    : baseline === "week"
                      ? ` · ${t("portTraffic.compareWeek")}`
                      : baseline === "shift"
                        ? ` · ${t("portTraffic.compareShift")}`
                        : ` · ${t("portTraffic.compareCustom")}`
              }${mapBaselineTargetId ? ` · ${t("portTraffic.mapBaselinePort")}` : ""}`}
              loading={compareQuery.isLoading || compareQuery.isFetching}
              hint={
                !wallTargetId
                  ? t("portTraffic.pickPort")
                  : selectedWallTarget?.last_error
                    ? t("portTraffic.waitAfterError")
                    : t("portTraffic.waitSamples")
              }
            />
          </div>
        </div>
      )}
    </section>
  );
}
