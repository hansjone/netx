import type { RefObject } from "react";
import { createPortal } from "react-dom";
import type { Edge, Node } from "@xyflow/react";
import { useI18n } from "../../i18n";
import type { CtxMenu } from "./pageTypes";
import type { EdgeStyleData } from "./edgeStyle";
import { formatPortPairLabel } from "./linkDisplay";
import { isPlaceholderSource } from "./searchUtils";
import type { NeNodeData } from "./TopologyReactFlowView";

export type ResolvedEdgeStyle = {
  stroke: string;
  strokeWidth: number;
  strokeDasharray?: string;
};

export type TopologyCtxMenuProps = {
  ctxMenu: CtxMenu;
  menuRef: RefObject<HTMLUListElement | null>;
  portalTarget: HTMLElement | null;
  fullscreen: boolean;
  selectedNodeCount: number;
  selectedNode: Node<NeNodeData> | null;
  selectedEdge: Edge | null;
  selectedEdgeData: EdgeStyleData;
  selectedEdgeResolved: ResolvedEdgeStyle | null;
  selectedEdgeSourceNode: Node<NeNodeData> | null;
  selectedEdgeTargetNode: Node<NeNodeData> | null;
  expandPhysicalLinks: boolean;
  discovering: boolean;
  isWorldFlatCanvas: boolean;
  createRegionPending: boolean;
  onClose: () => void;
  onToggleFullscreen: () => void;
  onRemoveSelected: () => void;
  onOpenCreateNe: (flowX: number, flowY: number) => void;
  onPromptNewSubRegion: () => void;
  onRenameSelectedNode: () => void;
  onDiscoverOne: (node: Node<NeNodeData> | null) => void;
  onOpenWebcrt: (node: Node<NeNodeData> | null) => void;
  onOpenNe: (node: Node<NeNodeData> | null) => void;
  onPurgePlaceholder: (id: string) => void;
  onRemoveNode: (id: string) => void;
  onExpandPhysicalLinks: () => void;
  onPushHistory: () => void;
  onPatchSelectedEdgeStyle: (
    patch: Partial<EdgeStyleData>,
    opts?: { skipHistory?: boolean },
  ) => void;
  onOpenPortTraffic: (edge: Edge | null) => void;
  onRemoveEdge: (id: string) => void;
};

