import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ListPager } from "../../components/ListPager";
import { queryKeys } from "../../constants/queryKeys";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import { fetchCliTargets, formatErr } from "../../services/api";
import type { CliTargetItem } from "../../types";
import { downloadCsv, fetchAllPages } from "../../utils/csvExport";
import { pageCount } from "../../utils/display";
import { openNewModuleWindow } from "../../utils/moduleWindows";

function connectStatusClass(status: string | null | undefined): string {
  const s = String(status || "").trim().toLowerCase();
  if (!s || s === "-" || s === "unknown") return "pt-list-status--unknown";
  if (s.includes("fail") || s.includes("down") || s.includes("error")) return "pt-list-status--down";
  if (s.includes("ok") || s.includes("up") || s.includes("connected") || s.includes("success")) {
    return "pt-list-status--ok";
  }
  return "pt-list-status--other";
}

function openWebcrt(row: CliTargetItem) {
  const path =
    row.source === "ume"
      ? `/webcrt?ne_id=${encodeURIComponent(row.id)}&source=ume`
      : `/webcrt?ne_id=${encodeURIComponent(row.id)}`;
  openNewModuleWindow({ moduleId: "webcrt", path });
}

/** Inventory of managed + UME NEs for Network Management. */
export function NetworkDevicesPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [keyword, setKeyword] = useState("");
  const [source, setSource] = useState<"all" | "managed" | "ume">("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);

  const debouncedKeyword = useDebouncedValue(keyword, 300);

  const listQuery = useQuery({
    queryKey: queryKeys.cliTargets(`${source}:${debouncedKeyword}`, page, pageSize),
    queryFn: () => fetchCliTargets({ source, keyword: debouncedKeyword, page, pageSize }),
    staleTime: 5000,
  });

  const items = listQuery.data?.items ?? [];
  const total = Number(listQuery.data?.total || 0);
  const pages = pageCount(total, pageSize);
  const hasFilters = Boolean(keyword.trim() || source !== "all");

  const exportCsv = async () => {
    setExporting(true);
    try {
      const rows = await fetchAllPages<CliTargetItem>({
        pageSize: 200,
        maxRows: 2000,
        fetchPage: (p, ps) =>
          fetchCliTargets({ source, keyword: debouncedKeyword, page: p, pageSize: ps }),
      });
      downloadCsv(`${t("networkDevices.exportName")}-${new Date().toISOString().slice(0, 10)}.csv`, rows, [
        { key: "source", header: t("networkDevices.col.source") },
        { key: "name", header: t("networkDevices.col.name"), value: (r) => r.name || r.id },
        { key: "ip_address", header: "IP" },
        { key: "vendor", header: t("networkDevices.col.vendor") },
        {
          key: "device_type",
          header: t("networkDevices.col.deviceType"),
          value: (r) => r.device_type || r.ne_type || "",
        },
        { key: "connect_status", header: t("networkDevices.col.connect") },
        { key: "id", header: "ID" },
      ]);
      if (rows.length < total) {
        showOk(t("common.exportTruncated", { count: String(rows.length), total: String(total) }));
      } else {
        showOk(t("common.exportOk", { count: String(rows.length) }));
      }
    } catch (err) {
      showError(t("common.exportFailed") + ": " + formatErr(err));
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("networkDevices.title")}</h2>
        <div className="btn-row">
          <button type="button" disabled={exporting || total === 0} onClick={() => void exportCsv()}>
            {exporting ? t("common.exporting") : t("common.exportCsv")}
          </button>
        </div>
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
        {listQuery.isError ? <p className="error-text">{t("common.opFailed")}</p> : null}

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
                  <th>{t("networkDevices.col.actions")}</th>
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
                    <td>
                      <div className="btn-row pt-list-actions table-actions">
                        <button type="button" onClick={() => openWebcrt(row)}>
                          WebCRT
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            openNewModuleWindow({
                              moduleId: "network",
                              path: `/network/configs?q=${encodeURIComponent(row.name || row.ip_address || row.id)}`,
                            })
                          }
                        >
                          {t("network.nav.configs")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <ListPager
          page={page}
          pages={pages}
          total={total}
          pageSize={pageSize}
          pageSizeOptions={[20, 50, 100, 200]}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          disabled={listQuery.isLoading}
        />
      </div>
    </section>
  );
}
