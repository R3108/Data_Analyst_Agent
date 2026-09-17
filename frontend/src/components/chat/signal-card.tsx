"use client";

import {
  Activity,
  ArrowRight,
  CalendarRange,
  ChartPie,
  GitCompareArrows,
  ShieldAlert,
  Shuffle,
  Split,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import { cn } from "@/lib/cn";
import type { Signal } from "@/lib/types";

import { Sparkline } from "./sparkline";

const KINDS: Record<Signal["kind"], { label: string; icon: typeof Activity }> = {
  trend: { label: "Trend", icon: TrendingUp },
  seasonality: { label: "Seasonality", icon: CalendarRange },
  anomaly: { label: "Anomaly", icon: Activity },
  concentration: { label: "Concentration", icon: ChartPie },
  mix_shift: { label: "Mix shift", icon: Shuffle },
  correlation: { label: "Relationship", icon: GitCompareArrows },
  quality: { label: "Data quality", icon: ShieldAlert },
};

const TONES: Record<Signal["severity"], string> = {
  positive: "bg-good-soft text-good",
  negative: "bg-bad-soft text-bad",
  warning: "bg-warn-soft text-warn",
  neutral: "bg-accent-soft text-accent-ink",
};

export function SignalCard({
  signal,
  onInvestigate,
  onExplain,
  disabled = false,
}: {
  signal: Signal;
  onInvestigate: (question: string) => void;
  /** Opens the deterministic drill-down aimed at whatever this signal spotted. */
  onExplain?: (query: { measure: string; focus?: string }) => void;
  disabled?: boolean;
}) {
  const kind = KINDS[signal.kind] ?? KINDS.trend;
  const Icon = signal.kind === "trend" && signal.severity === "negative" ? TrendingDown : kind.icon;

  return (
    <div className="flex flex-col rounded-xl border border-line bg-panel p-4 shadow-card">
      <div className="flex items-center gap-2">
        <span className={cn("flex size-6 items-center justify-center rounded-md", TONES[signal.severity])}>
          <Icon className="size-3.5" />
        </span>
        <span className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">{kind.label}</span>
      </div>
      <p className="mt-2.5 text-[14px] leading-snug font-semibold text-ink">{signal.title}</p>
      <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{signal.detail}</p>

      {signal.sparkline && signal.sparkline.length > 1 && <Sparkline values={signal.sparkline} className="mt-3" />}

      {signal.breakdown && signal.breakdown.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {signal.breakdown.map((row) => (
            <div key={row.label} className="flex items-center gap-2 text-[12px]">
              <span className="w-20 shrink-0 truncate text-ink-2" title={row.label}>
                {row.label}
              </span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-subtle">
                <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.max(2, row.share * 100)}%` }} />
              </span>
              <span className="w-9 shrink-0 text-right text-ink-3 tabular-nums">{Math.round(row.share * 100)}%</span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-auto flex items-center gap-3 pt-3">
        <button
          disabled={disabled}
          onClick={() => onInvestigate(signal.question)}
          className="group inline-flex items-center gap-1 text-[13px] font-medium text-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
        >
          Investigate
          <ArrowRight className="size-3.5 transition group-hover:translate-x-0.5" />
        </button>
        {onExplain && signal.explain && (
          <button
            onClick={() =>
              onExplain({ measure: signal.explain!.measure, focus: signal.explain!.dimension })
            }
            title="Break the change down deterministically — no model call"
            className="inline-flex items-center gap-1 text-[13px] font-medium text-ink-2 transition hover:text-ink"
          >
            <Split className="size-3.5" />
            Break it down
          </button>
        )}
      </div>
    </div>
  );
}
