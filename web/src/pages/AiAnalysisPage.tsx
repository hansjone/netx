import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { AiAnalyzeHistoryItem, Batch } from "../types";
import { fetchAiAnalyzeHistory } from "../services/api";
import { formatSystemTime } from "../utils/time";

type Props = {
  batches: Batch[];
  selectedBatch: string;
  onSelectBatch: (v: string) => void;
  aiAlarmCode: string;
  aiNeName: string;
  aiSeverity: string;
  onChangeAiAlarmCode: (v: string) => void;
  onChangeAiNeName: (v: string) => void;
  onChangeAiSeverity: (v: string) => void;
  onResetAiFilters: () => void;
  question: string;
  answer: string;
  isRunning: boolean;
  enabled: boolean;
  onChangeQuestion: (v: string) => void;
  onRunAnalyze: () => void;
};

export function AiAnalysisPage({
  batches,
  selectedBatch,
  onSelectBatch,
  aiAlarmCode,
  aiNeName,
  aiSeverity,
  onChangeAiAlarmCode,
  onChangeAiNeName,
  onChangeAiSeverity,
  onResetAiFilters,
  question,
  answer,
  isRunning,
  enabled,
  onChangeQuestion,
  onRunAnalyze,
}: Props) {
  const [histPage, setHistPage] = useState(1);
  const [histPageSize, setHistPageSize] = useState(10);
  const [selectedHistId, setSelectedHistId] = useState<number | null>(null);

  const historyQuery = useQuery({
    queryKey: ["aiAnalyzeHistory", selectedBatch, histPage, histPageSize],
    queryFn: () => fetchAiAnalyzeHistory({ batchId: selectedBatch, page: histPage, pageSize: histPageSize }),
    enabled: Boolean(selectedBatch),
    staleTime: 3000,
  });

  const historyItems: AiAnalyzeHistoryItem[] = useMemo(() => (historyQuery.data?.items || []) as AiAnalyzeHistoryItem[], [historyQuery.data]);
  const total = Number(historyQuery.data?.total || 0);
  const totalPages = Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, histPageSize)));
  const selectedItem = useMemo(
    () => historyItems.find((x) => Number(x.id) === Number(selectedHistId)),
    [historyItems, selectedHistId],
  );

  return (
    <section className="panel">
      <article className="card card--full">
        <h3>分析范围（前提条件）</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          此处筛选仅作用于 AI 分析请求中的 <code>dataset_ref.filters</code>，与告警列表页的过滤相互独立。
        </p>
        <div className="filter-inline">
          <select value={selectedBatch} onChange={(e) => onSelectBatch(e.target.value)}>
            {batches.length === 0 && <option value="">暂无批次</option>}
            {batches.map((b) => (
              <option key={b.batch_id} value={b.batch_id}>
                {b.batch_id} · success={b.success_rows} · failed={b.failed_rows}
              </option>
            ))}
          </select>
          <input
            placeholder="告警码 contains"
            value={aiAlarmCode}
            onChange={(e) => onChangeAiAlarmCode(e.target.value)}
            disabled={!selectedBatch}
          />
          <input
            placeholder="网元 contains"
            value={aiNeName}
            onChange={(e) => onChangeAiNeName(e.target.value)}
            disabled={!selectedBatch}
          />
          <select value={aiSeverity} onChange={(e) => onChangeAiSeverity(e.target.value)} disabled={!selectedBatch}>
            <option value="">全部级别</option>
            <option value="critical">critical</option>
            <option value="major">major</option>
            <option value="minor">minor</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
            <option value="unknown">unknown</option>
          </select>
        </div>
        <div className="actions-row actions-row--inline" style={{ marginTop: 10 }}>
          <button type="button" onClick={onResetAiFilters} disabled={!selectedBatch}>
            重置 AI 筛选
          </button>
        </div>
      </article>

      <div className="panel split" style={{ marginTop: 16 }}>
        <article>
          <h2>AI 分析输入</h2>
          <textarea value={question} onChange={(e) => onChangeQuestion(e.target.value)} rows={6} disabled={!enabled} />
          <button type="button" onClick={onRunAnalyze} disabled={!enabled || isRunning}>
            {isRunning ? "分析中..." : "运行分析"}
          </button>
          {!selectedBatch && <p className="muted">请先选择批次。</p>}
        </article>
        <article>
          <h2>AI 回答</h2>
          <pre>{answer || "暂无结果"}</pre>
        </article>
      </div>

      <article className="card card--full" style={{ marginTop: 16 }}>
        <h3>AI 运维问答历史</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          默认展示当前批次的历史记录（分页）。点击“查看”可在下方展开回答内容。
        </p>
        {historyQuery.isLoading && <div className="skeleton-line">历史记录加载中...</div>}
        <table>
          <thead>
            <tr>
              <th>time</th>
              <th>status</th>
              <th>question</th>
              <th style={{ width: 120 }}>action</th>
            </tr>
          </thead>
          <tbody>
            {historyItems.map((h) => (
              <tr key={h.id}>
                <td>{formatSystemTime(h.created_at)}</td>
                <td>{h.ok ? "ok" : "error"}</td>
                <td style={{ maxWidth: 780, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.question}</td>
                <td>
                  <button type="button" className="link-btn" onClick={() => setSelectedHistId(Number(h.id))}>
                    查看
                  </button>
                </td>
              </tr>
            ))}
            {!historyQuery.isLoading && historyItems.length === 0 && (
              <tr>
                <td colSpan={4}>暂无历史记录</td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="pager">
          <div className="pager__meta">
            共 {total} 条 · 第 {histPage}/{totalPages} 页
          </div>
          <div className="pager__controls">
            <button className="pager__btn" onClick={() => setHistPage(Math.max(1, histPage - 1))} disabled={histPage <= 1}>
              上一页
            </button>
            <button
              className="pager__btn"
              onClick={() => setHistPage(Math.min(totalPages, histPage + 1))}
              disabled={histPage >= totalPages}
            >
              下一页
            </button>
            <select value={histPageSize} onChange={(e) => setHistPageSize(Number(e.target.value) || 10)}>
              <option value={10}>10/页</option>
              <option value={20}>20/页</option>
              <option value={50}>50/页</option>
            </select>
          </div>
        </div>

        <div style={{ marginTop: 10 }}>
          <h4 style={{ marginBottom: 6 }}>回答预览</h4>
          {!selectedItem && <div className="muted">请选择一条历史记录查看回答。</div>}
          {selectedItem && (
            <>
              {!selectedItem.ok && <div className="muted">错误：{String(selectedItem.error || "")}</div>}
              <pre style={{ maxHeight: 420, overflow: "auto" }}>{String(selectedItem.answer || "").trim() || "（空）"}</pre>
            </>
          )}
        </div>
      </article>
    </section>
  );
}
