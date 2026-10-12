import { Button } from "@heroui/react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ListPager } from "../../components/ListPager";
import { ColumnFilterMenu } from "../../components/ui/ColumnFilterMenu";
import { queryKeys } from "../../constants/queryKeys";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import { fetchCliTargetFilterOptions, fetchCliTargets, formatErr } from "../../services/api";
import type { CliTargetColumnFilter, CliTargetFilterField, CliTargetItem } from "../../types";
import { downloadCsv, fetchAllPages } from "../../utils/csvExport";
import { pageCount } from "../../utils/display";
import { openNewModuleWindow } from "../../utils/moduleWindows";
import { connectChipColor, NmStatusChip, sourceChipColor } from "../../components/NmChips";

function openWebcrt(row: CliTargetItem) {
  const path =
    row.source === "ume"
      ? `/webcrt?ne_id=${encodeURIComponent(row.id)}&source=ume`
      : `/webcrt?ne_id=${encodeURIComponent(row.id)}`;
  openNewModuleWindow({ moduleId: "webcrt", path });
}

type DeviceColumnFilters = Record<CliTargetFilterField, CliTargetColumnFilter>;

const EMPTY_COLUMN_FILTERS: DeviceColumnFilters = {
  source: null,
  name: null,
  ip: null,
  vendor: null,
  deviceType: null,
  connectStatus: null,
};

/** Inventory of managed + UME NEs for Network Management. */
export function NetworkDevicesPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [columnFilters, setColumnFilters] = useState<DeviceColumnFilters>(EMPTY_COLUMN_FILTERS);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [exporting, setExporting] = useState(false);


  const listQuery = useQuery({
    queryKey: queryKeys.cliTargets(
      JSON.stringify(columnFilters),
      page,
      pageSize,
    ),
    queryFn: () =>
      fetchCliTargets({
        source: "inventory",
        columnFilters,
        page,
        pageSize,
      }),
    staleTime: 5000,
  });

  const items = listQuery.data?.items ?? [];
  const total = Number(listQuery.data?.total || 0);
  const pages = pageCount(total, pageSize);
  const hasFilters =
    Object.values(columnFilters).some(Boolean);

  if (listQuery.data && page > pages) setPage(pages);

  const exportCsv = async () => {
    setExporting(true);
    try {
      const rows = await fetchAllPages<CliTargetItem>({
        pageSize: 200,
        maxRows: 2000,
        fetchPage: (p, ps) =>
          fetchCliTargets({
            source: "inventory",
            columnFilters,
            page: p,
            pageSize: ps,
          }),
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
          <Button size="sm" variant="tertiary" isDisabled={!hasFilters} onPress={() => { setColumnFilters(EMPTY_COLUMN_FILTERS); setPage(1); }}>
            {t("common.clearFilters")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            isDisabled={exporting || total === 0 || listQuery.isFetching}
            onPress={() => void exportCsv()}
          >
            {exporting ? t("common.exporting") : t("common.exportCsv")}
          </Button>
        </div>
      </div>

      <div className="pt-list">
        {listQuery.isFetching ? <p className="muted">{t("common.refreshing")}</p> : null}
        {listQuery.isError ? <p className="error-text">{t("common.opFailed")}</p> : null}

        <div className="pt-list-table-wrap">
          <table className="data-table pt-list-table">
            <thead>
              <tr>
                {([
                  ["source", t("networkDevices.col.source")],
                  ["name", t("networkDevices.col.name")],
                  ["ip", "IP"],
                  ["vendor", t("networkDevices.col.vendor")],
                  ["deviceType", t("networkDevices.col.deviceType")],
                  ["connectStatus", t("networkDevices.col.connect")],
                ] as const).map(([key, label]) => (
                  <th key={key}>
                    <span className="ne-column-heading">
                      {label}
                      <ColumnFilterMenu
                        label={label}
                        value={columnFilters[key]}
                        queryKey={["cliTargetFilterOptions", key, "inventory", ""]}
                        loadOptions={() => fetchCliTargetFilterOptions({ field: key, source: "inventory" }).then((result) => result.options)}
                        onApply={(value) => {
                          setColumnFilters((current) => ({ ...current, [key]: value }));
                          setPage(1);
                        }}
                      />
                    </span>
                  </th>
                ))}
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
              {!items.length && !listQuery.isFetching && !listQuery.isError ? (
                <tr>
                  <td colSpan={7}>
                    <div className="pt-list-empty">
                      <p>{t(hasFilters ? "networkDevices.emptyFiltered" : "networkDevices.empty")}</p>
                    </div>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          </div>

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
