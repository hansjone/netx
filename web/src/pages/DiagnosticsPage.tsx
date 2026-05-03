import type { Diagnostics } from "../types";

type Props = {
  diagnostics: Diagnostics | null;
};

function MiniChart({ title, items }: { title: string; items: Array<{ key: string; count: number }> }) {
  const total = Math.max(1, Number(items.reduce((n, x) => n + Number(x.count || 0), 0)));
  return (
    <article className="card">
      <h3>{title}</h3>
      <div className="mini-chart">
        {items.map((x) => (
          <div className="mini-chart__row" key={`${title}-${x.key}-${x.count}`}>
            <span className="mini-chart__label">{x.key || "unknown"}</span>
            <div className="mini-chart__bar-wrap">
              <div className="mini-chart__bar" style={{ width: `${Math.max(2, Math.round((x.count / total) * 100))}%` }} />
            </div>
            <span className="mini-chart__value">{x.count}</span>
          </div>
        ))}
        {items.length === 0 && <div className="muted">暂无数据</div>}
      </div>
    </article>
  );
}

export function DiagnosticsPage({ diagnostics }: Props) {
  return (
    <>
      <section className="panel">
        <h2>统计看板（最新批次）</h2>
        <div className="muted">
          batch_id: {diagnostics?.batch_id || "-"} · total_alarms: {diagnostics?.total_alarms ?? "-"}
        </div>
      </section>

      <section className="cards">
        <MiniChart title="级别分布" items={(diagnostics?.severity_summary || []) as any} />
        <MiniChart title="告警类型 Top" items={(diagnostics?.top_alarm_codes || []) as any} />
        <MiniChart title="网元 Top" items={(diagnostics?.top_ne || []) as any} />
        <MiniChart title="协议/领域 Top" items={(diagnostics?.protocol_summary || []) as any} />
      </section>
    </>
  );
}
