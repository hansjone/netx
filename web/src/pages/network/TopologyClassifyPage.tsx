import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  bulkTagFabricNodes,
  deleteFabricNode,
  deleteFabricNodes,
  fetchFabricNodes,
  fetchTopologyTree,
  generateTopologySlices,
  matchFabricNodes,
  patchFabricNodeTags,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import { openOrFocusModule } from "../../utils/moduleWindows";
import type { FabricNodeSearchHit, SliceGenerateResult, TopologyTreeFolderItem } from "../../types";

const PAGE_SIZE = 50;

function flattenRegions(root: TopologyTreeFolderItem | null | undefined): TopologyTreeFolderItem[] {
  if (!root) return [];
  return (root.children || []).filter((c) => String(c.kind) === "region");
}

/** Prefer API flag; fall back so UI still works if backend is stale. */
function isFabricNodeDeletable(n: FabricNodeSearchHit): boolean {
  if (typeof n.deletable === "boolean") return n.deletable;
  const status = String(n.link_status || "").toLowerCase();
  if (status === "ume" || status === "both") return false;
  if (status === "orphaned") return true;
  if (!String(n.managed_ne_id || "").trim()) {
    return !String(n.ume_ne_id || "").trim();
  }
  if (n.managed_alive === false) return true;
  const src = String(n.managed_source || "").toLowerCase();
  return src === "lldp" || src === "webcrt";
}

