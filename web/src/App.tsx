import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AppLayout } from "./layout/AppLayout";
import { queryKeys } from "./constants/queryKeys";
import { WorkbenchPage } from "./pages/WorkbenchPage";
import { CollectPage } from "./pages/CollectPage";
import { ConfigSyncPage } from "./pages/ConfigSyncPage";
import { NePage } from "./pages/NePage";
import { UmePage } from "./pages/UmePage";
import { WebcrtPage } from "./pages/WebcrtPage";
import { TopologyPage } from "./pages/TopologyPage";
import { NetworkLayout } from "./pages/network/NetworkLayout";
import { NetworkDevicesPage } from "./pages/network/NetworkDevicesPage";
import { NetworkAlarmsPage } from "./pages/network/NetworkAlarmsPage";
import { NetworkConfigsPage } from "./pages/network/NetworkConfigsPage";
import { LldpLinksPage } from "./pages/network/LldpLinksPage";
import { TopologyClassifyPage } from "./pages/network/TopologyClassifyPage";
import { PortTrafficPage } from "./pages/network/PortTrafficPage";
import { PortTrafficBoardListPage } from "./pages/network/PortTrafficBoardListPage";
import { PortTrafficWallPage } from "./pages/network/PortTrafficWallPage";
import { LoginPage } from "./pages/LoginPage";
import { UsersPage } from "./pages/UsersPage";
import { AuditLayout } from "./pages/audit/AuditLayout";
import { TaskOverviewPage } from "./pages/audit/TaskOverviewPage";
import { AuditPage } from "./pages/AuditPage";
import { ApiTokensPage } from "./pages/ApiTokensPage";
import { ForceChangePasswordPage } from "./pages/ForceChangePasswordPage";
import { fetchIntegrationStatus } from "./services/api";
import { useAuth } from "./auth/AuthContext";

/** Preserve query when redirecting legacy /network/webcrt → /webcrt. */
function NetworkWebcrtRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/webcrt${search}`} replace />;
}

/** Legacy `?board_id=` under network wall → dedicated board tab route. */
function LegacyPortTrafficWallRedirect() {
  const { search } = useLocation();
  const boardId = new URLSearchParams(search).get("board_id")?.trim() || "";
  if (boardId) {
    return <Navigate to={`/port-traffic/wall/${encodeURIComponent(boardId)}`} replace />;
  }
  return <PortTrafficBoardListPage />;
}

function ProtectedApp() {
  const { ready, user } = useAuth();
  const integrationsQuery = useQuery({
    queryKey: queryKeys.integrationsStatus,
    queryFn: fetchIntegrationStatus,
    refetchInterval: 5000,
    staleTime: 2000,
    enabled: ready && Boolean(user) && !user?.must_change_password,
  });

  if (!ready) {
    return (
      <div className="login-page">
        <div className="login-card">Loading…</div>
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  if (user.must_change_password) {
    return <ForceChangePasswordPage />;
  }

  return (
    <AppLayout
      connections={{
        netxApi: integrationsQuery.data?.netx_api?.status ?? (integrationsQuery.isError ? "down" : "unknown"),
        netxApiLatencyMs:
          typeof integrationsQuery.data?.db?.latency_ms === "number" ? integrationsQuery.data.db.latency_ms : undefined,
        oclawBridge:
          integrationsQuery.data?.oclaw_bridge?.status ?? (integrationsQuery.isError ? "down" : "unknown"),
        oclawBridgeLatencyMs: undefined,
        oclawBridgeErrorKind:
          typeof integrationsQuery.data?.oclaw_bridge?.error_kind === "string"
            ? integrationsQuery.data.oclaw_bridge.error_kind
            : undefined,
        oclawBridgeError:
          typeof integrationsQuery.data?.oclaw_bridge?.error === "string"
            ? integrationsQuery.data.oclaw_bridge.error
            : undefined,
        oclawBridgeQueueSize:
          typeof integrationsQuery.data?.oclaw_bridge?.queue_size === "number"
            ? integrationsQuery.data.oclaw_bridge.queue_size
            : undefined,
        oclawBridgePublishedOk:
          typeof integrationsQuery.data?.oclaw_bridge?.published_ok === "number"
            ? integrationsQuery.data.oclaw_bridge.published_ok
            : undefined,
      }}
    >
      <Routes>
        <Route path="/" element={<WorkbenchPage />} />
        <Route path="/workbench" element={<Navigate to="/" replace />} />
        <Route path="/ume" element={<UmePage />} />
        <Route path="/ne" element={<NePage />} />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/webcrt" element={<WebcrtPage />} />
        <Route path="/port-traffic/wall/:boardId" element={<PortTrafficWallPage />} />
        <Route path="/network" element={<NetworkLayout />}>
          <Route index element={<Navigate to="devices" replace />} />
          <Route path="devices" element={<NetworkDevicesPage />} />
          <Route path="alarms" element={<NetworkAlarmsPage />} />
          <Route path="configs" element={<NetworkConfigsPage />} />
          <Route path="topology/lldp" element={<LldpLinksPage />} />
          <Route path="topology/classify" element={<TopologyClassifyPage />} />
          <Route path="topology" element={<Navigate to="/network/topology/lldp" replace />} />
          <Route path="webcrt" element={<NetworkWebcrtRedirect />} />
          <Route path="tasks/collect" element={<CollectPage />} />
          <Route path="tasks/config-sync" element={<ConfigSyncPage />} />
          <Route path="tasks/port-traffic/wall" element={<LegacyPortTrafficWallRedirect />} />
          <Route path="tasks/port-traffic" element={<PortTrafficPage />} />
        </Route>
        <Route path="/collect" element={<Navigate to="/network/tasks/collect" replace />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/audit" element={<AuditLayout />}>
          <Route index element={<Navigate to="tasks" replace />} />
          <Route path="tasks" element={<TaskOverviewPage />} />
          <Route path="logs" element={<AuditPage />} />
        </Route>
        <Route path="/api-keys" element={<ApiTokensPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/*" element={<ProtectedApp />} />
    </Routes>
  );
}

export default App;
