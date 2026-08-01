import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchUmeCurrentAlarms } from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
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
  const [curSeverity, setCurSeverity] = useState("");
  const [curCleared, setCurCleared] = useState("");
  const [curHostName, setCurHostName] = useState("");
  const [curKeyword, setCurKeyword] = useState("");
  const [curPage, setCurPage] = useState(1);
  const [curPageSize, setCurPageSize] = useState(50);

  const currentQuery = useQuery({
    queryKey: queryKeys.umeCurrentAlarms(
      curSeverity,
      curCleared,
      curHostName,
      curKeyword,
      curPage,
      curPageSize,
    ),
    queryFn: () =>
      fetchUmeCurrentAlarms({
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
  const perPage = (n: number) => t("common.perPage", { n: String(n) });
  const hasFilters = Boolean(curKeyword.trim() || curHostName.trim() || curSeverity || curCleared);

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("ume.alarms.title")}</h2>
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
            <option value="true">true</option>
            <option value="false">false</option>
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
                  <th>time_created</th>
                  <th>severity</th>
                  <th>ne_id</th>
                  <th>host_name</th>
                  <th>ne_type</th>
                  <th>cause</th>
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

        <div className="pager pt-list-pager">
          <div className="pager__meta muted">
            {t("common.pagerMeta", {
              total: String(curTotal),
              page: String(curPage),
              pages: String(curPages),
            })}
          </div>
          <div className="pager__controls btn-row">
            <button
              className="pager__btn"
              type="button"
              onClick={() => setCurPage(Math.max(1, curPage - 1))}
              disabled={curPage <= 1}
            >
              {t("common.prevPage")}
            </button>
            <button
              className="pager__btn"
              type="button"
              onClick={() => setCurPage(curPage + 1)}
              disabled={curPage >= curPages}
            >
              {t("common.nextPage")}
            </button>
            <select
              className="pager__size"
              value={String(curPageSize)}
              onChange={(e) => {
                setCurPageSize(Number(e.target.value) || 50);
                setCurPage(1);
              }}
            >
              <option value="50">{perPage(50)}</option>
              <option value="100">{perPage(100)}</option>
              <option value="200">{perPage(200)}</option>
              <option value="500">{perPage(500)}</option>
            </select>
          </div>
        </div>
      </div>
    </section>
  );
}
