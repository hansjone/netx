import { useMemo, useState } from "react";
import type { ImportHistoryItem } from "../types";
import { formatSystemTime } from "../utils/time";

type Props = {
  isImportingAlarm: boolean;
  alarmImportStatus: string;
  isImportingLogs: boolean;
  logsImportStatus: string;
  history: ImportHistoryItem[];
  onImportAlarmExcel: (file: File | null) => void;
  onImportLogs: (file: File | null) => void;
  onOpenBatch: (batchId: string) => void;
};

export function IngestPage({
  isImportingAlarm,
  alarmImportStatus,
  isImportingLogs,
  logsImportStatus,
  history,
  onImportAlarmExcel,
  onImportLogs,
  onOpenBatch,
}: Props) {
  const [alarmFileName, setAlarmFileName] = useState("");
  const [logFileName, setLogFileName] = useState("");
  const items = useMemo(() => history.slice().sort((a, b) => b.ts_ms - a.ts_ms).slice(0, 10), [history]);

  return (
    <>
      <section className="panel">
        <h2>数据接入中心</h2>
        <p className="muted">这里是 netx 的主页入口：导入告警 / 日志 / 配置等数据，后续扩展作业化能力。</p>
      </section>

      <section className="cards">
        <article className="card">
          <h3>告警导入（Excel）</h3>
          <input
            type="file"
            accept=".xlsx,.xls"
            disabled={isImportingAlarm}
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              setAlarmFileName(f?.name || "");
              onImportAlarmExcel(f);
            }}
          />
          <div className="muted">{alarmFileName ? `已选择：${alarmFileName}` : "选择 Excel 后自动上传导入"}</div>
          <div className="muted">{alarmImportStatus}</div>
        </article>

        <article className="card">
          <h3>日志导入（预留）</h3>
          <input
            type="file"
            disabled={isImportingLogs}
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              setLogFileName(f?.name || "");
              onImportLogs(f);
            }}
          />
          <div className="muted">{logFileName ? `已选择：${logFileName}` : "选择日志文件后上传（当前仅占位接口）"}</div>
          <div className="muted">{logsImportStatus}</div>
        </article>

        <article className="card">
          <h3>配置导入（预留）</h3>
          <button disabled={true} title="后续支持：配置文件导入、解析、对比与风险评估">
            配置导入（规划中）
          </button>
          <div className="muted">后续接入：配置采集/割接对比/配置诊断。</div>
        </article>
      </section>

      <section className="panel">
        <h2>最近导入</h2>
        <table>
          <thead>
            <tr>
              <th>time</th>
              <th>kind</th>
              <th>file</th>
              <th>result</th>
              <th>batch</th>
            </tr>
          </thead>
          <tbody>
            {items.map((x) => (
              <tr key={`${x.kind}-${x.ts_ms}-${x.file_name}`}>
                <td>{formatSystemTime(x.ts_ms)}</td>
                <td>{x.kind}</td>
                <td>{x.file_name}</td>
                <td>{x.ok ? "ok" : "error"}</td>
                <td>
                  {x.batch_id ? (
                    <button className="link-btn" onClick={() => onOpenBatch(x.batch_id!)}>
                      {x.batch_id}
                    </button>
                  ) : (
                    "-"
                  )}
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={5}>暂无导入记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}

