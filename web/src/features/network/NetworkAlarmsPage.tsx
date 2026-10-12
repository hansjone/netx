import { Button } from "@heroui/react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ListPager } from "../../components/ListPager";
import { useTableColumnFilters } from "../../hooks/useTableColumnFilters";
import { queryKeys } from "../../constants/queryKeys";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import { fetchUmeCurrentAlarms, formatErr } from "../../services/api";
import type { UmeAlarmItem } from "../../types";
import { downloadCsv, fetchAllPages } from "../../utils/csvExport";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";
import { NmStatusChip, severityChipColor } from "../../components/NmChips";

/** Current UME alarms query view for Network Management. */
export function NetworkAlarmsPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const curSeverity = searchParams.get("severity") || "";
  const curCleared = searchParams.get("cleared") || "";
  const curHostName = searchParams.get("host") || "";
  const curKeyword = searchParams.get("keyword") || "";
  const [curPage, setCurPage] = useState(1);
  const [curPageSize, setCurPageSize] = useState(10);
  const [exporting, setExporting] = useState(false);
  const columns = useTableColumnFilters("/v1/ume/alarms", () => setCurPage(1), {
    severity: curSeverity, is_cleared: curCleared, host_name: curHostName, keyword: curKeyword,
  });

  const currentQuery = useQuery({
    queryKey: [...queryKeys.umeCurrentAlarms(
      curSeverity,
      curCleared,
      curHostName,
      curKeyword,
      curPage,
      curPageSize,
    ), columns.key],
    queryFn: () =>
      fetchUmeCurrentAlarms({
        columnFilters: columns.filters,
        severity: curSeverity,
        isCleared: curCleared,
        hostName: curHostName,
        keyword: curKeyword,
        page: curPage,
        pageSize: curPageSize,
      }),
    staleTime: 5000,
  });

  const items = currentQuery.data?.items || [];
  const curTotal = Number(currentQuery.data?.total || 0);
  const curPages = pageCount(curTotal, curPageSize);
  const hasFilters = columns.active || Boolean(curKeyword.trim() || curHostName.trim() || curSeverity || curCleared);
  if (currentQuery.data && curPage > curPages) setCurPage(curPages);

  const exportCsv = async () => {
    setExporting(true);
    try {
      const rows = await fetchAllPages<UmeAlarmItem>({
        pageSize: 200,
        maxRows: 2000,
        fetchPage: (page, pageSize) =>
          fetchUmeCurrentAlarms({
            columnFilters: columns.filters,
            severity: curSeverity,
            isCleared: curCleared,
            hostName: curHostName,
            keyword: curKeyword,
            page,
            pageSize,
          }),
      });
      downloadCsv(`ume-alarms-${new Date().toISOString().slice(0, 10)}.csv`, rows, [
        { key: "time_created", header: t("ume.alarms.col.time"), value: (r) => formatSystemTime(r.time_created) },
        { key: "perceived_severity", header: t("ume.alarms.col.severity") },
        { key: "ne_id", header: t("ume.alarms.col.neId") },
        { key: "host_name", header: t("ume.alarms.col.hostName") },
        { key: "ne_type", header: t("ume.alarms.col.neType") },
        { key: "native_probable_cause", header: t("ume.alarms.col.cause") },
        { key: "is_cleared", header: "is_cleared" },
        { key: "alarm_key", header: "alarm_key" },
      ]);
      if (rows.length < curTotal) {
        showOk(
          t("common.exportTruncated", { count: String(rows.length), total: String(curTotal) }),
        );
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
        <h2>{t("ume.alarms.title")}</h2>
        <div className="btn-row">
          {curHostName || curKeyword ? <span className="muted">{curHostName || curKeyword}</span> : null}
          <Button size="sm" variant="tertiary" isDisabled={!hasFilters} onPress={() => { columns.reset(); setSearchParams({}); }}>
            {t("common.clearFilters")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            isDisabled={exporting || curTotal === 0 || currentQuery.isFetching}
            onPress={() => void exportCsv()}
          >
            {exporting ? t("common.exporting") : t("common.exportCsv")}
          </Button>
        </div>
      </div>

      <div className="pt-list">
        {currentQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
        {currentQuery.isError ? <p className="error-text">{t("common.opFailed")}</p> : null}

          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{columns.column("time", t("ume.alarms.col.time"), formatSystemTime)}</th>
                  <th>{columns.column("severity", t("ume.alarms.col.severity"))}</th>
                  <th>{columns.column("neId", t("ume.alarms.col.neId"))}</th>
                  <th>{columns.column("hostName", t("ume.alarms.col.hostName"))}</th>
                  <th>{columns.column("neType", t("ume.alarms.col.neType"))}</th>
                  <th>{columns.column("cause", t("ume.alarms.col.cause"))}</th>
                  <th>{columns.column("cleared", t("ume.alarms.col.cleared"), (value) => t(value === "true" ? "ume.alarms.clearedYes" : "ume.alarms.clearedNo"))}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((x) => (
                  <tr key={x.alarm_key}>
                    <td className="pt-list-time">{formatSystemTime(x.time_created)}</td>
                    <td>
                      <NmStatusChip color={severityChipColor(x.perceived_severity)}>
                        {x.perceived_severity || "—"}
                      </NmStatusChip>
                    </td>
                    <td className="pt-list-num">{x.ne_id}</td>
                    <td>
                      {(x.host_name || "").trim() ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="link-btn pt-list-task-name"
                          onPress={() => {
                            columns.setFilter("hostName", { mode: "include", values: [x.host_name || ""] });
                          }}
                          aria-label={t("ume.alarms.filterByHost")}
                        >
                          {x.host_name}
                        </Button>
                      ) : (
                        <span className="muted" title={t("ume.alarms.noHostName")}>
                          {t("common.empty")}
                        </span>
                      )}
                    </td>
                    <td>{x.ne_type ?? ""}</td>
                    <td>{x.native_probable_cause}</td>
                    <td>{t(x.is_cleared === "true" ? "ume.alarms.clearedYes" : "ume.alarms.clearedNo")}</td>
                  </tr>
                ))}
                {!items.length && !currentQuery.isFetching && !currentQuery.isError ? (
                  <tr><td colSpan={7} className="pt-list-empty">{t("common.empty")}</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>

        <ListPager
          page={curPage}
          pages={curPages}
          total={curTotal}
          pageSize={curPageSize}
          pageSizeOptions={[10, 20, 50, 100, 200]}
          onPageChange={setCurPage}
          onPageSizeChange={(size) => {
            setCurPageSize(size);
            setCurPage(1);
          }}
          disabled={currentQuery.isLoading}
        />
      </div>
    </section>
  );
}
