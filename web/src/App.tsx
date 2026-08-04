import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AppLayout } from "./layout/AppLayout";
import { queryKeys } from "./constants/queryKeys";
import { WorkbenchPage } from "./pages/WorkbenchPage";
import { LoginPage } from "./pages/LoginPage";
import { ForceChangePasswordPage } from "./pages/ForceChangePasswordPage";
import { fetchIntegrationStatus } from "./services/api";
import { useAuth } from "./auth/AuthContext";

const CollectPage = lazy(() => import("./pages/CollectPage").then((m) => ({ default: m.CollectPage })));
const ConfigSyncPage = lazy(() =>
  import("./pages/ConfigSyncPage").then((m) => ({ default: m.ConfigSyncPage })),
);
const NePage = lazy(() => import("./pages/NePage").then((m) => ({ default: m.NePage })));
const UmePage = lazy(() => import("./pages/UmePage").then((m) => ({ default: m.UmePage })));
const WebcrtPage = lazy(() => import("./pages/WebcrtPage").then((m) => ({ default: m.WebcrtPage })));
const TopologyPage = lazy(() => import("./pages/TopologyPage").then((m) => ({ default: m.TopologyPage })));
const NetworkLayout = lazy(() =>
  import("./pages/network/NetworkLayout").then((m) => ({ default: m.NetworkLayout })),
);
const NetworkDevicesPage = lazy(() =>
  import("./pages/network/NetworkDevicesPage").then((m) => ({ default: m.NetworkDevicesPage })),
);
const NetworkAlarmsPage = lazy(() =>
  import("./pages/network/NetworkAlarmsPage").then((m) => ({ default: m.NetworkAlarmsPage })),
);
const NetworkConfigsPage = lazy(() =>
  import("./pages/network/NetworkConfigsPage").then((m) => ({ default: m.NetworkConfigsPage })),
);
const LldpLinksPage = lazy(() =>
  import("./pages/network/LldpLinksPage").then((m) => ({ default: m.LldpLinksPage })),
);
const TopologyClassifyPage = lazy(() =>
  import("./pages/network/TopologyClassifyPage").then((m) => ({ default: m.TopologyClassifyPage })),
);
const PortTrafficPage = lazy(() =>
  import("./pages/network/PortTrafficPage").then((m) => ({ default: m.PortTrafficPage })),
);
const PortTrafficBoardListPage = lazy(() =>
  import("./pages/network/PortTrafficBoardListPage").then((m) => ({
    default: m.PortTrafficBoardListPage,
  })),
);
const PortTrafficWallPage = lazy(() =>
  import("./pages/network/PortTrafficWallPage").then((m) => ({ default: m.PortTrafficWallPage })),
);
const UsersPage = lazy(() => import("./pages/UsersPage").then((m) => ({ default: m.UsersPage })));
const AuditLayout = lazy(() =>
  import("./pages/audit/AuditLayout").then((m) => ({ default: m.AuditLayout })),
);
const TaskOverviewPage = lazy(() =>
  import("./pages/audit/TaskOverviewPage").then((m) => ({ default: m.TaskOverviewPage })),
);
const AuditPage = lazy(() => import("./pages/AuditPage").then((m) => ({ default: m.AuditPage })));
const ApiTokensPage = lazy(() =>
  import("./pages/ApiTokensPage").then((m) => ({ default: m.ApiTokensPage })),
);

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

function PageFallback() {
  return (
    <div className="page-loading" role="status" aria-live="polite">
      Loading…
    </div>
  );
}

function ProtectedApp() {
  const { ready, user } = useAuth();
  const integrationsQuery = useQuery({
    queryKey: queryKeys.integrationsStatus,
    queryFn: fetchIntegrationStatus,
    refetchInterval: 10_000,
    staleTime: 5_000,
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
      <Suspense fallback={<PageFallback />}>
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
      </Suspense>
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
