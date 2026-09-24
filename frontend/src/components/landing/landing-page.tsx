import {
  BadgeCheck,
  Bell,
  BookOpenCheck,
  CalendarClock,
  ChevronDown,
  Cloud,
  CloudUpload,
  Code,
  Database,
  FileDown,
  FileText,
  FlaskConical,
  Gauge,
  Link2,
  LayoutDashboard,
  Mail,
  MessageSquareText,
  Notebook,
  Presentation,
  ServerCog,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Split,
  TrendingUp,
  UserCog,
  Users,
  WandSparkles,
  Webhook,
} from "lucide-react";
import Link from "next/link";

import { LogoMark, Wordmark } from "@/components/brand";
import { LandingNav, PrimaryCta } from "@/components/landing/landing-nav";
import { BarField3D } from "@/components/landing/bar-field-3d";
import { CountUp, RevealObserver, Tilt } from "@/components/landing/motion";
import { ProductPreview } from "@/components/landing/product-preview";
import { SECTIONS } from "@/components/landing/sections";
import { cn } from "@/lib/cn";

/**
 * Props that make an element fade up when it scrolls into view, `delay` ms after.
 * The "3d" variant tips it up out of the page instead, for cards.
 */
const reveal = (delay = 0, variant: "" | "3d" = "") => ({
  "data-reveal-item": variant,
  style: { "--delay": `${delay}ms` } as React.CSSProperties,
});

/** Stagger offset for a child animation inside a revealed element. */
const stagger = (ms: number) => ({ "--d": `${ms}ms` }) as React.CSSProperties;

/** The shared hover response of every card: a small lift and a firmer edge. */
const LIFT = "transition duration-300 hover:-translate-y-1 hover:border-line-strong hover:shadow-pop";

/*
 * Everything claimed on this page is something the product does today, and every figure
 * shown is a real output for the bundled sample dataset. No customer logos, testimonials
 * or usage numbers: there are none to cite, and inventing them is the fastest way for a
 * data product to lose the trust it is selling.
 */

export function LandingPage() {
  return (
    <div className="min-h-dvh bg-canvas text-ink">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[60] focus:rounded-lg focus:bg-panel focus:px-3 focus:py-2 focus:shadow-pop"
      >
        Skip to content
      </a>
      <RevealObserver />
      <LandingNav />
      <main id="main">
        <Hero />
        <ProofStrip />
        <HowItWorks />
        <Capabilities />
        <Security />
        <Deliverables />
        <Faq />
        <ClosingCta />
      </main>
      <Footer />
    </div>
  );
}

/* ------------------------------------------------------------------ building blocks */

function Container({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("mx-auto w-full max-w-6xl px-5 sm:px-8", className)}>{children}</div>;
}

function SectionHeading({
  eyebrow,
  title,
  body,
  center = false,
}: {
  eyebrow: string;
  title: string;
  body?: string;
  center?: boolean;
}) {
  return (
    <div {...reveal()} className={cn("max-w-2xl", center && "mx-auto text-center")}>
      <p className="text-[13px] font-semibold tracking-wide text-accent uppercase">{eyebrow}</p>
      <h2 className="mt-3 text-3xl font-semibold tracking-tight text-balance text-ink sm:text-4xl">{title}</h2>
      {body && <p className="mt-4 text-[16px] leading-relaxed text-pretty text-ink-2 sm:text-[17px]">{body}</p>}
    </div>
  );
}

/**
 * `cn` is clsx, not tailwind-merge, so a size passed through `className` would sit beside
 * the default and lose or win by CSS order. The size is a prop instead.
 */
function IconTile({ children, small = false }: { children: React.ReactNode; small?: boolean }) {
  return (
    <span
      className={cn(
        // `tilt-pop`: inside a tilting card, the icon floats forward off the surface.
        "tilt-pop flex shrink-0 items-center justify-center bg-accent-soft text-accent",
        small ? "size-9 rounded-lg" : "size-10 rounded-xl",
      )}
    >
      {children}
    </span>
  );
}

/* ---------------------------------------------------------------------------- hero */

