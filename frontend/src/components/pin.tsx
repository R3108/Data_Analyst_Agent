"use client";

import { LayoutDashboard, LoaderCircle, Pin, Plus } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState } from "react";

import { IconButton } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import type {
  BoardSummary,
  CohortQuery,
  DriverQuery,
  ForecastQuery,
  PinKind,
  ScenarioQuery,
  SignificanceQuery,
} from "@/lib/types";

interface PinTargetBase {
  index: number;
  label: string;
}

/** The deterministic views that are pinned by provenance rather than by message. */
export type ComputedSource = "drivers" | "significance" | "scenarios" | "cohorts" | "forecast";

/**
 * Two provenances, never a payload: an analysis result is addressed by message, a
 * computed result by the query that produced it. The server recomputes and snapshots
 * either one, so a client can never pin content of its own invention.
 */
export type PinTarget =
  | (PinTargetBase & { kind: PinKind; source?: "analysis"; messageId: string })
  | (PinTargetBase & {
      kind: "chart" | "table";
      source: ComputedSource;
      datasetId: string;
      params: DriverQuery | SignificanceQuery | ScenarioQuery | CohortQuery | ForecastQuery;
    });

interface PinContextValue {
  boards: BoardSummary[];
  pin: (target: PinTarget, boardId: string | null, newBoardTitle?: string) => Promise<void>;
}

const PinContext = createContext<PinContextValue | null>(null);

export const PinProvider = PinContext.Provider;

/**
 * Classes for hover-revealed actions: always visible on touch, revealed on hover for pointer
 * devices. Laid out as a row — a group of actions must not stack and inflate its card.
 */
export const HOVER_ACTION =
  "flex items-center gap-0.5 transition sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100 sm:[&:has([data-open=true])]:opacity-100";

/** Renders nothing outside a PinProvider (e.g. on shared, read-only pages). */
export function PinButton({ target, className }: { target: PinTarget; className?: string }) {
  const context = useContext(PinContext);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
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

  if (!context) return null;

  const pinTo = async (boardId: string | null) => {
    setBusy(true);
    try {
      await context.pin(target, boardId, title.trim() || undefined);
      setOpen(false);
      setTitle("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={root} data-open={open} className={cn("relative", className)}>
      <IconButton
        label={`Pin “${target.label}” to a board`}
        onClick={() => setOpen((v) => !v)}
        size="sm"
        className={cn(open && "bg-muted text-ink")}
        aria-expanded={open}
      >
        <Pin className="size-3.5" />
      </IconButton>
      {open && (
        <div className="animate-rise absolute top-full right-0 z-40 mt-1 w-64 rounded-xl border border-line bg-panel p-1.5 text-left shadow-pop">
          <p className="px-2 pt-1 pb-1.5 text-[11px] font-medium tracking-wide text-ink-3 uppercase">Pin to board</p>
          {context.boards.length > 0 && (
            <div className="max-h-48 overflow-y-auto">
              {context.boards.map((board) => (
                <button
                  key={board.id}
                  disabled={busy}
                  onClick={() => pinTo(board.id)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] text-ink transition hover:bg-muted disabled:opacity-50"
                >
                  <LayoutDashboard className="size-3.5 shrink-0 text-ink-3" />
                  <span className="min-w-0 flex-1 truncate">{board.title}</span>
                  <span className="text-[11px] text-ink-3 tabular-nums">{board.item_count}</span>
                </button>
              ))}
            </div>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void pinTo(null);
            }}
            className={cn("flex gap-1 p-1", context.boards.length > 0 && "mt-1 border-t border-line pt-2")}
          >
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="New board name…"
              maxLength={120}
              className="h-8 min-w-0 flex-1 rounded-md bg-muted px-2 text-[13px] text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
            />
            <button
              type="submit"
              disabled={busy}
              aria-label="Create board and pin"
              className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-white transition hover:bg-accent-hover disabled:opacity-60"
            >
              {busy ? <LoaderCircle className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
