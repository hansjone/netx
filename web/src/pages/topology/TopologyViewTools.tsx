import { useQueryClient } from "@tanstack/react-query";
import type { RefObject } from "react";
import type { Node } from "@xyflow/react";
import { createPortal } from "react-dom";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import { updateLldpCollectPolicy } from "../../services/api";
import {
  DEFAULT_CANVAS_BG,
  DISCOVER_AUTO_ADD_KEY,
  DISCOVER_PROJECT_NEIGHBORS_KEY,
  SCALE_BUNDLE_WIDTH_KEY,
  SEP,
  SHOW_PLACEHOLDER_BADGE_KEY,
} from "./constants";
import {
  DEFAULT_LABEL_COLORS,
  DEFAULT_VENDOR_COLORS,
  VENDOR_TONE_KEYS,
  isHexColor,
  loadCanvasBg,
  loadLabelColors,
  loadVendorColors,
  persistAutoLayoutAfterDiscover,
  persistBoolFlag,
  persistCanvasBg,
  persistLabelColors,
  persistVendorColors,
  type LabelColors,
  type VendorColors,
} from "./displayPrefs";
import type { EdgeDefaultStyle, EdgeDefaults, EdgeLineStyle, EdgeSourceKind } from "./edgeStyle";
import type { alignNodes } from "./layoutGraph";
import type { NeNodeData } from "./TopologyReactFlowView";

export type AlignKind = Parameters<typeof alignNodes>[2];

export type TopologyViewToolsProps = {
  fullscreen: boolean;
  toolbarSlot: HTMLDivElement | null;
  displayMenuRef: RefObject<HTMLDetailsElement | null>;
  findBoxRef: RefObject<HTMLDivElement | null>;
  hideIp: boolean;
  onHideIpChange: (v: boolean) => void;
  hideVendor: boolean;
  onHideVendorChange: (v: boolean) => void;
  showPlaceholderBadge: boolean;
  onShowPlaceholderBadgeChange: (v: boolean) => void;
  hidePorts: boolean;
  onHidePortsChange: (v: boolean) => void;
  expandPhysicalLinks: boolean;
  onExpandPhysicalLinksChange: (v: boolean) => void;
  scaleBundleWidth: boolean;
  onScaleBundleWidthChange: (v: boolean) => void;
  edgeFlow: boolean;
  onEdgeFlowChange: (v: boolean) => void;
  snapToGrid: boolean;
  onSnapToGridChange: (v: boolean) => void;
  autoLayoutAfterDiscover: boolean;
  onAutoLayoutAfterDiscoverChange: (v: boolean) => void;
  discoverAutoAddUnmatched: boolean;
  onDiscoverAutoAddUnmatchedChange: (v: boolean) => void;
  discoverProjectNeighbors: boolean;
  onDiscoverProjectNeighborsChange: (v: boolean) => void;
  canvasBg: string;
  onCanvasBgChange: (v: string) => void;
  labelColors: LabelColors;
  onLabelColorsChange: (v: LabelColors) => void;
  vendorColors: VendorColors;
  onVendorColorsChange: (v: VendorColors) => void;
  edgeDefaults: EdgeDefaults;
  onUpdateEdgeDefault: (kind: EdgeSourceKind, patch: Partial<EdgeDefaultStyle>) => void;
  onResetEdgeDefaults: () => void;
  selectedNodeCount: number;
  onAlign: (kind: AlignKind) => void;
  mapId: string;
  nodeCount: number;
  canvasQuery: string;
  onCanvasQueryChange: (v: string) => void;
  findOpen: boolean;
  onFindOpenChange: (v: boolean) => void;
  findActiveIdx: number;
  onFindActiveIdxChange: (v: number | ((i: number) => number)) => void;
  canvasHits: Node<NeNodeData>[];
  onFindOnCanvas: (nodeId?: string) => void;
};

