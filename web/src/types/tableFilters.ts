export type ColumnValueFilter = { mode: "include" | "exclude"; values: string[] } | null;
export type TableColumnFilters = Record<string, ColumnValueFilter>;
