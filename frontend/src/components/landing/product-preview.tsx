import {
  BadgeCheck,
  Bell,
  Check,
  FlaskConical,
  LoaderCircle,
  MessageSquare,
  Split,
  TrendingUp,
  Users,
} from "lucide-react";

import { LogoMark } from "@/components/brand";
import { CountUp } from "@/components/landing/motion";

/*
 * A drawn, not screenshotted, picture of the workspace: it stays crisp at any size and
 * follows the theme. The figures are what Numera actually reports for the bundled
 * sample retail dataset, so the preview never promises a number the product can't show.
 *
 * On load it plays like a live run — question, agent steps, KPIs, chart, write-up — using
 * CSS delays (`--at`) alone. With reduced motion it is simply the finished answer.
 */

const HISTORY = 30;
const HORIZON = 6;
const W = 600;
const H = 170;
const PAD = 8;

// A deterministic trend-plus-seasonality series shaped like the sample's monthly revenue.
const series = Array.from({ length: HISTORY + HORIZON }, (_, i) => {
  const seasonal = 26 * Math.sin((2 * Math.PI * (i - 2)) / 12);
  const noise = i < HISTORY ? 7 * Math.sin(i * 1.7) + 4 * Math.cos(i * 2.9) : 0;
  return 205 + 1.3 * i + seasonal + noise;
});
const lo = Math.min(...series) - 30;
const hi = Math.max(...series) + 30;
const x = (i: number) => PAD + (i / (series.length - 1)) * (W - PAD * 2);
const y = (v: number) => H - PAD - ((v - lo) / (hi - lo)) * (H - PAD * 2);
const line = (points: [number, number][]) =>
  points.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");

const past = series.slice(0, HISTORY).map((v, i) => [x(i), y(v)] as [number, number]);
const future = series.slice(HISTORY - 1).map((v, i) => [x(HISTORY - 1 + i), y(v)] as [number, number]);
const band = series.slice(HISTORY - 1).map((v, i) => ({ i: HISTORY - 1 + i, spread: 4 + i * 5, v }));
const pastPath = line(past);
const areaPath = `${pastPath} L${x(HISTORY - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`;
const futurePath = line(future);
const bandPath =
  line(band.map(({ i, v, spread }) => [x(i), y(v + spread)])) +
  " " +
  band
    .slice()
    .reverse()
    .map(({ i, v, spread }) => `L${x(i).toFixed(1)},${y(v - spread).toFixed(1)}`)
    .join(" ") +
  " Z";
const [lastX, lastY] = past[past.length - 1];

/** The run's timeline, in milliseconds from first paint. */
const T = {
  question: 350,
  steps: [800, 1180, 1560, 1940],
  kpis: [2250, 2350, 2450],
  chart: 2650,
  forecast: 3750,
  write: 4350,
  badge: 4700,
};
const SPIN = 380;

const at = (ms: number, extra?: Record<string, string>) => ({ "--at": `${ms}ms`, ...extra }) as React.CSSProperties;

const KPIS = [
  { label: "Revenue, last 12 months", to: 2.6, decimals: 1, suffix: "M", delta: "+8.4%" },
  { label: "Online share of revenue", to: 28.5, decimals: 1, suffix: "%", delta: "+8.1 pts" },
  { label: "Projected, next 6 months", to: 1.6, decimals: 1, suffix: "M", delta: "+4.6%" },
];

const STEPS = ["Planned the analysis", "Wrote pandas", "Ran in the sandbox", "Every figure verified"];

const NAV = [
  { icon: Split, label: "Drivers" },
  { icon: FlaskConical, label: "Significance" },
  { icon: Users, label: "Retention" },
  { icon: TrendingUp, label: "Forecast" },
  { icon: Bell, label: "Monitors" },
];

