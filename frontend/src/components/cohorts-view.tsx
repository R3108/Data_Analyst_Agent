"use client";

import {
  Download,
  Info,
  LoaderCircle,
  MessageSquare,
  TriangleAlert,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ChartCard } from "@/components/chat/chart-card";
import { TableCard } from "@/components/chat/table-card";
import { Markdown } from "@/components/markdown";
import { PinButton } from "@/components/pin";
import {
  Badge,
  Button,
  EmptyState,
  SectionLabel,
  Select,
  ViewHeader,
} from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatValue } from "@/lib/format";
import type {
  CohortOptions,
  CohortQuery,
  CohortResult,
  DatasetSummary,
  Granularity,
} from "@/lib/types";

const GRAINS: { id: Granularity; label: string; hint: string }[] = [
  { id: "day", label: "Daily", hint: "One column per day since first seen" },
  { id: "week", label: "Weekly", hint: "One column per week since first seen" },
  { id: "month", label: "Monthly", hint: "One column per month since first seen" },
  { id: "quarter", label: "Quarterly", hint: "One column per quarter since first seen" },
];

const NO_MEASURE = "__none__";

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

const pct = (value: number | null | undefined, digits = 1) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

/**
 * Cohorts and retention: group everyone by when they first appeared, then follow each
 * group forward. Computed in pandas from the cleaned table — and, unlike most cohort
 * grids, it leaves a cell empty when a cohort has not lived long enough to fill it
 * rather than counting that as churn.
 */
