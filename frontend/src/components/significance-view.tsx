"use client";

import {
  CircleCheck,
  CircleHelp,
  Download,
  FlaskConical,
  Info,
  LoaderCircle,
  MessageSquare,
  Minus,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ChartCard } from "@/components/chat/chart-card";
import { TableCard } from "@/components/chat/table-card";
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
  DatasetSummary,
  MetricKind,
  SignificanceOptions,
  SignificanceQuery,
  SignificanceResult,
  VerdictLabel,
} from "@/lib/types";

const ALPHAS = [
  { value: "0.1", label: "90% confidence", title: "α = 0.10 — more findings, more false ones" },
  { value: "0.05", label: "95% confidence", title: "α = 0.05 — the usual default" },
  { value: "0.01", label: "99% confidence", title: "α = 0.01 — only strong evidence counts" },
];

const VERDICT: Record<VerdictLabel, { tone: "good" | "warn" | "neutral"; label: string; icon: React.ReactNode }> = {
  real: { tone: "good", label: "Real difference", icon: <CircleCheck className="size-4" /> },
  noise: { tone: "neutral", label: "Not distinguishable", icon: <Minus className="size-4" /> },
  underpowered: { tone: "warn", label: "Not enough data", icon: <TriangleAlert className="size-4" /> },
  inconclusive: { tone: "neutral", label: "No test applies", icon: <CircleHelp className="size-4" /> },
};

const VERDICT_STYLES: Record<"good" | "warn" | "neutral", string> = {
  good: "border-good/30 bg-good-soft",
  warn: "border-warn/30 bg-warn-soft",
  neutral: "border-line bg-subtle",
};

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

function showValue(value: number | null | undefined, kind: MetricKind): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return kind === "proportion" ? `${(value * 100).toFixed(2)}%` : formatValue(value);
}

function showP(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "n/a";
  return p < 0.0001 ? "< 0.0001" : p.toFixed(4);
}

/**
 * The significance lab: is the gap between two groups real, or is it what this much data
 * produces by chance? Deterministic Python on the server — no model call, so it is free
 * and gives the same answer twice.
 */
