import { useState } from "react";
import { ColumnFilterMenu } from "../components/ui/ColumnFilterMenu";
import type { TableColumnFilters } from "../types/tableFilters";

/** For detail tables whose complete displayed dataset is already in memory. */
export function useLocalTableColumnFilters<T>(scope: string, rows: T[], values: (row: T) => Record<string, string>) {
  const [filters, setFilters] = useState<TableColumnFilters>({});
  return {
    items: rows.filter((row) => Object.entries(filters).every(([field, filter]) => {
      if (!filter) return true;
      const included = filter.values.includes(values(row)[field] ?? "");
      return filter.mode === "include" ? included : !included;
    })),
    reset: () => setFilters({}),
    column: (field: string, label: string) => (
      <span className="ne-column-heading">
        {label}
        <ColumnFilterMenu label={label} value={filters[field] ?? null}
          queryKey={["localTableFilterOptions", scope, field, JSON.stringify(rows.map((row) => values(row)[field] ?? ""))]}
          loadOptions={() => Promise.resolve([...new Set(rows.map((row) => values(row)[field] ?? ""))].sort())}
          onApply={(value) => setFilters((current) => ({ ...current, [field]: value }))} />
      </span>
    ),
  };
}
