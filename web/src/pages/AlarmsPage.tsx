import type { Alarm, Batch } from "../types";
import { formatSystemTime } from "../utils/time";

type Props = {
  batches: Batch[];
  alarms: Alarm[];
  total: number;
  isLoading: boolean;
  selectedBatch: string;
  alarmCode: string;
  neName: string;
  severity: string;
  page: number;
  pageSize: number;
  onSelectBatch: (v: string) => void;
  onChangeAlarmCode: (v: string) => void;
  onChangeNeName: (v: string) => void;
  onChangeSeverity: (v: string) => void;
  onChangePage: (v: number) => void;
  onChangePageSize: (v: number) => void;
  onQueryNow: () => void;
  onResetFilters: () => void;
  onDeleteBatch: () => void;
  isDeletingBatch: boolean;
  onDeleteAllBatches: () => void;
  isDeletingAllBatches: boolean;
};

export function AlarmsPage(props: Props) {
  const {
    batches,
    alarms,
    total,
    isLoading,
    selectedBatch,
    alarmCode,
    neName,
    severity,
    page,
    pageSize,
    onSelectBatch,
    onChangeAlarmCode,
    onChangeNeName,
    onChangeSeverity,
    onChangePage,
    onChangePageSize,
    onQueryNow,
    onResetFilters,
    onDeleteBatch,
    isDeletingBatch,
    onDeleteAllBatches,
    isDeletingAllBatches,
  } = props;

  const totalPages = Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, pageSize)));

  return (
    <>
      <section className="cards">
        <article className="card card--full">
          <h3>批次与过滤</h3>
          <div className="filter-inline">
            <select value={selectedBatch} onChange={(e) => onSelectBatch(e.target.value)}>
              {batches.map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.batch_id} · success={b.success_rows} · failed={b.failed_rows}
                </option>
              ))}
            </select>
            <input placeholder="告警码 contains" value={alarmCode} onChange={(e) => onChangeAlarmCode(e.target.value)} />
            <input placeholder="网元 contains" value={neName} onChange={(e) => onChangeNeName(e.target.value)} />
            <select value={severity} onChange={(e) => onChangeSeverity(e.target.value)}>
              <option value="">全部级别</option>
              <option value="critical">critical</option>
              <option value="major">major</option>
              <option value="minor">minor</option>
              <option value="warning">warning</option>
              <option value="info">info</option>
              <option value="unknown">unknown</option>
            </select>
          </div>
          <div className="actions-row actions-row--inline">
            <button onClick={onQueryNow}>查询</button>
            <button onClick={onResetFilters}>重置过滤</button>
            <button onClick={() => window.open(`/v1/batches/${encodeURIComponent(selectedBatch)}/errors.csv`, "_blank")} disabled={!selectedBatch}>
              下载导入失败明细（CSV）
            </button>
            <button onClick={onDeleteBatch} disabled={!selectedBatch || isDeletingBatch}>
              {isDeletingBatch ? "删除中..." : "删除当前批次"}
            </button>
            <button onClick={onDeleteAllBatches} disabled={isDeletingAllBatches}>
              {isDeletingAllBatches ? "清空中..." : "清空全部批次"}
            </button>
          </div>
        </article>

      </section>

      <section className="panel">
        <h2>
          告警列表 ({alarms.length}/{total})
        </h2>
        {isLoading && <div className="skeleton-line">告警数据加载中...</div>}
        <table>
          <thead>
            <tr>
              <th>time</th>
              <th>severity</th>
              <th>ne</th>
              <th>alarm</th>
            </tr>
          </thead>
          <tbody>
            {alarms.map((a) => (
              <tr key={a.id}>
                <td>{formatSystemTime(a.alarm_time)}</td>
                <td>{a.severity_norm}</td>
                <td>{a.ne_name}</td>
                <td>
                  <button className="link-btn" onClick={() => onChangeAlarmCode(a.alarm_code)}>
                    {a.alarm_code}
                  </button>
                </td>
              </tr>
            ))}
            {!isLoading && alarms.length === 0 && (
              <tr>
                <td colSpan={4}>暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">共 {total} 条 · 第 {page}/{totalPages} 页</div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => onChangePage(Math.max(1, page - 1))} disabled={page <= 1}>
              上一页
            </button>
            {Array.from({ length: Math.min(7, totalPages) }).map((_, i) => {
              const start = Math.max(1, Math.min(totalPages - 6, page - 3));
              const p = start + i;
              if (p > totalPages) return null;
              return (
                <button key={p} className={`pager__num${p === page ? " active" : ""}`} onClick={() => onChangePage(p)}>
                  {p}
                </button>
              );
            })}
            <button
              className="pager__btn"
              onClick={() => onChangePage(Math.min(totalPages, page + 1))}
              disabled={page >= totalPages}
            >
              下一页
            </button>
            <select className="pager__size" value={String(pageSize)} onChange={(e) => onChangePageSize(Number(e.target.value) || 80)}>
              <option value="50">50/页</option>
              <option value="80">80/页</option>
              <option value="100">100/页</option>
              <option value="200">200/页</option>
            </select>
          </div>
        </div>
      </section>
    </>
  );
}
