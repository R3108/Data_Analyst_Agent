import { Info, TrendingDown, TrendingUp } from "lucide-react";

import { HOVER_ACTION } from "@/components/pin";
import { cn } from "@/lib/cn";
import { formatDelta, formatValue } from "@/lib/format";
import type { Kpi } from "@/lib/types";

export function KpiGrid({
  kpis,
  renderAction,
}: {
  kpis: Kpi[];
  renderAction?: (index: number, kpi: Kpi) => React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(168px,1fr))]">
      {kpis.map((kpi, i) => (
        <KpiTile key={`${kpi.label}-${i}`} kpi={kpi} action={renderAction?.(i, kpi)} />
      ))}
    </div>
  );
}

export function KpiTile({ kpi, action }: { kpi: Kpi; action?: React.ReactNode }) {
  const hasDelta = typeof kpi.delta === "number" && Number.isFinite(kpi.delta);
  const up = hasDelta && (kpi.delta as number) > 0;
  const flat = hasDelta && kpi.delta === 0;
  const good = hasDelta && !flat && up === kpi.higher_is_better;

  return (
    <div className="group relative h-full rounded-xl border border-line bg-panel p-3.5 shadow-card">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[13px] leading-snug text-ink-2">{kpi.label}</p>
        <div className="-my-1 -mr-1.5 flex items-center gap-0.5">
          {kpi.description && (
            <span title={kpi.description} className="p-1 text-ink-3">
              <Info className="size-3.5" />
            </span>
          )}
          {action && <div className={HOVER_ACTION}>{action}</div>}
        </div>
      </div>
      <p className="mt-1.5 truncate text-2xl font-semibold tracking-tight text-ink" title={String(kpi.value ?? "")}>
        {formatValue(kpi.value, kpi.format)}
      </p>
      {hasDelta && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
          <span
            className={cn(
              "inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 font-medium",
              flat ? "bg-muted text-ink-2" : good ? "bg-good-soft text-good" : "bg-bad-soft text-bad",
            )}
          >
            {!flat && (up ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />)}
            {formatDelta(kpi.delta as number)}
          </span>
          {kpi.delta_label && <span className="text-ink-3">{kpi.delta_label}</span>}
        </div>
      )}
    </div>
  );
}
