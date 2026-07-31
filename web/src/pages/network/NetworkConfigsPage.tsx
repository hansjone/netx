import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchNeConfigSnapshotDetail, fetchNeConfigSnapshots } from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";

function fmtBytes(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function NetworkConfigsPage() {
  const { t } = useI18n();
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [source, setSource] = useState("");
  const [vendor, setVendor] = useState("");
  const [selected, setSelected] = useState<{ source: string; id: string } | null>(null);
  const [tab, setTab] = useState<"primary" | "alt">("primary");

  const listQuery = useQuery({
    queryKey: queryKeys.networkConfigs(page, keyword, source, vendor),
    queryFn: () =>
      fetchNeConfigSnapshots({ page, pageSize: 20, keyword, source, vendor }),
    staleTime: 5000,
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.networkConfigDetail(selected?.source || "", selected?.id || ""),
    queryFn: () => fetchNeConfigSnapshotDetail(selected!.source, selected!.id, "both"),
    enabled: Boolean(selected),
    staleTime: 10000,
  });

  const items = listQuery.data?.items ?? [];
  const total = Number(listQuery.data?.total || 0);
  const pages = pageCount(total, 20);
  const detail = detailQuery.data;
  const showAlt = Boolean(detail?.has_alt);

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("networkConfigs.title")}</h2>
      </div>

      <div className="filter-inline">
        <input
          value={keyword}
          placeholder={t("networkConfigs.keywordPh")}
          onChange={(e) => {
            setKeyword(e.target.value);
            setPage(1);
          }}
        />
        <select
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setPage(1);
          }}
        >
          <option value="">{t("networkConfigs.allSource")}</option>
          <option value="managed">managed</option>
          <option value="ume">ume</option>
        </select>
        <input
          value={vendor}
          placeholder={t("networkConfigs.vendorPh")}
          onChange={(e) => {
            setVendor(e.target.value);
            setPage(1);
          }}
        />
        <button
          type="button"
          disabled={!keyword && !source && !vendor}
          onClick={() => {
            setKeyword("");
            setSource("");
            setVendor("");
            setPage(1);
          }}
        >
          {t("common.clearFilters")}
        </button>
      </div>

      {listQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

      <table className="data-table">
        <thead>
          <tr>
            <th>{t("networkConfigs.col.name")}</th>
            <th>IP</th>
            <th>{t("networkConfigs.col.vendor")}</th>
            <th>{t("networkConfigs.col.source")}</th>
            <th>{t("networkConfigs.col.size")}</th>
            <th>{t("networkConfigs.col.collected")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={`${row.source}:${row.target_id}`}>
              <td>{row.ne_name || row.target_id}</td>
              <td>{row.ne_ip}</td>
              <td>{row.vendor || "-"}</td>
              <td>{row.source}</td>
              <td>{fmtBytes(row.plain_size)}</td>
              <td>{row.collected_at ? formatSystemTime(row.collected_at) : "-"}</td>
              <td>
                <button
                  type="button"
                  onClick={() => {
                    setSelected({ source: row.source, id: row.target_id });
                    setTab("primary");
                  }}
                >
                  {t("networkConfigs.view")}
                </button>
                <Link
                  to={`/webcrt?${row.source === "ume" ? "ume_ne_id" : "ne_id"}=${encodeURIComponent(row.target_id)}`}
                  style={{ marginLeft: 8 }}
                >
                  WebCRT
                </Link>
              </td>
            </tr>
          ))}
          {!items.length && !listQuery.isLoading ? (
            <tr>
              <td colSpan={7} className="muted">
                {t("networkConfigs.empty")}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <div className="pager">
        <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          {t("common.prevPage")}
        </button>
        <span className="muted">
          {t("common.pagerMeta", { total: String(total), page: String(page), pages: String(pages) })}
        </span>
        <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
          {t("common.nextPage")}
        </button>
      </div>

      {selected ? (
        <div className="panel" style={{ marginTop: 16 }}>
          <div className="panel__toolbar">
            <h3>
              {detail?.ne_name || selected.id}{" "}
              <span className="muted">
                ({selected.source} / {detail?.ne_ip || "-"})
              </span>
            </h3>
            <button type="button" onClick={() => setSelected(null)}>
              {t("networkConfigs.close")}
            </button>
          </div>
          {detailQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
          {showAlt ? (
            <div className="btn-row" style={{ marginBottom: 8 }}>
              <button
                type="button"
                className={tab === "primary" ? "btn-primary" : undefined}
                onClick={() => setTab("primary")}
              >
                {t("networkConfigs.tabSet")}
              </button>
              <button
                type="button"
                className={tab === "alt" ? "btn-primary" : undefined}
                onClick={() => setTab("alt")}
              >
                {t("networkConfigs.tabHier")}
              </button>
            </div>
          ) : null}
          <textarea
            readOnly
            value={tab === "alt" ? detail?.config_alt_text || "" : detail?.config_text || ""}
            style={{ width: "100%", minHeight: 420, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
          />
          <p className="muted">
            SHA-256: {tab === "alt" ? detail?.config_alt_sha256 : detail?.config_sha256} ·{" "}
            {fmtBytes(tab === "alt" ? detail?.plain_alt_size || 0 : detail?.plain_size || 0)}
          </p>
        </div>
      ) : null}
    </section>
  );
}
