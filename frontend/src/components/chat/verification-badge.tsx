"use client";

import { ChevronDown, ShieldAlert, ShieldCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/cn";
import type { Verification, VerificationFinding, VerificationSeverity } from "@/lib/types";

const CONFIDENCE: Record<
  Verification["confidence"],
  { label: string; chip: string; icon: typeof ShieldCheck }
> = {
  high: { label: "Verified", chip: "bg-good-soft text-good", icon: ShieldCheck },
  medium: { label: "Verified with notes", chip: "bg-warn-soft text-warn", icon: TriangleAlert },
  low: { label: "Needs review", chip: "bg-bad-soft text-bad", icon: ShieldAlert },
  unverified: { label: "Not verified", chip: "bg-muted text-ink-2", icon: ShieldAlert },
};

const SEVERITY: Record<VerificationSeverity, string> = {
  high: "bg-bad-soft text-bad",
  medium: "bg-warn-soft text-warn",
  low: "bg-muted text-ink-2",
};

/**
 * Result of the deterministic post-analysis audit: whether every figure in the
 * write-up traces back to computed output, plus anything worth a second look.
 */
export function VerificationBadge({
  verification,
  defaultOpen,
}: {
  verification: Verification;
  defaultOpen?: boolean;
}) {
  const findings = verification.findings ?? [];
  const hasHigh = findings.some((f) => f.severity === "high");
  const [open, setOpen] = useState(defaultOpen ?? hasHigh);

  if (verification.score === null) return null;
  const tone = CONFIDENCE[verification.confidence] ?? CONFIDENCE.unverified;
  const Icon = tone.icon;

  return (
    <section
      className={cn(
        "print-avoid-break rounded-xl border bg-panel",
        hasHigh ? "border-bad/30" : "border-line",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        disabled={findings.length === 0}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left disabled:cursor-default"
      >
        <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-md", tone.chip)}>
          <Icon className="size-3.5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[13px] font-semibold text-ink">{tone.label}</span>
            <span className="text-[12px] text-ink-3 tabular-nums">
              {verification.score}/100 · {verification.checks} checks
            </span>
          </span>
          <span className="mt-0.5 block text-[12.5px] leading-snug text-ink-2">{verification.summary}</span>
        </span>
        {findings.length > 0 && (
          <ChevronDown
            className={cn("size-4 shrink-0 text-ink-3 transition-transform", open && "rotate-180")}
          />
        )}
      </button>

      {open && findings.length > 0 && (
        <ul className="space-y-2.5 border-t border-line px-3.5 py-3">
          {findings.map((finding) => (
            <FindingRow key={finding.id + finding.title} finding={finding} />
          ))}
        </ul>
      )}
    </section>
  );
}

function FindingRow({ finding }: { finding: VerificationFinding }) {
  return (
    <li className="text-[12.5px] leading-relaxed">
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10.5px] font-medium tracking-wide uppercase",
            SEVERITY[finding.severity],
          )}
        >
          {finding.severity}
        </span>
        <span className="font-medium text-ink">{finding.title}</span>
      </div>
      <p className="mt-0.5 text-ink-2">{finding.detail}</p>
    </li>
  );
}