export function CohortsView({
  datasets,
  datasetId,
  onDatasetChange,
  onAsk,
}: {
  datasets: DatasetSummary[];
  datasetId: string | null;
  onDatasetChange: (id: string) => void;
  onAsk: (datasetId: string, question: string) => void;
}) {
  const toast = useToast();
  const [options, setOptions] = useState<CohortOptions | null>(null);
  const [query, setQuery] = useState<CohortQuery>({});
  const [result, setResult] = useState<CohortResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = datasetId ?? datasets[0]?.id ?? null;

  // A new dataset invalidates every column name in the query.
  useEffect(() => {
    setOptions(null);
    setResult(null);
    setError(null);
    setQuery({});
  }, [active]);

  const run = useCallback(
    async (next: CohortQuery) => {
      if (!active) return;
      setLoading(true);
      setError(null);
      try {
        const computed = await api.analyzeCohorts(active, next);
        setResult(computed);
        setOptions(computed.options);
        setQuery({
          ...next,
          entity: computed.entity,
          measure: computed.measure,
          granularity: computed.granularity,
        });
      } catch (e) {
        setResult(null);
        setError(describe(e));
        void api.cohortOptions(active).then(setOptions).catch(() => undefined);
      } finally {
        setLoading(false);
      }
    },
    [active],
  );

  useEffect(() => {
    if (active) void run({});
  }, [active, run]);

  const update = (patch: CohortQuery) => {
    const next = { ...query, ...patch };
    setQuery(next);
    void run(next);
  };

  if (!datasets.length) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <EmptyState
          icon={<Users className="size-5" />}
          title="No data to follow over time yet"
          body="Upload a table with a customer, account or device column and a date, and the cohort grid will be ready straight away."
        />
      </div>
    );
  }

  const summary = result?.summary;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 pt-8 pb-16 sm:px-6">
        <ViewHeader
          eyebrow="Retention"
          icon={<Users className="size-3.5" />}
          title="Does what you win stay won?"
          body="Everyone is grouped by the period they first appeared, then followed forward. A cohort born last month cannot have a six-month retention rate, so those cells stay empty and are excluded from the average — which is the difference between a retention curve and a picture of the calendar running out."
          aside={
            datasets.length > 1 ? (
              <Select
                label="Dataset"
                value={active ?? ""}
                onChange={onDatasetChange}
                options={datasets.map((d) => ({ value: d.id, label: d.name }))}
              />
            ) : undefined
          }
        />

        <div className="mt-6 flex flex-wrap items-end gap-3 rounded-xl border border-line bg-panel p-3.5 shadow-card">
          <Select
            label="Follow"
            value={query.entity ?? options?.defaults.entity ?? ""}
            disabled={!options?.entities.length}
            onChange={(value) => update({ entity: value })}
            options={(options?.entities ?? []).map((e) => ({
              value: e.name,
              label: e.name,
              title: `${e.unique.toLocaleString()} distinct values · ${e.rows_per_entity} rows each on average`,
            }))}
          />
          <Select
            label="By"
            value={query.granularity ?? options?.defaults.granularity ?? "month"}
            onChange={(value) => update({ granularity: value as Granularity })}
            options={GRAINS.map((g) => ({ value: g.id, label: g.label, title: g.hint }))}
          />
          <Select
            label="Value"
            value={query.measure ?? options?.defaults.measure ?? NO_MEASURE}
            onChange={(value) => update({ measure: value === NO_MEASURE ? null : value })}
            options={[
              { value: NO_MEASURE, label: "Count only", title: "Retention by headcount alone" },
              ...(options?.measures ?? []).map((m) => ({
                value: m,
                label: m,
                title: `Also track ${m} per cohort`,
              })),
            ]}
          />
          <Select
            label="Horizon"
            value={String(query.periods ?? options?.defaults.periods ?? 12)}
            onChange={(value) => update({ periods: Number(value) })}
            options={[6, 9, 12, 18, 24].map((n) => ({ value: String(n), label: `${n} periods` }))}
          />
          {loading && <LoaderCircle className="mb-2 size-4 animate-spin text-ink-3" />}
        </div>

        {error && (
          <div className="mt-5 flex items-start gap-2.5 rounded-xl border border-bad/30 bg-bad-soft p-4 text-[13px] leading-relaxed text-ink">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-bad" />
            <p>{error}</p>
          </div>
        )}

        {!result && !error && loading && (
          <div className="mt-6 space-y-4">
            <div className="h-28 animate-pulse rounded-xl bg-muted" />
            <div className="h-80 animate-pulse rounded-xl bg-muted" />
          </div>
        )}

        {result && summary && active && (
          <>
            <section className="mt-6 rounded-xl border border-line bg-panel p-5 shadow-card">
              <div className="grid gap-4 sm:grid-cols-4">
                <Figure label="Come back at all" value={pct(summary.repeat_rate)} tone="accent" />
                <Figure
                  label={`Next ${result.period_noun}`}
                  value={pct(summary.retention_1)}
                />
                <Figure
                  label={`Median ${result.period_noun}s active`}
                  value={
                    summary.median_active_periods === null
                      ? "—"
                      : summary.median_active_periods.toFixed(0)
                  }
                />
                <Figure
                  label={result.measure ? `${result.measure} each` : "One and done"}
                  value={
                    result.measure
                      ? formatValue(summary.value_per_entity_total)
                      : pct(summary.one_and_done_pct)
                  }
                />
              </div>
              <p className="mt-3.5 border-t border-line pt-3.5 text-[14px] leading-relaxed font-medium text-ink">
                {result.headline}
              </p>
              <p className="mt-1 text-[12px] text-ink-3">
                {result.coverage.first_period.slice(0, 10)} → {result.coverage.last_period.slice(0, 10)}
                {` · ${summary.entities.toLocaleString()} ${result.entity} values in ${summary.cohorts} cohorts`}
                {result.folded ? ` · ${result.folded} older cohorts hidden` : ""}
              </p>
            </section>

            <RetentionLadder result={result} />

            {result.narrative.length > 0 && (
              <section className="mt-6 rounded-xl border border-line bg-panel p-4 shadow-card">
                <ul className="space-y-2">
                  {result.narrative.map((line) => (
                    <li key={line} className="flex gap-2.5 text-[14px] leading-relaxed text-ink">
                      <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
                      <Markdown>{line}</Markdown>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <div className="mt-6 space-y-5">
              {result.charts.map((chart, index) => (
                <ChartCard
                  key={chart.title}
                  chart={chart}
                  actions={
                    <PinButton
                      target={{
                        kind: "chart",
                        source: "cohorts",
                        datasetId: active,
                        params: query,
                        index,
                        label: chart.title,
                      }}
                    />
                  }
                />
              ))}
            </div>

            <CohortSpread result={result} />

            <div className="mt-6 space-y-5">
              {result.tables.map((table, index) => (
                <TableCard
                  key={table.title}
                  table={table}
                  actions={
                    <PinButton
                      target={{
                        kind: "table",
                        source: "cohorts",
                        datasetId: active,
                        params: query,
                        index,
                        label: table.title,
                      }}
                    />
                  }
                />
              ))}
            </div>

            {result.caveats.length > 0 && (
              <section className="mt-6 rounded-xl border border-line bg-subtle p-4">
                <SectionLabel icon={<Info className="size-3.5" />}>What this does not say</SectionLabel>
                <ul className="space-y-1.5">
                  {result.caveats.map((caveat) => (
                    <li key={caveat} className="text-[12.5px] leading-relaxed text-ink-2">
                      {caveat}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <div className="mt-7 flex flex-wrap gap-2">
              <Button variant="primary" onClick={() => onAsk(active, result.follow_up)}>
                <MessageSquare className="size-4" />
                Ask the analyst about this
              </Button>
              <Button
                onClick={() =>
                  void api
                    .downloadCohorts(active, query)
                    .catch((e) => toast.error("Export failed", describe(e)))
                }
              >
                <Download className="size-4" />
                Export briefing
              </Button>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-ink-3">
              Hands the agent: “{result.follow_up}”
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: "accent" }) {
  return (
    <div className="min-w-0">
      <p className="truncate text-[12px] text-ink-2" title={label}>
        {label}
      </p>
      <p
        className={cn(
          "mt-0.5 text-2xl font-semibold tracking-tight tabular-nums",
          tone === "accent" ? "text-accent" : "text-ink",
        )}
      >
        {value}
      </p>
    </div>
  );
}

/** The curve as a ladder of horizons — how much is left, and how many cohorts said so. */
function RetentionLadder({ result }: { result: CohortResult }) {
  const curve = result.curve;
  const steps = curve.offsets
    .map((offset, index) => ({
      offset,
      retention: curve.retention[index],
      cohorts: curve.cohorts_observed[index],
    }))
    .filter((step) => step.offset > 0 && step.retention !== null)
    .slice(0, 12);
  if (!steps.length) return null;

  return (
    <section className="mt-6">
      <SectionLabel>How much is left, and how much of the data says so</SectionLabel>
      <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-card">
        {steps.map((step) => (
          <div
            key={step.offset}
            className="flex items-center gap-3 border-b border-line px-3.5 py-2 last:border-b-0"
          >
            <span className="w-24 shrink-0 text-[12.5px] text-ink-2">
              {step.offset} {result.period_noun}
              {step.offset === 1 ? "" : "s"} on
            </span>
            <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-accent transition-all"
                style={{ width: `${Math.min((step.retention ?? 0) * 100, 100)}%` }}
              />
            </div>
            <span className="w-14 shrink-0 text-right text-[13px] font-medium text-ink tabular-nums">
              {pct(step.retention, 0)}
            </span>
            <span
              className="w-24 shrink-0 text-right text-[11px] text-ink-3 tabular-nums"
              title={`Only cohorts old enough to have reached ${result.period_noun} ${step.offset} are counted here`}
            >
              {step.cohorts} cohort{step.cohorts === 1 ? "" : "s"}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Best against worst at the same age — the comparison a single average hides. */
function CohortSpread({ result }: { result: CohortResult }) {
  const { best_cohort: best, worst_cohort: worst, benchmark_offset: offset } = result.summary;
  if (!best || !worst || offset === null || best.cohort === worst.cohort) return null;
  const labels = Object.fromEntries(result.cohorts.map((c) => [c.cohort, c.label]));

  return (
    <section className="mt-6">
      <SectionLabel>
        Best and worst cohort at {offset} {result.period_noun}
        {offset === 1 ? "" : "s"}
      </SectionLabel>
      <div className="grid gap-2.5 sm:grid-cols-2">
        {[
          { entry: best, tone: "good" as const, label: "Best" },
          { entry: worst, tone: "bad" as const, label: "Worst" },
        ].map(({ entry, tone, label }) => (
          <div key={label} className="rounded-xl border border-line bg-panel p-3.5 shadow-card">
            <div className="flex items-center gap-2">
              <Badge tone={tone}>{label}</Badge>
              <span className="min-w-0 truncate text-[13px] text-ink">
                {labels[entry.cohort] ?? "—"}
              </span>
            </div>
            <p
              className={cn(
                "mt-1 text-2xl font-semibold tracking-tight tabular-nums",
                tone === "good" ? "text-good" : "text-bad",
              )}
            >
              {pct(entry.retention)}
            </p>
            <p className="mt-0.5 text-[11.5px] text-ink-3">
              {entry.size.toLocaleString()} {result.entity} values in the cohort
            </p>
          </div>
        ))}
      </div>
      <p className="mt-2 flex items-start gap-1.5 text-[12px] leading-relaxed text-ink-3">
        <Info className="mt-px size-3.5 shrink-0" />
        Both cohorts have lived at least {offset} {result.period_noun}
        {offset === 1 ? "" : "s"}, so this is a like-for-like comparison rather than an
        artefact of one being younger than the other.
      </p>
    </section>
  );
}
