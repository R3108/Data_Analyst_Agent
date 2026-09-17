"use client";

import { ChevronDown, CircleCheck, CircleX, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";
import type { Step } from "@/lib/types";

export function AgentTimeline({ steps, live, summary }: { steps: Step[]; live: boolean; summary?: string }) {
  const [open, setOpen] = useState(live);

  useEffect(() => {
    if (live) setOpen(true);
  }, [live]);

  const running = [...steps].reverse().find((s) => s.status === "running");
  const errors = steps.filter((s) => s.status === "error").length;

  return (
    <div className="rounded-xl border border-line bg-panel/60">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px]"
        aria-expanded={open}
      >
        {live ? (
          <LoaderCircle className="size-4 shrink-0 animate-spin text-accent" />
        ) : (
          <CircleCheck className="size-4 shrink-0 text-ink-3" />
        )}
        <span className={cn("min-w-0 flex-1 truncate", live ? "shimmer-text font-medium" : "text-ink-2")}>
          {live ? `${running?.label ?? "Thinking"}…` : summary ?? `${steps.length} steps`}
        </span>
        {!live && errors > 0 && (
          <span className="text-xs text-ink-3">
            {errors} self-correction{errors > 1 ? "s" : ""}
          </span>
        )}
        <ChevronDown className={cn("size-4 shrink-0 text-ink-3 transition-transform", open && "rotate-180")} />
      </button>

      {open && steps.length > 0 && (
        <ol className="relative space-y-3 border-t border-line px-3.5 py-3">
          {steps.map((step, index) => (
            <li key={step.id} className="relative flex gap-3">
              {index < steps.length - 1 && (
                <span className="absolute top-5 bottom-[-12px] left-[7px] w-px bg-line" aria-hidden="true" />
              )}
              <StepIcon status={step.status} />
              <div className="min-w-0 flex-1 pb-0.5">
                <p className={cn("text-[13px] font-medium", step.status === "error" ? "text-bad" : "text-ink")}>
                  {step.label}
                  {step.attempt && step.attempt > 1 ? (
                    <span className="ml-1.5 font-normal text-ink-3">attempt {step.attempt}</span>
                  ) : null}
                </p>
                {step.detail && <p className="mt-0.5 text-[13px] leading-snug break-words text-ink-2">{step.detail}</p>}
                {step.items && step.items.length > 0 && (
                  <ol className="mt-1.5 list-decimal space-y-0.5 pl-4 text-[13px] text-ink-2 marker:text-ink-3">
                    {step.items.map((item, i) => (
                      <li key={i}>{item}</li>
                    ))}
                  </ol>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function StepIcon({ status }: { status: Step["status"] }) {
  const base = "relative z-10 mt-0.5 size-[15px] shrink-0 rounded-full bg-panel";
  if (status === "running") return <LoaderCircle className={cn(base, "animate-spin text-accent")} />;
  if (status === "error") return <CircleX className={cn(base, "text-bad")} />;
  return <CircleCheck className={cn(base, "text-good")} />;
}
