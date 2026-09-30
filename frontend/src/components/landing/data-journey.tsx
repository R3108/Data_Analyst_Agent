"use client";

import { BookOpenCheck, EyeOff, FileSpreadsheet, ShieldAlert, WandSparkles } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/cn";

/*
 * Four rows of the bundled sample dataset, followed from the uploaded file to the prompt.
 * The rows, the cleaned values and the notes under the table are what Numera's own
 * cleaning and privacy steps produce for that file, not an illustration of them.
 */

const STAGES = [
  { id: "raw", label: "As uploaded", icon: FileSpreadsheet },
  { id: "clean", label: "After cleaning", icon: WandSparkles },
  { id: "model", label: "What the model sees", icon: EyeOff },
] as const;
type Stage = (typeof STAGES)[number]["id"];

/** A cell as `[uploaded, cleaned]`; a cleaned `null` is a value marked as missing. */
type Pair = readonly [string, string | null];
const same = (value: string): Pair => [value, value];

const ROWS: { id: string; email: string; region: Pair; segment: Pair; discount: Pair; revenue: Pair }[] = [
  {
    id: "ORD-102586",
    email: "harper.vasquez786@example.com",
    region: ["EAST", "East"],
    segment: same("Small Business"),
    discount: ["10%", "0.10"],
    revenue: same("914.11"),
  },
  {
    id: "ORD-100180",
    email: "indigo.bianchi27@example.com",
    region: ["north", "North"],
    segment: same("Small Business"),
    discount: ["10%", "0.10"],
    revenue: ["$2,454.28", "2454.28"],
  },
  {
    id: "ORD-102168",
    email: "frankie.haddad545@example.com",
    region: same("South"),
    segment: ["N/A", null],
    discount: ["5%", "0.05"],
    revenue: ["$594.38", "594.38"],
  },
  {
    id: "ORD-101843",
    email: "marlow.haddad551@example.com",
    region: ["west", "West"],
    segment: same("Enterprise"),
    discount: ["10%", "0.10"],
    revenue: same("2381.76"),
  },
];

const NOTES: Record<Stage, { icon: typeof BookOpenCheck; tone: string; summary: string; lines: string[] }> = {
  raw: {
    icon: FileSpreadsheet,
    tone: "text-ink-3",
    summary: "4,828 rows and 15 columns, exactly as they were in the file.",
    lines: [
      "Region is written three ways: East, EAST and east",
      "Discount and some Revenue values are text, not numbers",
      "Customer Segment uses “N/A” for unknown",
    ],
  },
  clean: {
    icon: BookOpenCheck,
    tone: "text-good",
    summary: "4,800 rows remain: 25 exact duplicates and 3 empty rows were removed.",
    lines: [
      "Region: merged 8 inconsistent capitalisation variants",
      "Discount: percentages converted to fractions",
      "Revenue: removed currency symbols and thousands separators",
      "Customer Segment: 40 placeholder values marked as missing, not guessed",
    ],
  },
  model: {
    icon: ShieldAlert,
    tone: "text-accent",
    summary: "One column holds directly identifying data: Customer Email.",
    lines: [
      "Its values are replaced before any prompt is built",
      "The model gets column summaries and a few sample rows like these, never the full table",
      "The analysis itself runs on your server, on the real values",
    ],
  },
};

const at = (ms: number) => ({ "--at": `${ms}ms` }) as React.CSSProperties;

export function DataJourney() {
  const [stage, setStage] = useState<Stage>("raw");
  const notes = NOTES[stage];

  return (
    <div className="overflow-clip rounded-2xl border border-line-strong bg-panel shadow-pop">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-muted/60 px-4 py-3">
        <p className="text-[12.5px] font-medium text-ink-2">retail_sales.csv</p>
        <div role="group" aria-label="Stage of the data" className="flex flex-wrap gap-1 rounded-xl bg-subtle p-1">
          {STAGES.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setStage(id)}
              aria-pressed={stage === id}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium transition duration-200",
                stage === id ? "bg-panel text-ink shadow-card" : "text-ink-2 hover:text-ink",
              )}
            >
              <Icon className={cn("size-3.5", stage === id && "text-accent")} />
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[44rem] border-collapse text-left font-mono text-[12.5px]">
          <thead>
            <tr className="border-b border-line text-[11.5px] text-ink-3">
              {["Order ID", "Customer Email", "Region", "Customer Segment", "Discount", "Revenue"].map((name, index) => (
                <th key={name} scope="col" className={cn("px-4 py-2.5 font-medium", index > 3 && "text-right")}>
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          {/* Keyed by stage so the cells that just changed flash again on every switch. */}
          <tbody key={stage} className="divide-y divide-line">
            {ROWS.map((row, index) => (
              <tr key={row.id}>
                <td className="px-4 py-2.5 text-ink-2">{row.id}</td>
                <td className="px-4 py-2.5">
                  {stage === "model" ? (
                    <Changed delay={index * 70} tone="accent">
                      (withheld)
                    </Changed>
                  ) : (
                    <span className="text-ink">{row.email}</span>
                  )}
                </td>
                <Cell pair={row.region} stage={stage} delay={index * 70} />
                <Cell pair={row.segment} stage={stage} delay={index * 70 + 60} />
                <Cell pair={row.discount} stage={stage} delay={index * 70 + 120} right />
                <Cell pair={row.revenue} stage={stage} delay={index * 70 + 180} right />
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div key={stage} aria-live="polite" className="border-t border-line p-4 sm:p-5">
        <p className="seq flex items-start gap-2 text-[13.5px] font-medium text-ink" style={at(0)}>
          <notes.icon className={cn("mt-0.5 size-4 shrink-0", notes.tone)} />
          {notes.summary}
        </p>
        <ul className="mt-3 grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
          {notes.lines.map((line, index) => (
            <li key={line} className="seq flex gap-2 text-[12.5px] leading-snug text-ink-2" style={at(80 + index * 70)}>
              <span aria-hidden="true" className="mt-[7px] size-1 shrink-0 rounded-full bg-ink-3" />
              {line}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** A value the current stage altered: tinted, and turned over into place as it changes. */
function Changed({ delay, tone, children }: { delay: number; tone: "good" | "accent"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        // An inline box cannot be transformed. The negative margin gives back the height
        // its padding would add, so rows keep the same height at every stage.
        "seq-flip -my-0.5 inline-block rounded-md px-1.5 py-0.5 text-ink",
        tone === "good" ? "bg-good-soft" : "bg-accent-soft",
      )}
      style={at(delay)}
    >
      {children}
    </span>
  );
}

function Cell({ pair, stage, delay, right = false }: { pair: Pair; stage: Stage; delay: number; right?: boolean }) {
  const [uploaded, cleaned] = pair;
  const value =
    stage === "raw" ? uploaded : cleaned ?? <span className="font-sans text-ink-3 italic">missing</span>;
  return (
    <td className={cn("px-4 py-2.5 whitespace-nowrap text-ink", right && "text-right tabular-nums")}>
      {/* Only the cleaning stage marks its own edits; by the prompt stage they are old news. */}
      {stage === "clean" && uploaded !== cleaned ? (
        <Changed delay={delay} tone="good">
          {value}
        </Changed>
      ) : (
        value
      )}
    </td>
  );
}