export function TopologyCtxMenu({
  ctxMenu,
  menuRef,
  portalTarget,
  fullscreen,
  selectedNodeCount,
  selectedNode,
  selectedEdge,
  selectedEdgeData,
  selectedEdgeResolved,
  selectedEdgeSourceNode,
  selectedEdgeTargetNode,
  expandPhysicalLinks,
  discovering,
  isWorldFlatCanvas,
  createRegionPending,
  onClose,
  onToggleFullscreen,
  onRemoveSelected,
  onOpenCreateNe,
  onPromptNewSubRegion,
  onRenameSelectedNode,
  onDiscoverOne,
  onOpenWebcrt,
  onOpenNe,
  onPurgePlaceholder,
  onRemoveNode,
  onExpandPhysicalLinks,
  onPushHistory,
  onPatchSelectedEdgeStyle,
  onOpenPortTraffic,
  onRemoveEdge,
}: TopologyCtxMenuProps) {
  const { t } = useI18n();

  return createPortal(
    <ul
      ref={menuRef}
      className={`topo-ctx${ctxMenu.kind === "edge" ? " topo-ctx--edge" : ""}`}
      style={{ left: ctxMenu.x, top: ctxMenu.y }}
      role="menu"
      onContextMenu={(e) => e.preventDefault()}
    >
      {ctxMenu.kind === "selection" ? (
        <>
          <li className="topo-ctx__head" role="presentation">
            {t("topology.selectionMenu")}
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() => {
                onClose();
                void onToggleFullscreen();
              }}
            >
              {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item topo-ctx__item--danger"
              role="menuitem"
              onClick={() => onRemoveSelected()}
            >
              {t("topology.removeSelected").replace("{{count}}", String(selectedNodeCount))}
            </button>
          </li>
        </>
      ) : ctxMenu.kind === "pane" ? (
        <>
          <li className="topo-ctx__head" role="presentation">
            {t("topology.paneMenu")}
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              disabled={isWorldFlatCanvas}
              onClick={() => onOpenCreateNe(ctxMenu.flowX, ctxMenu.flowY)}
            >
              {t("topology.createNe")}
            </button>
          </li>
          {!isWorldFlatCanvas ? (
            <li role="none">
              <button
                type="button"
                className="topo-ctx__item"
                role="menuitem"
                disabled={createRegionPending}
                onClick={() => onPromptNewSubRegion()}
              >
                {t("topology.newSubRegion")}
              </button>
            </li>
          ) : null}
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() => {
                onClose();
                void onToggleFullscreen();
              }}
            >
              {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
            </button>
          </li>
        </>
      ) : ctxMenu.kind === "node" ? (
        <>
          <li className="topo-ctx__head" role="presentation">
            {t("topology.nodeMenu")}
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() => {
                onClose();
                void onToggleFullscreen();
              }}
            >
              {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={onRenameSelectedNode}
            >
              {t("topology.renameNode")}
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              disabled={discovering}
              onClick={() => onDiscoverOne(selectedNode)}
            >
              {t("topology.discoverOne")}
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() => onOpenWebcrt(selectedNode)}
            >
              {t("topology.openWebcrt")}
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() => onOpenNe(selectedNode)}
            >
              {t("topology.openNe")}
            </button>
          </li>
          <li className="topo-ctx__sep" aria-hidden />
          {selectedNode &&
          isPlaceholderSource(selectedNode.data.managed_source, selectedNode.data.ne_ip) ? (
            <li role="none">
              <button
                type="button"
                className="topo-ctx__item topo-ctx__item--danger"
                role="menuitem"
                onClick={() => void onPurgePlaceholder(ctxMenu.id)}
              >
                {t("topology.deletePlaceholder")}
              </button>
            </li>
          ) : null}
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item topo-ctx__item--danger"
              role="menuitem"
              onClick={() => onRemoveNode(ctxMenu.id)}
            >
              {t("topology.removeNode")}
            </button>
          </li>
        </>
      ) : (
        <>
          <li className="topo-ctx__head" role="presentation">
            {t("topology.edgeMenu")}
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() => {
                onClose();
                void onToggleFullscreen();
              }}
            >
              {fullscreen ? t("topology.exitFullscreen") : t("topology.fullscreen")}
            </button>
          </li>
          <li className="topo-ctx__section" role="none">
            <div className="topo-ctx__style" onMouseDown={(e) => e.stopPropagation()}>
              <div className="topo-ctx__style-title">{t("topology.edgeStyle")}</div>
              {selectedEdgeResolved ? (
                <div className="topo-ctx__style-grid">
                  {Number(selectedEdgeData.member_count || 1) > 1 ? (
                    <div className="topo-ctx__style-members">
                      <div className="topo-ctx__style-title">
                        {t("topology.linkMembers").replace(
                          "{{count}}",
                          String(selectedEdgeData.member_count || 0),
                        )}
                      </div>
                      <ul className="topo-ctx__member-list">
                        {(selectedEdgeData.members || []).map((m) => (
                          <li key={m.id}>
                            {formatPortPairLabel(m.a_port, m.b_port, m.display_label) ||
                              m.id.slice(0, 8)}
                          </li>
                        ))}
                      </ul>
                      {!expandPhysicalLinks ? (
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() => {
                            onExpandPhysicalLinks();
                            onClose();
                          }}
                        >
                          {t("topology.expandPhysicalLinks")}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  <label className="topo-ctx__style-row">
                    <span>
                      {(selectedEdgeSourceNode?.data.label ||
                        selectedEdgeSourceNode?.data.ne_ip ||
                        t("topology.endpointA")) +
                        " · " +
                        t("topology.port")}
                    </span>
                    <input
                      type="text"
                      className="topo-ctx__style-input"
                      value={selectedEdgeData.source_port || ""}
                      placeholder="?"
                      disabled={
                        Number(selectedEdgeData.member_count || 1) > 1 && !expandPhysicalLinks
                      }
                      onFocus={() => onPushHistory()}
                      onChange={(e) =>
                        onPatchSelectedEdgeStyle({ source_port: e.target.value }, { skipHistory: true })
                      }
                    />
                  </label>
                  <label className="topo-ctx__style-row">
                    <span>
                      {(selectedEdgeTargetNode?.data.label ||
                        selectedEdgeTargetNode?.data.ne_ip ||
                        t("topology.endpointB")) +
                        " · " +
                        t("topology.port")}
                    </span>
                    <input
                      type="text"
                      className="topo-ctx__style-input"
                      value={selectedEdgeData.target_port || ""}
                      placeholder="?"
                      disabled={
                        Number(selectedEdgeData.member_count || 1) > 1 && !expandPhysicalLinks
                      }
                      onFocus={() => onPushHistory()}
                      onChange={(e) =>
                        onPatchSelectedEdgeStyle({ target_port: e.target.value }, { skipHistory: true })
                      }
                    />
                  </label>
                  <label className="topo-ctx__style-row">
                    <span>{t("topology.edgeColor")}</span>
                    <input
                      type="color"
                      value={selectedEdgeResolved.stroke}
                      onFocus={() => onPushHistory()}
                      onChange={(e) =>
                        onPatchSelectedEdgeStyle(
                          { stroke_color: e.target.value },
                          { skipHistory: true },
                        )
                      }
                    />
                  </label>
                  <label className="topo-ctx__style-row">
                    <span>{t("topology.edgeLineStyle")}</span>
                    <select
                      value={
                        selectedEdgeData.line_style ||
                        (selectedEdgeResolved.strokeDasharray ? "dashed" : "solid")
                      }
                      onChange={(e) => onPatchSelectedEdgeStyle({ line_style: e.target.value })}
                    >
                      <option value="solid">{t("topology.edgeLineSolid")}</option>
                      <option value="dashed">{t("topology.edgeLineDashed")}</option>
                      <option value="dotted">{t("topology.edgeLineDotted")}</option>
                    </select>
                  </label>
                  <label className="topo-ctx__style-row">
                    <span>{t("topology.edgeWidth")}</span>
                    <select
                      value={String(
                        Number(selectedEdgeData.stroke_width || 0) > 0
                          ? Number(selectedEdgeData.stroke_width)
                          : Math.round(Number(selectedEdgeResolved.strokeWidth || 2)),
                      )}
                      onChange={(e) =>
                        onPatchSelectedEdgeStyle({ stroke_width: Number(e.target.value) || 0 })
                      }
                    >
                      {[1, 2, 3, 4, 5, 6, 8].map((w) => (
                        <option key={w} value={w}>
                          {w}px
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              ) : null}
            </div>
          </li>
          <li className="topo-ctx__sep" aria-hidden />
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() => onOpenPortTraffic(selectedEdge)}
            >
              {t("topology.openPortTraffic")}
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item"
              role="menuitem"
              onClick={() =>
                onPatchSelectedEdgeStyle({ stroke_color: "", stroke_width: 0, line_style: "" })
              }
            >
              {t("topology.edgeStyleReset")}
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              className="topo-ctx__item topo-ctx__item--danger"
              role="menuitem"
              onClick={() => onRemoveEdge(ctxMenu.id)}
            >
              {t("topology.removeEdge")}
            </button>
          </li>
        </>
      )}
    </ul>,
    portalTarget || document.body,
  );
}
