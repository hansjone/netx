import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AppLayout } from "./layout/AppLayout";
import { queryKeys } from "./constants/queryKeys";
import { WorkbenchPage } from "./pages/WorkbenchPage";
import { CollectPage } from "./pages/CollectPage";
import { NePage } from "./pages/NePage";
import { UmePage } from "./pages/UmePage";
import { WebcrtPage } from "./pages/WebcrtPage";
import { TopologyPage } from "./pages/TopologyPage";
import { NetworkLayout } from "./pages/network/NetworkLayout";
import { NetworkAlarmsPage } from "./pages/network/NetworkAlarmsPage";
import { NetworkPlaceholderPage } from "./pages/network/NetworkPlaceholderPage";
import { LoginPage } from "./pages/LoginPage";
import { UsersPage } from "./pages/UsersPage";
import { AuditPage } from "./pages/AuditPage";
import { ApiTokensPage } from "./pages/ApiTokensPage";
import { ForceChangePasswordPage } from "./pages/ForceChangePasswordPage";
import { fetchIntegrationStatus } from "./services/api";
import { useAuth } from "./auth/AuthContext";

/** Preserve ?ne_id=… when redirecting legacy /webcrt into the network module. */
function WebcrtLegacyRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/network/webcrt${search}`} replace />;
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
        <Route path="/network" element={<NetworkLayout />}>
          <Route index element={<Navigate to="devices" replace />} />
          <Route path="devices" element={<NePage />} />
          <Route path="topology" element={<TopologyPage />} />
          <Route path="alarms" element={<NetworkAlarmsPage />} />
          <Route path="webcrt" element={<WebcrtPage />} />
          <Route path="tasks/config-sync" element={<CollectPage />} />
          <Route path="tasks/port-traffic" element={<NetworkPlaceholderPage kind="port-traffic" />} />
        </Route>
        <Route path="/ne" element={<Navigate to="/network/devices" replace />} />
        <Route path="/collect" element={<Navigate to="/network/tasks/config-sync" replace />} />
        <Route path="/topology" element={<Navigate to="/network/topology" replace />} />
        <Route path="/webcrt" element={<WebcrtLegacyRedirect />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/audit" element={<AuditPage />} />
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
