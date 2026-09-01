import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ListPager } from "../../components/ListPager";
import { queryKeys } from "../../constants/queryKeys";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import { fetchUmeCurrentAlarms, formatErr } from "../../services/api";
import type { UmeAlarmItem } from "../../types";
import { downloadCsv, fetchAllPages } from "../../utils/csvExport";
import { pageCount } from "../../utils/display";
import { formatSystemTime } from "../../utils/time";

function severityClass(sev: string | null | undefined): string {
  const s = String(sev || "").trim().toLowerCase();
  if (s.includes("critical")) return "pt-list-status--critical";
  if (s.includes("major")) return "pt-list-status--major";
  if (s.includes("minor")) return "pt-list-status--minor";
  if (s.includes("warning") || s.includes("warn")) return "pt-list-status--warning";
  if (s.includes("info") || s.includes("indeterminate")) return "pt-list-status--info";
  return "pt-list-status--unknown";
}

/** Current UME alarms query view for Network Management. */
export function NetworkAlarmsPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [searchParams] = useSearchParams();
  const [curSeverity, setCurSeverity] = useState("");
  const [curCleared, setCurCleared] = useState("");
  const [curHostName, setCurHostName] = useState("");
  const [curKeyword, setCurKeyword] = useState("");
  const [curPage, setCurPage] = useState(1);
  const [curPageSize, setCurPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);

  // Deep-link from topology (and similar): ?host=&cleared=&severity=&keyword=
  useEffect(() => {
    const host = searchParams.get("host");
    const cleared = searchParams.get("cleared");
    const severity = searchParams.get("severity");
    const keyword = searchParams.get("keyword");
    let touched = false;
    if (host != null) {
      setCurHostName(host);
      touched = true;
    }
    if (cleared != null) {
      setCurCleared(cleared);
      touched = true;
    }
    if (severity != null) {
      setCurSeverity(severity);
      touched = true;
    }
    if (keyword != null) {
      setCurKeyword(keyword);
      touched = true;
    }
    if (touched) setCurPage(1);
  }, [searchParams]);

  const debouncedKeyword = useDebouncedValue(curKeyword, 300);
  const debouncedHost = useDebouncedValue(curHostName, 300);

  const currentQuery = useQuery({
    queryKey: queryKeys.umeCurrentAlarms(
      curSeverity,
      curCleared,
      debouncedHost,
      debouncedKeyword,
      curPage,
      curPageSize,
    ),
    queryFn: () =>
      fetchUmeCurrentAlarms({
        severity: curSeverity,
        isCleared: curCleared,
        hostName: debouncedHost,
        keyword: debouncedKeyword,
        page: curPage,
        pageSize: curPageSize,
      }),
    staleTime: 5000,
  });

  const items = currentQuery.data?.items || [];
  const curTotal = Number(currentQuery.data?.total || 0);
  const curPages = pageCount(curTotal, curPageSize);
  const hasFilters = Boolean(curKeyword.trim() || curHostName.trim() || curSeverity || curCleared);

  const exportCsv = async () => {
    setExporting(true);
    try {
      const rows = await fetchAllPages<UmeAlarmItem>({
        pageSize: 200,
        maxRows: 2000,
        fetchPage: (page, pageSize) =>
          fetchUmeCurrentAlarms({
            severity: curSeverity,
            isCleared: curCleared,
            hostName: debouncedHost,
            keyword: debouncedKeyword,
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
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("ume.alarms.title")}</h2>
        <div className="btn-row">
          <button type="button" disabled={exporting || curTotal === 0} onClick={() => void exportCsv()}>
            {exporting ? t("common.exporting") : t("common.exportCsv")}
          </button>
        </div>
      </div>

      <div className="pt-list">
        <div className="filter-inline">
          <input
            value={curKeyword}
            placeholder={t("ume.alarms.keywordPh")}
            onChange={(e) => {
              setCurKeyword(e.target.value);
              setCurPage(1);
            }}
          />
          <input
            value={curHostName}
            placeholder={t("ume.alarms.hostNamePh")}
            onChange={(e) => {
              setCurHostName(e.target.value);
              setCurPage(1);
            }}
          />
          <select
            value={curSeverity}
            onChange={(e) => {
              setCurSeverity(e.target.value);
              setCurPage(1);
            }}
          >
            <option value="">{t("ume.alarms.allSeverity")}</option>
            <option value="critical">critical</option>
            <option value="major">major</option>
            <option value="minor">minor</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
          </select>
          <select
            value={curCleared}
            onChange={(e) => {
              setCurCleared(e.target.value);
              setCurPage(1);
            }}
          >
            <option value="">{t("ume.alarms.clearedAll")}</option>
            <option value="false">{t("ume.alarms.clearedNo")}</option>
            <option value="true">{t("ume.alarms.clearedYes")}</option>
          </select>
          <button
            type="button"
            title={t("ume.alarms.clearTitle")}
            onClick={() => {
              setCurKeyword("");
              setCurHostName("");
              setCurSeverity("");
              setCurCleared("");
              setCurPage(1);
            }}
            disabled={!hasFilters}
          >
            {t("common.clearFilters")}
          </button>
        </div>

        {currentQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
        {currentQuery.isError ? <p className="error-text">{t("common.opFailed")}</p> : null}

        {!items.length && !currentQuery.isLoading ? (
          <div className="pt-list-empty">
            <p>{t("common.empty")}</p>
          </div>
        ) : (
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("ume.alarms.col.time")}</th>
                  <th>{t("ume.alarms.col.severity")}</th>
                  <th>{t("ume.alarms.col.neId")}</th>
                  <th>{t("ume.alarms.col.hostName")}</th>
                  <th>{t("ume.alarms.col.neType")}</th>
                  <th>{t("ume.alarms.col.cause")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((x) => (
                  <tr key={x.alarm_key}>
                    <td className="pt-list-time">{formatSystemTime(x.time_created)}</td>
                    <td>
                      <span className={`pt-list-status ${severityClass(x.perceived_severity)}`}>
                        {x.perceived_severity || "—"}
                      </span>
                    </td>
                    <td className="pt-list-num">{x.ne_id}</td>
                    <td>
                      {(x.host_name || "").trim() ? (
                        <button
                          className="link-btn pt-list-task-name"
                          type="button"
                          onClick={() => {
                            setCurHostName(x.host_name || "");
                            setCurKeyword("");
                            setCurPage(1);
                          }}
                          title={t("ume.alarms.filterByHost")}
                        >
                          {x.host_name}
                        </button>
                      ) : (
                        <span className="muted" title={t("ume.alarms.noHostName")}>
                          {t("common.empty")}
                        </span>
                      )}
                    </td>
                    <td>{x.ne_type ?? ""}</td>
                    <td>{x.native_probable_cause}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <ListPager
          page={curPage}
          pages={curPages}
          total={curTotal}
          pageSize={curPageSize}
          pageSizeOptions={[50, 100, 200, 500]}
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
