"use client";

import { CircleCheck, CircleSlash, Compass, HelpCircle, TriangleAlert } from "lucide-react";

import { Badge, SectionLabel } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import type { Investigation } from "@/lib/types";

/**
 * The map of an investigation: which questions it asked, which of them actually produced
 * a trustworthy answer, and what it could not cover.
 *
 * Every step links to the full analysis it came from — with its code, charts and
 * verification verdict — so the brief above it is never the only thing a reader can see.
 */
export function InvestigationCard({
  investigation,
  degraded,
  onOpenStep,
}: {
  investigation: Investigation;
  degraded?: boolean;
  onOpenStep?: (messageId: string) => void;
}) {
  const failed = investigation.total - investigation.completed;

  return (
    <section className="rounded-xl border border-line bg-panel p-4 shadow-card">
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <SectionLabel icon={<Compass className="size-3.5" />}>Investigation</SectionLabel>
        <span className="mb-2.5 flex items-center gap-1.5">
          <Badge tone={failed ? "warn" : "good"}>
            {investigation.completed} of {investigation.total} analyses completed
          </Badge>
          {degraded && <Badge tone="warn">Brief unavailable</Badge>}
        </span>
      </div>

      <p className="text-[13.5px] leading-relaxed text-ink-2">{investigation.objective}</p>

      <ol className="mt-3 space-y-1.5">
        {investigation.steps.map((step, index) => {
          const clickable = Boolean(onOpenStep && step.message_id);
          return (
            <li key={step.message_id || index}>
              <button
                type="button"
                disabled={!clickable}
                onClick={() => onOpenStep?.(step.message_id)}
                className={cn(
                  "flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left transition",
                  clickable ? "hover:bg-muted" : "cursor-default",
                )}
              >
                <span className="mt-0.5 shrink-0">
                  {step.ok ? (
                    <CircleCheck className="size-4 text-good" />
                  ) : (
                    <CircleSlash className="size-4 text-bad" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-medium text-ink">{step.question}</span>
                  <span className="block text-[12.5px] leading-relaxed text-ink-2">
                    {step.ok ? step.headline : (step.error ?? "This step did not complete.")}
                  </span>
                  <span className="mt-0.5 block text-[11.5px] text-ink-3">{step.why}</span>
                </span>
                {step.verification_score !== null && (
                  <span
                    className={cn(
                      "shrink-0 pt-0.5 text-[11.5px] tabular-nums",
                      step.verification_score >= 80
                        ? "text-good"
                        : step.verification_score >= 55
                          ? "text-warn"
                          : "text-bad",
                    )}
                    title="Deterministic verification score for this step"
                  >
                    {step.verification_score}/100
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>

      {investigation.out_of_scope.length > 0 && (
        <div className="mt-3 border-t border-line pt-3">
          <p className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-ink-2">
            <TriangleAlert className="size-3.5 text-warn" />
            What this dataset cannot answer
          </p>
          <ul className="space-y-1 text-[12.5px] leading-relaxed text-ink-2">
            {investigation.out_of_scope.map((item) => (
              <li key={item}>• {item}</li>
            ))}
          </ul>
        </div>
      )}

      {investigation.open_questions.length > 0 && (
        <div className="mt-3 border-t border-line pt-3">
          <p className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-ink-2">
            <HelpCircle className="size-3.5 text-ink-3" />
            Worth looking at next
          </p>
          <ul className="space-y-1 text-[12.5px] leading-relaxed text-ink-2">
            {investigation.open_questions.map((item) => (
              <li key={item}>• {item}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
