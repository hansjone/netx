import { Button, Input } from "@heroui/react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ListPager } from "../../components/ListPager";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { queryKeys } from "../../constants/queryKeys";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import { fetchCliTargets, formatErr } from "../../services/api";
import type { CliTargetItem } from "../../types";
import { downloadCsv, fetchAllPages } from "../../utils/csvExport";
import { pageCount } from "../../utils/display";
import { openNewModuleWindow } from "../../utils/moduleWindows";
import { connectChipColor, NmStatusChip, sourceChipColor } from "./nmChips";

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
  const [pageSize, setPageSize] = useState(10);
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
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("networkDevices.title")}</h2>
        <div className="btn-row">
          <Button
            size="sm"
            variant="secondary"
            isDisabled={exporting || total === 0}
            onPress={() => void exportCsv()}
          >
            {exporting ? t("common.exporting") : t("common.exportCsv")}
          </Button>
        </div>
      </div>

      <div className="pt-list">
        <div className="filter-inline">
          <Input
            value={keyword}
            placeholder={t("networkDevices.keywordPh")}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
          />
          <FieldSelect
            value={source}
            onChange={(e) => {
              setSource(e.target.value as "all" | "managed" | "ume");
              setPage(1);
            }}
          >
            <option value="all">{t("networkDevices.allSource")}</option>
            <option value="managed">managed</option>
            <option value="ume">ume</option>
          </FieldSelect>
          <Button
            size="sm"
            variant="tertiary"
            isDisabled={!hasFilters}
            onPress={() => {
              setKeyword("");
              setSource("all");
              setPage(1);
            }}
          >
            {t("common.clearFilters")}
          </Button>
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
                    <td>
                      <NmStatusChip color={sourceChipColor(row.source)}>{row.source}</NmStatusChip>
                    </td>
                    <td className="pt-list-task-name">{row.name || row.id}</td>
                    <td className="pt-list-num">{row.ip_address || "—"}</td>
                    <td>{row.vendor || "—"}</td>
                    <td>{row.device_type || row.ne_type || "—"}</td>
                    <td>
                      <NmStatusChip color={connectChipColor(row.connect_status)}>
                        {row.connect_status || "—"}
                      </NmStatusChip>
                    </td>
                    <td>
                      <div className="btn-row pt-list-actions table-actions">
                        <Button size="sm" variant="ghost" onPress={() => openWebcrt(row)}>
                          WebCRT
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onPress={() =>
                            openNewModuleWindow({
                              moduleId: "network",
                              path: `/network/configs?q=${encodeURIComponent(row.name || row.ip_address || row.id)}`,
                            })
                          }
                        >
                          {t("network.nav.configs")}
                        </Button>
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
          pageSizeOptions={[10, 20, 50, 100, 200]}
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
