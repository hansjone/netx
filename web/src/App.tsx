import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes, useNavigate, useSearchParams } from "react-router-dom";
import { AppLayout } from "./layout/AppLayout";
import { AlarmsPage } from "./pages/AlarmsPage";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { AiAnalysisPage } from "./pages/AiAnalysisPage";
import { JobsPage } from "./pages/JobsPage";
import { IngestPage } from "./pages/IngestPage";
import type { Alarm, Batch, Diagnostics, ImportHistoryItem } from "./types";
import { apiDelete, apiPost, apiPostForm, fetchAlarms, fetchBatches, fetchDiagnostics, fetchIntegrationStatus } from "./services/api";
import { fetchJobs } from "./services/api";
import { useToast } from "./hooks/useToast";

function App() {
  const capabilities = {
    alarms: (import.meta.env.VITE_FEATURE_ALARMS ?? "1") !== "0",
    diagnostics: (import.meta.env.VITE_FEATURE_DIAGNOSTICS ?? "1") !== "0",
    aiAnalysis: (import.meta.env.VITE_FEATURE_AI_ANALYSIS ?? "1") !== "0",
    jobs: (import.meta.env.VITE_FEATURE_JOBS ?? "0") === "1",
  };

  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [selectedBatch, setSelectedBatch] = useState(() => searchParams.get("batch_id") || "");
  const [alarmCode, setAlarmCode] = useState(() => searchParams.get("alarm_code") || "");
  const [neName, setNeName] = useState(() => searchParams.get("ne_name") || "");
  const [severity, setSeverity] = useState(() => searchParams.get("severity") || "");
  /** AI 分析页专用筛选（与告警列表页过滤独立） */
  const [aiAlarmCode, setAiAlarmCode] = useState(() => searchParams.get("ai_alarm_code") || "");
  const [aiNeName, setAiNeName] = useState(() => searchParams.get("ai_ne_name") || "");
  const [aiSeverity, setAiSeverity] = useState(() => searchParams.get("ai_severity") || "");
  const [page, setPage] = useState(() => Number(searchParams.get("page") || "1") || 1);
  const [pageSize, setPageSize] = useState(() => Number(searchParams.get("page_size") || "80") || 80);
  const [question, setQuestion] = useState("请总结当前告警风险并给出行动建议");
  const [answer, setAnswer] = useState("");
  const [status, setStatus] = useState("ready");
  const { toast, showError, showOk } = useToast();

  const batchesQuery = useQuery({
    queryKey: ["batches"],
    queryFn: fetchBatches,
    staleTime: 30_000,
  });
  const alarmsQuery = useQuery({
    queryKey: ["alarms", selectedBatch, alarmCode, neName, severity, page, pageSize],
    queryFn: () => fetchAlarms({ batchId: selectedBatch, alarmCode, neName, severity, page, pageSize }),
    enabled: Boolean(selectedBatch),
    staleTime: 10_000,
  });
  const diagnosticsQuery = useQuery({
    queryKey: ["diagnostics", selectedBatch],
    queryFn: () => fetchDiagnostics(selectedBatch),
    enabled: Boolean(selectedBatch) && capabilities.diagnostics,
    staleTime: 10_000,
  });
  const integrationsQuery = useQuery({
    queryKey: ["integrationsStatus"],
    queryFn: fetchIntegrationStatus,
    refetchInterval: 5000,
    staleTime: 2000,
  });
  const analyzeMutation = useMutation({
    mutationFn: async () =>
      apiPost<any>("/v1/ap/analyze", {
        analysis_request_id: `vite-${Date.now()}`,
        batch_id: selectedBatch,
        question,
        filters: { alarm_code: aiAlarmCode, ne_name: aiNeName, severity: aiSeverity },
        constraints: { language: "zh-CN", max_points: 8 },
      }),
    onMutate: () => setStatus("analyzing"),
    onSuccess: async (data) => {
      const ok = Boolean(data?.ok) && Boolean(data?.oclaw?.ok);
      const ans = String(data?.oclaw?.answer || "").trim();
      setAnswer(ans);
      setStatus("ready");
      if (!ok) {
        showError(`AI 分析失败: ${String(data?.error || "oclaw bridge unavailable")}`);
      } else if (!ans) {
        showError("AI 分析已执行，但未返回内容");
      } else {
        showOk("AI 分析完成");
      }
      await queryClient.invalidateQueries({ queryKey: ["diagnostics"] });
      await queryClient.invalidateQueries({ queryKey: ["aiAnalyzeHistory"] });
    },
    onError: (e) => {
      setStatus(String(e));
      showError(`AI 分析失败: ${String(e)}`);
    },
  });

  const [alarmImportStatus, setAlarmImportStatus] = useState("");
  const [logsImportStatus, setLogsImportStatus] = useState("");
  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: fetchJobs,
    refetchInterval: 5000,
    staleTime: 2000,
  });
  const importAlarmMutation = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return await apiPostForm<any>("/v1/alarms/import", fd);
    },
    onMutate: () => setAlarmImportStatus("上传中..."),
    onSuccess: async (data) => {
      const bid = String(data?.batch_id || "").trim();
      setAlarmImportStatus(`导入完成 batch_id=${bid} success=${data?.success_rows ?? ""} failed=${data?.failed_rows ?? ""}`);
      showOk("告警导入完成");
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await queryClient.invalidateQueries({ queryKey: ["batches"] });
      if (bid) {
        setSelectedBatch(bid);
        // Clear filters to ensure the new batch is visible immediately.
        setAlarmCode("");
        setNeName("");
        setSeverity("");
        // Ensure alarms list refreshes after state changes and navigation.
        await queryClient.invalidateQueries({ queryKey: ["alarms"] });
        navigate("/alarms");
        await queryClient.invalidateQueries({ queryKey: ["alarms"] });
      }
    },
    onError: async (e) => {
      setAlarmImportStatus(`导入失败: ${String(e)}`);
      showError(`告警导入失败: ${String(e)}`);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const importLogsMutation = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return await apiPostForm<any>("/v1/logs/import", fd);
    },
    onMutate: () => setLogsImportStatus("上传中..."),
    onSuccess: async () => {
      setLogsImportStatus("导入完成（占位）");
      showOk("日志导入完成");
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: async (e) => {
      setLogsImportStatus(`导入失败: ${String(e)}`);
      showError(`日志导入失败: ${String(e)}`);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const deleteBatchMutation = useMutation({
    mutationFn: async (batchId: string) => apiDelete<any>(`/v1/batches/${encodeURIComponent(batchId)}`),
    onSuccess: async (_data, batchId) => {
      showOk(`已删除批次 ${batchId}`);
      setSelectedBatch("");
      await queryClient.invalidateQueries({ queryKey: ["batches"] });
      await queryClient.invalidateQueries({ queryKey: ["alarms"] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => {
      showError(`删除批次失败: ${String(e)}`);
    },
  });

  const deleteAllBatchesMutation = useMutation({
    mutationFn: async () => apiDelete<any>("/v1/batches"),
    onSuccess: async () => {
      showOk("已清空全部批次数据");
      setSelectedBatch("");
      setAlarmCode("");
      setNeName("");
      setSeverity("");
      setPage(1);
      await queryClient.invalidateQueries({ queryKey: ["batches"] });
      await queryClient.invalidateQueries({ queryKey: ["alarms"] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => {
      showError(`清空全部批次失败: ${String(e)}`);
    },
  });

  useEffect(() => {
    const next = new URLSearchParams();
    if (selectedBatch) next.set("batch_id", selectedBatch);
    if (alarmCode) next.set("alarm_code", alarmCode);
    if (neName) next.set("ne_name", neName);
    if (severity) next.set("severity", severity);
    if (aiAlarmCode) next.set("ai_alarm_code", aiAlarmCode);
    if (aiNeName) next.set("ai_ne_name", aiNeName);
    if (aiSeverity) next.set("ai_severity", aiSeverity);
    next.set("page", String(page));
    next.set("page_size", String(pageSize));
    setSearchParams(next, { replace: true });
  }, [selectedBatch, alarmCode, neName, severity, aiAlarmCode, aiNeName, aiSeverity, page, pageSize, setSearchParams]);

  useEffect(() => {
    if (!selectedBatch && batchesQuery.data?.items?.length) {
      setSelectedBatch(batchesQuery.data.items[0].batch_id);
    }
  }, [selectedBatch, batchesQuery.data]);

  useEffect(() => {
    // Any filter change should reset pagination to first page.
    setPage(1);
  }, [selectedBatch, alarmCode, neName, severity]);

  useEffect(() => {
    if (batchesQuery.error) {
      setStatus(String(batchesQuery.error));
      showError(`批次加载失败: ${String(batchesQuery.error)}`);
    } else if (alarmsQuery.error) {
      setStatus(String(alarmsQuery.error));
      showError(`告警加载失败: ${String(alarmsQuery.error)}`);
    }
  }, [batchesQuery.error, alarmsQuery.error, batchesQuery.isSuccess, alarmsQuery.isSuccess, showError]);

  return (
    <AppLayout
      status={status}
      connections={{
        netxApi: integrationsQuery.data?.netx_api?.status ?? (integrationsQuery.isError ? "down" : "unknown"),
        oclawBridge: integrationsQuery.data?.oclaw_bridge?.status ?? (integrationsQuery.isError ? "down" : "unknown"),
        netxApiLatencyMs: typeof integrationsQuery.data?.db?.latency_ms === "number" ? integrationsQuery.data.db.latency_ms : undefined,
        oclawBridgeLatencyMs:
          typeof integrationsQuery.data?.oclaw_bridge?.latency_ms === "number"
            ? integrationsQuery.data.oclaw_bridge.latency_ms
            : undefined,
        oclawBridgeErrorKind:
          typeof integrationsQuery.data?.oclaw_bridge?.error_kind === "string"
            ? integrationsQuery.data.oclaw_bridge.error_kind
            : undefined,
      }}
      capabilities={capabilities}
      onRefreshBatches={() => queryClient.invalidateQueries({ queryKey: ["batches"] })}
      onRefreshAlarms={() => queryClient.invalidateQueries({ queryKey: ["alarms"] })}
    >
        <Routes>
          <Route path="/" element={<Navigate to="/diagnostics" replace />} />
          <Route
            path="/ingest"
            element={
              <IngestPage
                isImportingAlarm={importAlarmMutation.isPending}
                alarmImportStatus={alarmImportStatus}
                isImportingLogs={importLogsMutation.isPending}
                logsImportStatus={logsImportStatus}
                history={(jobsQuery.data?.items || []) as ImportHistoryItem[]}
                onImportAlarmExcel={(f) => f && importAlarmMutation.mutate(f)}
                onImportLogs={(f) => f && importLogsMutation.mutate(f)}
                onOpenBatch={(bid) => {
                  setSelectedBatch(String(bid || "").trim());
                  setAlarmCode("");
                  setNeName("");
                  setSeverity("");
                  navigate("/alarms");
                }}
              />
            }
          />
          <Route
            path="/alarms"
            element={
              <AlarmsPage
                batches={(batchesQuery.data?.items || []) as Batch[]}
                alarms={(alarmsQuery.data?.items || []) as Alarm[]}
                total={Number(alarmsQuery.data?.total || 0)}
                isLoading={alarmsQuery.isLoading || alarmsQuery.isFetching}
                selectedBatch={selectedBatch}
                alarmCode={alarmCode}
                neName={neName}
                severity={severity}
                page={page}
                pageSize={pageSize}
                onSelectBatch={setSelectedBatch}
                onChangeAlarmCode={setAlarmCode}
                onChangeNeName={setNeName}
                onChangeSeverity={setSeverity}
                onChangePage={setPage}
                onChangePageSize={setPageSize}
                onQueryNow={() => queryClient.invalidateQueries({ queryKey: ["alarms"] })}
                onResetFilters={() => {
                  setAlarmCode("");
                  setNeName("");
                  setSeverity("");
                  setPage(1);
                }}
                isDeletingBatch={deleteBatchMutation.isPending}
                onDeleteBatch={() => {
                  const bid = String(selectedBatch || "").trim();
                  if (!bid) {
                    showError("请先选择批次");
                    return;
                  }
                  if (!window.confirm(`确认删除批次 ${bid} 吗？将删除该批次告警、错误记录与作业记录。`)) return;
                  deleteBatchMutation.mutate(bid);
                }}
                isDeletingAllBatches={deleteAllBatchesMutation.isPending}
                onDeleteAllBatches={() => {
                  if (!window.confirm("确认清空全部批次吗？这会删除所有告警、错误明细和导入作业记录。")) return;
                  if (!window.confirm("这是高危操作，无法恢复。请再次确认执行清空全部批次。")) return;
                  deleteAllBatchesMutation.mutate();
                }}
              />
            }
          />
          <Route path="/diagnostics" element={<DiagnosticsPage diagnostics={(diagnosticsQuery.data || null) as Diagnostics | null} />} />
          <Route
            path="/ai-analysis"
            element={
              <AiAnalysisPage
                batches={(batchesQuery.data?.items || []) as Batch[]}
                selectedBatch={selectedBatch}
                onSelectBatch={setSelectedBatch}
                aiAlarmCode={aiAlarmCode}
                aiNeName={aiNeName}
                aiSeverity={aiSeverity}
                onChangeAiAlarmCode={setAiAlarmCode}
                onChangeAiNeName={setAiNeName}
                onChangeAiSeverity={setAiSeverity}
                onResetAiFilters={() => {
                  setAiAlarmCode("");
                  setAiNeName("");
                  setAiSeverity("");
                }}
                question={question}
                answer={answer}
                isRunning={analyzeMutation.isPending}
                enabled={capabilities.aiAnalysis && Boolean(selectedBatch)}
                onChangeQuestion={setQuestion}
                onRunAnalyze={() => analyzeMutation.mutate()}
              />
            }
          />
          <Route path="/jobs" element={<JobsPage />} />
        </Routes>
        {toast && <div className={`toast toast--${toast.type}`}>{toast.text}</div>}
    </AppLayout>
  );
}

export default App;
