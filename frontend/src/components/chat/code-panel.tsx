"use client";

import { ChevronDown, CodeXml } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge, CopyButton } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import type { Execution } from "@/lib/types";

const TOKEN_RE =
  /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|[fr]?"(?:\\.|[^"\\\n])*"|[fr]?'(?:\\.|[^'\\\n])*')|\b(\d+(?:\.\d+)?)\b|\b(def|return|if|elif|else|for|in|while|import|from|as|with|lambda|not|and|or|is|None|True|False|try|except|finally|class|pass|break|continue|yield)\b|\b([A-Za-z_]\w*)(?=\()/g;

/** Tiny dependency-free Python highlighter — good enough for generated analysis code. */
function highlight(code: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  for (const match of code.matchAll(TOKEN_RE)) {
    const index = match.index ?? 0;
    if (index > last) nodes.push(code.slice(last, index));
    const [text, comment, string, number, keyword, call] = match;
    const cls = comment
      ? "text-ink-3 italic"
      : string
        ? "text-[#1b7f52] dark:text-[#5fc99a]"
        : number
          ? "text-[#a65400] dark:text-[#f0a35a]"
          : keyword
            ? "text-[#7a3fb8] dark:text-[#b69cf5]"
            : call
              ? "text-accent-ink"
              : undefined;
    nodes.push(
      <span key={index} className={cls}>
        {text}
      </span>,
    );
    last = index + text.length;
  }
  if (last < code.length) nodes.push(code.slice(last));
  return nodes;
}

type Tab = "code" | "output" | "log";

export function CodePanel({
  code,
  execution,
  attemptLog,
}: {
  code: string;
  execution?: Execution;
  attemptLog?: { attempt: number; ok: boolean; error: string | null }[];
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("code");
  const source = code.trimEnd();
  const highlighted = useMemo(() => highlight(source), [source]);
  const lineCount = source.split("\n").length;
  const failures = (attemptLog ?? []).filter((a) => !a.ok);

  const tabs: { id: Tab; label: string; show: boolean }[] = [
    { id: "code", label: "Code", show: true },
    { id: "output", label: "Printed output", show: Boolean(execution?.stdout?.trim()) },
    { id: "log", label: `Self-corrections (${failures.length})`, show: failures.length > 0 },
  ];

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-panel">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px]"
        aria-expanded={open}
      >
        <CodeXml className="size-4 text-ink-3" />
        <span className="font-medium text-ink">Analysis code</span>
        <span className="hidden text-ink-3 sm:inline">Python · {lineCount} lines</span>
        {execution && (
          <Badge tone={execution.ok ? "good" : "bad"} className="ml-1">
            {execution.ok ? `ran in ${(execution.duration_ms / 1000).toFixed(1)}s` : (execution.error_type ?? "failed")}
          </Badge>
        )}
        <ChevronDown className={cn("ml-auto size-4 text-ink-3 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="border-t border-line">
          <div className="flex items-center gap-1 border-b border-line px-2 py-1.5">
            {tabs
              .filter((t) => t.show)
              .map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={cn(
                    "rounded-md px-2 py-1 text-xs font-medium transition",
                    tab === t.id ? "bg-muted text-ink" : "text-ink-3 hover:text-ink",
                  )}
                >
                  {t.label}
                </button>
              ))}
            <div className="ml-auto">
              <CopyButton text={tab === "output" ? (execution?.stdout ?? "") : source} />
            </div>
          </div>

          {tab === "code" && (
            <div className="flex max-h-[440px] overflow-auto bg-muted/40 font-mono text-[12.5px] leading-[1.65]">
              <pre aria-hidden="true" className="sticky left-0 bg-muted/80 py-3 pr-3 pl-3.5 text-right text-ink-3 select-none">
                {Array.from({ length: lineCount }, (_, i) => i + 1).join("\n")}
              </pre>
              <pre className="flex-1 py-3 pr-4 pl-3">
                <code>{highlighted}</code>
              </pre>
            </div>
          )}

          {tab === "output" && (
            <pre className="max-h-[360px] overflow-auto bg-muted/40 px-4 py-3 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink-2">
              {execution?.stdout}
            </pre>
          )}

          {tab === "log" && (
            <div className="max-h-[360px] space-y-3 overflow-auto px-4 py-3">
              {failures.map((f) => (
                <div key={f.attempt}>
                  <p className="text-xs font-medium text-ink-2">Attempt {f.attempt}</p>
                  <pre className="mt-1 overflow-x-auto rounded-lg bg-bad-soft px-3 py-2 font-mono text-[12px] whitespace-pre-wrap text-bad">
                    {f.error}
                  </pre>
                </div>
              ))}
              <p className="text-xs text-ink-3">
                Numera read each error, diagnosed the cause and rewrote the code automatically.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
