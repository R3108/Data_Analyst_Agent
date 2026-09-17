import { cn } from "@/lib/cn";
import { formatCell } from "@/lib/format";

interface Column {
  name: string;
  kind?: string;
}

function inferKind(rows: unknown[][], index: number): string {
  for (const row of rows) {
    const value = row[index];
    if (value === null || value === undefined) continue;
    if (typeof value === "number") return "number";
    if (typeof value === "boolean") return "boolean";
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) return "datetime";
    return "text";
  }
  return "text";
}

export function DataTable({
  columns,
  rows,
  totalRows,
  maxHeight = 360,
  className,
}: {
  columns: Column[];
  rows: unknown[][];
  totalRows?: number;
  /** A number is pixels; a CSS length lets a table grow with the surface it sits on. */
  maxHeight?: number | string;
  className?: string;
}) {
  const kinds = columns.map((column, i) => column.kind ?? inferKind(rows, i));
  const total = totalRows ?? rows.length;

  return (
    <div className={className}>
      <div className="overflow-auto rounded-lg border border-line" style={{ maxHeight }}>
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10">
            <tr>
              {columns.map((column, i) => (
                <th
                  key={`${column.name}-${i}`}
                  scope="col"
                  className={cn(
                    "border-b border-line bg-muted px-3 py-2 font-medium whitespace-nowrap text-ink-2",
                    kinds[i] === "number" ? "text-right" : "text-left",
                  )}
                >
                  {column.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="transition-colors hover:bg-muted/60">
                {row.map((value, c) => (
                  <td
                    key={c}
                    className={cn(
                      "max-w-[280px] truncate border-b border-line px-3 py-1.5 whitespace-nowrap",
                      kinds[c] === "number" && "text-right tabular-nums",
                    )}
                    title={typeof value === "string" && value.length > 40 ? value : undefined}
                  >
                    {value === null || value === undefined ? (
                      <span className="text-ink-3">—</span>
                    ) : (
                      formatCell(value, kinds[c])
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > rows.length && (
        <p className="mt-1.5 text-xs text-ink-3">
          Showing {rows.length.toLocaleString()} of {total.toLocaleString()} rows
        </p>
      )}
    </div>
  );
}

export function toCsv(columns: Column[], rows: unknown[][]): string {
  const escape = (value: unknown) => {
    if (value === null || value === undefined) return "";
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [columns.map((c) => escape(c.name)).join(","), ...rows.map((row) => row.map(escape).join(","))].join("\n");
}
