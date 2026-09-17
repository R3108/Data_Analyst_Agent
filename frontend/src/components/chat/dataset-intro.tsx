"use client";

import { ArrowRight, BarChart3, Check, Radar, ShieldAlert, Sparkles, Telescope, Target } from "lucide-react";

import { Badge, SectionLabel } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { formatValue } from "@/lib/format";
import type { Dataset } from "@/lib/types";

import { SignalCard } from "./signal-card";

export const EXECUTIVE_BRIEF_PROMPT =
  "Create an executive brief of this dataset: the headline KPIs, how performance is trending, the biggest " +
  "drivers and segments, notable risks or anomalies, and the top three opportunities to act on.";

export function DatasetIntro({
  dataset,
  onAsk,
  onOpenCleaning,
  onExplain,
  disabled = false,
}: {
  dataset: Dataset;
  onAsk: (question: string) => void;
  onOpenCleaning: () => void;
  onExplain?: (query: { measure: string; focus?: string }) => void;
  disabled?: boolean;
}) {
  const { profile, cleaning } = dataset;
  const actions = cleaning.actions.filter((a) => a.step !== "missing_values");
  const signals = profile.signals ?? [];
  const measure = profile.roles.measure?.[0];
  const dimension = profile.roles.dimension?.[0];
  const date = profile.roles.datetime?.[0];
  const dataFocus = [measure && `primary measure: ${measure}`, dimension && `key segment: ${dimension}`, date && `time field: ${date}`]
    .filter(Boolean)
    .join(", ");
  const suggestedQuestions = [...profile.suggested_questions];
  if (date && measure) suggestedQuestions.push(`Forecast ${measure} for the next 6 periods and show the confidence range.`);
  const playbooks = [
    {
      id: "executive",
      title: "Executive brief",
      description: "KPIs, momentum, risks and three prioritized actions.",
      icon: <Sparkles className="size-4" />,
      prompt: `${EXECUTIVE_BRIEF_PROMPT}${dataFocus ? ` Focus first on ${dataFocus}.` : ""} Quantify every claim you can.`,
    },
    {
      id: "drivers",
      title: "Growth drivers",
      description: "Rank the factors and segments moving performance.",
      icon: <BarChart3 className="size-4" />,
      prompt:
        `Run a growth-driver analysis${measure ? ` for ${measure}` : ""}. Quantify the change over time where possible, ` +
        `rank the dimensions or segments contributing most to gains and losses, separate volume from mix effects when supported, ` +
        `and recommend the two highest-leverage actions. Include a contribution chart and a supporting table.`,
    },
    {
      id: "risk",
      title: "Risk radar",
      description: "Find anomalies, declines, concentration and weak data.",
      icon: <ShieldAlert className="size-4" />,
      prompt:
        "Build a risk radar for this dataset. Test for material anomalies, deteriorating trends, concentration risk, volatile segments, " +
        "and data-quality limitations. Rank findings by likely business impact, quantify the evidence, and distinguish facts from caveats.",
    },
    date && measure
      ? {
          id: "forecast",
          title: "Forecast outlook",
          description: `Project ${measure} with scenarios and uncertainty.`,
          icon: <Telescope className="size-4" />,
          prompt:
            `Forecast ${measure} over ${date} for the next 6 periods. Show the historical series, baseline forecast and prediction ` +
            "intervals. Summarize direction, uncertainty, major assumptions, downside risk and one upside scenario. Do not claim causality.",
        }
      : {
          id: "opportunity",
          title: "Opportunity map",
          description: "Surface promising segments and practical next moves.",
          icon: <Target className="size-4" />,
          prompt:
            "Create an opportunity map from this dataset. Compare meaningful segments, identify high-performing and underpenetrated areas, " +
            "quantify their potential using only available evidence, and prioritize three practical opportunities with caveats.",
        },
  ];

  return (
    <div className="animate-rise space-y-8">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Badge tone="good">
            <Check className="size-3" />
            Cleaned &amp; profiled
          </Badge>
          <h2 className="mt-3 text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">{dataset.name}</h2>
          <p className="mt-1.5 text-[15px] text-ink-2">
            {profile.n_rows.toLocaleString()} rows · {profile.n_columns} columns · data quality{" "}
            <span className="font-medium text-ink">{cleaning.quality_score}/100</span>
          </p>
        </div>
        <Badge tone="accent" className="self-start sm:self-auto">
          <Target className="size-3" />
          4 decision playbooks ready
        </Badge>
      </div>

      <section>
        <SectionLabel icon={<Target className="size-3.5" />}>Decision playbooks</SectionLabel>
        <div className="grid gap-3 sm:grid-cols-2">
          {playbooks.map((playbook) => (
            <button
              key={playbook.id}
              type="button"
              disabled={disabled}
              onClick={() => onAsk(playbook.prompt)}
              className={cn(
                "group rounded-xl border border-line bg-panel p-4 text-left shadow-card transition",
                "hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-pop",
                "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 disabled:hover:border-line",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-semibold text-ink">
                <span className="flex size-8 items-center justify-center rounded-lg bg-accent-soft text-accent">{playbook.icon}</span>
                {playbook.title}
                <ArrowRight className="ml-auto size-3.5 text-ink-3 transition group-hover:translate-x-0.5 group-hover:text-accent" />
              </span>
              <span className="mt-2 block text-[12.5px] leading-relaxed text-ink-2">{playbook.description}</span>
            </button>
          ))}
        </div>
        <p className="mt-2 text-[12px] text-ink-3">Each playbook shows its reasoning steps, evidence and estimated API cost.</p>
      </section>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {profile.highlights.slice(0, 4).map((h) => (
          <div key={h.label} className="rounded-xl border border-line bg-panel p-3.5 shadow-card">
            <p className="truncate text-[12px] text-ink-2" title={h.label}>
              {h.label}
            </p>
            <p className="mt-1 truncate text-lg font-semibold tracking-tight text-ink">
              {formatValue(h.value, h.format)}
            </p>
          </div>
        ))}
      </div>

      {signals.length > 0 && (
        <section>
          <SectionLabel icon={<Radar className="size-3.5" />}>Signals spotted in your data</SectionLabel>
          <div className="grid gap-3 sm:grid-cols-2">
            {signals.map((signal) => (
              <SignalCard
                key={signal.id}
                signal={signal}
                onInvestigate={onAsk}
                onExplain={onExplain}
                disabled={disabled}
              />
            ))}
          </div>
          <p className="mt-2 text-[12px] text-ink-3">
            Found by a deterministic scan when the file was uploaded — no AI involved. Investigate to verify.
          </p>
        </section>
      )}

      {actions.length > 0 && (
        <div className="rounded-xl border border-line bg-panel p-4 shadow-card">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-semibold text-ink">What I fixed before analysis</p>
            <button onClick={onOpenCleaning} className="text-[13px] font-medium text-accent-ink hover:underline">
              Full report
            </button>
          </div>
          <ul className="mt-3 space-y-2">
            {actions.slice(0, 4).map((action, i) => (
              <li key={i} className="flex gap-2.5 text-[13px] leading-snug text-ink-2">
                <Check className="mt-0.5 size-3.5 shrink-0 text-good" />
                <span>
                  {action.column && <span className="font-medium text-ink">{action.column}: </span>}
                  {action.detail}
                </span>
              </li>
            ))}
          </ul>
          {actions.length > 4 && <p className="mt-2 text-xs text-ink-3">+ {actions.length - 4} more steps</p>}
        </div>
      )}

      <div>
        <SectionLabel icon={<Sparkles className="size-3.5" />}>Try asking</SectionLabel>
        <div className="grid gap-2 sm:grid-cols-2">
          {suggestedQuestions.map(
            (question) => (
              <button
                key={question}
                disabled={disabled}
                onClick={() => onAsk(question)}
                className="group flex items-start gap-2 rounded-xl border border-line bg-panel p-3 text-left text-[13.5px] leading-snug text-ink shadow-card transition hover:border-line-strong hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="flex-1">{question}</span>
                <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-ink-3 transition group-hover:translate-x-0.5 group-hover:text-accent" />
              </button>
            ),
          )}
        </div>
      </div>
    </div>
  );
}
