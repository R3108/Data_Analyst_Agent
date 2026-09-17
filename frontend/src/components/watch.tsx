"use client";

import { Eye, LoaderCircle } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState } from "react";

import { IconButton } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { KpiFormat, Monitor, MonitorDirection } from "@/lib/types";

export interface WatchTarget {
  messageId: string;
  index: number;
  label: string;
  value: number | null;
  format: KpiFormat;
}

interface WatchContextValue {
  onCreated?: (monitor: Monitor) => void;
}

const WatchContext = createContext<WatchContextValue | null>(null);

export const WatchProvider = WatchContext.Provider;

const DIRECTIONS: { id: MonitorDirection; label: string }[] = [
  { id: "below", label: "Falls below" },
  { id: "above", label: "Rises above" },
  { id: "change_pct", label: "Moves by more than" },
];

/** Sensible starting threshold so one click is usually enough. */
function suggestThreshold(direction: MonitorDirection, value: number | null): string {
  if (direction === "change_pct") return "10";
  if (typeof value !== "number" || !Number.isFinite(value) || value === 0) return "0";
  const target = direction === "below" ? value * 0.9 : value * 1.1;
  const magnitude = Math.abs(target);
  const decimals = magnitude >= 1000 ? 0 : magnitude >= 1 ? 2 : 4;
  return target.toFixed(decimals);
}

/** Renders nothing outside a WatchProvider (e.g. on shared, read-only pages). */
export function WatchButton({ target, className }: { target: WatchTarget; className?: string }) {
  const context = useContext(WatchContext);
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [direction, setDirection] = useState<MonitorDirection>("below");
  const [threshold, setThreshold] = useState(() => suggestThreshold("below", target.value));
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!context || typeof target.value !== "number") return null;

  const isPercentRule = direction === "change_pct";
  const pick = (next: MonitorDirection) => {
    setDirection(next);
    setThreshold(suggestThreshold(next, target.value));
  };

  const submit = async () => {
    const parsed = Number(threshold);
    if (!Number.isFinite(parsed)) {
      toast.error("Enter a number", "The alert threshold must be numeric.");
      return;
    }
    setBusy(true);
    try {
      const monitor = await api.createMonitor({
        message_id: target.messageId,
        index: target.index,
        direction,
        // A percentage move is stored as a fraction, matching KPI deltas.
        threshold: isPercentRule ? parsed / 100 : parsed,
        title: target.label,
      });
      context.onCreated?.(monitor);
      toast.success(`Watching ${monitor.title}`, `Alerts when it ${monitor.rule.replace("alert ", "")}.`);
      setOpen(false);
    } catch (error) {
      toast.error("Couldn't start watching", error instanceof ApiError ? error.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={root} data-open={open} className={cn("relative", className)}>
      <IconButton
        label={`Watch “${target.label}” and alert me when it changes`}
        onClick={() => setOpen((value) => !value)}
        size="sm"
        className={cn(open && "bg-muted text-ink")}
        aria-expanded={open}
      >
        <Eye className="size-3.5" />
      </IconButton>

      {/* Kept narrow: anchored to a ~200px KPI tile, a wider panel overhangs the column. */}
      {open && (
        <div className="animate-rise absolute top-full right-0 z-40 mt-1 w-64 rounded-xl border border-line bg-panel p-2.5 text-left shadow-pop">
          <p className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">Watch this metric</p>
          <p className="mt-1 text-[12.5px] leading-snug text-ink-2">
            Re-checks <span className="font-medium text-ink">{target.label}</span> whenever new data
            arrives — no model tokens used.
          </p>

          <p className="mt-2.5 text-[11px] font-medium tracking-wide text-ink-3 uppercase">Alert me when it</p>
          <div className="mt-1 space-y-0.5">
            {DIRECTIONS.map((option) => (
              <label
                key={option.id}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] whitespace-nowrap transition",
                  direction === option.id ? "bg-accent-soft text-accent-ink" : "text-ink hover:bg-muted",
                )}
              >
                <input
                  type="radio"
                  name="watch-direction"
                  checked={direction === option.id}
                  onChange={() => pick(option.id)}
                  className="accent-accent"
                />
                {option.label}
              </label>
            ))}
          </div>

          <div className="mt-2 flex items-center gap-1.5">
            <input
              value={threshold}
              onChange={(event) => setThreshold(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && void submit()}
              inputMode="decimal"
              aria-label="Alert threshold"
              className="h-8 min-w-0 flex-1 rounded-md bg-muted px-2 text-[13px] text-ink tabular-nums outline-none focus-visible:outline-none"
            />
            <span className="shrink-0 text-[12px] text-ink-3">
              {isPercentRule ? "% change" : target.format === "percent" ? "(fraction)" : ""}
            </span>
          </div>

          <div className="mt-2.5 flex items-center justify-end gap-2">
            <button
              onClick={() => setOpen(false)}
              className="h-8 rounded-lg px-2.5 text-[13px] text-ink-2 transition hover:bg-muted hover:text-ink"
            >
              Cancel
            </button>
            <button
              onClick={() => void submit()}
              disabled={busy}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[13px] font-medium text-white transition hover:bg-accent-hover disabled:opacity-60"
            >
              {busy && <LoaderCircle className="size-3.5 animate-spin" />}
              Start watching
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
