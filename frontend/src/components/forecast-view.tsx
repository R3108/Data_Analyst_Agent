"use client";

import {
  Award,
  Download,
  Info,
  LoaderCircle,
  MessageSquare,
  TrendingUp,
  TriangleAlert,
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
  Aggregation,
  DatasetSummary,
  ForecastOptions,
  ForecastQuery,
  ForecastResult,
  Granularity,
} from "@/lib/types";

const GRAINS: { id: Granularity; label: string }[] = [
  { id: "day", label: "Daily" },
  { id: "week", label: "Weekly" },
  { id: "month", label: "Monthly" },
  { id: "quarter", label: "Quarterly" },
];

const VERDICT_TONE = {
  useful: { tone: "good" as const, label: "Beats the baseline" },
  weak: { tone: "warn" as const, label: "Weak" },
  "no better": { tone: "warn" as const, label: "No better than the baseline" },
  baseline: { tone: "neutral" as const, label: "Baseline is the answer" },
};

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

const pct = (value: number | null | undefined, digits = 1) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

/**
 * The forecast lab. Nothing here is shown without the walk-forward backtest that chose
 * it: every candidate is refitted at several origins and scored on periods it never saw,
 * and the interval comes from those errors rather than from an assumption about them.
 */
export function ForecastView({
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
  const [options, setOptions] = useState<ForecastOptions | null>(null);
  const [query, setQuery] = useState<ForecastQuery>({});
  const [result, setResult] = useState<ForecastResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = datasetId ?? datasets[0]?.id ?? null;

  useEffect(() => {
    setOptions(null);
    setResult(null);
    setError(null);
    setQuery({});
  }, [active]);

  const run = useCallback(
    async (next: ForecastQuery) => {
      if (!active) return;
      setLoading(true);
      setError(null);
      try {
        const computed = await api.forecast(active, next);
        setResult(computed);
        setOptions(computed.options);
        setQuery({
          ...next,
          measure: computed.measure,
          aggregation: computed.aggregation,
          granularity: computed.granularity,
        });
      } catch (e) {
        setResult(null);
        setError(describe(e));
        void api.forecastOptions(active).then(setOptions).catch(() => undefined);
      } finally {
        setLoading(false);
      }
    },
    [active],
  );

  useEffect(() => {
    if (active) void run({});
  }, [active, run]);

  const update = (patch: ForecastQuery) => {
    const next = { ...query, ...patch };
    setQuery(next);
    void run(next);
  };

  if (!datasets.length) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <EmptyState
          icon={<TrendingUp className="size-5" />}
          title="Nothing to project yet"
          body="Upload a table with a date column and a numeric measure, and the forecast lab will backtest a projection for it straight away."
        />
      </div>
    );
  }

  const totals = result?.totals;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 pt-8 pb-16 sm:px-6">
        <ViewHeader
          eyebrow="Forecast"
          icon={<TrendingUp className="size-3.5" />}
          title="A projection, and its track record"
          body="Eight methods are refitted at several points in the past and scored on periods they never saw. The winner is shown with the margin by which it beat the naive and seasonal-naive baselines — and when nothing beats them, Numera says so and shows the baseline instead."
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
            label="Measure"
            value={query.measure ?? options?.defaults.measure ?? ""}
            disabled={!options?.measures.length}
            onChange={(value) => {
              const picked = options?.measures.find((m) => m.name === value);
              update({ measure: value, aggregation: picked?.aggregation });
            }}
            options={(options?.measures ?? []).map((m) => ({ value: m.name, label: m.name }))}
          />
          <Select
            label="By"
            value={query.granularity ?? options?.defaults.granularity ?? "month"}
            onChange={(value) => update({ granularity: value as Granularity })}
            options={GRAINS.map((g) => ({ value: g.id, label: g.label }))}
          />
          <Select
            label="Aggregate"
            value={query.aggregation ?? options?.defaults.aggregation ?? "sum"}
            onChange={(value) => update({ aggregation: value as Aggregation })}
            options={[
              { value: "sum", label: "Total", title: "Sum every row in the period" },
              { value: "mean", label: "Average", title: "Average per row — for rates and prices" },
            ]}
          />
          <Select
            label="Horizon"
            value={String(query.horizon ?? options?.defaults.horizon ?? 6)}
            onChange={(value) => update({ horizon: Number(value) })}
            options={[3, 6, 9, 12, 18, 24].map((n) => ({ value: String(n), label: `${n} ahead` }))}
          />
          <Select
            label="Method"
            value={query.method ?? "auto"}
            onChange={(value) => update({ method: value })}
            options={[
              { value: "auto", label: "Pick the best", title: "Whichever wins the backtest" },
              ...(options?.methods ?? []).map((m) => ({
                value: m.id,
                label: m.label,
                title: m.detail,
              })),
            ]}
          />
          <Select
            label="Interval"
            value={String(query.interval ?? options?.defaults.interval ?? 0.8)}
            onChange={(value) => update({ interval: Number(value) })}
            options={[0.5, 0.8, 0.9, 0.95].map((n) => ({
              value: String(n),
              label: `${n * 100}%`,
            }))}
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

        {result && totals && active && (
          <>
            <section className="mt-6 rounded-xl border border-line bg-panel p-5 shadow-card">
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-[13px] text-ink-2">
                    Last {totals.recent_periods} {result.period_noun}s
                  </p>
                  <p className="text-xl font-semibold tracking-tight text-ink tabular-nums">
                    {formatValue(totals.recent)}
                  </p>
                </div>
                <div className="min-w-0">
                  <p className="text-[13px] text-ink-2">
                    Next {result.horizon} {result.period_noun}s
                  </p>
                  <p className="text-xl font-semibold tracking-tight text-ink tabular-nums">
                    {formatValue(totals.projected)}
                  </p>
                </div>
                <div className="ml-auto text-right">
                  <p className="text-[13px] text-ink-2">Change</p>
                  <p
                    className={cn(
                      "text-2xl font-semibold tracking-tight tabular-nums",
                      totals.direction === "up"
                        ? "text-good"
                        : totals.direction === "down"
                          ? "text-bad"
                          : "text-ink",
                    )}
                  >
                    {totals.change > 0 ? "+" : ""}
                    {formatValue(totals.change)}
                    {totals.change_pct !== null && (
                      <span className="ml-1.5 text-base font-medium">
                        ({totals.change_pct > 0 ? "+" : "−"}
                        {Math.abs(totals.change_pct * 100).toFixed(1)}%)
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <p className="mt-3.5 border-t border-line pt-3.5 text-[14px] leading-relaxed font-medium text-ink">
                {result.headline}
              </p>
              <p className="mt-1 text-[12px] text-ink-3">
                {result.coverage.observations} complete {result.period_noun}s ·{" "}
                {result.coverage.first_period.slice(0, 10)} → {result.coverage.last_period.slice(0, 10)}
                {result.season_length
                  ? ` · seasonal cycle of ${result.season_length} ${result.period_noun}s`
                  : " · no seasonal cycle available"}
              </p>
            </section>

            <TrackRecord result={result} />

            {/* The verdict already leads the track-record panel above, so it is dropped
                here rather than printed twice. The export keeps it, where there is no panel. */}
            {result.narrative.filter((line) => line !== result.verdict.summary).length > 0 && (
              <section className="mt-6 rounded-xl border border-line bg-panel p-4 shadow-card">
                <ul className="space-y-2">
                  {result.narrative.filter((line) => line !== result.verdict.summary).map((line) => (
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
                        source: "forecast",
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

            <Scoreboard result={result} />

            <div className="mt-6 space-y-5">
              {result.tables.map((table, index) => (
                <TableCard
                  key={table.title}
                  table={table}
                  actions={
                    <PinButton
                      target={{
                        kind: "table",
                        source: "forecast",
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
                    .downloadForecast(active, query)
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

/** The verdict up front: did this method actually earn the right to be shown? */
function TrackRecord({ result }: { result: ForecastResult }) {
  const verdict = VERDICT_TONE[result.verdict.label] ?? VERDICT_TONE.baseline;
  const chosen = result.accuracy.find((score) => score.method === result.method);

  return (
    <section className="mt-6 rounded-xl border border-line bg-panel p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <Award className="size-4 text-accent" />
        <span className="text-[14px] font-semibold text-ink">{result.method_label}</span>
        <Badge tone={verdict.tone}>{verdict.label}</Badge>
        <span className="text-[12px] text-ink-3">{result.selection}</span>
        <span className="ml-auto text-[12px] text-ink-3">
          {result.backtest.folds} refits · {result.backtest.tested_points} held-out{" "}
          {result.period_noun}s
        </span>
      </div>
      <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2">{result.verdict.summary}</p>
      {chosen && (
        <div className="mt-3 grid gap-3 border-t border-line pt-3 sm:grid-cols-4">
          <Metric
            label="MASE"
            value={chosen.mase === null ? "—" : chosen.mase.toFixed(2)}
            hint="Error scaled by the naive baseline's own. Below 1.0 is better than doing nothing."
            tone={chosen.mase !== null && chosen.mase < 1 ? "good" : "bad"}
          />
          <Metric label="MAPE" value={pct(chosen.mape)} hint="Average absolute percentage error out of sample." />
          <Metric label="MAE" value={formatValue(chosen.mae)} hint="Average absolute error, in the measure's own units." />
          <Metric
            label="Bias"
            value={formatValue(chosen.bias)}
            hint="Average signed error. Positive means the method under-shoots."
          />
        </div>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "good" | "bad";
}) {
  return (
    <div className="min-w-0" title={hint}>
      <p className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">{label}</p>
      <p
        className={cn(
          "mt-0.5 text-lg font-semibold tracking-tight tabular-nums",
          tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : "text-ink",
        )}
      >
        {value}
      </p>
    </div>
  );
}

/** Every candidate, in the order the backtest ranked them. */
function Scoreboard({ result }: { result: ForecastResult }) {
  if (result.accuracy.length < 2) return null;
  const worst = Math.max(...result.accuracy.map((s) => s.mase ?? s.mae));

  return (
    <section className="mt-6">
      <SectionLabel>Every method, scored on data it never saw</SectionLabel>
      <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-card">
        {result.accuracy.map((score) => {
          const value = score.mase ?? score.mae;
          const chosen = score.method === result.method;
          return (
            <div
              key={score.method}
              title={score.detail}
              className={cn(
                "flex items-center gap-3 border-b border-line px-3.5 py-2 last:border-b-0",
                chosen && "bg-accent-soft",
              )}
            >
              <span className="w-44 shrink-0 truncate text-[12.5px] text-ink">
                {score.label}
                {score.baseline && (
                  <span className="ml-1.5 text-[11px] text-ink-3">baseline</span>
                )}
              </span>
              <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn("h-full rounded-full", chosen ? "bg-accent" : "bg-line-strong")}
                  style={{ width: `${worst ? Math.min((value / worst) * 100, 100) : 0}%` }}
                />
              </div>
              <span className="w-16 shrink-0 text-right text-[13px] font-medium text-ink tabular-nums">
                {score.mase === null ? formatValue(score.mae) : score.mase.toFixed(2)}
              </span>
              {!score.complete && (
                <span
                  className="w-20 shrink-0 text-right text-[11px] text-ink-3"
                  title="Could not be fitted at every origin, so it is shown but never allowed to win"
                >
                  partial
                </span>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-2 flex items-start gap-1.5 text-[12px] leading-relaxed text-ink-3">
        <Info className="mt-px size-3.5 shrink-0" />
        Bars are MASE — error scaled by the naive baseline&apos;s own, so 1.0 means &ldquo;no
        better than doing nothing&rdquo;. Shorter is better.
      </p>
    </section>
  );
}