export function TopologyClassifyPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();

  const [keyword, setKeyword] = useState("");
  const [filterRole, setFilterRole] = useState("");
  const [filterRegion, setFilterRegion] = useState("");
  const [unmatched, setUnmatched] = useState("");
  const [linkStatus, setLinkStatus] = useState("");
  const [page, setPage] = useState(1);

  const linkLabel = (status: string | undefined) => {
    switch (String(status || "").toLowerCase()) {
      case "managed":
        return t("topoClassify.linkManaged");
      case "ume":
        return t("topoClassify.linkUme");
      case "both":
        return t("topoClassify.linkBoth");
      case "orphaned":
        return t("topoClassify.linkOrphaned");
      default:
        return status || "—";
    }
  };

  const [regex, setRegex] = useState("");
  const [matchField, setMatchField] = useState<"name" | "ip" | "name_ip">("name");
  const [matchedIds, setMatchedIds] = useState<string[]>([]);
  const [matchTotal, setMatchTotal] = useState(0);
  const [assignRole, setAssignRole] = useState("core");
  const [assignRegion, setAssignRegion] = useState("");
  const [assignWhat, setAssignWhat] = useState<"role" | "region" | "both">("role");

  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [editingId, setEditingId] = useState("");
  const [editRole, setEditRole] = useState("");
  const [editRegion, setEditRegion] = useState("");

  const [sliceFolderId, setSliceFolderId] = useState("");
  const [sliceTemplate, setSliceTemplate] = useState<"core_only" | "core_agg" | "agg_access">(
    "core_agg",
  );
  const [slicePreview, setSlicePreview] = useState<SliceGenerateResult | null>(null);
  const [seedPhysical, setSeedPhysical] = useState(true);
  const [showSlices, setShowSlices] = useState(false);

  const treeQuery = useQuery({
    queryKey: queryKeys.topologyTree,
    queryFn: fetchTopologyTree,
  });
  const regions = useMemo(() => flattenRegions(treeQuery.data?.root), [treeQuery.data?.root]);
  const regionName = useMemo(() => {
    const m: Record<string, string> = {};
    for (const r of regions) m[r.id] = r.name;
    return m;
  }, [regions]);

  const listQuery = useQuery({
    queryKey: queryKeys.fabricNodeInventory(
      keyword,
      filterRole,
      filterRegion,
      unmatched,
      linkStatus,
      page,
    ),
    queryFn: () =>
      fetchFabricNodes({
        keyword,
        role: filterRole,
        regionFolderId: filterRegion,
        unmatched,
        linkStatus,
        page,
        pageSize: PAGE_SIZE,
      }),
  });

  const invalidateList = async () => {
    await queryClient.invalidateQueries({ queryKey: ["fabricNodeInventory"] });
  };

  const matchMut = useMutation({
    mutationFn: () =>
      matchFabricNodes({ pattern: regex, match_field: matchField, sample_limit: 80 }),
    onSuccess: (out) => {
      setMatchedIds(out.fabric_node_ids || []);
      setMatchTotal(out.total_matched || 0);
      const next: Record<string, boolean> = {};
      for (const id of out.fabric_node_ids || []) next[id] = true;
      setSelected(next);
      showOk(t("topoClassify.matchOk").replace("{{count}}", String(out.total_matched)));
    },
    onError: (err) => showError(String(err)),
  });

  const bulkMut = useMutation({
    mutationFn: () => {
      const ids = Object.keys(selected).filter((k) => selected[k]);
      const body: Parameters<typeof bulkTagFabricNodes>[0] = {
        fabric_node_ids: ids.length ? ids : matchedIds,
        dry_run: false,
      };
      if (assignWhat === "role" || assignWhat === "both") body.role = assignRole;
      if (assignWhat === "region" || assignWhat === "both") {
        body.region_folder_id = assignRegion || "";
      }
      return bulkTagFabricNodes(body);
    },
    onSuccess: async (out) => {
      showOk(t("topoClassify.bulkOk").replace("{{count}}", String(out.updated)));
      setMatchedIds([]);
      setMatchTotal(0);
      setSelected({});
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  const patchMut = useMutation({
    mutationFn: () =>
      patchFabricNodeTags(editingId, {
        role: editRole,
        region_folder_id: editRegion || "",
      }),
    onSuccess: async () => {
      showOk(t("topoClassify.rowSaved"));
      setEditingId("");
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  const deleteOneMut = useMutation({
    mutationFn: (id: string) => deleteFabricNode(id),
    onSuccess: async (out) => {
      showOk(t("topoClassify.deleteOk").replace("{{count}}", String(out.deleted)));
      setSelected({});
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  const deleteBulkMut = useMutation({
    mutationFn: (ids: string[]) => deleteFabricNodes(ids),
    onSuccess: async (out) => {
      showOk(t("topoClassify.deleteOk").replace("{{count}}", String(out.deleted)));
      setSelected({});
      await invalidateList();
    },
    onError: (err) => showError(String(err)),
  });

  const slicePreviewMut = useMutation({
    mutationFn: () =>
      generateTopologySlices({
        folder_id: sliceFolderId,
        template: sliceTemplate,
        dry_run: true,
        max_nodes: 300,
        seed_physical_cores: seedPhysical,
      }),
    onSuccess: (out) => setSlicePreview(out),
    onError: (err) => showError(String(err)),
  });

  const sliceApplyMut = useMutation({
    mutationFn: () =>
      generateTopologySlices({
        folder_id: sliceFolderId,
        template: sliceTemplate,
        dry_run: false,
        max_nodes: 300,
        seed_physical_cores: seedPhysical,
      }),
    onSuccess: async (out) => {
      showOk(t("topoClassify.sliceOk").replace("{{count}}", String(out.created_view_ids.length)));
      setSlicePreview(out);
      await queryClient.invalidateQueries({ queryKey: queryKeys.topologyTree });
    },
    onError: (err) => showError(String(err)),
  });

  const items = listQuery.data?.items || [];
  const deletableSelected = items
    .filter((n) => selected[n.id] && isFabricNodeDeletable(n))
    .map((n) => n.id);
  const total = listQuery.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const selectedCount = Object.values(selected).filter(Boolean).length;
  const highlight = useMemo(() => new Set(matchedIds), [matchedIds]);

  const startEdit = (n: FabricNodeSearchHit) => {
    setEditingId(n.id);
    setEditRole(n.role || "");
    setEditRegion(n.region_folder_id || "");
  };

  const toggleRow = (id: string) => {
    setSelected((s) => ({ ...s, [id]: !s[id] }));
  };

  const togglePage = (on: boolean) => {
    setSelected((s) => {
      const next = { ...s };
      for (const n of items) next[n.id] = on;
      return next;
    });
  };

  return (
    <div className="panel topo-classify">
      <div className="panel__toolbar">
        <h2>{t("topoClassify.title")}</h2>
        <div className="btn-row">
          <button
            type="button"
            className="btn-primary"
            onClick={() => openOrFocusModule({ moduleId: "topology", path: "/topology" })}
          >
            {t("topoClassify.openTopo")}
          </button>
        </div>
      </div>

      <section className="topo-classify__section topo-classify__section--first">
        <h3>{t("topoClassify.inventory")}</h3>
        <div className="topo-classify__draft">
          <input
            className="input"
            placeholder={t("topoClassify.keywordPh")}
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
          />
          <select
            className="input"
            value={filterRole}
            onChange={(e) => {
              setFilterRole(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("topoClassify.filterRoleAll")}</option>
            <option value="core">core</option>
            <option value="aggregation">aggregation</option>
            <option value="access">access</option>
            <option value="unknown">unknown</option>
          </select>
          <select
            className="input"
            value={filterRegion}
            onChange={(e) => {
              setFilterRegion(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("topoClassify.filterRegionAll")}</option>
            {regions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <select
            className="input"
            value={unmatched}
            onChange={(e) => {
              setUnmatched(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("topoClassify.filterUnmatchedOff")}</option>
            <option value="any">{t("topoClassify.kindAny")}</option>
            <option value="role">{t("topoClassify.kindRole")}</option>
            <option value="region">{t("topoClassify.kindRegion")}</option>
          </select>
          <select
            className="input"
            value={linkStatus}
            onChange={(e) => {
              setLinkStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("topoClassify.filterLinkAll")}</option>
            <option value="linked">{t("topoClassify.filterLinkLinked")}</option>
            <option value="orphaned">{t("topoClassify.filterLinkOrphaned")}</option>
          </select>
        </div>

        <div className="topo-classify__regex-bar">
          <strong>{t("topoClassify.regexTitle")}</strong>
          <input
            className="input topo-classify__regex-input"
            placeholder={t("topoClassify.patternPh")}
            value={regex}
            onChange={(e) => setRegex(e.target.value)}
          />
          <select
            className="input"
            value={matchField}
            onChange={(e) => setMatchField(e.target.value as "name" | "ip" | "name_ip")}
          >
            <option value="name">{t("topoClassify.fieldName")}</option>
            <option value="ip">IP</option>
            <option value="name_ip">{t("topoClassify.fieldNameIp")}</option>
          </select>
          <button
            type="button"
            className="btn btn--sm"
            disabled={!regex.trim() || matchMut.isPending}
            onClick={() => matchMut.mutate()}
          >
            {t("topoClassify.findMatches")}
          </button>
          {matchTotal > 0 ? (
            <span className="panel__hint">
              {t("topoClassify.matchHint").replace("{{count}}", String(matchTotal))}
            </span>
          ) : null}
        </div>

        <div className="topo-classify__assign-bar">
          <select
            className="input"
            value={assignWhat}
            onChange={(e) => setAssignWhat(e.target.value as "role" | "region" | "both")}
          >
            <option value="role">{t("topoClassify.assignRole")}</option>
            <option value="region">{t("topoClassify.assignRegion")}</option>
            <option value="both">{t("topoClassify.assignBoth")}</option>
          </select>
          {assignWhat !== "region" ? (
            <select
              className="input"
              value={assignRole}
              onChange={(e) => setAssignRole(e.target.value)}
            >
              <option value="core">core</option>
              <option value="aggregation">aggregation</option>
              <option value="access">access</option>
              <option value="unknown">unknown</option>
            </select>
          ) : null}
          {assignWhat !== "role" ? (
            <select
              className="input"
              value={assignRegion}
              onChange={(e) => setAssignRegion(e.target.value)}
            >
              <option value="">{t("topoClassify.clearRegion")}</option>
              {regions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          ) : null}
          <button
            type="button"
            className="btn btn--sm"
            disabled={bulkMut.isPending || (selectedCount === 0 && matchedIds.length === 0)}
            onClick={() => {
              const n = selectedCount || matchedIds.length;
              const msg = t("topoClassify.bulkConfirm").replace("{{count}}", String(n));
              if (window.confirm(msg)) bulkMut.mutate();
            }}
          >
            {t("topoClassify.confirmAssign")}
          </button>
          <button
            type="button"
            className="btn btn--sm btn--danger"
            disabled={deleteBulkMut.isPending || deletableSelected.length === 0}
            title={t("topoClassify.deleteHint")}
            onClick={() => {
              const msg = t("topoClassify.deleteConfirm").replace(
                "{{count}}",
                String(deletableSelected.length),
              );
              if (window.confirm(msg)) deleteBulkMut.mutate(deletableSelected);
            }}
          >
            {t("topoClassify.deleteSelected").replace(
              "{{count}}",
              String(deletableSelected.length),
            )}
          </button>
          <span className="topo-classify__help" tabIndex={0} aria-label={t("topoClassify.deleteHint")}>
            ?
            <span className="topo-classify__help-tip" role="tooltip">
              {t("topoClassify.deleteHint")}
            </span>
          </span>
        </div>

        <table className="data-table topo-classify__table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={items.length > 0 && items.every((n) => selected[n.id])}
                  onChange={(e) => togglePage(e.target.checked)}
                  aria-label={t("topoClassify.selectPage")}
                />
              </th>
              <th>{t("topoClassify.colNe")}</th>
              <th>IP</th>
              <th>{t("topoClassify.colLink")}</th>
              <th>{t("topoClassify.colRole")}</th>
              <th>{t("topoClassify.colRegion")}</th>
              <th>{t("topoClassify.colActions")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((n) => {
              const editing = editingId === n.id;
              return (
                <tr
                  key={n.id}
                  className={highlight.has(n.id) || selected[n.id] ? "is-match" : undefined}
                >
                  <td>
                    <input
                      type="checkbox"
                      checked={Boolean(selected[n.id])}
                      onChange={() => toggleRow(n.id)}
                    />
                  </td>
                  <td>{n.name || n.id}</td>
                  <td>{n.ip}</td>
                  <td>
                    {linkLabel(n.link_status)}
                    {n.managed_source ? ` · ${n.managed_source}` : ""}
                  </td>
                  <td>
                    {editing ? (
                      <select
                        className="input"
                        value={editRole}
                        onChange={(e) => setEditRole(e.target.value)}
                      >
                        <option value="">-</option>
                        <option value="core">core</option>
                        <option value="aggregation">aggregation</option>
                        <option value="access">access</option>
                        <option value="unknown">unknown</option>
                      </select>
                    ) : (
                      n.role || "-"
                    )}
                  </td>
                  <td>
                    {editing ? (
                      <select
                        className="input"
                        value={editRegion}
                        onChange={(e) => setEditRegion(e.target.value)}
                      >
                        <option value="">-</option>
                        {regions.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      (n.region_folder_id && regionName[n.region_folder_id]) ||
                      n.region_folder_id ||
                      "-"
                    )}
                  </td>
                  <td>
                    {editing ? (
                      <div className="topo-classify__row-actions">
                        <button
                          type="button"
                          className="btn btn--sm"
                          disabled={patchMut.isPending}
                          onClick={() => patchMut.mutate()}
                        >
                          {t("topoClassify.save")}
                        </button>
                        <button
                          type="button"
                          className="btn btn--sm btn--ghost"
                          onClick={() => setEditingId("")}
                        >
                          {t("topoClassify.cancel")}
                        </button>
                      </div>
                    ) : (
                      <div className="topo-classify__row-actions">
                        <button
                          type="button"
                          className="btn btn--sm btn--ghost"
                          onClick={() => startEdit(n)}
                        >
                          {t("topoClassify.edit")}
                        </button>
                        <button
                          type="button"
                          className="btn btn--sm btn--danger"
                          disabled={deleteOneMut.isPending || !isFabricNodeDeletable(n)}
                          title={
                            isFabricNodeDeletable(n)
                              ? t("topoClassify.deleteHint")
                              : t("topoClassify.deleteBlocked")
                          }
                          onClick={() => {
                            if (!isFabricNodeDeletable(n)) return;
                            const msg = t("topoClassify.deleteConfirm").replace(
                              "{{count}}",
                              "1",
                            );
                            if (window.confirm(msg)) deleteOneMut.mutate(n.id);
                          }}
                        >
                          {t("topoClassify.delete")}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
            {items.length === 0 ? (
              <tr>
                <td colSpan={7} className="panel__hint">
                  {listQuery.isLoading ? t("topoClassify.loading") : t("topoClassify.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        <div className="topo-classify__pager">
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            {t("common.prevPage")}
          </button>
          <span>
            {t("common.pagerMeta")
              .replace("{{total}}", String(total))
              .replace("{{page}}", String(page))
              .replace("{{pages}}", String(pages))}
          </span>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            disabled={page >= pages}
            onClick={() => setPage((p) => p + 1)}
          >
            {t("common.nextPage")}
          </button>
        </div>
      </section>

      <section className="topo-classify__section">
        <button
          type="button"
          className="btn btn--sm btn--ghost"
          onClick={() => setShowSlices((v) => !v)}
        >
          {showSlices ? t("topoClassify.hideSlices") : t("topoClassify.showSlices")}
        </button>
        {showSlices ? (
          <>
            <h3>{t("topoClassify.slices")}</h3>
            <div className="topo-classify__draft">
              <select
                className="input"
                value={sliceFolderId}
                onChange={(e) => {
                  setSliceFolderId(e.target.value);
                  setSlicePreview(null);
                }}
              >
                <option value="">{t("topoClassify.pickRegion")}</option>
                {regions.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
              <select
                className="input"
                value={sliceTemplate}
                onChange={(e) =>
                  setSliceTemplate(e.target.value as "core_only" | "core_agg" | "agg_access")
                }
              >
                <option value="core_only">{t("topoClassify.tplCore")}</option>
                <option value="core_agg">{t("topoClassify.tplCoreAgg")}</option>
                <option value="agg_access">{t("topoClassify.tplAggAcc")}</option>
              </select>
              <label className="topo-classify__check">
                <input
                  type="checkbox"
                  checked={seedPhysical}
                  onChange={(e) => setSeedPhysical(e.target.checked)}
                />
                {t("topoClassify.seedPhysical")}
              </label>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={!sliceFolderId || slicePreviewMut.isPending}
                onClick={() => slicePreviewMut.mutate()}
              >
                {t("topoClassify.slicePreview")}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={!sliceFolderId || sliceApplyMut.isPending}
                onClick={() => {
                  if (window.confirm(t("topoClassify.sliceConfirm"))) sliceApplyMut.mutate();
                }}
              >
                {t("topoClassify.sliceApply")}
              </button>
            </div>
            {slicePreview ? (
              <div className="topo-classify__preview">
                <p className="panel__hint">
                  {t("topoClassify.sliceStats")
                    .replace("{{maps}}", String(slicePreview.map_count))
                    .replace("{{overlap}}", String(slicePreview.overlap_node_count))}
                </p>
                <ul className="topo-classify__slice-list">
                  {slicePreview.maps.map((m) => (
                    <li key={m.name}>
                      <strong>{m.name}</strong>
                      {" · "}
                      {m.role}
                      {" · "}
                      {t("topoClassify.nodeCount").replace("{{count}}", String(m.node_count))}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
      </section>
    </div>
  );
}
