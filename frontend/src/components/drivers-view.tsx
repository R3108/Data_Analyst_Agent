"use client";

import {
  ArrowRight,
  Download,
  GitBranchPlus,
  Info,
  LoaderCircle,
  MessageSquare,
  Split,
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
import { formatChange, formatValue } from "@/lib/format";
import type {
  Aggregation,
  DatasetSummary,
  DriverOptions,
  DriverQuery,
  DriverResult,
  PeriodMode,
} from "@/lib/types";

const PERIODS: { id: PeriodMode; label: string; hint: string }[] = [
  { id: "auto", label: "Automatic", hint: "Pick the longest like-for-like window the data supports" },
  { id: "yoy", label: "Last 12 months", hint: "Versus the 12 months before" },
  { id: "month", label: "Last 30 days", hint: "Versus the 30 days before" },
  { id: "week", label: "Last 4 weeks", hint: "Versus the 4 weeks before" },
  { id: "halves", label: "Split in half", hint: "Second half of the history versus the first" },
];

const STATUS_TONE: Record<string, string> = {
  new: "bg-accent-soft text-accent-ink",
  lost: "bg-bad-soft text-bad",
  other: "bg-muted text-ink-3",
};

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

/**
 * The deterministic drill-down: what moved a number, how much of it was volume, mix or
 * rate, and which dimension separates the change best. No model call, so it is free and
 * gives the same answer twice.
 */
export function DriversView({
  datasets,
  datasetId,
  initialQuery,
  onDatasetChange,
  onAsk,
}: {
  datasets: DatasetSummary[];
  datasetId: string | null;
  initialQuery?: DriverQuery;
  onDatasetChange: (id: string) => void;
  onAsk: (datasetId: string, question: string) => void;
}) {
  const toast = useToast();
  const [options, setOptions] = useState<DriverOptions | null>(null);
  const [query, setQuery] = useState<DriverQuery>(initialQuery ?? {});
  const [result, setResult] = useState<DriverResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = datasetId ?? datasets[0]?.id ?? null;

  // A new dataset invalidates the measure and dimension names entirely.
  useEffect(() => {
    setOptions(null);
    setResult(null);
    setError(null);
    setQuery(initialQuery ?? {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const run = useCallback(
    async (next: DriverQuery) => {
      if (!active) return;
      setLoading(true);
      setError(null);
      try {
        const computed = await api.explainDrivers(active, next);
        setResult(computed);
        setOptions(computed.options);
        setQuery({
          ...next,
          measure: computed.measure,
          aggregation: computed.aggregation,
          focus: computed.best_dimension ?? undefined,
        });
      } catch (e) {
        setResult(null);
        setError(describe(e));
        // Still populate the pickers, so a rejected request can be corrected in place.
        void api.driverOptions(active).then(setOptions).catch(() => undefined);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [active],
  );

  useEffect(() => {
    if (active) void run(initialQuery ?? {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, run]);

  const update = (patch: DriverQuery) => {
    const next = { ...query, ...patch };
    setQuery(next);
    void run(next);
  };

  const measures = options?.measures ?? [];
  const total = result?.total;

  if (!datasets.length) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <EmptyState
          icon={<Split className="size-5" />}
          title="No data to drill into yet"
          body="Upload a CSV or Excel file, connect a SQL source, or load the sample dataset, and the drill-down will be ready straight away."
        />
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 pt-8 pb-16 sm:px-6">
        <ViewHeader
          eyebrow="Drivers"
          icon={<Split className="size-3.5" />}
          title="Why the number moved"
          body="Numera splits the change into each segment's contribution and into volume, mix and rate — three terms that add back to the total exactly. Computed in pandas from the cleaned table, so it costs nothing and never invents a figure."
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
            disabled={!measures.length}
            onChange={(value) => {
              const picked = measures.find((m) => m.name === value);
              update({ measure: value, aggregation: picked?.aggregation, focus: undefined });
            }}
            options={measures.map((m) => ({ value: m.name, label: m.name }))}
          />
          <Select
            label="Compare"
            value={query.period ?? "auto"}
            onChange={(value) => update({ period: value as PeriodMode })}
            options={PERIODS.map((p) => ({ value: p.id, label: p.label, title: p.hint }))}
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
            <div className="h-72 animate-pulse rounded-xl bg-muted" />
          </div>
        )}

        {result && total && active && (
          <>
            <section className="mt-6 rounded-xl border border-line bg-panel p-5 shadow-card">
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-[13px] text-ink-2">{result.period.baseline.label}</p>
                  <p className="text-xl font-semibold tracking-tight text-ink tabular-nums">
                    {formatValue(total.baseline)}
                  </p>
                </div>
                <ArrowRight className="mb-1.5 size-4 shrink-0 text-ink-3" />
                <div className="min-w-0">
                  <p className="text-[13px] text-ink-2">{result.period.current.label}</p>
                  <p className="text-xl font-semibold tracking-tight text-ink tabular-nums">
                    {formatValue(total.current)}
                  </p>
                </div>
                <div className="ml-auto text-right">
                  <p className="text-[13px] text-ink-2">Change</p>
                  <p
                    className={cn(
                      "text-2xl font-semibold tracking-tight tabular-nums",
                      total.direction === "up" ? "text-good" : total.direction === "down" ? "text-bad" : "text-ink",
                    )}
                  >
                    {formatChange(total.change, Math.max(Math.abs(total.baseline), Math.abs(total.current)))}
                    {total.change_pct !== null && (
                      <span className="ml-1.5 text-base font-medium">
                        ({total.change_pct > 0 ? "+" : "−"}
                        {Math.abs(total.change_pct * 100).toFixed(1)}%)
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <p className="mt-3.5 border-t border-line pt-3.5 text-[14px] leading-relaxed font-medium text-ink">
                {result.headline}
              </p>
              <p className="mt-1 text-[12px] text-ink-3">
                {result.period.baseline.start.slice(0, 10)} → {result.period.baseline.end.slice(0, 10)}
                {"  vs  "}
                {result.period.current.start.slice(0, 10)} → {result.period.current.end.slice(0, 10)}
                {` · ${result.period.baseline_rows.toLocaleString()} and ${result.period.current_rows.toLocaleString()} rows`}
              </p>
            </section>

            {result.dimensions.length > 1 && (
              <section className="mt-6">
                <SectionLabel icon={<GitBranchPlus className="size-3.5" />}>
                  Look through
                </SectionLabel>
                <div className="flex flex-wrap gap-2">
                  {result.dimensions.map((dimension) => (
                    <button
                      key={dimension.column}
                      onClick={() => update({ focus: dimension.column })}
                      disabled={loading}
                      title={`Its categories deviate ${(dimension.score * 100).toFixed(1)}% of the baseline total from a proportional move`}
                      className={cn(
                        "rounded-lg border px-2.5 py-1.5 text-[13px] transition disabled:opacity-50",
                        dimension.column === result.best_dimension
                          ? "border-accent bg-accent-soft text-accent-ink"
                          : "border-line text-ink-2 hover:bg-muted hover:text-ink",
                      )}
                    >
                      {dimension.column}
                      <span className="ml-1.5 text-[11px] text-ink-3 tabular-nums">
                        {(dimension.score * 100).toFixed(1)}
                      </span>
                    </button>
                  ))}
                </div>
                <p className="mt-2 flex items-start gap-1.5 text-[12px] leading-relaxed text-ink-3">
                  <Info className="mt-px size-3.5 shrink-0" />
                  The score is how far each dimension&apos;s categories deviate from moving in
                  proportion to their size — higher means it explains more than the total already does.
                </p>
              </section>
            )}

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
                        source: "drivers",
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

            {result.shift_share && (
              <section className="mt-6">
                <SectionLabel>Volume · mix · rate</SectionLabel>
                <div className="grid gap-2.5 sm:grid-cols-3">
                  {result.shift_share.terms.map((term) => (
                    <div key={term.key} className="rounded-xl border border-line bg-panel p-3.5 shadow-card">
                      <p className="text-[12px] text-ink-2">{term.label}</p>
                      <p
                        className={cn(
                          "mt-0.5 text-xl font-semibold tracking-tight tabular-nums",
                          term.value > 0 ? "text-good" : term.value < 0 ? "text-bad" : "text-ink",
                        )}
                      >
                        {term.value > 0 ? "+" : ""}
                        {formatValue(term.value)}
                      </p>
                      <p className="mt-1 text-[11.5px] leading-snug text-ink-3">{term.detail}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}

            <div className="mt-6 space-y-5">
              {result.tables.map((table, index) => (
                <TableCard
                  key={table.title}
                  table={table}
                  actions={
                    <PinButton
                      target={{
                        kind: "table",
                        source: "drivers",
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

            <MoversStrip result={result} />

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
                    .downloadDrivers(active, query)
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

/** New and lost categories — the movements a percentage change hides completely. */
function MoversStrip({ result }: { result: DriverResult }) {
  const best = result.dimensions.find((d) => d.column === result.best_dimension);
  const movers = [...(best?.gained ?? []), ...(best?.lost ?? [])];
  if (!best || movers.length === 0) return null;

  return (
    <section className="mt-6">
      <SectionLabel>Appeared and disappeared</SectionLabel>
      <div className="flex flex-wrap gap-2">
        {movers.map((mover) => (
          <span
            key={`${mover.status}-${mover.label}`}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px]",
              STATUS_TONE[mover.status] ?? "bg-muted text-ink-2",
            )}
          >
            <Badge tone={mover.status === "new" ? "accent" : "bad"}>
              {mover.status === "new" ? "New" : "Gone"}
            </Badge>
            {mover.label}
            <span className="tabular-nums">{formatValue(mover.change)}</span>
          </span>
        ))}
      </div>
    </section>
  );
}