export function TopologyViewTools(props: TopologyViewToolsProps) {
  const { t } = useI18n();
  const { showError } = useToast();
  const queryClient = useQueryClient();
  const {
    fullscreen,
    toolbarSlot,
    displayMenuRef,
    findBoxRef,
    hideIp,
    onHideIpChange,
    hideVendor,
    onHideVendorChange,
    showPlaceholderBadge,
    onShowPlaceholderBadgeChange,
    hidePorts,
    onHidePortsChange,
    expandPhysicalLinks,
    onExpandPhysicalLinksChange,
    scaleBundleWidth,
    onScaleBundleWidthChange,
    edgeFlow,
    onEdgeFlowChange,
    snapToGrid,
    onSnapToGridChange,
    autoLayoutAfterDiscover,
    onAutoLayoutAfterDiscoverChange,
    discoverAutoAddUnmatched,
    onDiscoverAutoAddUnmatchedChange,
    discoverProjectNeighbors,
    onDiscoverProjectNeighborsChange,
    canvasBg,
    onCanvasBgChange,
    labelColors,
    onLabelColorsChange,
    vendorColors,
    onVendorColorsChange,
    edgeDefaults,
    onUpdateEdgeDefault,
    onResetEdgeDefaults,
    selectedNodeCount,
    onAlign,
    mapId,
    nodeCount,
    canvasQuery,
    onCanvasQueryChange,
    findOpen,
    onFindOpenChange,
    findActiveIdx,
    onFindActiveIdxChange,
    canvasHits,
    onFindOnCanvas,
  } = props;

  const body = (
    <>
      <details className="topo-toolbar__display" ref={displayMenuRef}>
        <summary>{t("topology.display")}</summary>
        <div className="topo-display-toggles" role="group" aria-label={t("topology.display")}>
          <label className="topo-display-toggles__item">
            <input type="checkbox" checked={hideIp} onChange={(e) => onHideIpChange(e.target.checked)} />
            {t("topology.hideIp")}
          </label>
          <label className="topo-display-toggles__item">
            <input
              type="checkbox"
              checked={hideVendor}
              onChange={(e) => onHideVendorChange(e.target.checked)}
            />
            {t("topology.hideVendor")}
          </label>
          <label className="topo-display-toggles__item">
            <input
              type="checkbox"
              checked={showPlaceholderBadge}
              onChange={(e) => {
                const next = e.target.checked;
                onShowPlaceholderBadgeChange(next);
                persistBoolFlag(SHOW_PLACEHOLDER_BADGE_KEY, next);
              }}
            />
            {t("topology.showPlaceholderBadge")}
          </label>
          <label className="topo-display-toggles__item">
            <input type="checkbox" checked={hidePorts} onChange={(e) => onHidePortsChange(e.target.checked)} />
            {t("topology.hidePorts")}
          </label>
          <label className="topo-display-toggles__item">
            <input
              type="checkbox"
              checked={expandPhysicalLinks}
              onChange={(e) => onExpandPhysicalLinksChange(e.target.checked)}
            />
            {t("topology.expandPhysicalLinks")}
          </label>
          <label className="topo-display-toggles__item">
            <input
              type="checkbox"
              checked={scaleBundleWidth}
              disabled={expandPhysicalLinks}
              onChange={(e) => {
                const next = e.target.checked;
                onScaleBundleWidthChange(next);
                persistBoolFlag(SCALE_BUNDLE_WIDTH_KEY, next);
              }}
            />
            {t("topology.scaleBundleWidth")}
          </label>
          <label className="topo-display-toggles__item">
            <input type="checkbox" checked={edgeFlow} onChange={(e) => onEdgeFlowChange(e.target.checked)} />
            {t("topology.edgeFlow")}
          </label>
          <label className="topo-display-toggles__item">
            <input type="checkbox" checked={snapToGrid} onChange={(e) => onSnapToGridChange(e.target.checked)} />
            {t("topology.snapGrid")}
          </label>
          <label className="topo-display-toggles__item">
            <input
              type="checkbox"
              checked={autoLayoutAfterDiscover}
              onChange={(e) => {
                const next = e.target.checked;
                onAutoLayoutAfterDiscoverChange(next);
                persistAutoLayoutAfterDiscover(next);
              }}
            />
            {t("topology.autoLayoutDiscover")}
          </label>
          <label className="topo-display-toggles__item">
            <input
              type="checkbox"
              checked={discoverAutoAddUnmatched}
              onChange={(e) => {
                const next = e.target.checked;
                onDiscoverAutoAddUnmatchedChange(next);
                persistBoolFlag(DISCOVER_AUTO_ADD_KEY, next);
                void updateLldpCollectPolicy({ auto_add_unmatched: next })
                  .then(() => {
                    void queryClient.invalidateQueries({ queryKey: queryKeys.lldpCollectDashboard });
                  })
                  .catch(() => {
                    showError(t("topology.policySyncFailed"));
                  });
              }}
            />
            {t("topology.discoverAutoAddUnmatched")}
          </label>
          <label className="topo-display-toggles__item">
            <input
              type="checkbox"
              checked={discoverProjectNeighbors}
              onChange={(e) => {
                const next = e.target.checked;
                onDiscoverProjectNeighborsChange(next);
                persistBoolFlag(DISCOVER_PROJECT_NEIGHBORS_KEY, next);
              }}
            />
            {t("topology.discoverProjectNeighbors")}
          </label>
          <div className="topo-display-defaults topo-display-defaults--canvas-bg topo-display-defaults--colors">
            <div className="topo-display-defaults__head">
              <strong>{t("topology.canvasBg")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => {
                  onCanvasBgChange(DEFAULT_CANVAS_BG);
                  persistCanvasBg(DEFAULT_CANVAS_BG);
                }}
              >
                {t("topology.canvasBgReset")}
              </button>
            </div>
            <div className="topo-display-defaults__row">
              <span className="topo-display-defaults__name">{t("topology.canvasBgColor")}</span>
              <input
                type="color"
                value={canvasBg}
                title={t("topology.canvasBgColor")}
                onChange={(e) => {
                  const next = e.target.value;
                  onCanvasBgChange(next);
                  persistCanvasBg(next);
                }}
              />
              <input
                className="topo-toolbar__select topo-canvas-bg-hex"
                value={canvasBg}
                spellCheck={false}
                aria-label={t("topology.canvasBgColor")}
                onChange={(e) => {
                  const raw = e.target.value.trim();
                  if (!/^#[0-9a-fA-F]{0,6}$/.test(raw)) return;
                  onCanvasBgChange(raw);
                  if (isHexColor(raw)) persistCanvasBg(raw.toLowerCase());
                }}
                onBlur={() => {
                  if (!isHexColor(canvasBg)) onCanvasBgChange(loadCanvasBg());
                }}
              />
            </div>
          </div>
          <div className="topo-display-defaults topo-display-defaults--canvas-bg topo-display-defaults--colors">
            <div className="topo-display-defaults__head">
              <strong>{t("topology.textColors")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => {
                  onLabelColorsChange({ ...DEFAULT_LABEL_COLORS });
                  persistLabelColors({ ...DEFAULT_LABEL_COLORS });
                }}
              >
                {t("topology.textColorsReset")}
              </button>
            </div>
            {(
              [
                ["name", t("topology.labelColor")],
                ["edgeLabel", t("topology.edgeLabelColor")],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="topo-display-defaults__row">
                <span className="topo-display-defaults__name">{label}</span>
                <input
                  type="color"
                  value={labelColors[key]}
                  title={label}
                  onChange={(e) => {
                    const next = { ...labelColors, [key]: e.target.value.toLowerCase() };
                    onLabelColorsChange(next);
                    persistLabelColors(next);
                  }}
                />
                <input
                  className="topo-toolbar__select topo-canvas-bg-hex"
                  value={labelColors[key]}
                  spellCheck={false}
                  aria-label={label}
                  onChange={(e) => {
                    const raw = e.target.value.trim();
                    if (!/^#[0-9a-fA-F]{0,6}$/.test(raw)) return;
                    const next = { ...labelColors, [key]: raw };
                    onLabelColorsChange(next);
                    if (isHexColor(raw)) persistLabelColors({ ...next, [key]: raw.toLowerCase() });
                  }}
                  onBlur={() => {
                    if (!isHexColor(labelColors[key])) onLabelColorsChange(loadLabelColors());
                  }}
                />
              </div>
            ))}
          </div>
          <div className="topo-display-defaults topo-display-defaults--canvas-bg topo-display-defaults--colors">
            <div className="topo-display-defaults__head">
              <strong>{t("topology.vendorColors")}</strong>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => {
                  onVendorColorsChange({ ...DEFAULT_VENDOR_COLORS });
                  persistVendorColors({ ...DEFAULT_VENDOR_COLORS });
                }}
              >
                {t("topology.vendorColorsReset")}
              </button>
            </div>
            {VENDOR_TONE_KEYS.map((key) => {
              const label = t(`topology.vendorTone.${key}`);
              return (
                <div key={key} className="topo-display-defaults__row">
                  <span className="topo-display-defaults__name">{label}</span>
                  <input
                    type="color"
                    value={vendorColors[key]}
                    title={label}
                    onChange={(e) => {
                      const next = { ...vendorColors, [key]: e.target.value.toLowerCase() };
                      onVendorColorsChange(next);
                      persistVendorColors(next);
                    }}
                  />
                  <input
                    className="topo-toolbar__select topo-canvas-bg-hex"
                    value={vendorColors[key]}
                    spellCheck={false}
                    aria-label={label}
                    onChange={(e) => {
                      const raw = e.target.value.trim();
                      if (!/^#[0-9a-fA-F]{0,6}$/.test(raw)) return;
                      const next = { ...vendorColors, [key]: raw };
                      onVendorColorsChange(next);
                      if (isHexColor(raw)) {
                        persistVendorColors({ ...next, [key]: raw.toLowerCase() });
                      }
                    }}
                    onBlur={() => {
                      if (!isHexColor(vendorColors[key])) onVendorColorsChange(loadVendorColors());
                    }}
                  />
                </div>
              );
            })}
          </div>
          <div className="topo-display-defaults">
            <div className="topo-display-defaults__head">
              <strong>{t("topology.edgeDefaults")}</strong>
              <button type="button" className="btn btn--sm btn--ghost" onClick={onResetEdgeDefaults}>
                {t("topology.edgeDefaultsReset")}
              </button>
            </div>
            {(
              [
                ["ume", t("topology.edgeUme")],
                ["discovered", t("topology.edgeDiscovered")],
                ["stale", t("topology.edgeStale")],
                ["manual", t("topology.edgeManual")],
              ] as const
            ).map(([kind, label]) => {
              const d = edgeDefaults[kind];
              return (
                <div key={kind} className="topo-display-defaults__row">
                  <span className="topo-display-defaults__name">{label}</span>
                  <input
                    type="color"
                    value={d.stroke_color}
                    title={t("topology.edgeColor")}
                    onChange={(e) => onUpdateEdgeDefault(kind, { stroke_color: e.target.value })}
                  />
                  <select
                    className="topo-toolbar__select"
                    aria-label={t("topology.edgeLineStyle")}
                    value={d.line_style}
                    onChange={(e) =>
                      onUpdateEdgeDefault(kind, {
                        line_style: e.target.value as EdgeLineStyle,
                      })
                    }
                  >
                    <option value="solid">{t("topology.edgeLineSolid")}</option>
                    <option value="dashed">{t("topology.edgeLineDashed")}</option>
                    <option value="dotted">{t("topology.edgeLineDotted")}</option>
                  </select>
                  <select
                    className="topo-toolbar__select"
                    aria-label={t("topology.edgeWidth")}
                    value={String(d.stroke_width)}
                    onChange={(e) =>
                      onUpdateEdgeDefault(kind, { stroke_width: Number(e.target.value) || 2 })
                    }
                  >
                    {[1, 2, 3, 4, 5, 6, 8].map((w) => (
                      <option key={w} value={w}>
                        {w}px
                      </option>
                    ))}
                  </select>
                </div>
              );
            })}
          </div>
        </div>
      </details>
      <select
        className="topo-toolbar__select"
        aria-label={t("topology.align")}
        disabled={selectedNodeCount < 2}
        defaultValue=""
        onChange={(e) => {
          const v = e.target.value as AlignKind | "";
          e.target.value = "";
          if (v) onAlign(v);
        }}
      >
        <option value="" disabled>
          {t("topology.align")}
        </option>
        <option value="left">{t("topology.alignLeft")}</option>
        <option value="right">{t("topology.alignRight")}</option>
        <option value="top">{t("topology.alignTop")}</option>
        <option value="bottom">{t("topology.alignBottom")}</option>
        <option value="h-center">{t("topology.alignHCenter")}</option>
        <option value="v-center">{t("topology.alignVCenter")}</option>
        <option value="h-distribute">{t("topology.alignHDistribute")}</option>
        <option value="v-distribute">{t("topology.alignVDistribute")}</option>
      </select>
      <div
        className="topo-toolbar__group topo-toolbar__group--find"
        aria-label={t("topology.findNode")}
        ref={findBoxRef}
      >
        <input
          className="topo-toolbar__find"
          value={canvasQuery}
          onChange={(e) => onCanvasQueryChange(e.target.value)}
          onFocus={() => {
            if (canvasQuery.trim()) onFindOpenChange(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              if (!canvasHits.length) return;
              onFindOpenChange(true);
              onFindActiveIdxChange((i) => (i + 1) % canvasHits.length);
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              if (!canvasHits.length) return;
              onFindOpenChange(true);
              onFindActiveIdxChange((i) => (i - 1 + canvasHits.length) % canvasHits.length);
              return;
            }
            if (e.key === "Escape") {
              onFindOpenChange(false);
              return;
            }
            if (e.key === "Enter") {
              e.preventDefault();
              onFindOnCanvas();
            }
          }}
          placeholder={t("topology.findNodePh")}
          disabled={!mapId || nodeCount === 0}
          aria-label={t("topology.findNode")}
          aria-autocomplete="list"
          aria-expanded={findOpen}
        />
        {findOpen && canvasQuery.trim() ? (
          <div className="topo-find-suggest" role="listbox">
            {canvasHits.length === 0 ? (
              <div className="topo-find-suggest__empty">{t("topology.findNoMatch")}</div>
            ) : (
              canvasHits.slice(0, 12).map((n, idx) => (
                <button
                  key={n.id}
                  type="button"
                  role="option"
                  aria-selected={idx === findActiveIdx}
                  className={`topo-find-suggest__item${idx === findActiveIdx ? " is-active" : ""}`}
                  onMouseEnter={() => onFindActiveIdxChange(idx)}
                  onClick={() => onFindOnCanvas(n.id)}
                >
                  <span className="topo-find-suggest__name">{n.data.label || n.id}</span>
                  <span className="topo-find-suggest__meta">
                    {[n.data.ne_ip, n.data.vendor].filter(Boolean).join(SEP)}
                  </span>
                </button>
              ))
            )}
            {canvasHits.length > 12 ? (
              <div className="topo-find-suggest__more">
                {t("topology.findMore").replace("{{count}}", String(canvasHits.length - 12))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  );

  if (!fullscreen) {
    if (!toolbarSlot) return null;
    return createPortal(body, toolbarSlot);
  }

  return (
    <div className="topo-view-tools" role="toolbar" aria-label={t("topology.display")}>
      {body}
    </div>
  );
}
