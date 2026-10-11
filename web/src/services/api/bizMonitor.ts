import { apiDelete, apiGet, apiPatch, apiPost } from "./http";

export const bizMonitorListTemplates = () =>
  apiGet<{ items: Record<string, unknown>[] }>("/v1/biz-migration/monitor-templates");

export const bizMonitorCreateTemplate = (body: Record<string, unknown>) =>
  apiPost<Record<string, unknown>>("/v1/biz-migration/monitor-templates", body);

export const bizMonitorUpdateTemplate = (templateId: string, body: Record<string, unknown>) =>
  apiPatch<Record<string, unknown>>(
    `/v1/biz-migration/monitor-templates/${encodeURIComponent(templateId)}`,
    body,
  );

export const bizMonitorDeleteTemplate = (templateId: string) =>
  apiDelete<{ ok: boolean }>(
    `/v1/biz-migration/monitor-templates/${encodeURIComponent(templateId)}`,
  );
