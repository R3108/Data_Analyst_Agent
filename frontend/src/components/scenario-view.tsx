"use client";

import {
  ArrowRight,
  Download,
  Info,
  LoaderCircle,
  MessageSquare,
  RotateCcw,
  SlidersHorizontal,
  Target,
  TriangleAlert,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

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
  TextInput,
  ViewHeader,
} from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatValue } from "@/lib/format";
import type {
  Aggregation,
  DatasetSummary,
  GoalSeekResult,
  LeverKind,
  Levers,
  PeriodMode,
  ScenarioQuery,
  ScenarioResult,
  SegmentLever,
} from "@/lib/types";

const PERIODS: { id: PeriodMode; label: string }[] = [
  { id: "auto", label: "Automatic" },
  { id: "yoy", label: "Last 12 months" },
  { id: "month", label: "Last 30 days" },
  { id: "week", label: "Last 4 weeks" },
  { id: "halves", label: "Second half" },
];

// The API takes fractions; the sliders speak percentages.
const LEVER_MIN = -95;
const LEVER_MAX = 200;
const SHARE_MIN = -50;
const SHARE_MAX = 50;

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

function signed(value: number, digits = 1): string {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(digits)}%`;
}

function isEmpty(levers: Levers): boolean {
  const global = levers.global ?? {};
  const touched = Object.values(levers.segments ?? {}).some((lever) =>
    Object.values(lever).some((v) => v),
  );
  return !global.rate_pct && !global.volume_pct && !touched;
}

/**
 * Scenario studio: move volume, rate and mix, and read off what the measure becomes.
 * The projection is deterministic and decomposes with the same volume/mix/rate identity
 * the drill-down uses, so the forward and backward views of the table reconcile.
 */
export function ScenarioView({
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
  const [query, setQuery] = useState<ScenarioQuery>({});
  const [levers, setLevers] = useState<Levers>({ global: {}, segments: {} });
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [goal, setGoal] = useState<GoalSeekResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = datasetId ?? datasets[0]?.id ?? null;

  useEffect(() => {
    setResult(null);
    setGoal(null);
    setError(null);
    setQuery({});
    setLevers({ global: {}, segments: {} });
  }, [active]);

  const run = useCallback(
    async (next: ScenarioQuery, nextLevers: Levers) => {
      if (!active) return;
      setLoading(true);
      setError(null);
      try {
        const computed = await api.simulateScenario(active, { ...next, levers: nextLevers });
        setResult(computed);
        setQuery({
          ...next,
          measure: computed.measure,
          dimension: computed.dimension,
          aggregation: computed.aggregation,
        });
      } catch (e) {
        setError(describe(e));
      } finally {
        setLoading(false);
      }
    },
    [active],
  );

  useEffect(() => {
    if (active) void run({}, { global: {}, segments: {} });
  }, [active, run]);

  const update = (patch: ScenarioQuery) => {
    // Changing the measure or the grouping invalidates every lever set against it.
    const resetLevers = patch.dimension !== undefined || patch.measure !== undefined;
    const nextLevers = resetLevers ? { global: {}, segments: {} } : levers;
    if (resetLevers) setLevers(nextLevers);
    setGoal(null);
    const next = { ...query, ...patch };
    setQuery(next);
    void run(next, nextLevers);
  };

  const setLever = (next: Levers) => {
    setLevers(next);
    setGoal(null);
    void run(query, next);
  };

  const reset = () => setLever({ global: {}, segments: {} });

  const seek = async (target: number, lever: LeverKind, segment: string | null) => {
    if (!active) return;
    setLoading(true);
    try {
      const found = await api.goalSeek(active, { ...query, levers, target, lever, segment });
      setGoal(found);
      setResult(found.scenario);
      if (found.achievable) setLevers(found.scenario.levers);
    } catch (e) {
      toast.error("Goal seek failed", describe(e));
    } finally {
      setLoading(false);
    }
  };

  const measures = result?.options.measures ?? [];
  const dimensions = result?.options.dimensions ?? [];
  const untouched = useMemo(() => isEmpty(levers), [levers]);

  if (!datasets.length) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <EmptyState
          icon={<SlidersHorizontal className="size-5" />}
          title="Nothing to plan with yet"
          body="Upload a CSV or Excel file, connect a SQL source, or load the sample dataset, and you can model what would move the number."
        />
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 pt-8 pb-16 sm:px-6">
        <ViewHeader
          eyebrow="Scenarios"
          icon={<SlidersHorizontal className="size-3.5" />}
          title="What would have to be true?"
          body="Move volume, per-record rate or the mix between segments and read off what the measure becomes — or name a target and let Numera solve for the lever. Projected in pandas from the cleaned table, and decomposed with the same volume/mix/rate identity as the drill-down."
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
            value={query.measure ?? ""}
            disabled={!measures.length}
            onChange={(value) => {
              const picked = measures.find((m) => m.name === value);
              update({ measure: value, aggregation: picked?.aggregation });
            }}
            options={measures.map((m) => ({ value: m.name, label: m.name }))}
          />
          <Select
            label="Segment by"
            value={query.dimension ?? ""}
            disabled={!dimensions.length}
            onChange={(value) => update({ dimension: value })}
            options={dimensions.map((d) => ({ value: d, label: d }))}
          />
          <Select
            label="Baseline"
            value={query.period ?? "auto"}
            onChange={(value) => update({ period: value as PeriodMode })}
            options={PERIODS.map((p) => ({ value: p.id, label: p.label }))}
          />
          <Select
            label="Aggregate"
            value={query.aggregation ?? "sum"}
            onChange={(value) => update({ aggregation: value as Aggregation })}
            options={[
              { value: "sum", label: "Total" },
              { value: "mean", label: "Average" },
            ]}
          />
          {loading && <LoaderCircle className="mb-2 size-4 animate-spin text-ink-3" />}
          {!untouched && (
            <Button size="sm" variant="ghost" className="mb-0.5 ml-auto" onClick={reset}>
              <RotateCcw className="size-3.5" />
              Reset levers
            </Button>
          )}
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

        {result && active && (
          <>
            <section className="mt-6 rounded-xl border border-line bg-panel p-5 shadow-card">
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-[13px] text-ink-2">Today</p>
                  <p className="text-xl font-semibold tracking-tight text-ink tabular-nums">
                    {formatValue(result.baseline.value)}
                  </p>
                </div>
                <ArrowRight className="mb-1.5 size-4 shrink-0 text-ink-3" />
                <div className="min-w-0">
                  <p className="text-[13px] text-ink-2">Scenario</p>
                  <p className="text-xl font-semibold tracking-tight text-ink tabular-nums">
                    {formatValue(result.scenario.value)}
                  </p>
                </div>
                <div className="ml-auto text-right">
                  <p className="text-[13px] text-ink-2">Change</p>
                  <p
                    className={cn(
                      "text-2xl font-semibold tracking-tight tabular-nums",
                      result.change.direction === "up"
                        ? "text-good"
                        : result.change.direction === "down"
                          ? "text-bad"
                          : "text-ink",
                    )}
                  >
                    {result.change.absolute > 0 ? "+" : ""}
                    {formatValue(result.change.absolute)}
                    {result.change.pct !== null && (
                      <span className="ml-1.5 text-base font-medium">
                        ({signed(result.change.pct * 100)})
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <p className="mt-3.5 border-t border-line pt-3.5 text-[14px] leading-relaxed font-medium text-ink">
                {result.headline}
              </p>
              {result.window && (
                <p className="mt-1 text-[12px] text-ink-3">
                  Baseline: {result.window.label} ({result.window.start.slice(0, 10)} →{" "}
                  {result.window.end.slice(0, 10)}) · {Math.round(result.baseline.rows).toLocaleString()} records
                </p>
              )}
            </section>

            <GoalSeekPanel
              result={result}
              goal={goal}
              busy={loading}
              onSeek={seek}
              onClear={() => setGoal(null)}
            />

            <LeverPanel result={result} levers={levers} onChange={setLever} />

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

            {result.shift_share && !untouched && (
              <section className="mt-6">
                <SectionLabel>Volume · mix · rate</SectionLabel>
                <div className="grid gap-2.5 sm:grid-cols-3">
                  {result.shift_share.terms.map((term) => (
                    <div
                      key={term.key}
                      className="rounded-xl border border-line bg-panel p-3.5 shadow-card"
                    >
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
                <p className="mt-2 text-[11.5px] text-ink-3">
                  {result.shift_share.closes
                    ? "The three terms add back to the projected change exactly."
                    : "The split does not reconcile exactly — treat it as indicative."}
                </p>
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
                        source: "scenarios",
                        datasetId: active,
                        params: { ...query, levers },
                        index,
                        label: chart.title,
                      }}
                    />
                  }
                />
              ))}
            </div>

            <div className="mt-6 space-y-5">
              {result.tables.map((table, index) => (
                <TableCard
                  key={table.title}
                  table={table}
                  actions={
                    <PinButton
                      target={{
                        kind: "table",
                        source: "scenarios",
                        datasetId: active,
                        params: { ...query, levers },
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
                <SectionLabel icon={<Info className="size-3.5" />}>
                  What this does not say
                </SectionLabel>
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
                    .downloadScenario(active, { ...query, levers })
                    .catch((e) => toast.error("Export failed", describe(e)))
                }
              >
                <Download className="size-4" />
                Export scenario
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** Name a target and solve for the lever, rather than guessing at the slider. */
function GoalSeekPanel({
  result,
  goal,
  busy,
  onSeek,
  onClear,
}: {
  result: ScenarioResult;
  goal: GoalSeekResult | null;
  busy: boolean;
  onSeek: (target: number, lever: LeverKind, segment: string | null) => void;
  onClear: () => void;
}) {
  const [target, setTarget] = useState("");
  const [lever, setLever] = useState<LeverKind>("rate");
  const [segment, setSegment] = useState("");

  const suggested = Math.round(result.baseline.value * 1.1);

  return (
    <section className="mt-5 rounded-xl border border-line bg-panel p-4 shadow-card">
      <SectionLabel icon={<Target className="size-3.5" />}>Goal seek</SectionLabel>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          const value = Number(target);
          if (Number.isFinite(value)) onSeek(value, lever, segment || null);
        }}
      >
        <TextInput
          label={`Target ${result.measure}`}
          type="number"
          step="any"
          value={target}
          placeholder={String(suggested)}
          onChange={(event) => setTarget(event.target.value)}
          className="w-40"
        />
        <Select
          label="By moving"
          value={lever}
          onChange={(value) => setLever(value as LeverKind)}
          options={[
            { value: "rate", label: "Value per record" },
            { value: "volume", label: "Number of records" },
          ]}
        />
        <Select
          label="Where"
          value={segment}
          onChange={setSegment}
          options={[
            { value: "", label: "Across the board" },
            ...result.segments.map((s) => ({ value: s.label, label: s.label })),
          ]}
        />
        <Button type="submit" variant="primary" loading={busy} disabled={!target}>
          <Target className="size-4" />
          Solve
        </Button>
        {goal && (
          <Button type="button" variant="ghost" onClick={onClear}>
            Clear
          </Button>
        )}
      </form>
      {goal && (
        <div
          className={cn(
            "mt-3.5 flex items-start gap-2.5 rounded-lg border p-3 text-[13px] leading-relaxed",
            goal.achievable ? "border-good/30 bg-good-soft" : "border-warn/30 bg-warn-soft",
          )}
        >
          {goal.achievable ? (
            <Target className="mt-0.5 size-4 shrink-0 text-good" />
          ) : (
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" />
          )}
          <div className="min-w-0">
            <p className="text-ink">{goal.message}</p>
            {goal.achievable && (
              <p className="mt-1 text-[12px] text-ink-2">
                The levers below have been set to that value — adjust them to see what else
                would get you there.
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/** Sliders for the levers, plus the leverage ranking that says which one to pull. */
function LeverPanel({
  result,
  levers,
  onChange,
}: {
  result: ScenarioResult;
  levers: Levers;
  onChange: (levers: Levers) => void;
}) {
  const leverage = new Map(result.sensitivity.map((s) => [s.label, s]));
  const globalLevers = levers.global ?? {};

  const setGlobal = (key: keyof SegmentLever, value: number) =>
    onChange({ ...levers, global: { ...globalLevers, [key]: value / 100 } });

  const setSegment = (label: string, key: keyof SegmentLever, value: number) =>
    onChange({
      ...levers,
      segments: {
        ...levers.segments,
        [label]: { ...(levers.segments?.[label] ?? {}), [key]: value / 100 },
      },
    });

  return (
    <section className="mt-5 rounded-xl border border-line bg-panel p-4 shadow-card">
      <SectionLabel icon={<Zap className="size-3.5" />}>Levers</SectionLabel>
      <div className="grid gap-3 sm:grid-cols-2">
        <Slider
          label="Value per record, everywhere"
          value={Math.round((globalLevers.rate_pct ?? 0) * 100)}
          min={LEVER_MIN}
          max={LEVER_MAX}
          onChange={(value) => setGlobal("rate_pct", value)}
        />
        <Slider
          label="Number of records, everywhere"
          value={Math.round((globalLevers.volume_pct ?? 0) * 100)}
          min={LEVER_MIN}
          max={LEVER_MAX}
          disabled={result.aggregation === "mean"}
          hint={
            result.aggregation === "mean"
              ? "An averaged measure does not move with the record count"
              : undefined
          }
          onChange={(value) => setGlobal("volume_pct", value)}
        />
      </div>

      <div className="mt-4 space-y-3 border-t border-line pt-4">
        {result.segments.map((segment) => {
          const lever = levers.segments?.[segment.label] ?? {};
          const sensitivity = leverage.get(segment.label);
          return (
            <div key={segment.label} className="rounded-lg border border-line bg-subtle p-3">
              <div className="mb-2.5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="truncate text-[13px] font-medium text-ink" title={segment.label}>
                  {segment.label}
                </span>
                <span className="text-[11.5px] text-ink-3 tabular-nums">
                  {formatValue(segment.baseline_value)} → {formatValue(segment.scenario_value)}
                </span>
                {sensitivity?.leverage != null && (
                  <Badge
                    tone={sensitivity.rank === 1 ? "accent" : "neutral"}
                    className="ml-auto"
                  >
                    {sensitivity.leverage.toFixed(1)}× leverage
                  </Badge>
                )}
              </div>
              <div className="grid gap-2.5 sm:grid-cols-3">
                <Slider
                  label="Value per record"
                  compact
                  value={Math.round((lever.rate_pct ?? 0) * 100)}
                  min={LEVER_MIN}
                  max={LEVER_MAX}
                  onChange={(value) => setSegment(segment.label, "rate_pct", value)}
                />
                <Slider
                  label="Records"
                  compact
                  value={Math.round((lever.volume_pct ?? 0) * 100)}
                  min={LEVER_MIN}
                  max={LEVER_MAX}
                  onChange={(value) => setSegment(segment.label, "volume_pct", value)}
                />
                <Slider
                  label={`Share of mix (now ${(segment.scenario_share * 100).toFixed(1)}%)`}
                  compact
                  suffix="pts"
                  value={Math.round((lever.share_points ?? 0) * 100)}
                  min={SHARE_MIN}
                  max={SHARE_MAX}
                  onChange={(value) => setSegment(segment.label, "share_points", value)}
                />
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-3 flex items-start gap-1.5 border-t border-line pt-3 text-[12px] leading-relaxed text-ink-3">
        <Info className="mt-px size-3.5 shrink-0" />
        Moving one segment&apos;s share of the mix redistributes the rest in proportion, so the
        shares always add to 100%. Leverage is what a 1% lift there is worth relative to that
        segment&apos;s share of records.
      </p>
    </section>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  onChange,
  disabled,
  hint,
  compact,
  suffix = "%",
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  hint?: string;
  compact?: boolean;
  suffix?: string;
}) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1", disabled && "opacity-50")}>
      <span className="flex items-baseline justify-between gap-2">
        <span
          className={cn("truncate text-ink-2", compact ? "text-[11.5px]" : "text-[12.5px]")}
          title={label}
        >
          {label}
        </span>
        <span
          className={cn(
            "shrink-0 font-medium tabular-nums",
            compact ? "text-[11.5px]" : "text-[13px]",
            value > 0 ? "text-good" : value < 0 ? "text-bad" : "text-ink-3",
          )}
        >
          {value > 0 ? "+" : value < 0 ? "−" : ""}
          {Math.abs(value)}
          {suffix}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-muted accent-accent disabled:cursor-not-allowed"
      />
      {hint && <span className="text-[11px] leading-snug text-ink-3">{hint}</span>}
    </label>
  );
}
