"use client";

import { Maximize2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { IconButton } from "@/components/ui/primitives";
import type { ChartOutput } from "@/lib/types";

import { PlotlyFigure } from "./plotly-figure";

export function ChartCard({ chart, actions }: { chart: ChartOutput; actions?: React.ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  const [expandedHeight, setExpandedHeight] = useState(560);

  useEffect(() => {
    if (!expanded) return;
    setExpandedHeight(Math.max(320, Math.min(680, window.innerHeight - 180)));
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setExpanded(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  return (
    <figure className="print-avoid-break rounded-xl border border-line bg-panel p-4 shadow-card">
      <div className="mb-2 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <figcaption className="text-sm font-semibold text-ink">{chart.title}</figcaption>
          {chart.caption && <p className="mt-0.5 text-[13px] leading-snug text-ink-2">{chart.caption}</p>}
        </div>
        <div className="no-print flex items-center gap-0.5">
          {actions}
          <IconButton label="Expand chart" onClick={() => setExpanded(true)} size="sm">
            <Maximize2 className="size-3.5" />
          </IconButton>
        </div>
      </div>
      <PlotlyFigure figure={chart.figure} height={320} />

      {expanded && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={chart.title}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
          onClick={() => setExpanded(false)}
        >
          <div
            className="animate-rise w-full max-w-6xl rounded-2xl border border-line bg-panel p-5 shadow-pop"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-base font-semibold text-ink">{chart.title}</p>
                {chart.caption && <p className="mt-0.5 text-sm text-ink-2">{chart.caption}</p>}
              </div>
              <IconButton label="Close" onClick={() => setExpanded(false)}>
                <X className="size-4" />
              </IconButton>
            </div>
            <PlotlyFigure figure={chart.figure} height={expandedHeight} />
          </div>
        </div>
      )}
    </figure>
  );
}
