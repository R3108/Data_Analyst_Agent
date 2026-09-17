"use client";

import { History } from "lucide-react";

import { Badge } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { formatValue, relativeTime } from "@/lib/format";
import type { RecallSummary } from "@/lib/types";

/**
 * "You asked this before."
 *
 * Shown before the work starts, so a reader who recognises the earlier answer can stop
 * the run instead of paying for it twice. The figures are explicitly labelled as
 * historical — the agent is given the same warning, and the verifier flags any number it
 * repeats that this run did not compute.
 */
export function RecallNotice({
  recall,
  onOpen,
  className,
}: {
  recall: RecallSummary;
  onOpen?: (sessionId: string) => void;
  className?: string;
}) {
  if (!recall.matches.length) return null;

  return (
    <aside
      className={cn(
        "rounded-xl border p-3.5",
        recall.duplicate ? "border-warn/30 bg-warn-soft" : "border-line bg-subtle",
        className,
      )}
    >
      <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
        <History className="size-3.5 shrink-0 text-ink-3" />
        {recall.headline}
      </p>
      <ul className="mt-2 space-y-2">
        {recall.matches.map((match) => {
          const clickable = Boolean(onOpen);
          return (
            <li key={match.message_id}>
              <button
                type="button"
                disabled={!clickable}
                onClick={() => onOpen?.(match.session_id)}
                className={cn(
                  "w-full rounded-lg px-2 py-1.5 text-left transition",
                  clickable ? "hover:bg-muted" : "cursor-default",
                )}
              >
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-[13px] text-ink">“{match.question}”</span>
                  <span className="text-[11px] text-ink-3">{relativeTime(match.created_at)}</span>
                  {match.duplicate && <Badge tone="warn">Same question</Badge>}
                </span>
                {match.headline && (
                  <span className="mt-0.5 block text-[12.5px] leading-relaxed text-ink-2">
                    {match.headline}
                  </span>
                )}
                {match.kpis.length > 0 && (
                  <span className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-ink-3">
                    {match.kpis.slice(0, 3).map((kpi) => (
                      <span key={kpi.label} className="tabular-nums">
                        {kpi.label}: {formatValue(kpi.value, kpi.format)}
                      </span>
                    ))}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-1.5 px-2 text-[11.5px] leading-relaxed text-ink-3">
        These figures are from the earlier run, not this one — the data may have changed since.
      </p>
    </aside>
  );
}
