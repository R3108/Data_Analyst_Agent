import { Lightbulb, TrendingDown, TrendingUp } from "lucide-react";

import { HOVER_ACTION } from "@/components/pin";
import { cn } from "@/lib/cn";
import type { Insight } from "@/lib/types";

const SENTIMENT = {
  positive: { icon: TrendingUp, cls: "bg-good-soft text-good" },
  negative: { icon: TrendingDown, cls: "bg-bad-soft text-bad" },
  neutral: { icon: Lightbulb, cls: "bg-accent-soft text-accent-ink" },
};

export function InsightCard({ insight, action }: { insight: Insight; action?: React.ReactNode }) {
  const config = SENTIMENT[insight.sentiment] ?? SENTIMENT.neutral;
  const Icon = config.icon;
  return (
    <div className="group relative h-full rounded-xl border border-line bg-panel p-3.5 shadow-card">
      <div className="flex items-start gap-2.5">
        <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-md", config.cls)}>
          <Icon className="size-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[14px] leading-snug font-semibold text-ink">{insight.title}</p>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{insight.detail}</p>
        </div>
        {action && <div className={cn("-my-1 -mr-1.5", HOVER_ACTION)}>{action}</div>}
      </div>
    </div>
  );
}