export function ProductPreview() {
  return (
    <figure
      className="overflow-clip rounded-2xl border border-line-strong bg-panel text-left shadow-pop"
      aria-label="The Numera workspace answering why revenue grew in the sample retail dataset, with KPIs, a forecast chart and a verified write-up"
    >
      {/* Window chrome */}
      <div className="flex h-10 items-center gap-3 border-b border-line bg-muted/60 px-4">
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="size-2.5 rounded-full bg-line-strong" />
          <span className="size-2.5 rounded-full bg-line-strong" />
          <span className="size-2.5 rounded-full bg-line-strong" />
        </div>
        <p className="min-w-0 flex-1 truncate text-center text-[12px] text-ink-3">
          Retail Sales (Sample) · Revenue growth review
        </p>
        <span
          className="seq-pop hidden items-center gap-1 rounded-md bg-good-soft px-1.5 py-0.5 text-[11px] font-medium text-good sm:inline-flex"
          style={at(T.badge)}
        >
          <BadgeCheck className="size-3" />
          Verified
        </span>
      </div>

      <div className="flex" aria-hidden="true">
        {/* Sidebar */}
        <div className="hidden w-48 shrink-0 border-r border-line p-3 md:block">
          <div className="flex items-center gap-2 px-1">
            <LogoMark className="size-6" />
            <span className="text-[13px] font-semibold text-ink">Numera</span>
          </div>
          <p className="mt-5 px-1 text-[10px] font-medium tracking-wide text-ink-3 uppercase">Tools</p>
          <div className="mt-1.5 space-y-0.5">
            {NAV.map(({ icon: Icon, label }) => (
              <div key={label} className="flex items-center gap-2 rounded-md px-1.5 py-1.5 text-[12px] text-ink-2">
                <Icon className="size-3.5 text-ink-3" />
                {label}
              </div>
            ))}
          </div>
          <p className="mt-5 px-1 text-[10px] font-medium tracking-wide text-ink-3 uppercase">Analyses</p>
          <div className="mt-1.5 space-y-0.5">
            <div className="flex items-center gap-2 rounded-md bg-muted px-1.5 py-1.5 text-[12px] font-medium text-ink">
              <MessageSquare className="size-3.5 text-accent" />
              <span className="truncate">Revenue growth review</span>
            </div>
            <div className="flex items-center gap-2 rounded-md px-1.5 py-1.5 text-[12px] text-ink-2">
              <MessageSquare className="size-3.5 text-ink-3" />
              <span className="truncate">Regional mix, Q4</span>
            </div>
          </div>
        </div>

        {/* Conversation */}
        <div className="min-w-0 flex-1 space-y-4 p-4 sm:p-6">
          <div className="seq flex justify-end" style={at(T.question)}>
            <p className="max-w-md rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-[13px] leading-snug text-white">
              Why did revenue grow last year, and what should we expect next?
            </p>
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {STEPS.map((step, index) => (
              <span
                key={step}
                className="seq inline-flex items-center gap-1.5 text-[11.5px] text-ink-3"
                style={at(T.steps[index])}
              >
                {/* Works, then ticks: a spinner for a beat, replaced by the check. */}
                <span className="relative inline-flex size-3">
                  <LoaderCircle className="seq-spinner absolute inset-0 size-3 text-accent" style={at(T.steps[index])} />
                  <Check className="seq-pop absolute inset-0 size-3 text-good" style={at(T.steps[index] + SPIN)} />
                </span>
                {step}
              </span>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
            {KPIS.map((kpi, index) => (
              <div
                key={kpi.label}
                data-count-sync
                className="seq rounded-xl border border-line p-3"
                style={at(T.kpis[index])}
              >
                <p className="truncate text-[11px] text-ink-3">{kpi.label}</p>
                <p className="mt-1 flex items-baseline gap-2">
                  <span className="text-xl font-semibold tracking-tight text-ink tabular-nums">
                    <CountUp to={kpi.to} decimals={kpi.decimals} suffix={kpi.suffix} duration={1000} />
                  </span>
                  <span className="seq-fade text-[12px] font-medium text-good tabular-nums" style={at(T.kpis[index] + 700)}>
                    {kpi.delta}
                  </span>
                </p>
              </div>
            ))}
          </div>

          <div className="seq rounded-xl border border-line p-3" style={at(T.chart - 150)}>
            <div className="flex items-center justify-between gap-2">
              <p className="text-[12px] font-medium text-ink">Monthly revenue, with a 6-month forecast</p>
              <span className="hidden items-center gap-3 text-[10.5px] text-ink-3 sm:flex">
                <span className="flex items-center gap-1">
                  <span className="h-0.5 w-3 rounded bg-accent" /> Actual
                </span>
                <span className="seq-fade flex items-center gap-1" style={at(T.forecast)}>
                  <span className="h-0.5 w-3 rounded border-t border-dashed border-accent" /> Forecast · 80% interval
                </span>
              </span>
            </div>
            <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 h-auto w-full" preserveAspectRatio="none">
              {[0.25, 0.5, 0.75].map((f) => (
                <line key={f} x1={PAD} x2={W - PAD} y1={H * f} y2={H * f} className="stroke-line" strokeWidth="1" />
              ))}
              {/* History draws left to right, then the forecast extends from its last point. */}
              <g className="seq-wipe" style={at(T.chart, { "--dur": "1.1s" })}>
                <path d={areaPath} className="fill-accent/10" />
                <path d={pastPath} className="fill-none stroke-accent" strokeWidth="2" strokeLinejoin="round" />
              </g>
              <g className="seq-wipe" style={at(T.forecast, { "--dur": "0.7s" })}>
                <path d={bandPath} className="fill-accent/15" />
                <path
                  d={futurePath}
                  className="fill-none stroke-accent"
                  strokeWidth="2"
                  strokeDasharray="5 4"
                  strokeLinejoin="round"
                />
              </g>
              <circle cx={lastX} cy={lastY} r="3.5" className="pulse-ring fill-accent" style={at(T.forecast + 300)} />
              <circle cx={lastX} cy={lastY} r="3.5" className="seq-pop fill-accent" style={at(T.forecast - 80)} />
            </svg>
          </div>

          <p className="seq text-[13px] leading-relaxed text-ink-2" style={at(T.write)}>
            <span className="font-semibold text-ink">Online drove the growth.</span> It contributed +254.9K — more
            than the whole 204.2K increase — while Retail Store fell −140.8K. A linear trend beat the naive baseline by
            37% in backtesting and projects revenue 4.6% higher over the next six months.
          </p>
        </div>
      </div>
    </figure>
  );
}
