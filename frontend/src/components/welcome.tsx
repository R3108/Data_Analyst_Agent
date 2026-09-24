"use client";

import {
  ArrowRight,
  BadgeCheck,
  ChartLine,
  ChartSpline,
  CloudUpload,
  Eye,
  FileSpreadsheet,
  LayoutDashboard,
  LoaderCircle,
  Notebook,
  Radar,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Users,
  WandSparkles,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import type { DatasetSummary } from "@/lib/types";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm";

const FEATURES = [
  {
    icon: WandSparkles,
    title: "Automatic cleaning",
    body: "Types, currency strings, duplicates, placeholders and inconsistent labels fixed — with a full audit trail.",
  },
  {
    icon: ShieldCheck,
    title: "Sandboxed Python",
    body: "Every answer is backed by policy-checked pandas code running in an isolated process you can inspect.",
  },
  {
    icon: ChartLine,
    title: "Insights, not just numbers",
    body: "KPIs, interactive charts and recommendations written for decision-makers, not data scientists.",
  },
  {
    icon: Radar,
    title: "Signals before you ask",
    body: "Trends, anomalies, seasonality, concentration and mix shifts are detected the moment you upload.",
  },
  {
    icon: ChartSpline,
    title: "Forecasts with a track record",
    body: "Eight methods are refitted at several points in the past and scored on periods they never saw — so a projection arrives with the margin by which it beat doing nothing.",
  },
  {
    icon: Users,
    title: "Retention, honestly measured",
    body: "Follow each cohort forward. A cohort too young to have a six-month rate leaves that cell empty instead of counting it as churn.",
  },
  {
    icon: ShieldAlert,
    title: "Personal data never reaches the model",
    body: "Emails, card numbers and phone numbers are found the moment a file lands, and their values are withheld from every prompt before you decide anything.",
  },
  {
    icon: LayoutDashboard,
    title: "Boards & share links",
    body: "Pin results to live dashboards, share read-only links with stakeholders or export a polished PDF.",
  },
  {
    icon: BadgeCheck,
    title: "Every figure verified",
    body: "A deterministic audit checks each number in the write-up against the computed output, and flags anything it cannot match.",
  },
  {
    icon: Eye,
    title: "Monitors that keep watching",
    body: "Watch any KPI. Numera re-runs the same code when new data lands and tells you when it moves out of range.",
  },
  {
    icon: Notebook,
    title: "Yours to take away",
    body: "Export a runnable Jupyter notebook, a real PDF or an editable PowerPoint deck with native charts.",
  },
];

export function Welcome({
  uploading,
  onUpload,
  onSample,
  datasets,
  onPickDataset,
  maxUploadMb,
}: {
  uploading: "file" | "sample" | null;
  onUpload: (file: File) => void;
  onSample: () => void;
  datasets: DatasetSummary[];
  onPickDataset: (id: string) => void;
  maxUploadMb: number;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [stage, setStage] = useState("Uploading…");

  useEffect(() => {
    if (!uploading) return;
    setStage(uploading === "file" ? "Uploading…" : "Loading sample…");
    const timer = window.setTimeout(() => setStage("Cleaning and profiling your data…"), 900);
    return () => window.clearTimeout(timer);
  }, [uploading]);

  const pick = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onUpload(file);
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-5 py-10 sm:px-8 sm:py-16">
        <div className="animate-rise">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-panel px-2.5 py-1 text-xs font-medium text-ink-2 shadow-card">
            <Sparkles className="size-3.5 text-accent" />
            Agentic AI data analyst
          </span>
          <h1 className="mt-5 text-4xl font-semibold tracking-tight text-balance text-ink sm:text-5xl">
            Ask your data anything.
          </h1>
          <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-pretty text-ink-2 sm:text-lg">
            Upload a spreadsheet and ask questions in plain English. Numera cleans it, writes and runs Python in a
            secure sandbox, and turns the results into KPIs, charts and business insights.
          </p>
        </div>

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            if (!uploading) pick(event.dataTransfer.files);
          }}
          className={cn(
            "animate-rise mt-9 rounded-2xl border-2 border-dashed bg-panel p-8 text-center shadow-card transition-colors sm:p-10",
            dragging ? "border-accent bg-accent-soft" : "border-line-strong",
          )}
          style={{ animationDelay: "60ms" }}
        >
          {uploading ? (
            <div className="flex flex-col items-center py-4" aria-live="polite">
              <LoaderCircle className="size-8 animate-spin text-accent" />
              <p className="shimmer-text mt-4 text-[15px] font-medium">{stage}</p>
              <p className="mt-1 text-[13px] text-ink-3">Large files can take a few seconds.</p>
            </div>
          ) : (
            <>
              <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <CloudUpload className="size-6" />
              </div>
              <p className="mt-4 text-[15px] font-medium text-ink">Drop a file here, or browse</p>
              <p className="mt-1 text-[13px] text-ink-3">CSV, TSV or Excel (.xlsx) · up to {maxUploadMb} MB</p>
              <div className="mt-6 flex flex-col items-center justify-center gap-2.5 sm:flex-row">
                <Button variant="primary" size="lg" onClick={() => input.current?.click()}>
                  <CloudUpload className="size-4" />
                  Upload dataset
                </Button>
                <Button size="lg" onClick={onSample}>
                  <Sparkles className="size-4 text-accent" />
                  Try sample retail data
                </Button>
              </div>
              <input
                ref={input}
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(event) => {
                  pick(event.target.files);
                  event.target.value = "";
                }}
              />
            </>
          )}
        </div>

        {/* Returning users come back for their data, so it sits above the feature tour. */}
        {datasets.length > 0 && (
          <div className="animate-rise mt-10" style={{ animationDelay: "120ms" }}>
            <p className="mb-3 text-xs font-medium tracking-wide text-ink-3 uppercase">Continue with a dataset</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {datasets.slice(0, 6).map((dataset) => (
                <button
                  key={dataset.id}
                  onClick={() => onPickDataset(dataset.id)}
                  className="group flex items-center gap-3 rounded-xl border border-line bg-panel p-3 text-left shadow-card transition hover:border-line-strong"
                >
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-ink-2">
                    <FileSpreadsheet className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-medium text-ink">{dataset.name}</span>
                    <span className="block truncate text-[12px] text-ink-3">
                      {dataset.n_rows.toLocaleString()} rows · {relativeTime(dataset.created_at)}
                    </span>
                  </span>
                  <ArrowRight className="size-4 text-ink-3 transition group-hover:translate-x-0.5 group-hover:text-accent" />
                </button>
              ))}
            </div>
          </div>
        )}

        <p className="animate-rise mt-12 mb-3 text-xs font-medium tracking-wide text-ink-3 uppercase" style={{ animationDelay: "180ms" }}>
          What Numera does
        </p>
        <div className="animate-rise grid gap-4 sm:grid-cols-2 lg:grid-cols-3" style={{ animationDelay: "180ms" }}>
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div key={title} className="rounded-xl border border-line bg-panel/60 p-4">
              <Icon className="size-5 text-accent" />
              <p className="mt-3 text-sm font-semibold text-ink">{title}</p>
              <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{body}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
