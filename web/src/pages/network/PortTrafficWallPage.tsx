import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import {
  deletePortTrafficBoard,
  fetchPortTrafficBoard,
  fetchPortTrafficDevices,
  fetchPortTrafficTargets,
  putPortTrafficBoardPanels,
  updatePortTrafficBoard,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import type { PortTrafficBoardPanel, PortTrafficBoardPanelIn } from "../../types";
import { PortTrafficBoardPanelCell } from "./PortTrafficBoardPanelCell";
import type { WallYMode } from "./PortTrafficWall";

type BaselineMode = "off" | "shift" | "day" | "week" | "custom";

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

function newPanelId() {
  return `p_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

function panelToIn(p: PortTrafficBoardPanel): PortTrafficBoardPanelIn {
  return {
    id: p.id,
    title: p.title || "",
    target_id: p.target_id,
    range_hours: p.range_hours,
    baseline: p.baseline || "off",
    offset_hours: p.offset_hours || 0,
    baseline_target_id: p.baseline_target_id || "",
    y_mode: p.y_mode || "auto",
    ord: p.ord,
    col_span: p.col_span || 1,
    row_span: p.row_span || 1,
  };
}

/** Dedicated board wall tab at `/port-traffic/wall/:boardId` (outside Network shell). */
export function PortTrafficWallPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();
  const { boardId: boardIdParam = "" } = useParams<{ boardId: string }>();
  const boardId = decodeURIComponent(boardIdParam).trim();
  const stageRef = useRef<HTMLDivElement | null>(null);

  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [draftPanels, setDraftPanels] = useState<PortTrafficBoardPanel[]>([]);
  const [draftName, setDraftName] = useState("");
  const [draftCols, setDraftCols] = useState(2);
  const [editPanelId, setEditPanelId] = useState("");
  const [editPickDeviceId, setEditPickDeviceId] = useState("");
  const [editMapBaselineDeviceId, setEditMapBaselineDeviceId] = useState("");
  const [addDeviceId, setAddDeviceId] = useState("");
  const [addTargetId, setAddTargetId] = useState("");
  const [deleted, setDeleted] = useState(false);

  const boardQuery = useQuery({
    queryKey: queryKeys.portTrafficBoard(boardId),
    queryFn: () => fetchPortTrafficBoard(boardId),
    enabled: Boolean(boardId) && !deleted,
    staleTime: 2000,
  });

  const devicesQuery = useQuery({
    queryKey: queryKeys.portTrafficDevices(0),
    queryFn: () => fetchPortTrafficDevices({ page: 1, pageSize: 100 }),
    enabled: editing,
    staleTime: 5000,
  });

  const addTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(addDeviceId),
    queryFn: () => fetchPortTrafficTargets(addDeviceId),
    enabled: editing && Boolean(addDeviceId),
    staleTime: 2000,
  });

  const editPanel = draftPanels.find((p) => p.id === editPanelId) || null;
  const editTargetsDeviceId = editPickDeviceId || editPanel?.target?.device_id || "";
  const editTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(editTargetsDeviceId),
    queryFn: () => fetchPortTrafficTargets(editTargetsDeviceId),
    enabled: editing && Boolean(editPanelId) && Boolean(editTargetsDeviceId),
    staleTime: 2000,
  });
  const mapBaselineTargetsQuery = useQuery({
    queryKey: queryKeys.portTrafficTargets(editMapBaselineDeviceId),
    queryFn: () => fetchPortTrafficTargets(editMapBaselineDeviceId),
    enabled: editing && Boolean(editPanelId) && Boolean(editMapBaselineDeviceId),
    staleTime: 2000,
  });

  const mapBaselineOptions = useMemo(
    () =>
      (mapBaselineTargetsQuery.data?.items || []).filter(
        (x) =>
          (x.status === "active" || x.id === editPanel?.baseline_target_id) &&
          x.id !== editPanel?.target_id,
      ),
    [mapBaselineTargetsQuery.data?.items, editPanel?.baseline_target_id, editPanel?.target_id],
  );

  const closePanelSettings = () => {
    setEditPanelId("");
    setEditPickDeviceId("");
    setEditMapBaselineDeviceId("");
  };

  const openPanelSettings = (panelId: string) => {
    const p = draftPanels.find((x) => x.id === panelId);
    setEditPanelId(panelId);
    setEditPickDeviceId(p?.target?.device_id || "");
    setEditMapBaselineDeviceId(p?.baseline_target?.device_id || "");
  };

  const board = boardQuery.data || null;
  const panels = editing ? draftPanels : board?.panels || [];
  const cols = editing ? draftCols : board?.cols || 2;

  useEffect(() => {
    if (!board || editing) return;
    setDraftPanels(board.panels || []);
    setDraftName(board.name || "");
    setDraftCols(board.cols || 2);
  }, [board, editing]);

  useEffect(() => {
    if (!board?.name) return;
    try {
      document.title = `${board.name} · NetX`;
    } catch {
      /* ignore */
    }
  }, [board?.name]);

  const savePanelsMut = useMutation({
    mutationFn: async () => {
      if (draftName.trim() && draftName.trim() !== (board?.name || "")) {
        await updatePortTrafficBoard(boardId, { name: draftName.trim(), cols: draftCols });
      } else if (draftCols !== (board?.cols || 2)) {
        await updatePortTrafficBoard(boardId, { cols: draftCols });
      }
      return putPortTrafficBoardPanels(
        boardId,
        draftPanels.map((p, i) => ({ ...panelToIn(p), ord: i })),
      );
    },
    onSuccess: () => {
      showOk(t("portTraffic.boardSaved"));
      setDirty(false);
      setEditing(false);
      closePanelSettings();
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficBoards });
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficBoard(boardId) });
    },
    onError: (e: Error) => showError(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: () => deletePortTrafficBoard(boardId),
    onSuccess: () => {
      showOk(t("portTraffic.boardDeleted"));
      setDeleted(true);
      setEditing(false);
      setDirty(false);
      closePanelSettings();
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficBoards });
    },
    onError: (e: Error) => showError(e.message),
  });

  const startEdit = () => {
    if (!board) return;
    setDraftPanels([...(board.panels || [])]);
    setDraftName(board.name || "");
    setDraftCols(board.cols || 2);
    setEditing(true);
    setDirty(false);
  };

  const cancelEdit = () => {
    if (dirty && !window.confirm(t("portTraffic.boardUnsavedLeave"))) return;
    setEditing(false);
    setDirty(false);
    closePanelSettings();
    if (board) {
      setDraftPanels(board.panels || []);
      setDraftName(board.name || "");
      setDraftCols(board.cols || 2);
    }
  };

  useEffect(() => {
    const syncFs = () => {
      const el = stageRef.current;
      setFullscreen(Boolean(el && document.fullscreenElement === el));
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

  const markDirty = (next: PortTrafficBoardPanel[]) => {
    setDraftPanels(next);
    setDirty(true);
  };

  const addPanel = () => {
    if (!addTargetId) {
      showError(t("portTraffic.pickPort"));
      return;
    }
    const tgt = (addTargetsQuery.data?.items || []).find((x) => x.id === addTargetId);
    const panel: PortTrafficBoardPanel = {
      id: newPanelId(),
      board_id: boardId,
      title: "",
      target_id: addTargetId,
      range_hours: 24,
      baseline: "off",
      offset_hours: 0,
      baseline_target_id: "",
      y_mode: "auto",
      ord: draftPanels.length,
      col_span: 1,
      row_span: 1,
      stale: false,
      target: tgt || null,
      baseline_target: null,
    };
    markDirty([...draftPanels, panel]);
    setAddTargetId("");
  };

  const updatePanel = (id: string, patch: Partial<PortTrafficBoardPanel>) => {
    markDirty(draftPanels.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  };

  const movePanel = (id: string, dir: -1 | 1) => {
    const idx = draftPanels.findIndex((p) => p.id === id);
    if (idx < 0) return;
    const j = idx + dir;
    if (j < 0 || j >= draftPanels.length) return;
    const next = [...draftPanels];
    const tmp = next[idx];
    next[idx] = next[j];
    next[j] = tmp;
    markDirty(next.map((p, i) => ({ ...p, ord: i })));
  };

  const activeAddTargets = useMemo(
    () => (addTargetsQuery.data?.items || []).filter((x) => x.status === "active"),
    [addTargetsQuery.data?.items],
  );

  if (!boardId) {
    return (
      <section className="pt-wall-shell">
        <div className="pt-wall-shell__empty">
          <p>{t("portTraffic.boardPickHint")}</p>
        </div>
      </section>
    );
  }

  if (deleted) {
    return (
      <section className="pt-wall-shell">
        <div className="pt-wall-shell__empty">
          <p>{t("portTraffic.boardDeleted")}</p>
          <p className="muted">{t("portTraffic.boardClosedHint")}</p>
        </div>
      </section>
    );
  }

  return (
    <section className={`pt-wall-shell${editing ? " is-editing" : ""}`}>
      <div className="pt-board-toolbar">
        <div className="pt-board-toolbar__left">
          {editing ? (
            <>
              <label className="pt-board-toolbar__field">
                {t("portTraffic.boardName")}
                <input
                  value={draftName}
                  onChange={(e) => {
                    setDraftName(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <label className="pt-board-toolbar__field pt-board-toolbar__field--sm">
                {t("portTraffic.boardCols")}
                <select
                  value={draftCols}
                  onChange={(e) => {
                    setDraftCols(Number(e.target.value) || 2);
                    setDirty(true);
                  }}
                >
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                  <option value={3}>3</option>
                  <option value={4}>4</option>
                </select>
              </label>
            </>
          ) : (
            <div className="pt-board-toolbar__title">
              {board?.name || t("portTraffic.wallTitle")}
              {dirty ? " *" : ""}
            </div>
          )}
        </div>
        <div className="btn-row pt-board-toolbar__actions">
          {panels.length && !editing ? (
            <button
              type="button"
              className="pt-wall-page__fs-btn pt-wall-page__fs-btn--toolbar"
              onClick={() => void toggleFullscreen()}
              title={fullscreen ? t("portTraffic.exitFullscreen") : t("portTraffic.fullscreen")}
              aria-label={fullscreen ? t("portTraffic.exitFullscreen") : t("portTraffic.fullscreen")}
            >
              <FullscreenIcon />
              <span>{fullscreen ? t("portTraffic.exitFullscreen") : t("portTraffic.fullscreen")}</span>
            </button>
          ) : null}
          {!editing ? (
            <button type="button" className="btn-primary" onClick={startEdit} disabled={!board}>
              {t("portTraffic.boardEdit")}
            </button>
          ) : (
            <>
              <button
                type="button"
                className="btn-primary"
                disabled={savePanelsMut.isPending}
                onClick={() => savePanelsMut.mutate()}
              >
                {t("portTraffic.boardSave")}
              </button>
              <button type="button" onClick={cancelEdit}>
                {t("portTraffic.boardCancel")}
              </button>
            </>
          )}
          {!editing ? (
            <button
              type="button"
              className="btn--danger"
              disabled={deleteMut.isPending || !board}
              onClick={() => {
                if (window.confirm(t("portTraffic.boardConfirmDelete"))) deleteMut.mutate();
              }}
            >
              {t("portTraffic.delete")}
            </button>
          ) : null}
        </div>
      </div>

      {boardQuery.isLoading ? (
        <div className="pt-wall-shell__empty">
          <p>…</p>
        </div>
      ) : boardQuery.isError ? (
        <div className="pt-wall-shell__empty">
          <p>{(boardQuery.error as Error)?.message || t("common.opFailed")}</p>
        </div>
      ) : (
        <div className="pt-wall-page pt-board-page">
          {editing ? (
            <div className="pt-board-add">
              <label className="pt-wall-page__field">
                {t("portTraffic.wallDevice")}
                <select
                  value={addDeviceId}
                  onChange={(e) => {
                    setAddDeviceId(e.target.value);
                    setAddTargetId("");
                  }}
                >
                  <option value="">{t("portTraffic.boardPickDevice")}</option>
                  {(devicesQuery.data?.items || []).map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.ne_name || d.ne_ip || d.ne_id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="pt-wall-page__field pt-wall-page__field--port">
                {t("portTraffic.wallPort")}
                <select value={addTargetId} onChange={(e) => setAddTargetId(e.target.value)}>
                  <option value="">{t("portTraffic.pickPort")}</option>
                  {activeAddTargets.map((tgt) => (
                    <option key={tgt.id} value={tgt.id}>
                      {tgt.ifname}
                    </option>
                  ))}
                </select>
              </label>
              <button type="button" className="btn-primary" onClick={addPanel}>
                {t("portTraffic.boardAddPanel")}
              </button>
            </div>
          ) : null}

          {!panels.length ? (
            <div className="pt-wall-shell__empty">
              <p>{t("portTraffic.boardNoPanels")}</p>
              {!editing ? (
                <button type="button" className="btn-primary" onClick={startEdit}>
                  {t("portTraffic.boardEdit")}
                </button>
              ) : null}
            </div>
          ) : (
            <div
              ref={stageRef}
              className={`pt-wall-page__stage${fullscreen ? " is-fullscreen" : ""}`}
            >
              <div
                className="pt-board-grid"
                style={{ gridTemplateColumns: `repeat(${Math.max(1, cols)}, minmax(0, 1fr))` }}
              >
                {panels.map((p) => (
                  <PortTrafficBoardPanelCell
                    key={p.id}
                    boardId={boardId}
                    panel={p}
                    editing={editing}
                    dense={fullscreen}
                    onEdit={() => openPanelSettings(p.id)}
                    onRemove={() => markDirty(draftPanels.filter((x) => x.id !== p.id))}
                    onMove={(dir) => movePanel(p.id, dir)}
                  />
                ))}
              </div>
              {fullscreen ? (
                <div className="pt-board-fs-legend" aria-hidden={false}>
                  <span className="pt-wall__legend-item pt-wall__legend-item--in">
                    <i className="pt-wall__legend-swatch" />
                    {t("portTraffic.seriesCurrentIn")}
                  </span>
                  <span className="pt-wall__legend-item pt-wall__legend-item--out">
                    <i className="pt-wall__legend-swatch" />
                    {t("portTraffic.seriesCurrentOut")}
                  </span>
                  <span className="pt-wall__legend-item pt-wall__legend-item--base-in">
                    <i className="pt-wall__legend-swatch" />
                    {t("portTraffic.seriesBaselineIn")}
                  </span>
                  <span className="pt-wall__legend-item pt-wall__legend-item--base-out">
                    <i className="pt-wall__legend-swatch" />
                    {t("portTraffic.seriesBaselineOut")}
                  </span>
                </div>
              ) : null}
            </div>
          )}
        </div>
      )}

      {editing && editPanel ? (
        <div className="modal-backdrop" role="presentation" onClick={closePanelSettings}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <h3>{t("portTraffic.boardPanelSettings")}</h3>
            <div className="form-grid" style={{ marginTop: 12 }}>
              <label>
                {t("portTraffic.boardPanelTitle")}
                <input
                  value={editPanel.title}
                  onChange={(e) => updatePanel(editPanel.id, { title: e.target.value })}
                />
              </label>
              <label>
                {t("portTraffic.boardPickDevice")}
                <select
                  value={editTargetsDeviceId}
                  onChange={(e) => {
                    setEditPickDeviceId(e.target.value);
                    updatePanel(editPanel.id, { target_id: "", target: null, stale: true });
                  }}
                >
                  <option value="">{t("portTraffic.boardPickDevice")}</option>
                  {(devicesQuery.data?.items || []).map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.ne_name || d.ne_ip || d.id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("portTraffic.wallPort")}
                <select
                  value={editPanel.target_id}
                  disabled={!editTargetsDeviceId}
                  onChange={(e) => {
                    const tid = e.target.value;
                    const tgt = (editTargetsQuery.data?.items || []).find((x) => x.id === tid);
                    updatePanel(editPanel.id, {
                      target_id: tid,
                      target: tgt || null,
                      stale: !tgt,
                      ...(tid && tid === editPanel.baseline_target_id
                        ? { baseline_target_id: "", baseline_target: null }
                        : {}),
                    });
                  }}
                >
                  <option value="">{t("portTraffic.pickPort")}</option>
                  {(editTargetsQuery.data?.items || [])
                    .filter((x) => x.status === "active" || x.id === editPanel.target_id)
                    .map((tgt) => (
                      <option key={tgt.id} value={tgt.id}>
                        {tgt.ifname}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                {t("portTraffic.range")}
                <select
                  value={editPanel.range_hours}
                  onChange={(e) =>
                    updatePanel(editPanel.id, { range_hours: Number(e.target.value) || 24 })
                  }
                >
                  <option value={1}>1h</option>
                  <option value={6}>6h</option>
                  <option value={24}>24h</option>
                </select>
              </label>
              <label>
                {t("portTraffic.compare")}
                <select
                  value={editPanel.baseline}
                  onChange={(e) =>
                    updatePanel(editPanel.id, { baseline: e.target.value as BaselineMode })
                  }
                >
                  <option value="off">{t("portTraffic.compareOff")}</option>
                  <option value="day">{t("portTraffic.compareDay")}</option>
                  <option value="week">{t("portTraffic.compareWeek")}</option>
                  <option value="shift">{t("portTraffic.compareShift")}</option>
                  <option value="custom">{t("portTraffic.compareCustom")}</option>
                </select>
              </label>
              {editPanel.baseline === "custom" ? (
                <label>
                  {t("portTraffic.offsetHours")}
                  <input
                    type="number"
                    min={1}
                    max={24 * 90}
                    value={editPanel.offset_hours || 48}
                    onChange={(e) =>
                      updatePanel(editPanel.id, {
                        offset_hours: Number(e.target.value) || 24,
                      })
                    }
                  />
                </label>
              ) : null}
              <label>
                {t("portTraffic.yMode")}
                <select
                  value={editPanel.y_mode}
                  onChange={(e) =>
                    updatePanel(editPanel.id, { y_mode: e.target.value as WallYMode })
                  }
                >
                  <option value="auto">{t("portTraffic.yModeAuto")}</option>
                  <option value="current">{t("portTraffic.yModeCurrent")}</option>
                  <option value="util">{t("portTraffic.yModeUtil")}</option>
                </select>
              </label>
              <label>
                {t("portTraffic.boardColSpan")}
                <select
                  value={editPanel.col_span || 1}
                  onChange={(e) =>
                    updatePanel(editPanel.id, {
                      col_span: Math.min(cols, Number(e.target.value) || 1),
                    })
                  }
                >
                  {Array.from({ length: cols }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("portTraffic.filterMap")} · {t("portTraffic.wallDevice")}
                <select
                  value={editMapBaselineDeviceId}
                  disabled={!editPanel.target_id}
                  onChange={(e) => {
                    setEditMapBaselineDeviceId(e.target.value);
                    updatePanel(editPanel.id, {
                      baseline_target_id: "",
                      baseline_target: null,
                    });
                  }}
                >
                  <option value="">{t("portTraffic.mapBaselineNone")}</option>
                  {(devicesQuery.data?.items || []).map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.ne_name || d.ne_ip || d.id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("portTraffic.mapBaselinePort")}
                <select
                  value={editPanel.baseline_target_id || ""}
                  disabled={!editMapBaselineDeviceId}
                  onChange={(e) => {
                    const tid = e.target.value;
                    const tgt = mapBaselineOptions.find((x) => x.id === tid) || null;
                    updatePanel(editPanel.id, {
                      baseline_target_id: tid,
                      baseline_target: tgt,
                    });
                  }}
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
            {editPanel.baseline === "week" ? (
              <p className="muted" style={{ marginTop: 10 }}>
                {t("portTraffic.retentionHint")}
              </p>
            ) : null}
            <div className="modal__actions">
              <button type="button" className="btn-primary" onClick={closePanelSettings}>
                {t("portTraffic.logClose")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
