import { useState } from "react";
import { ColumnFilterMenu } from "../components/ui/ColumnFilterMenu";
import { fetchTableFilterOptions } from "../services/api/tableFilters";
import type { ColumnValueFilter, TableColumnFilters } from "../types/tableFilters";

export function useTableColumnFilters(endpoint: string, onChange: () => void, scope: Record<string, string> = {}) {
  const [filters, setFilters] = useState<TableColumnFilters>({});
  const [currentEndpoint, setCurrentEndpoint] = useState(endpoint);
  if (currentEndpoint !== endpoint) {
    setCurrentEndpoint(endpoint);
    setFilters({});
  }
  const scopeKey = JSON.stringify(scope);
  const setFilter = (field: string, value: ColumnValueFilter) => {
    setFilters((current) => ({ ...current, [field]: value }));
    onChange();
  };
  return {
    filters,
    key: JSON.stringify(filters),
    active: Object.values(filters).some(Boolean),
    setFilter,
    reset: () => { setFilters({}); onChange(); },
    column: (field: string, label: string, formatValue?: (value: string) => string) => (
      <span className="ne-column-heading">
        {label}
        <ColumnFilterMenu label={label} value={filters[field] ?? null}
          queryKey={["tableFilterOptions", endpoint, scopeKey, field]}
          loadOptions={() => fetchTableFilterOptions(endpoint, field, scope)}
          formatValue={formatValue} onApply={(value) => setFilter(field, value)} />
      </span>
    ),
  };
}