export function SignificanceView({
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
  const [options, setOptions] = useState<SignificanceOptions | null>(null);
  const [query, setQuery] = useState<SignificanceQuery>({});
  const [result, setResult] = useState<SignificanceResult | null>(null);
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
    async (next: SignificanceQuery) => {
      if (!active) return;
      setLoading(true);
      setError(null);
      try {
        const computed = await api.testSignificance(active, next);
        setResult(computed);
        setOptions(computed.options);
        setQuery({
          ...next,
          measure: computed.measure,
          dimension: computed.dimension ?? undefined,
          group_a: computed.groups[0].label,
          group_b: computed.groups[1].label,
        });
      } catch (e) {
        setResult(null);
        setError(describe(e));
        // Still populate the pickers so a rejected request can be corrected in place.
        void api.significanceOptions(active).then(setOptions).catch(() => undefined);
      } finally {
        setLoading(false);
      }
    },
    [active],
  );

  useEffect(() => {
    if (active) void run({});
  }, [active, run]);

  const update = (patch: SignificanceQuery) => {
    const next = { ...query, ...patch };
    setQuery(next);
    void run(next);
  };

  if (!datasets.length) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <EmptyState
          icon={<FlaskConical className="size-5" />}
          title="Nothing to test yet"
          body="Upload a CSV or Excel file, connect a SQL source, or load the sample dataset, and you can test whether any difference in it is real."
        />
      </div>
    );
  }

  const measures = options?.measures ?? [];
  const dimensions = options?.dimensions ?? [];
  // Group choices come from the scan, which already lists every value with data.
  const groupChoices = result?.scan?.rows.map((row) => row.label) ?? [];
  const verdict = result ? VERDICT[result.verdict.label] : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 pt-8 pb-16 sm:px-6">
        <ViewHeader
          eyebrow="Significance"
          icon={<FlaskConical className="size-3.5" />}
          title="Is the difference real?"
          body="Numera compares two groups with the right test for the measure, puts a confidence interval on the gap itself, and says what size of difference this much data could actually have detected. Computed in Python from the cleaned table, so it costs nothing and never invents a p-value."
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
            onChange={(value) => update({ measure: value, group_a: undefined, group_b: undefined })}
            options={measures.map((m) => ({
              value: m.name,
              label: m.name,
              title: m.kind === "proportion" ? "A yes/no rate" : "An average",
            }))}
          />
          <Select
            label="Compare"
            value={query.mode ?? options?.defaults.mode ?? "segments"}
            onChange={(value) =>
              update({
                mode: value as SignificanceQuery["mode"],
                group_a: undefined,
                group_b: undefined,
              })
            }
            options={[
              { value: "segments", label: "Two segments", title: "Two values of a category" },
              { value: "periods", label: "Two periods", title: "This period versus the one before" },
            ]}
          />
          {(query.mode ?? "segments") === "segments" && (
            <Select
              label="Split by"
              value={query.dimension ?? options?.defaults.dimension ?? ""}
              disabled={!dimensions.length}
              onChange={(value) =>
                update({ dimension: value, group_a: undefined, group_b: undefined })
              }
              options={dimensions.map((d) => ({ value: d, label: d }))}
            />
          )}
          {(query.mode ?? "segments") === "segments" && groupChoices.length > 1 && (
            <>
              <Select
                label="Group A"
                value={query.group_a ?? ""}
                onChange={(value) => update({ group_a: value })}
                options={groupChoices.map((g) => ({ value: g, label: g }))}
              />
              <Select
                label="Group B"
                value={query.group_b ?? ""}
                onChange={(value) => update({ group_b: value })}
                options={groupChoices.map((g) => ({ value: g, label: g }))}
              />
            </>
          )}
          <Select
            label="Threshold"
            value={String(query.alpha ?? 0.05)}
            onChange={(value) => update({ alpha: Number(value) })}
            options={ALPHAS}
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
            <div className="h-32 animate-pulse rounded-xl bg-muted" />
            <div className="h-64 animate-pulse rounded-xl bg-muted" />
          </div>
        )}

        {result && verdict && active && (
          <>
            <section
              className={cn("mt-6 rounded-xl border p-5 shadow-card", VERDICT_STYLES[verdict.tone])}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={verdict.tone === "neutral" ? "neutral" : verdict.tone}>
                  <span className="flex items-center gap-1">
                    {verdict.icon}
                    {verdict.label}
                  </span>
                </Badge>
                <span className="text-[12px] text-ink-3 tabular-nums">
                  p = {showP(result.p_value)} · {result.tests[0]?.name}
                </span>
              </div>
              <p className="mt-2.5 text-[15px] leading-snug font-semibold text-ink">
                {result.verdict.headline}
              </p>
              <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{result.verdict.detail}</p>
            </section>

            <section className="mt-5 grid gap-2.5 sm:grid-cols-3">
              {result.groups.map((group) => (
                <div key={group.label} className="rounded-xl border border-line bg-panel p-3.5 shadow-card">
                  <p className="truncate text-[12px] text-ink-2" title={group.label}>
                    {group.label}
                  </p>
                  <p className="mt-0.5 text-xl font-semibold tracking-tight text-ink tabular-nums">
                    {showValue(group.value, result.metric_kind)}
                  </p>
                  <p className="mt-1 text-[11.5px] text-ink-3 tabular-nums">
                    {showValue(group.ci_low, result.metric_kind)} –{" "}
                    {showValue(group.ci_high, result.metric_kind)} · {group.n.toLocaleString()} records
                  </p>
                  {group.small && (
                    <Badge tone="warn" className="mt-1.5">
                      Too few records
                    </Badge>
                  )}
                </div>
              ))}
              <div className="rounded-xl border border-line bg-panel p-3.5 shadow-card">
                <p className="text-[12px] text-ink-2">Difference</p>
                <p
                  className={cn(
                    "mt-0.5 text-xl font-semibold tracking-tight tabular-nums",
                    result.significant
                      ? result.difference.direction === "up"
                        ? "text-good"
                        : "text-bad"
                      : "text-ink",
                  )}
                >
                  {result.difference.absolute > 0 ? "+" : ""}
                  {showValue(result.difference.absolute, result.metric_kind)}
                </p>
                <p className="mt-1 text-[11.5px] text-ink-3 tabular-nums">
                  {showValue(result.difference.ci_low, result.metric_kind)} to{" "}
                  {showValue(result.difference.ci_high, result.metric_kind)}
                  {result.difference.crosses_zero ? " · includes zero" : ""}
                </p>
              </div>
            </section>

            <EffectAndPower result={result} />

            <div className="mt-6 space-y-5">
              {result.charts.map((chart, index) => (
                <ChartCard
                  key={chart.title}
                  chart={chart}
                  actions={
                    <PinButton
                      target={{
                        kind: "chart",
                        source: "significance",
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

            {result.scan && <ScanSection result={result} />}

            <section className="mt-6 rounded-xl border border-line bg-panel p-4 shadow-card">
              <SectionLabel>How this was tested</SectionLabel>
              <ul className="space-y-2">
                {result.tests.map((test) => (
                  <li key={test.id} className="text-[13px] leading-relaxed text-ink-2">
                    <span className="font-medium text-ink">{test.name}</span>
                    {" — "}
                    <span className="tabular-nums">p = {showP(test.p_value)}</span>. {test.assumption}
                  </li>
                ))}
              </ul>
            </section>

            <div className="mt-6 space-y-5">
              {result.tables.map((table, index) => (
                <TableCard
                  key={table.title}
                  table={table}
                  actions={
                    <PinButton
                      target={{
                        kind: "table",
                        source: "significance",
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
                    .downloadSignificance(active, query)
                    .catch((e) => toast.error("Export failed", describe(e)))
                }
              >
                <Download className="size-4" />
                Export briefing
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function EffectAndPower({ result }: { result: SignificanceResult }) {
  const { effect, power, metric_kind: kind } = result;
  return (
    <section className="mt-5 rounded-xl border border-line bg-panel p-4 shadow-card">
      <SectionLabel>Effect size and what this sample could see</SectionLabel>
      <div className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
        <Fact
          label="Effect size"
          value={effect.magnitude}
          detail={`Hedges' g ${effect.hedges_g?.toFixed(2) ?? "—"} · Cliff's delta ${
            effect.cliffs_delta?.toFixed(2) ?? "—"
          }`}
        />
        <Fact
          label={`Power to detect a medium effect (d = ${power.reference_effect})`}
          value={power.power_at_reference === null ? "—" : `${Math.round(power.power_at_reference * 100)}%`}
          detail={`Target ${Math.round(power.target_power * 100)}%`}
          tone={power.adequate ? "good" : "warn"}
        />
        <Fact
          label="Smallest difference this sample could detect"
          value={showValue(power.mde_absolute, kind)}
          detail="At 80% power, two-sided"
        />
        <Fact
          label="Records per group to confirm this effect"
          value={power.required_n_per_group ? power.required_n_per_group.toLocaleString() : "—"}
          detail={
            power.required_n_per_group
              ? `You have ${result.groups.map((g) => g.n.toLocaleString()).join(" and ")}`
              : "The observed effect is zero, so no sample size applies"
          }
        />
      </div>
      <p className="mt-3 flex items-start gap-1.5 border-t border-line pt-3 text-[12px] leading-relaxed text-ink-3">
        <Info className="mt-px size-3.5 shrink-0" />
        Power here is prospective, not computed from the result: post-hoc power is just the
        p-value restated, and it makes every null result look inconclusive.
      </p>
    </section>
  );
}

function Fact({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "good" | "warn";
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11.5px] text-ink-3">{label}</p>
      <p
        className={cn(
          "text-[15px] font-semibold tracking-tight capitalize tabular-nums",
          tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : "text-ink",
        )}
      >
        {value}
      </p>
      <p className="text-[11.5px] leading-snug text-ink-3">{detail}</p>
    </div>
  );
}

/** Every segment tested at once, with the false-discovery rate controlled. */
function ScanSection({ result }: { result: SignificanceResult }) {
  const scan = result.scan;
  if (!scan) return null;
  const inflated = scan.n_significant_uncorrected - scan.n_significant;

  return (
    <section className="mt-6 rounded-xl border border-line bg-panel p-4 shadow-card">
      <SectionLabel>Every {scan.dimension} against the rest</SectionLabel>
      <p className="mb-3 text-[13px] leading-relaxed text-ink-2">
        <span className="font-medium text-ink">{scan.n_significant}</span> of {scan.n_tests} survive
        false-discovery correction
        {inflated > 0 && (
          <>
            {" — "}
            <span className="font-medium text-ink">{inflated} more</span> looked significant before
            it. Testing {scan.n_tests} segments at once is {scan.n_tests} chances to find a
            one-in-twenty fluke.
          </>
        )}
        .
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-[12.5px]">
          <thead>
            <tr className="border-b border-line text-left text-ink-3">
              <th className="py-1.5 pr-3 font-medium">{scan.dimension}</th>
              <th className="py-1.5 pr-3 text-right font-medium">Records</th>
              <th className="py-1.5 pr-3 text-right font-medium">Value</th>
              <th className="py-1.5 pr-3 text-right font-medium">vs rest</th>
              <th className="py-1.5 pr-3 text-right font-medium">p</th>
              <th className="py-1.5 pr-3 text-right font-medium" title="Benjamini-Hochberg adjusted">
                q
              </th>
              <th className="py-1.5 font-medium">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {scan.rows.map((row) => (
              <tr key={row.label} className="border-b border-line/60 last:border-0">
                <td className="max-w-40 truncate py-1.5 pr-3 text-ink" title={row.label}>
                  {row.label}
                </td>
                <td className="py-1.5 pr-3 text-right text-ink-2 tabular-nums">
                  {row.n.toLocaleString()}
                </td>
                <td className="py-1.5 pr-3 text-right text-ink tabular-nums">
                  {showValue(row.value, result.metric_kind)}
                </td>
                <td
                  className={cn(
                    "py-1.5 pr-3 text-right tabular-nums",
                    row.difference > 0 ? "text-good" : row.difference < 0 ? "text-bad" : "text-ink-2",
                  )}
                >
                  {row.difference > 0 ? "+" : ""}
                  {showValue(row.difference, result.metric_kind)}
                </td>
                <td className="py-1.5 pr-3 text-right text-ink-3 tabular-nums">
                  {showP(row.p_value)}
                </td>
                <td className="py-1.5 pr-3 text-right text-ink-2 tabular-nums">
                  {showP(row.p_adjusted)}
                </td>
                <td className="py-1.5">
                  {row.significant ? (
                    <Badge tone="good">Real</Badge>
                  ) : row.small ? (
                    <Badge tone="warn">Too few</Badge>
                  ) : (
                    <span className="text-ink-3">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {scan.truncated && (
        <p className="mt-2 text-[11.5px] text-ink-3">
          Only the largest segments are tested; the rest are omitted.
        </p>
      )}
    </section>
  );
}
