import { Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AppLayout } from "./layout/AppLayout";
import { queryKeys } from "./constants/queryKeys";
import { WorkbenchPage } from "./pages/WorkbenchPage";
import { CollectPage } from "./pages/CollectPage";
import { NePage } from "./pages/NePage";
import { UmePage } from "./pages/UmePage";
import { fetchIntegrationStatus } from "./services/api";

function App() {
  const integrationsQuery = useQuery({
    queryKey: queryKeys.integrationsStatus,
    queryFn: fetchIntegrationStatus,
    refetchInterval: 5000,
    staleTime: 2000,
  });

  return (
    <AppLayout
      connections={{
        netxApi: integrationsQuery.data?.netx_api?.status ?? (integrationsQuery.isError ? "down" : "unknown"),
        netxApiLatencyMs:
          typeof integrationsQuery.data?.db?.latency_ms === "number" ? integrationsQuery.data.db.latency_ms : undefined,
        oclawBridge:
          integrationsQuery.data?.oclaw_bridge?.status ?? (integrationsQuery.isError ? "down" : "unknown"),
        oclawBridgeLatencyMs:
          typeof integrationsQuery.data?.oclaw_bridge?.latency_ms === "number"
            ? integrationsQuery.data.oclaw_bridge.latency_ms
            : undefined,
        oclawBridgeErrorKind:
          typeof integrationsQuery.data?.oclaw_bridge?.error_kind === "string"
            ? integrationsQuery.data.oclaw_bridge.error_kind
            : undefined,
        oclawBridgeError:
          typeof integrationsQuery.data?.oclaw_bridge?.error === "string"
            ? integrationsQuery.data.oclaw_bridge.error
            : undefined,
      }}
    >
      <Routes>
        <Route path="/" element={<WorkbenchPage />} />
        <Route path="/workbench" element={<Navigate to="/" replace />} />
        <Route path="/ume" element={<UmePage />} />
        <Route path="/ne" element={<NePage />} />
        <Route path="/collect" element={<CollectPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  );
}

export default App;
