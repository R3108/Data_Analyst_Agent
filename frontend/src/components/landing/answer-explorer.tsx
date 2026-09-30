"use client";

import { BadgeCheck, Check, Split, TrendingUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { FORECAST_CHART } from "@/components/landing/forecast-geometry";
import { CountUp } from "@/components/landing/motion";
import { cn } from "@/lib/cn";

/*
 * Three questions a visitor can pick between, each answered the way the product answers
 * it. Like the hero preview, every figure is what Numera reports for the bundled sample
 * retail dataset.
 *
 * It advances by itself until someone chooses a tab. The timer is the progress bar's own
 * CSS animation: it pauses with the bar (pointer over it, focus inside it, scrolled away)
 * and never starts under reduced motion, so there is no second clock to keep in step.
 */

const TABS = [
  { id: "drivers", icon: Split, tool: "Drivers", question: "Why did revenue grow last year?" },
  { id: "forecast", icon: TrendingUp, tool: "Forecast", question: "What should we expect over the next six months?" },
  { id: "verify", icon: BadgeCheck, tool: "Verification", question: "How do I know these figures are right?" },
] as const;

const at = (ms: number, extra?: Record<string, string>) => ({ "--at": `${ms}ms`, ...extra }) as React.CSSProperties;

export function AnswerExplorer({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const [active, setActive] = useState(0);
  const [auto, setAuto] = useState(true);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [inView, setInView] = useState(false);
  // Flips once, the first time the explorer is on screen, to replay the first answer's
  // entrance: it would otherwise have played out of sight at page load.
  const [seen, setSeen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    // The same trigger as the scroll reveal, so the answer starts as its card fades in.
    const observer = new IntersectionObserver(
      ([entry]) => {
        setInView(entry.isIntersecting);
        if (entry.isIntersecting) setSeen(true);
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.15 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const select = (index: number) => {
    setAuto(false);
    setActive(index);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const last = TABS.length - 1;
    let next: number;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = active === last ? 0 : active + 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = active === 0 ? last : active - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else return;
    event.preventDefault();
    select(next);
    tabRefs.current[next]?.focus();
  };

  const paused = hovered || focused || !inView;
  const tab = TABS[active];

  return (
    <div
      ref={rootRef}
      {...props}
      className={cn("grid gap-5 lg:grid-cols-[minmax(0,4fr)_minmax(0,8fr)]", className)}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(event) => !event.currentTarget.contains(event.relatedTarget) && setFocused(false)}
    >
      <div
        role="tablist"
        aria-label="Example questions"
        onKeyDown={onKeyDown}
        className="grid content-start gap-2.5 sm:grid-cols-3 lg:grid-cols-1"
      >
        {TABS.map(({ id, icon: Icon, tool, question }, index) => {
          const selected = index === active;
          return (
            <button
              key={id}
              ref={(el) => {
                tabRefs.current[index] = el;
              }}
              role="tab"
              id={`explorer-tab-${id}`}
              aria-selected={selected}
              aria-controls="explorer-panel"
              tabIndex={selected ? 0 : -1}
              onClick={() => select(index)}
              className={cn(
                "relative overflow-clip rounded-2xl border p-4 text-left transition duration-300",
                selected
                  ? "border-line-strong bg-panel shadow-pop"
                  : "border-line bg-panel/40 hover:border-line-strong hover:bg-panel",
              )}
            >
              <span className="flex items-center gap-2 text-[12px] font-medium text-ink-3">
                <Icon className={cn("size-3.5 transition-colors", selected && "text-accent")} />
                {tool}
              </span>
              <span
                className={cn(
                  "mt-2 block text-[14.5px] leading-snug font-medium transition-colors",
                  selected ? "text-ink" : "text-ink-2",
                )}
              >
                {question}
              </span>
              {selected && auto && (
                <span
                  key={`${active}-${seen}`}
                  aria-hidden="true"
                  className="explorer-progress"
                  style={{ animationPlayState: paused ? "paused" : "running" }}
                  onAnimationEnd={() => setActive((current) => (current + 1) % TABS.length)}
                />
              )}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id="explorer-panel"
        aria-labelledby={`explorer-tab-${tab.id}`}
        tabIndex={0}
        className="overflow-clip rounded-2xl border border-line-strong bg-panel shadow-pop"
      >
        {/* Keyed so the entrance sequence restarts with each answer. */}
        <div key={`${active}-${seen}`} className="panel-turn flex min-h-[25rem] flex-col gap-5 p-5 sm:p-7">
          <div className="seq flex justify-end" style={at(0)}>
            <p className="max-w-md rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-[13.5px] leading-snug text-white">
              {tab.question}
            </p>
          </div>
          {tab.id === "drivers" && <DriversAnswer />}
          {tab.id === "forecast" && <ForecastAnswer />}
          {tab.id === "verify" && <VerifyAnswer />}
        </div>
      </div>
    </div>
  );
}

function Method({ delay, children }: { delay: number; children: React.ReactNode }) {
  return (
    <p
      className="seq-fade mt-auto flex items-start gap-2 border-t border-line pt-4 text-[12.5px] leading-snug text-ink-3"
      style={at(delay)}
    >
      <BadgeCheck className="mt-px size-3.5 shrink-0 text-good" />
      <span>{children}</span>
    </p>
  );
}

/* ------------------------------------------------------------------------- drivers */

const DRIVERS = [
  { label: "Online", value: 254.9 },
  { label: "Other", value: 90.1 },
  { label: "Retail Store", value: -140.8 },
];
const LARGEST = Math.max(...DRIVERS.map((driver) => Math.abs(driver.value)));
const signed = (value: number) => `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(1)}K`;

function DriversAnswer() {
  return (
    <>
      <div className="seq" style={at(250)}>
        <p className="text-[12px] text-ink-3">Revenue, last 12 months vs. the 12 before</p>
        <p className="mt-1 flex items-baseline gap-2.5">
          <span className="text-3xl font-semibold tracking-tight text-ink tabular-nums">
            <CountUp to={204.2} decimals={1} prefix="+" suffix="K" delay={250} duration={900} />
          </span>
          <span className="text-[13px] font-medium text-good tabular-nums">+8.4%</span>
        </p>
      </div>
      <ul className="space-y-3">
        {DRIVERS.map(({ label, value }, index) => (
          <li
            key={label}
            className="seq grid grid-cols-[7rem_1fr_4.5rem] items-center gap-3 text-[13px]"
            style={at(450 + index * 140)}
          >
            <span className="truncate text-ink-2">{label}</span>
            {/* Gains grow right from the centre line, losses grow left. */}
            <span className="relative h-6 border-l border-line-strong [margin-left:50%]">
              <span
                className={cn(
                  "seq-grow-x absolute inset-y-0 rounded-sm",
                  value < 0 ? "right-full bg-bad/70" : "left-0 bg-good/70",
                )}
                style={at(600 + index * 140, {
                  width: `${(Math.abs(value) / LARGEST) * 100}%`,
                  "--origin": value < 0 ? "right center" : "left center",
                })}
              />
            </span>
            <span className={cn("text-right font-medium tabular-nums", value < 0 ? "text-bad" : "text-good")}>
              {signed(value)}
            </span>
          </li>
        ))}
      </ul>
      <p className="seq text-[13.5px] leading-relaxed text-ink-2" style={at(1100)}>
        <span className="font-semibold text-ink">Online drove the growth.</span> It added more than the whole
        increase, while Retail Store gave part of it back.
      </p>
      <Method delay={1400}>
        Computed directly, with no model tokens: +254.9K + 90.1K − 140.8K adds back to +204.2K exactly.
      </Method>
    </>
  );
}

/* ------------------------------------------------------------------------ forecast */

const { W, H, PAD, pastPath, areaPath, futurePath, bandPath, lastX, lastY } = FORECAST_CHART;

function ForecastAnswer() {
  return (
    <>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        <div className="seq rounded-xl border border-line p-3" style={at(250)}>
          <p className="text-[11px] text-ink-3">Chosen by backtest</p>
          <p className="mt-1 text-[15px] font-semibold text-ink">Linear trend</p>
        </div>
        <div className="seq rounded-xl border border-line p-3" style={at(350)}>
          <p className="text-[11px] text-ink-3">Error vs. naive baseline</p>
          <p className="mt-1 text-[15px] font-semibold text-good tabular-nums">
            <CountUp to={37} prefix="−" suffix="%" delay={350} duration={900} />
          </p>
        </div>
        <div className="seq col-span-2 rounded-xl border border-line p-3 sm:col-span-1" style={at(450)}>
          <p className="text-[11px] text-ink-3">Projected, next 6 months</p>
          <p className="mt-1 flex items-baseline gap-2">
            <span className="text-[15px] font-semibold text-ink tabular-nums">
              <CountUp to={1.6} decimals={1} suffix="M" delay={450} duration={900} />
            </span>
            <span className="text-[12px] font-medium text-good tabular-nums">+4.6%</span>
          </p>
        </div>
      </div>
      <div className="seq rounded-xl border border-line p-3" style={at(550)}>
        <div className="flex items-center justify-between gap-2 text-[10.5px] text-ink-3">
          <p className="text-[12px] font-medium text-ink">Monthly revenue</p>
          <span className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <span className="h-0.5 w-3 rounded bg-accent" /> Actual
            </span>
            <span className="flex items-center gap-1">
              <span className="h-0.5 w-3 rounded border-t border-dashed border-accent" /> Forecast · 80% interval
            </span>
          </span>
        </div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="mt-2 h-auto w-full"
          preserveAspectRatio="none"
          role="img"
          aria-label="Monthly revenue rising with a seasonal pattern, continued by a six-month forecast and its 80% interval"
        >
          {[0.25, 0.5, 0.75].map((f) => (
            <line key={f} x1={PAD} x2={W - PAD} y1={H * f} y2={H * f} className="stroke-line" strokeWidth="1" />
          ))}
          <g className="seq-wipe" style={at(700, { "--dur": "0.9s" })}>
            <path d={areaPath} className="fill-accent/10" />
            <path d={pastPath} className="fill-none stroke-accent" strokeWidth="2" strokeLinejoin="round" />
          </g>
          <g className="seq-wipe" style={at(1550, { "--dur": "0.6s" })}>
            <path d={bandPath} className="fill-accent/15" />
            <path
              d={futurePath}
              className="fill-none stroke-accent"
              strokeWidth="2"
              strokeDasharray="5 4"
              strokeLinejoin="round"
            />
          </g>
          <circle cx={lastX} cy={lastY} r="3.5" className="pulse-ring fill-accent" style={at(1850)} />
          <circle cx={lastX} cy={lastY} r="3.5" className="seq-pop fill-accent" style={at(1470)} />
        </svg>
      </div>
      <Method delay={2100}>
        Eight methods were backtested on periods they never saw. The winner is reported with the margin by which it
        beat doing nothing.
      </Method>
    </>
  );
}

/* -------------------------------------------------------------------- verification */

/** The write-up, split around the figures the audit checks. */
const WRITE_UP = [
  "Online contributed ",
  ["+254.9K"],
  ", more than the whole ",
  ["204.2K"],
  " increase, while Retail Store fell ",
  ["−140.8K"],
  ". A linear trend beat the naive baseline by ",
  ["37%"],
  " in backtesting and projects revenue ",
  ["4.6%"],
  " higher over the next six months.",
] as const;
const FIGURES = WRITE_UP.filter((part) => typeof part !== "string").length;
const CHECK_START = 500;
const CHECK_STEP = 320;

function VerifyAnswer() {
  let figure = 0;
  return (
    <>
      <div className="seq rounded-xl border border-line p-4" style={at(250)}>
        <p className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">Write-up</p>
        <p className="mt-2 text-[14px] leading-[1.9] text-ink-2">
          {WRITE_UP.map((part) => {
            if (typeof part === "string") return part;
            const delay = CHECK_START + figure++ * CHECK_STEP;
            return (
              // Each figure lights up as the audit reaches it, then earns its tick.
              <span
                key={part[0]}
                className="seq-mark inline-flex items-center gap-1 rounded-md bg-good-soft px-1.5 py-px font-medium whitespace-nowrap text-ink tabular-nums"
                style={at(delay)}
              >
                {part[0]}
                <Check className="seq-pop size-3 text-good" style={at(delay + 180)} aria-hidden="true" />
              </span>
            );
          })}
        </p>
      </div>
      <div
        className="seq flex items-center gap-3 rounded-xl border border-line bg-good-soft p-4"
        style={at(CHECK_START + FIGURES * CHECK_STEP + 150)}
      >
        <BadgeCheck className="size-5 shrink-0 text-good" />
        <p className="text-[13.5px] leading-snug text-ink">
          <span className="font-semibold">
            {FIGURES} of {FIGURES} figures matched
          </span>{" "}
          the output of the code that ran. Anything that can&rsquo;t be matched is flagged instead.
        </p>
      </div>
      <Method delay={CHECK_START + FIGURES * CHECK_STEP + 450}>
        A deterministic check, not a second opinion from the model. The code behind every answer can be read or
        exported as a notebook.
      </Method>
    </>
  );
}
