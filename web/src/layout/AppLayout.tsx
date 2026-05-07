import { NavLink } from "react-router-dom";
import type { ReactNode } from "react";

type Props = {
  status: string;
  connections: {
    netxApi: "up" | "down" | "unknown";
    oclawBridge: "up" | "down" | "unknown";
    netxApiLatencyMs?: number;
    oclawBridgeLatencyMs?: number;
    oclawBridgeErrorKind?: string;
  };
  onRefreshBatches: () => void;
  onRefreshAlarms: () => void;
  capabilities: {
    alarms: boolean;
    diagnostics: boolean;
    aiAnalysis: boolean;
  };
  children: ReactNode;
};

export function AppLayout({ status, connections, onRefreshBatches, onRefreshAlarms, capabilities, children }: Props) {
  const netxSuffix =
    typeof connections.netxApiLatencyMs === "number" ? ` (${connections.netxApiLatencyMs}ms)` : "";
  const oclawSuffix =
    typeof connections.oclawBridgeLatencyMs === "number"
      ? ` (${connections.oclawBridgeLatencyMs}ms)`
      : connections.oclawBridgeErrorKind
        ? ` (${connections.oclawBridgeErrorKind})`
        : "";
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">netx</div>
        <NavLink
          to="/diagnostics"
          className={({ isActive }) => `nav-item${isActive ? " active" : ""}${!capabilities.diagnostics ? " disabled" : ""}`}
        >
          诊断中心
        </NavLink>
        <NavLink to="/ingest" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          数据接入
        </NavLink>
        <NavLink to="/alarms" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          告警中心
        </NavLink>
        <NavLink
          to="/ai-analysis"
          className={({ isActive }) => `nav-item${isActive ? " active" : ""}${!capabilities.aiAnalysis ? " disabled" : ""}`}
        >
          AI 分析
        </NavLink>
        <NavLink to="/ume" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          UME 对接
        </NavLink>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h1>运维中心</h1>
          </div>
          <div className="top-actions">
            <span className={`conn-pill conn-pill--${connections.netxApi}`}>netx api: {connections.netxApi}{netxSuffix}</span>
            <span className={`conn-pill conn-pill--${connections.oclawBridge}`}>oclaw bridge: {connections.oclawBridge}{oclawSuffix}</span>
            <button onClick={onRefreshBatches} disabled={!capabilities.alarms}>刷新批次</button>
            <button onClick={onRefreshAlarms} disabled={!capabilities.alarms}>刷新告警</button>
          </div>
        </header>

        {children}
        <footer className="status">status: {status}</footer>
      </main>
    </div>
  );
}