function Hero() {
  return (
    // `overflow-clip`, not `overflow-hidden`: a hidden overflow makes the section a scroll
    // container, and the hero window's scroll-driven unfold would track it instead of the page.
    <section className="relative overflow-clip">
      {/* Faint grid and glow, faded out toward the edges. */}
      <div
        aria-hidden="true"
        className="landing-grid pointer-events-none absolute inset-0 [mask-image:radial-gradient(ellipse_70%_60%_at_50%_0%,black,transparent)]"
      />
      {/* The glow drifts slowly, like light moving across the page. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 left-1/2 h-[520px] w-[900px] -translate-x-1/2"
      >
        <div className="animate-drift size-full rounded-full bg-accent/15 blur-3xl" />
      </div>
      <Container className="relative pt-16 pb-20 sm:pt-24 sm:pb-28">
        <div className="mx-auto max-w-3xl text-center">
          <span className="animate-rise inline-flex items-center gap-1.5 rounded-full border border-line bg-panel px-3 py-1 text-[12.5px] font-medium text-ink-2 shadow-card">
            <Sparkles className="size-3.5 text-accent" />
            The AI data analyst that shows its work
          </span>
          <h1
            className="animate-rise mt-6 text-[2.6rem] leading-[1.08] font-semibold tracking-tight text-balance text-ink sm:text-6xl"
            style={{ animationDelay: "80ms" }}
          >
            Ask your data anything.{" "}
            <span className="bg-linear-to-r from-accent to-accent-ink bg-clip-text text-transparent">
              Get answers you can check.
            </span>
          </h1>
          <p
            className="animate-rise mx-auto mt-6 max-w-2xl text-[17px] leading-relaxed text-pretty text-ink-2 sm:text-lg"
            style={{ animationDelay: "160ms" }}
          >
            Upload a spreadsheet or connect a database, then ask in plain English. Numera cleans the data, writes and
            runs Python in a secure sandbox, and verifies every figure before it reaches your report.
          </p>
          <div className="animate-rise mt-9 flex justify-center" style={{ animationDelay: "240ms" }}>
            <PrimaryCta />
          </div>
          <p className="animate-rise mt-4 text-[13px] text-ink-3" style={{ animationDelay: "300ms" }}>
            CSV, Excel and SQL databases · Runs on your own infrastructure
          </p>
        </div>

        <div className="animate-rise relative mx-auto mt-16 max-w-5xl sm:mt-20" style={{ animationDelay: "280ms" }}>
          {/* A soft accent halo that lifts the preview off the page. */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -inset-x-6 -inset-y-4 -z-10 rounded-[28px] bg-linear-to-b from-accent/20 to-transparent blur-2xl"
          />
          {/* Three layers, three jobs: perspective, the scroll-driven unfold, the pointer tilt. */}
          <div className="[perspective:2000px]">
            <div className="hero-unfold">
              <Tilt max={3} className="rounded-2xl">
                <ProductPreview />
              </Tilt>
            </div>
          </div>
          <p className="mt-3 text-center text-[12px] text-ink-3">
            Shown with the sample retail dataset included with Numera.
          </p>
        </div>
      </Container>
    </section>
  );
}

/* --------------------------------------------------------------------- proof strip */

const PROOF = [
  { icon: BadgeCheck, title: "Every figure verified", body: "Checked against the computed output" },
  { icon: ShieldCheck, title: "Sandboxed Python", body: "Policy-checked, isolated, time-limited" },
  { icon: ShieldAlert, title: "Personal data withheld", body: "Never included in a prompt" },
  { icon: ServerCog, title: "Self-hosted", body: "Your data stays on your servers" },
];

function ProofStrip() {
  return (
    <section aria-label="Why teams trust Numera" className="border-y border-line bg-panel">
      <Container className="grid grid-cols-2 gap-x-6 gap-y-8 py-10 lg:grid-cols-4">
        {PROOF.map(({ icon: Icon, title, body }, index) => (
          <div key={title} {...reveal(index * 80)} className="flex items-start gap-3">
            <Icon className="mt-0.5 size-5 shrink-0 text-accent" />
            <div>
              <p className="text-[14.5px] font-semibold text-ink">{title}</p>
              <p className="mt-0.5 text-[13px] leading-snug text-ink-2">{body}</p>
            </div>
          </div>
        ))}
      </Container>
    </section>
  );
}

/* --------------------------------------------------------------------- how it works */

const STEPS = [
  {
    icon: CloudUpload,
    title: "Bring your data",
    body: "Drop in a CSV or Excel file, or connect PostgreSQL, MySQL, SQL Server, DuckDB or SQLite. Types, currency strings, duplicates and messy labels are fixed automatically — with an audit trail of every change.",
  },
  {
    icon: MessageSquareText,
    title: "Ask in plain English",
    body: "Numera plans the analysis, writes pandas code, runs it in an isolated sandbox and repairs it if it fails. You get KPIs, interactive charts and a write-up for decision-makers — plus the exact code behind it.",
  },
  {
    icon: LayoutDashboard,
    title: "Share and keep watching",
    body: "Pin results to live boards, share read-only links, export to PDF, PowerPoint or a Jupyter notebook — and set monitors that tell you when a number moves.",
  },
];

function HowItWorks() {
  return (
    <section id="how-it-works" className="scroll-mt-16 py-24 sm:py-32">
      <Container>
        <SectionHeading
          center
          eyebrow="How it works"
          title="From raw file to board-ready answer in minutes"
          body="No SQL, no notebooks to maintain, no waiting in the analytics queue."
        />
        <ol className="mt-16 grid gap-6 md:grid-cols-3">
          {STEPS.map(({ icon: Icon, title, body }, index) => (
            <li key={title} {...reveal(index * 120, "3d")}>
              <Tilt className={cn("h-full rounded-2xl border border-line bg-panel p-6 shadow-card", LIFT)}>
                <div className="flex items-center justify-between">
                  <IconTile>
                    <Icon className="size-5" />
                  </IconTile>
                  <span className="tilt-pop text-[13px] font-semibold text-ink-3 tabular-nums">0{index + 1}</span>
                </div>
                <h3 className="mt-5 text-lg font-semibold tracking-tight text-ink">{title}</h3>
                <p className="mt-2 text-[14.5px] leading-relaxed text-ink-2">{body}</p>
              </Tilt>
            </li>
          ))}
        </ol>
      </Container>
    </section>
  );
}

/* --------------------------------------------------------------------- capabilities */

function Card({
  className,
  delay = 0,
  icon,
  title,
  body,
  children,
}: {
  className?: string;
  delay?: number;
  icon: React.ReactNode;
  title: string;
  body: string;
  children?: React.ReactNode;
}) {
  return (
    <div {...reveal(delay, "3d")} className={className}>
      <Tilt
        max={5}
        className={cn("flex h-full flex-col rounded-2xl border border-line bg-panel p-6 shadow-card", LIFT)}
      >
        <div className="flex items-center gap-3">
          <IconTile small>{icon}</IconTile>
          <h3 className="text-[16px] font-semibold tracking-tight text-ink">{title}</h3>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed text-ink-2">{body}</p>
        {children && (
          <div className="tilt-pop mt-6 flex-1" aria-hidden="true">
            {children}
          </div>
        )}
      </Tilt>
    </div>
  );
}

const CLEANING = [
  ["Order Date", "Parsed text into dates"],
  ["Region", "Trimmed stray whitespace in 96 values"],
  ["Rows", "Removed 25 duplicates and 3 empty rows"],
  ["Headers", "Tidied a malformed column name"],
];

const WATERFALL = [
  { label: "Prior year", value: "2.4M", start: 0, size: 78, tone: "base" },
  { label: "Online", value: "+254.9K", start: 78, size: 16, tone: "up" },
  { label: "Retail Store", value: "−140.8K", start: 85, size: 9, tone: "down" },
  { label: "Other", value: "+90.1K", start: 85, size: 6, tone: "up" },
  { label: "Last year", value: "2.6M", start: 0, size: 91, tone: "base" },
] as const;

const COHORT = [100, 42, 35, 31, 28, 26];

function Capabilities() {
  return (
    <section id="capabilities" className="scroll-mt-16 border-t border-line bg-panel/50 py-24 sm:py-32">
      <Container>
        <SectionHeading
          eyebrow="Capabilities"
          title="A full analytics team's toolkit, without the backlog"
          body="Ask the agent anything, or reach for purpose-built tools that compute answers deterministically — no model tokens, no guesswork."
        />

        <div className="mt-14 grid gap-5 lg:grid-cols-3">
          <Card
            className="lg:col-span-2"
            icon={<WandSparkles className="size-4" />}
            title="Clean data before the first question"
            body="Every upload is profiled and repaired the moment it lands, and each fix is written down, so you always know what the analysis ran on."
          >
            <div className="rounded-xl border border-line bg-canvas/60 p-4">
              <div className="flex items-center justify-between text-[12px]">
                <span className="font-medium text-ink">Data quality</span>
                <span className="font-semibold text-ink tabular-nums">99/100</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-subtle">
                <div className="reveal-grow-x h-full w-[99%] rounded-full bg-good" />
              </div>
              <ul className="mt-4 space-y-2">
                {CLEANING.map(([column, fix], index) => (
                  <li
                    key={column}
                    className="reveal-rise flex items-start gap-2 text-[12.5px] text-ink-2"
                    style={stagger(300 + index * 110)}
                  >
                    <BookOpenCheck className="mt-px size-3.5 shrink-0 text-good" />
                    <span>
                      <span className="font-medium text-ink">{column}:</span> {fix}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </Card>

          <Card
            delay={120}
            icon={<Split className="size-4" />}
            title="Know why a number moved"
            body="Split any change into segment contributions and volume, mix and rate effects that add back to the total exactly."
          >
            <div className="space-y-2">
              {WATERFALL.map((bar, index) => (
                <div key={bar.label} className="grid grid-cols-[84px_1fr] items-center gap-2 text-[11.5px]">
                  <span className="truncate text-ink-3">{bar.label}</span>
                  <div className="relative h-5">
                    {/* Each bar grows from where the previous one ended, like a waterfall filling in. */}
                    <div
                      className={cn(
                        "reveal-grow-x absolute inset-y-0 rounded",
                        bar.tone === "base" ? "bg-accent/70" : bar.tone === "up" ? "bg-good/70" : "bg-bad/70",
                      )}
                      style={{
                        left: `${bar.start}%`,
                        width: `${bar.size}%`,
                        // A decrease eats back from the running total, so it grows leftward.
                        ...(bar.tone === "down" ? { transformOrigin: "right center" } : {}),
                        ...stagger(index * 140),
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card
            icon={<TrendingUp className="size-4" />}
            title="Forecasts with a track record"
            body="Eight methods are backtested on periods they never saw. The winner arrives with the margin by which it beat doing nothing."
          >
            <div className="rounded-xl border border-line bg-canvas/60 p-4">
              <p className="text-[12px] text-ink-3">Chosen by backtest</p>
              <p className="mt-1 text-[15px] font-semibold text-ink">Linear trend</p>
              <p className="mt-3 flex items-baseline gap-2">
                <span className="text-2xl font-semibold tracking-tight text-good tabular-nums">
                  <CountUp to={37} prefix="−" suffix="%" delay={350} />
                </span>
                <span className="text-[12px] text-ink-2">error vs. the naive baseline</span>
              </p>
            </div>
          </Card>

          <Card
            delay={120}
            icon={<Users className="size-4" />}
            title="Retention, honestly measured"
            body="Follow each cohort forward. A cohort too young for a six-month rate leaves the cell empty instead of counting it as churn."
          >
            {/* Cells fill in along the diagonal, the way cohorts accumulate history. */}
            <div className="grid grid-cols-6 gap-1">
              {Array.from({ length: 5 }).flatMap((_, row) =>
                COHORT.map((value, col) => (
                  <span
                    key={`${row}-${col}`}
                    className={cn("reveal-pop aspect-square rounded-[3px]", col >= 6 - row ? "bg-muted" : "bg-accent")}
                    style={{
                      ...stagger((row + col) * 55),
                      ...(col >= 6 - row ? {} : { opacity: 0.15 + (value / 100) * 0.85 - row * 0.02 }),
                    }}
                  />
                )),
              )}
            </div>
          </Card>

          <Card
            delay={240}
            icon={<Bell className="size-4" />}
            title="Monitors that keep watching"
            body="Watch any KPI. When new data lands, Numera re-runs the same code and alerts you only when the number crosses its line."
          >
            <div className="space-y-2">
              {[
                { name: "Weekly revenue", status: "Within range", tone: "good" },
                { name: "Return rate", status: "Breached", tone: "bad" },
              ].map((monitor, index) => (
                <div
                  key={monitor.name}
                  className="reveal-rise flex items-center justify-between rounded-lg border border-line px-3 py-2 text-[12.5px]"
                  style={stagger(200 + index * 150)}
                >
                  <span className="text-ink">{monitor.name}</span>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium",
                      monitor.tone === "good" ? "bg-good-soft text-good" : "bg-bad-soft text-bad",
                    )}
                  >
                    {monitor.tone === "bad" && (
                      <span className="relative flex size-1.5">
                        <span className="absolute inset-0 animate-ping rounded-full bg-bad opacity-60 motion-reduce:hidden" />
                        <span className="relative size-1.5 rounded-full bg-bad" />
                      </span>
                    )}
                    {monitor.status}
                  </span>
                </div>
              ))}
              <div className="flex items-center gap-3 pt-1 text-[11.5px] text-ink-3">
                <span className="flex items-center gap-1">
                  <MessageSquareText className="size-3.5" /> Slack
                </span>
                <span className="flex items-center gap-1">
                  <Webhook className="size-3.5" /> Webhook
                </span>
                <span className="flex items-center gap-1">
                  <Mail className="size-3.5" /> Email
                </span>
              </div>
            </div>
          </Card>

          <Card
            icon={<Database className="size-4" />}
            title="Connect your warehouse"
            body="Point Numera at a read-only query and refresh it on a schedule. Each sync becomes a new dataset version, with a diff of what changed."
          >
            <div className="flex flex-wrap gap-1.5">
              {["PostgreSQL", "MySQL", "SQL Server", "DuckDB", "SQLite"].map((dialect, index) => (
                <span
                  key={dialect}
                  className="reveal-pop rounded-md border border-line px-2 py-1 text-[12px] text-ink-2"
                  style={stagger(200 + index * 80)}
                >
                  {dialect}
                </span>
              ))}
            </div>
          </Card>

          <div className="grid gap-5 sm:grid-cols-3 lg:col-span-2">
            {[
              {
                icon: <FlaskConical className="size-4" />,
                title: "Significance",
                body: "Test whether a difference is real or just noise.",
              },
              {
                icon: <SlidersHorizontal className="size-4" />,
                title: "Scenarios",
                body: "Model what would move a number, or solve for a target.",
              },
              {
                icon: <CalendarClock className="size-4" />,
                title: "Briefings",
                body: "Save a question and have it answer itself on a schedule.",
              },
            ].map((item, index) => (
              <div key={item.title} {...reveal(120 + index * 100, "3d")}>
                <Tilt max={7} className={cn("h-full rounded-2xl border border-line bg-panel p-5 shadow-card", LIFT)}>
                  <IconTile small>{item.icon}</IconTile>
                  <h3 className="mt-4 text-[15px] font-semibold tracking-tight text-ink">{item.title}</h3>
                  <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{item.body}</p>
                </Tilt>
              </div>
            ))}
          </div>
        </div>
      </Container>
    </section>
  );
}

/* ------------------------------------------------------------------------ security */

const SAFEGUARDS = [
  {
    icon: Code,
    title: "Code runs in a sandbox",
    body: "Model-written Python is policy-checked, then executed in a separate process with time and memory limits. You can read every line it ran.",
  },
  {
    icon: ShieldAlert,
    title: "Personal data stays out of prompts",
    body: "Emails, phone and card numbers are detected at upload and replaced before any prompt is built. Mask, hash or drop them from the table for good.",
  },
  {
    icon: BadgeCheck,
    title: "Figures are audited",
    body: "A deterministic check matches every number in the write-up against the computed output, and flags anything it cannot match.",
  },
  {
    icon: UserCog,
    title: "Private workspaces and roles",
    body: "Each account's data is isolated. Admins manage roles and suspensions, review the audit log, and anyone can sign out every device at once.",
  },
  {
    icon: Gauge,
    title: "Predictable AI spend",
    body: "Set a monthly AI budget. Drivers, forecasts, significance tests and monitors are computed directly and use no model tokens at all.",
  },
  {
    icon: Cloud,
    title: "Your infrastructure",
    body: "Deploy with Docker Compose on your own servers. Tables are processed where they live; the model sees a schema summary, not your database.",
  },
];

function Security() {
  return (
    <section id="security" className="scroll-mt-16 py-24 sm:py-32">
      <Container className="grid gap-14 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-20">
        <div className="lg:sticky lg:top-28 lg:self-start">
          <SectionHeading
            eyebrow="Security & trust"
            title="Built for data you can't afford to get wrong"
            body="An analyst you can't audit is a liability. Numera is designed so that every answer can be traced, reproduced and kept inside your walls."
          />
          <div {...reveal(150)} className="mt-8 flex items-center gap-3 rounded-xl border border-line bg-panel p-4 shadow-card">
            <ShieldCheck className="size-5 shrink-0 text-good" />
            <p className="text-[13.5px] leading-snug text-ink-2">
              Download the cleaned table and the notebook to reproduce any figure yourself.
            </p>
          </div>
        </div>
        <div className="grid gap-x-8 gap-y-10 sm:grid-cols-2">
          {SAFEGUARDS.map(({ icon: Icon, title, body }, index) => (
            <div key={title} {...reveal((index % 2) * 120)}>
              <IconTile>
                <Icon className="size-5" />
              </IconTile>
              <h3 className="mt-4 text-[16px] font-semibold tracking-tight text-ink">{title}</h3>
              <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{body}</p>
            </div>
          ))}
        </div>
      </Container>
    </section>
  );
}

/* --------------------------------------------------------------------- deliverables */

const OUTPUTS = [
  { icon: FileDown, label: "PDF report" },
  { icon: Presentation, label: "PowerPoint, native charts" },
  { icon: Notebook, label: "Runnable Jupyter notebook" },
  { icon: FileText, label: "Markdown" },
  { icon: LayoutDashboard, label: "Live boards" },
  { icon: Link2, label: "Read-only share links" },
];

function Deliverables() {
  return (
    <section className="border-y border-line bg-panel py-20">
      <Container className="flex flex-col items-start gap-10 lg:flex-row lg:items-center lg:justify-between">
        <div {...reveal()} className="max-w-md">
          <h2 className="text-2xl font-semibold tracking-tight text-balance text-ink sm:text-3xl">
            Take the answer wherever the decision is made
          </h2>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-2">
            Stakeholders get a polished deliverable. Analysts get the code to reproduce it.
          </p>
        </div>
        <ul className="grid w-full grid-cols-2 gap-3 sm:grid-cols-3 lg:max-w-xl">
          {OUTPUTS.map(({ icon: Icon, label }, index) => (
            <li
              key={label}
              {...reveal(index * 70)}
              className={cn(
                "flex items-center gap-2.5 rounded-xl border border-line bg-canvas/60 px-3.5 py-3 text-[13.5px] text-ink",
                LIFT,
              )}
            >
              <Icon className="size-4 shrink-0 text-accent" />
              {label}
            </li>
          ))}
        </ul>
      </Container>
    </section>
  );
}

/* ----------------------------------------------------------------------------- FAQ */

const FAQ = [
  {
    q: "What does the AI model actually see?",
    a: "A compact summary of the dataset — column names and types, summary statistics, the most common values and a few sample rows — plus the results of the code it runs. The full table is processed on your server. Values in columns detected as personal data are replaced with “(withheld)” before any prompt is built.",
  },
  {
    q: "How do I know an answer is right?",
    a: "Every answer comes with the exact code that produced it, which you can inspect or export as a notebook. A deterministic audit then checks each figure in the write-up against the computed output and flags anything it cannot match.",
  },
  {
    q: "Which data sources are supported?",
    a: "CSV, TSV and Excel files up to 50 MB by default, and read-only queries against PostgreSQL, MySQL, SQL Server, DuckDB and SQLite, refreshed on demand or on a schedule.",
  },
  {
    q: "What does it cost to run?",
    a: "Questions to the agent use your own OpenAI API key, and a monthly budget cap stops spending at a limit you choose. The deterministic tools — drivers, forecasts, significance, retention and monitors — use no model tokens.",
  },
  {
    q: "Can I host it myself?",
    a: "Yes. Numera ships with Docker Compose and runs on your own infrastructure, with private workspaces per account, admin roles, Google sign-in and an audit log.",
  },
  {
    q: "Do my stakeholders need an account?",
    a: "No. Share a read-only link to an analysis or board, or send a PDF or PowerPoint export. You can disable a share link at any time.",
  },
];

function Faq() {
  return (
    <section id="faq" className="scroll-mt-16 py-24 sm:py-32">
      <Container className="grid gap-12 lg:grid-cols-[minmax(0,4fr)_minmax(0,8fr)]">
        <SectionHeading eyebrow="FAQ" title="Questions, answered" />
        <div {...reveal(100)} className="divide-y divide-line border-y border-line">
          {FAQ.map(({ q, a }) => (
            <details key={q} className="faq-item group">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-5 text-left text-[16px] font-medium text-ink transition-colors hover:text-accent [&::-webkit-details-marker]:hidden">
                {q}
                <ChevronDown className="size-5 shrink-0 text-ink-3 transition duration-300 group-open:rotate-180" />
              </summary>
              <p className="-mt-1 pb-5 text-[15px] leading-relaxed text-ink-2">{a}</p>
            </details>
          ))}
        </div>
      </Container>
    </section>
  );
}

/* ---------------------------------------------------------------------- closing CTA */

function ClosingCta() {
  return (
    <section className="pb-24 sm:pb-32">
      <Container>
        <div
          {...reveal()}
          // `overflow-clip`: a hidden overflow can still be scrolled by focus or find-in-page,
          // which would slide the band's contents out from under its rounded frame.
          className="relative grid items-center gap-6 overflow-clip rounded-3xl bg-accent px-6 pt-14 pb-6 text-center sm:px-12 sm:pt-20 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:py-16 lg:text-left"
        >
          <div aria-hidden="true" className="pointer-events-none absolute -top-24 -right-24 size-80">
            <div className="animate-drift size-full rounded-full bg-white/15 blur-2xl" />
          </div>
          <div aria-hidden="true" className="pointer-events-none absolute -bottom-32 -left-16 size-80">
            <div
              className="animate-drift size-full rounded-full bg-black/15 blur-2xl"
              style={{ animationDelay: "-8s" }}
            />
          </div>
          <div aria-hidden="true" className="landing-grid-light pointer-events-none absolute inset-0 opacity-40" />
          <div className="relative">
            <h2 className="mx-auto max-w-2xl text-3xl font-semibold tracking-tight text-balance text-white sm:text-4xl lg:mx-0">
              Your next answer is one question away
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-[16px] leading-relaxed text-white/85 lg:mx-0">
              Try it on your own file, or start with the sample retail dataset — no setup required.
            </p>
            <div className="mt-9 flex justify-center lg:justify-start">
              <PrimaryCta inverted />
            </div>
          </div>
          {/* Leans toward the pointer too, so the 3D chart can be looked around. */}
          {/* The bars rise up from a floor centred in this box, so the drawing is nudged
              down to sit visually centred rather than top-heavy. */}
          <Tilt
            max={10}
            glare={false}
            className="relative mx-auto flex h-60 w-full max-w-sm items-center justify-center sm:h-80"
          >
            <BarField3D className="translate-y-8 scale-[0.82] sm:translate-y-6 sm:scale-100" />
          </Tilt>
        </div>
      </Container>
    </section>
  );
}

/* --------------------------------------------------------------------------- footer */

function Footer() {
  return (
    <footer className="border-t border-line">
      <Container className="flex flex-col gap-10 py-12 md:flex-row md:justify-between">
        <div className="max-w-xs">
          <Wordmark />
          <p className="mt-4 text-[13.5px] leading-relaxed text-ink-2">
            The AI data analyst that cleans your data, runs real code and shows its work.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-10 text-[13.5px] sm:gap-16">
          <div>
            <p className="font-semibold text-ink">Product</p>
            <ul className="mt-3 space-y-2">
              {SECTIONS.map((section) => (
                <li key={section.href}>
                  <a href={section.href} className="text-ink-2 transition hover:text-ink">
                    {section.label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="font-semibold text-ink">Account</p>
            <ul className="mt-3 space-y-2">
              <li>
                <Link href="/login" className="text-ink-2 transition hover:text-ink">
                  Sign in
                </Link>
              </li>
              <li>
                <Link href="/register" className="text-ink-2 transition hover:text-ink">
                  Create an account
                </Link>
              </li>
              <li>
                <Link href="/forgot-password" className="text-ink-2 transition hover:text-ink">
                  Reset password
                </Link>
              </li>
            </ul>
          </div>
        </div>
      </Container>
      <Container className="flex items-center justify-between gap-4 border-t border-line py-6 text-[12.5px] text-ink-3">
        <span>© {new Date().getFullYear()} Numera</span>
        <LogoMark className="size-5 opacity-60" />
      </Container>
    </footer>
  );
}
