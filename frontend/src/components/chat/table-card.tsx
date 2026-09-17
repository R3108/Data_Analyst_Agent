"use client";

import { Download } from "lucide-react";

import type { TableOutput } from "@/lib/types";

import { DataTable, toCsv } from "./data-table";

export function TableCard({ table, actions }: { table: TableOutput; actions?: React.ReactNode }) {
  const download = () => {
    const blob = new Blob([toCsv(table.columns, table.rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${table.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "table"}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="print-avoid-break rounded-xl border border-line bg-panel p-4 shadow-card">
      <div className="mb-2.5 flex items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{table.title}</p>
        <span className="text-xs text-ink-3">{table.total_rows.toLocaleString()} rows</span>
        <div className="no-print flex items-center gap-0.5">
          <button
            onClick={download}
            className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-ink-2 transition hover:bg-muted hover:text-ink"
          >
            <Download className="size-3.5" />
            CSV
          </button>
          {actions}
        </div>
      </div>
      <DataTable columns={table.columns} rows={table.rows} totalRows={table.total_rows} maxHeight={320} />
    </div>
  );
}
