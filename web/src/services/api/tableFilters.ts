import type { TableColumnFilters } from "../../types/tableFilters";
import { apiGet } from "./http";

export function appendColumnFilters(params: URLSearchParams, filters?: TableColumnFilters) {
  const active = Object.fromEntries(Object.entries(filters ?? {}).filter(([, filter]) => filter !== null));
  if (Object.keys(active).length) params.set("column_filters", JSON.stringify(active));
}

export function fetchTableFilterOptions(endpoint: string, field: string, scope: Record<string, string> = {}) {
  const params = new URLSearchParams(scope);
  params.set("filter_field", field);
  return apiGet<{ field: string; options: string[] }>(`${endpoint}?${params}`).then((data) => data.options);
}
