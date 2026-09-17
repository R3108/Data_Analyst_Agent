"use client";

import { ArrowRight, CircleAlert, RotateCcw, TriangleAlert } from "lucide-react";

import { LogoMark } from "@/components/brand";
import { Markdown } from "@/components/markdown";
import { PinButton } from "@/components/pin";
import { Button, SectionLabel } from "@/components/ui/primitives";
import { WatchButton } from "@/components/watch";
import { formatCost, formatTokens } from "@/lib/format";
import type { Message } from "@/lib/types";

import { VerificationBadge } from "./verification-badge";

import { AgentTimeline } from "./agent-timeline";
import { ChartCard } from "./chart-card";
import { CodePanel } from "./code-panel";
import { InsightCard } from "./insight-card";
import { InvestigationCard } from "./investigation-card";
import { KpiGrid } from "./kpi-grid";
import { RecallNotice } from "./recall-notice";
import { TableCard } from "./table-card";

const ERROR_TITLES: Record<string, string> = {
  llm_budget_exceeded: "Monthly AI budget reached",
  llm_not_configured: "OpenAI API key required",
  llm_rate_limited: "The model is busy",
  llm_unreachable: "Couldn't reach the model",
  llm_refusal: "The model declined this request",
  internal_error: "Something went wrong",
};

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function AssistantAvatar() {
  return <LogoMark className="mt-0.5 size-7 shrink-0" />;
}

export function AssistantMessage({
  message,
  isLast,
  elapsedMs,
  onFollowUp,
  onRetry,
  onOpenSession,
  onOpenMessage,
}: {
  message: Message;
  isLast: boolean;
  elapsedMs?: number;
  onFollowUp?: (question: string) => void;
  onRetry?: () => void;
  onOpenSession?: (sessionId: string) => void;
  onOpenMessage?: (messageId: string) => void;
}) {
  const payload = message.payload ?? {};
  const report = payload.report;
  const execution = payload.execution;
  const steps = payload.steps ?? [];
  const usage = payload.usage;
  const failedHard = message.status === "error" || message.status === "cancelled";
  const pin = (kind: "kpi" | "chart" | "table" | "insight", index: number, label: string) => (
    <PinButton target={{ kind, messageId: message.id, index, label }} />
  );

  const summary = [
    elapsedMs ? `Worked for ${formatDuration(elapsedMs)}` : null,
    `${steps.length} steps`,
    usage && usage.calls > 0 && usage.cost_usd !== null ? formatCost(usage.cost_usd) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article id={`message-${message.id}`} className="animate-rise flex scroll-mt-4 gap-3">
      <AssistantAvatar />
      <div className="min-w-0 flex-1 space-y-4">
        {steps.length > 0 && (
          <div title={usage ? `${formatTokens(usage.input_tokens)} in · ${formatTokens(usage.output_tokens)} out` : undefined}>
            <AgentTimeline steps={steps} live={false} summary={summary} />
          </div>
        )}

        {failedHard && (
          <div className="rounded-xl border border-bad/25 bg-bad-soft p-4">
            <div className="flex items-start gap-3">
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-bad" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-ink">
                  {message.status === "cancelled"
                    ? "Analysis stopped"
                    : (ERROR_TITLES[payload.error?.code ?? ""] ?? "The analysis failed")}
                </p>
                <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{message.content}</p>
              </div>
              {onRetry && (
                <Button size="sm" onClick={onRetry} className="no-print">
                  <RotateCcw className="size-3.5" />
                  Retry
                </Button>
              )}
            </div>
          </div>
        )}

        {payload.recall && (
          <div className="no-print">
            <RecallNotice recall={payload.recall} onOpen={onOpenSession} />
          </div>
        )}

        {report?.headline && (
          <h3 className="text-[17px] leading-snug font-semibold tracking-tight text-ink">{report.headline}</h3>
        )}
        {report?.answer_markdown && <Markdown>{report.answer_markdown}</Markdown>}

        {payload.investigation && (
          <InvestigationCard
            investigation={payload.investigation}
            degraded={payload.degraded}
            onOpenStep={onOpenMessage}
          />
        )}

        {message.status === "failed" && onRetry && (
          <Button size="sm" onClick={onRetry} className="no-print">
            <RotateCcw className="size-3.5" />
            Try again
          </Button>
        )}

        {payload.report_error && (
          <p className="flex items-center gap-2 text-[13px] text-warn">
            <TriangleAlert className="size-3.5" />
            Narrative unavailable: {payload.report_error.message}
          </p>
        )}

        {payload.verification && <VerificationBadge verification={payload.verification} />}

        {execution?.ok && execution.kpis.length > 0 && (
          <KpiGrid
            kpis={execution.kpis}
            renderAction={(i, kpi) => (
              <>
                <WatchButton
                  target={{
                    messageId: message.id,
                    index: i,
                    label: kpi.label,
                    value: typeof kpi.value === "number" ? kpi.value : null,
                    format: kpi.format,
                  }}
                />
                {pin("kpi", i, kpi.label)}
              </>
            )}
          />
        )}
        {execution?.ok &&
          execution.charts.map((chart, i) => (
            <ChartCard key={`chart-${i}`} chart={chart} actions={pin("chart", i, chart.title)} />
          ))}
        {execution?.ok &&
          execution.tables.map((table, i) => (
            <TableCard key={`table-${i}`} table={table} actions={pin("table", i, table.title)} />
          ))}

        {report && report.insights.length > 0 && (
          <section>
            <SectionLabel>Key insights</SectionLabel>
            <div className="grid gap-3 sm:grid-cols-2">
              {report.insights.map((insight, i) => (
                <InsightCard key={i} insight={insight} action={pin("insight", i, insight.title)} />
              ))}
            </div>
          </section>
        )}

        {report && report.recommendations.length > 0 && (
          <section className="print-avoid-break">
            <SectionLabel>Recommended actions</SectionLabel>
            <ol className="space-y-2">
              {report.recommendations.map((recommendation, i) => (
                <li key={i} className="flex gap-3 text-[14px] leading-relaxed text-ink">
                  <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent-ink">
                    {i + 1}
                  </span>
                  <span>{recommendation}</span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {report && report.caveats.length > 0 && (
          <section className="print-avoid-break rounded-xl border border-line bg-muted/50 p-3.5">
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-ink-2">
              <TriangleAlert className="size-3.5 text-warn" />
              Caveats
            </p>
            <ul className="space-y-1 text-[13px] leading-relaxed text-ink-2">
              {report.caveats.map((caveat, i) => (
                <li key={i}>• {caveat}</li>
              ))}
            </ul>
          </section>
        )}

        {payload.code && (
          <div className="no-print">
            <CodePanel code={payload.code} execution={execution} attemptLog={payload.attempt_log} />
          </div>
        )}

        {isLast && onFollowUp && report && report.follow_up_questions.length > 0 && (
          <section className="no-print">
            <SectionLabel>Ask a follow-up</SectionLabel>
            <div className="flex flex-col gap-2">
              {report.follow_up_questions.map((question) => (
                <button
                  key={question}
                  onClick={() => onFollowUp(question)}
                  className="group flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-left text-[13px] text-ink-2 transition hover:border-line-strong hover:text-ink"
                >
                  <span className="flex-1">{question}</span>
                  <ArrowRight className="size-3.5 shrink-0 text-ink-3 transition group-hover:translate-x-0.5 group-hover:text-accent" />
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </article>
  );
}
