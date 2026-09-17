"use client";

import { useEffect, useRef, useState } from "react";

import { PLOTLY_CONFIG, themeFigure } from "@/lib/plotly-theme";
import { useTheme } from "@/lib/theme";
import type { ChartOutput } from "@/lib/types";

interface PlotlyApi {
  react: (root: HTMLElement, data: unknown[], layout?: object, config?: object) => Promise<HTMLElement>;
  purge: (root: HTMLElement) => void;
  Plots: { resize: (root: HTMLElement) => void };
}

let plotlyPromise: Promise<PlotlyApi> | null = null;

function loadPlotly(): Promise<PlotlyApi> {
  plotlyPromise ??= import("plotly.js-dist-min").then(
    (mod) => ((mod as unknown as { default?: PlotlyApi }).default ?? mod) as PlotlyApi,
  );
  return plotlyPromise;
}

export function PlotlyFigure({ figure, height = 320 }: { figure: ChartOutput["figure"]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();
  const [status, setStatus] = useState<"loading" | "ready" | "failed">("loading");

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let disposed = false;
    let plotly: PlotlyApi | null = null;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (plotly && element.isConnected) plotly.Plots.resize(element);
      });
    });

    loadPlotly()
      .then(async (mod) => {
        if (disposed) return;
        plotly = mod;
        const { data, layout } = themeFigure(figure, resolved, height);
        await mod.react(element, data, layout, PLOTLY_CONFIG);
        if (!disposed) {
          setStatus("ready");
          observer.observe(element);
        }
      })
      .catch(() => !disposed && setStatus("failed"));

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      if (plotly) plotly.purge(element);
    };
  }, [figure, resolved, height]);

  return (
    <div className="relative w-full" style={{ height }}>
      {status === "loading" && <div className="absolute inset-0 animate-pulse rounded-lg bg-muted" />}
      {status === "failed" && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-ink-3">
          This chart could not be rendered.
        </div>
      )}
      <div ref={ref} className="h-full w-full" />
    </div>
  );
}
