import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchCliTargets } from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { pageCount } from "../../utils/display";

const PAGE_SIZE = 50;

function connectStatusClass(status: string | null | undefined): string {
  const s = String(status || "").trim().toLowerCase();
  if (!s || s === "-" || s === "unknown") return "pt-list-status--unknown";
  if (s.includes("fail") || s.includes("down") || s.includes("error")) return "pt-list-status--down";
  if (s.includes("ok") || s.includes("up") || s.includes("connected") || s.includes("success")) {
    return "pt-list-status--ok";
  }
  return "pt-list-status--other";
}

/** Read-only inventory of managed + UME NEs for Network Management. */
export function NetworkDevicesPage() {
  const { t } = useI18n();
  const [keyword, setKeyword] = useState("");
  const [source, setSource] = useState<"all" | "managed" | "ume">("all");
  const [page, setPage] = useState(1);

  const listQuery = useQuery({
    queryKey: queryKeys.cliTargets(`${source}:${keyword}`, page, PAGE_SIZE),
    queryFn: () => fetchCliTargets({ source, keyword, page, pageSize: PAGE_SIZE }),
    staleTime: 5000,
  });

  const items = listQuery.data?.items ?? [];
  const total = Number(listQuery.data?.total || 0);
  const pages = pageCount(total, PAGE_SIZE);
  const hasFilters = Boolean(keyword.trim() || source !== "all");

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("networkDevices.title")}</h2>
      </div>

      <div className="pt-list">
        <div className="filter-inline">
          <input
            value={keyword}
            placeholder={t("networkDevices.keywordPh")}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
          />
          <select
            value={source}
            onChange={(e) => {
              setSource(e.target.value as "all" | "managed" | "ume");
              setPage(1);
            }}
          >
            <option value="all">{t("networkDevices.allSource")}</option>
            <option value="managed">managed</option>
            <option value="ume">ume</option>
          </select>
          <button
            type="button"
            disabled={!hasFilters}
            onClick={() => {
              setKeyword("");
              setSource("all");
              setPage(1);
            }}
          >
            {t("common.clearFilters")}
          </button>
        </div>

        {listQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}

        {!items.length && !listQuery.isLoading ? (
          <div className="pt-list-empty">
            <p>{t("networkDevices.empty")}</p>
          </div>
        ) : (
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("networkDevices.col.source")}</th>
                  <th>{t("networkDevices.col.name")}</th>
                  <th>IP</th>
                  <th>{t("networkDevices.col.vendor")}</th>
                  <th>{t("networkDevices.col.deviceType")}</th>
                  <th>{t("networkDevices.col.connect")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={`${row.source}:${row.id}`}>
                    <td>{row.source}</td>
                    <td className="pt-list-task-name">{row.name || row.id}</td>
                    <td className="pt-list-num">{row.ip_address || "—"}</td>
                    <td>{row.vendor || "—"}</td>
                    <td>{row.device_type || row.ne_type || "—"}</td>
                    <td>
                      <span className={`pt-list-status ${connectStatusClass(row.connect_status)}`}>
                        {row.connect_status || "—"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="pager pt-list-pager">
          <span className="muted">
            {t("common.pagerMeta", {
              total: String(total),
              page: String(page),
              pages: String(pages),
            })}
          </span>
          <div className="btn-row">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              {t("common.prevPage")}
            </button>
            <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              {t("common.nextPage")}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
