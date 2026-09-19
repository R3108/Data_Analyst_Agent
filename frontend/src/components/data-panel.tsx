"use client";

import {
  Calendar,
  Check,
  ChevronDown,
  CloudUpload,
  Download,
  EyeOff,
  FingerprintPattern,
  Hash,
  Layers,
  LoaderCircle,
  Lock,
  Plus,
  ScanEye,
  Search,
  ShieldAlert,
  ShieldCheck,
  ToggleLeft,
  Trash,
  TriangleAlert,
  Type,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { DataTable } from "@/components/chat/data-table";
import { Badge, Button, IconButton, SectionLabel } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatValue, relativeTime } from "@/lib/format";
import type {
  ColumnProfile,
  ContractState,
  Dataset,
  DatasetVersion,
  Expectation,
  KpiFormat,
  MetricDefinition,
  Preview,
  PrivacyAction,
  PrivacyReport,
  PrivacySeverity,
  Semantics,
  VersionDiff,
} from "@/lib/types";

export type DataTab =
  | "overview"
  | "columns"
  | "metrics"
  | "contract"
  | "privacy"
  | "cleaning"
  | "versions"
  | "preview";

const TABS: { id: DataTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "columns", label: "Columns" },
  { id: "metrics", label: "Metrics" },
  { id: "contract", label: "Contract" },
  { id: "privacy", label: "Privacy" },
  { id: "cleaning", label: "Cleaning" },
  { id: "versions", label: "Versions" },
  { id: "preview", label: "Preview" },
];

const CONTRACT_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  pass: "good",
  warn: "warn",
  fail: "bad",
  empty: "neutral",
};

const CHECK_TONE: Record<string, string> = {
  pass: "bg-good",
  warn: "bg-warn",
  error: "bg-warn",
  fail: "bg-bad",
};

export function DataPanel({
  dataset,
  tab,
  onTabChange,
  onClose,
  onSemanticsChange,
  onUploadVersion,
  onDatasetChanged,
}: {
  dataset: Dataset;
  tab: DataTab;
  onTabChange: (tab: DataTab) => void;
  onClose: () => void;
  onSemanticsChange?: (semantics: Semantics) => void;
  onUploadVersion?: (file: File) => void;
  /** Applying a redaction rewrites the table, so the open copy has to be reloaded. */
  onDatasetChanged?: () => void;
}) {
  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/30 xl:hidden" onClick={onClose} aria-hidden="true" />
      <aside className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[400px] flex-col border-l border-line bg-panel shadow-pop xl:static xl:z-auto xl:w-[400px] xl:max-w-none xl:shadow-none">
        <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line px-4">
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 truncate text-sm font-semibold text-ink">
              <span className="truncate">{dataset.name}</span>
              {(dataset.version ?? 1) > 1 && <Badge tone="accent">v{dataset.version}</Badge>}
            </p>
            <p className="truncate text-[11px] text-ink-3">{dataset.original_filename}</p>
          </div>
          <IconButton label="Close data panel" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>

        {/* Eight tabs do not fit one row of a 400px panel without truncating every label to
            four letters, so they wrap. A readable second row beats "Contr…" and "Previ…". */}
        <div className="flex shrink-0 flex-wrap gap-0.5 border-b border-line px-1.5 py-1.5" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => onTabChange(t.id)}
              className={cn(
                "rounded-md px-2 py-1 text-[12px] font-medium whitespace-nowrap transition",
                tab === t.id ? "bg-muted text-ink" : "text-ink-3 hover:bg-muted/60 hover:text-ink",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {tab === "overview" && <Overview dataset={dataset} />}
          {tab === "columns" && <Columns dataset={dataset} />}
          {tab === "metrics" && <Metrics dataset={dataset} onSaved={onSemanticsChange} />}
          {tab === "contract" && <Contract dataset={dataset} />}
          {tab === "privacy" && <Privacy dataset={dataset} onApplied={onDatasetChanged} />}
          {tab === "cleaning" && <Cleaning dataset={dataset} />}
          {tab === "versions" && <Versions dataset={dataset} onUploadVersion={onUploadVersion} />}
          {tab === "preview" && <PreviewTab datasetId={dataset.id} />}
        </div>
      </aside>
    </>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-line p-3">
      <p className="text-[12px] text-ink-2">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tracking-tight text-ink">{value}</p>
      {hint && <p className="text-[11px] text-ink-3">{hint}</p>}
    </div>
  );
}

function Overview({ dataset }: { dataset: Dataset }) {
  const { profile, cleaning } = dataset;
  const score = cleaning.quality_score;
  const tone = score >= 85 ? "bg-good" : score >= 60 ? "bg-warn" : "bg-bad";
  const roleCounts = Object.entries(profile.roles).map(([role, cols]) => ({ role, count: cols.length }));

  return (
    <div className="space-y-6">
      {dataset.version_diff && <DiffSummary diff={dataset.version_diff} />}
      {dataset.contract_result && dataset.contract_result.status !== "empty" && (
        <div
          className={cn(
            "flex items-start gap-2.5 rounded-lg border p-3",
            dataset.contract_result.status === "pass"
              ? "border-good/30 bg-good-soft"
              : dataset.contract_result.status === "fail"
                ? "border-bad/30 bg-bad-soft"
                : "border-warn/30 bg-warn-soft",
          )}
        >
          <ShieldCheck
            className={cn(
              "mt-px size-4 shrink-0",
              dataset.contract_result.status === "pass"
                ? "text-good"
                : dataset.contract_result.status === "fail"
                  ? "text-bad"
                  : "text-warn",
            )}
          />
          <p className="text-[12.5px] leading-relaxed text-ink">{dataset.contract_result.headline}</p>
        </div>
      )}

      <section>
        <div className="flex items-baseline justify-between">
          <SectionLabel>Data quality</SectionLabel>
          <span className="text-sm font-semibold text-ink">{score}/100</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-subtle">
          <div className={cn("h-full rounded-full", tone)} style={{ width: `${score}%` }} />
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-ink-3">
          Based on missing values, duplicate rows and values that could not be parsed.
        </p>
      </section>

      <section className="grid grid-cols-2 gap-2.5">
        <Stat
          label="Rows"
          value={profile.n_rows.toLocaleString()}
          hint={cleaning.rows_before !== cleaning.rows_after ? `from ${cleaning.rows_before.toLocaleString()}` : undefined}
        />
        <Stat label="Columns" value={String(profile.n_columns)} />
        <Stat label="Missing cells" value={`${cleaning.missing_cells_pct}%`} hint={cleaning.missing_cells.toLocaleString()} />
        <Stat label="Duplicates removed" value={cleaning.duplicates_removed.toLocaleString()} />
      </section>

      {profile.date_range && (
        <section>
          <SectionLabel>Time coverage</SectionLabel>
          <p className="text-[13px] text-ink">
            {new Date(profile.date_range.start).toLocaleDateString()} –{" "}
            {new Date(profile.date_range.end).toLocaleDateString()}
            <span className="text-ink-3"> · {profile.date_range.column}</span>
          </p>
        </section>
      )}

      <section>
        <SectionLabel>Column roles</SectionLabel>
        <div className="flex flex-wrap gap-1.5">
          {roleCounts.map(({ role, count }) => (
            <Badge key={role}>
              {count} {role}
              {count === 1 ? "" : "s"}
            </Badge>
          ))}
        </div>
      </section>

      <section>
        <SectionLabel>Source</SectionLabel>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
          <dt className="text-ink-3">File</dt>
          <dd className="truncate text-ink">{dataset.original_filename}</dd>
          <dt className="text-ink-3">Size</dt>
          <dd className="text-ink">{formatBytes(dataset.size_bytes)}</dd>
          {cleaning.ingestion?.encoding && (
            <>
              <dt className="text-ink-3">Encoding</dt>
              <dd className="text-ink">{cleaning.ingestion.encoding}</dd>
            </>
          )}
          {cleaning.ingestion?.delimiter && (
            <>
              <dt className="text-ink-3">Delimiter</dt>
              <dd className="font-mono text-ink">
                {cleaning.ingestion.delimiter === "\t" ? "tab" : cleaning.ingestion.delimiter}
              </dd>
            </>
          )}
          {cleaning.ingestion?.sheet_name && (
            <>
              <dt className="text-ink-3">Sheet</dt>
              <dd className="text-ink">{cleaning.ingestion.sheet_name}</dd>
            </>
          )}
          <dt className="text-ink-3">Memory</dt>
          <dd className="text-ink">{profile.memory_mb} MB</dd>
        </dl>
      </section>

      <section>
        <SectionLabel>Reproducibility</SectionLabel>
        <p className="mb-2 text-[12px] leading-relaxed text-ink-3">
          Download the exact cleaned table the agent analysed — pair it with the notebook export to
          reproduce every figure.
        </p>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => void api.downloadDataset(dataset.id, "csv")}>
            <Download className="size-3.5" />
            CSV
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void api.downloadDataset(dataset.id, "parquet")}>
            <Download className="size-3.5" />
            Parquet
          </Button>
        </div>
      </section>
    </div>
  );
}

function DiffSummary({ diff }: { diff: VersionDiff }) {
  const rowChange = diff.rows.change;
  return (
    <section className="rounded-xl border border-accent/30 bg-accent-soft/40 p-3.5">
      <p className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-accent-ink uppercase">
        <Layers className="size-3.5" />
        Version {diff.current_version} · what changed
      </p>
      <p className="mt-1.5 text-[13px] leading-snug font-medium text-ink">{diff.headline}</p>
      {diff.notable.length > 0 && (
        <ul className="mt-2 space-y-1 text-[12.5px] text-ink-2">
          {diff.notable.map((note) => (
            <li key={note}>• {note}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[11.5px] text-ink-3 tabular-nums">
        {diff.rows.previous.toLocaleString()} → {diff.rows.current.toLocaleString()} rows
        {rowChange !== 0 && ` (${rowChange > 0 ? "+" : "−"}${Math.abs(rowChange).toLocaleString()})`}
      </p>
    </section>
  );
}

function columnIcon(column: ColumnProfile) {
  if (column.role === "identifier") return FingerprintPattern;
  if (column.dtype === "datetime") return Calendar;
  if (column.dtype === "boolean") return ToggleLeft;
  if (column.dtype === "integer" || column.dtype === "decimal") return Hash;
  return Type;
}

const SEVERITY_TONE: Record<PrivacySeverity, "bad" | "warn" | "neutral"> = {
  high: "bad",
  medium: "warn",
  low: "neutral",
};

const ACTION_HINT: Record<PrivacyAction, string> = {
  keep: "Leave the values exactly as they are.",
  mask: "Replace each value in place, keeping its shape and last few characters.",
  hash: "Replace with a stable salted pseudonym — grouping, joins and retention still work.",
  drop: "Remove the column from the table entirely.",
};

/**
 * The privacy guard. Detection happens at upload and withholds example values from every
 * prompt without anyone pressing anything; this tab is where the second half — actually
 * rewriting the table — is decided and audited.
 */
function Privacy({ dataset, onApplied }: { dataset: Dataset; onApplied?: () => void }) {
  const toast = useToast();
  const [report, setReport] = useState<PrivacyReport | null>(null);
  const [draft, setDraft] = useState<Record<string, PrivacyAction>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"save" | "apply" | "scan" | null>(null);

  const adopt = (next: PrivacyReport) => {
    setReport(next);
    const policy: Record<string, PrivacyAction> = {};
    for (const finding of next.scan.findings) {
      policy[finding.column] = next.state.policy[finding.column] ?? "keep";
    }
    // A policy may name a column the scan did not flag — the user's judgement outranks it.
    for (const [column, action] of Object.entries(next.state.policy)) policy[column] = action;
    setDraft(policy);
  };

  useEffect(() => {
    setLoading(true);
    api
      .getPrivacy(dataset.id)
      .then(adopt)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [dataset.id]);

  const act = async (
    kind: "save" | "apply" | "scan",
    run: () => Promise<PrivacyReport>,
    success: [string, string],
  ) => {
    setBusy(kind);
    try {
      adopt(await run());
      toast.success(success[0], success[1]);
      if (kind === "apply") onApplied?.();
    } catch (error) {
      toast.error(
        "Couldn't update the privacy policy",
        error instanceof ApiError ? error.message : "Please try again.",
      );
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <div className="h-40 animate-pulse rounded-lg bg-muted" />;
  if (!report) return <p className="text-[13px] text-ink-3">The privacy scan is unavailable.</p>;

  const { scan, state } = report;
  const applied = report.applied;
  const dirty =
    JSON.stringify(Object.entries(draft).filter(([, a]) => a !== "keep").sort()) !==
    JSON.stringify(Object.entries(state.policy).sort());
  const planned = Object.entries(draft).filter(([, action]) => action !== "keep");
  const tone =
    scan.status === "sensitive" ? "bad" : scan.status === "review" ? "warn" : "good";

  return (
    <div className="space-y-5">
      <div
        className={cn(
          "flex items-start gap-2.5 rounded-lg border p-3",
          tone === "bad"
            ? "border-bad/30 bg-bad-soft"
            : tone === "warn"
              ? "border-warn/30 bg-warn-soft"
              : "border-good/30 bg-good-soft",
        )}
      >
        <ShieldAlert
          className={cn(
            "mt-px size-4 shrink-0",
            tone === "bad" ? "text-bad" : tone === "warn" ? "text-warn" : "text-good",
          )}
        />
        <div className="min-w-0">
          <p className="text-[13px] leading-snug font-medium text-ink">{scan.headline}</p>
          <p className="mt-0.5 text-[11.5px] text-ink-2">
            {scan.scanned_columns} columns scanned · {scan.counts.high} high · {scan.counts.medium}{" "}
            medium · {scan.counts.low} low
          </p>
        </div>
      </div>

      {dataset.profile.withheld_columns?.length ? (
        <div className="flex items-start gap-2.5 rounded-lg border border-line bg-subtle p-3">
          <EyeOff className="mt-px size-4 shrink-0 text-ink-3" />
          <p className="text-[12px] leading-relaxed text-ink-2">
            Example values for{" "}
            <span className="font-medium text-ink">
              {dataset.profile.withheld_columns.join(", ")}
            </span>{" "}
            are already withheld from every prompt. The model is told the columns exist and what
            they are for, and never sees a value — whatever you decide below.
          </p>
        </div>
      ) : null}

      {applied && (
        <div>
          <SectionLabel icon={<Check className="size-3.5" />}>
            Redaction applied {state.applied_at ? relativeTime(state.applied_at) : ""}
          </SectionLabel>
          <ul className="space-y-1.5 rounded-lg border border-line p-3">
            {state.applied.map((entry) => (
              <li key={entry.column} className="text-[12px] leading-relaxed text-ink-2">
                <span className="font-medium text-ink">{entry.column}</span> → {entry.action}
                {entry.status === "missing" && " (column not present)"}
                <span className="block text-[11px] text-ink-3">{entry.detail}</span>
              </li>
            ))}
          </ul>
          {state.raw_purged && (
            <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">
              The original uploaded file was deleted too — keeping it would have defeated the
              redaction. Every version filed after this one inherits the same policy and salt.
            </p>
          )}
        </div>
      )}

      {scan.findings.length === 0 ? (
        <p className="text-[13px] leading-relaxed text-ink-3">
          No column matched a personal-data pattern. Detection recognises what it knows —
          a free-text column can still hold something no pattern can see.
        </p>
      ) : (
        <div>
          <div className="mb-2.5 flex items-center justify-between">
            <SectionLabel>What to do with each column</SectionLabel>
            <button
              onClick={() => setDraft({ ...draft, ...scan.suggested_policy })}
              className="mb-2.5 text-[12px] text-accent hover:underline"
            >
              Use suggestions
            </button>
          </div>
          <ul className="space-y-2">
            {scan.findings.map((finding) => (
              <li key={finding.column} className="rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="min-w-0 truncate font-mono text-[12.5px] text-ink">
                    {finding.column}
                  </span>
                  <Badge tone={SEVERITY_TONE[finding.severity]}>{finding.label}</Badge>
                  <span className="ml-auto text-[11px] text-ink-3 tabular-nums">
                    {(finding.confidence * 100).toFixed(0)}% · {finding.basis}
                  </span>
                </div>
                <p className="mt-1 text-[11.5px] leading-relaxed text-ink-2">{finding.why}</p>
                {finding.shape && (
                  <p
                    className="mt-1 font-mono text-[11px] text-ink-3"
                    title="The structure of a value — letters as a, digits as 9. Never a real value."
                  >
                    looks like {finding.shape}
                  </p>
                )}
                <div className="mt-2 grid grid-cols-4 gap-1">
                  {(["keep", "mask", "hash", "drop"] as PrivacyAction[]).map((action) => (
                    <button
                      key={action}
                      disabled={applied}
                      title={ACTION_HINT[action]}
                      onClick={() => setDraft({ ...draft, [finding.column]: action })}
                      className={cn(
                        "rounded-md px-1.5 py-1 text-[11.5px] font-medium capitalize transition disabled:opacity-50",
                        (draft[finding.column] ?? "keep") === action
                          ? "bg-accent text-white"
                          : "bg-muted text-ink-2 hover:text-ink",
                      )}
                    >
                      {action}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-2 border-t border-line pt-4">
        {!applied && (
          <>
            <Button
              size="sm"
              variant="secondary"
              loading={busy === "save"}
              disabled={!dirty || busy !== null}
              onClick={() =>
                void act(
                  "save",
                  () =>
                    api.savePrivacy(
                      dataset.id,
                      Object.fromEntries(planned) as Record<string, PrivacyAction>,
                    ),
                  ["Policy saved", "The table is untouched until you apply it."],
                )
              }
            >
              Save policy
            </Button>
            <Button
              size="sm"
              variant="primary"
              loading={busy === "apply"}
              disabled={busy !== null || (!planned.length && !state.policy)}
              onClick={() => {
                const columns = planned.length
                  ? planned.map(([column, action]) => `${column} → ${action}`)
                  : Object.entries(state.policy).map(([c, a]) => `${c} → ${a}`);
                if (!columns.length) {
                  toast.error("Nothing to redact", "Choose mask, hash or drop for a column first.");
                  return;
                }
                if (
                  !window.confirm(
                    `Rewrite this table permanently?\n\n${columns.join(
                      "\n",
                    )}\n\nThe original upload is deleted too, and this cannot be undone.`,
                  )
                ) {
                  return;
                }
                void act(
                  "apply",
                  async () => {
                    if (dirty) {
                      await api.savePrivacy(
                        dataset.id,
                        Object.fromEntries(planned) as Record<string, PrivacyAction>,
                      );
                    }
                    return api.applyPrivacy(dataset.id);
                  },
                  ["Table redacted", "Every preview, export and share link now reads the new table."],
                );
              }}
            >
              <Lock className="size-3.5" />
              Apply to the table
            </Button>
          </>
        )}
        <Button
          size="sm"
          loading={busy === "scan"}
          disabled={busy !== null}
          onClick={() =>
            void act("scan", () => api.scanPrivacy(dataset.id), [
              "Re-scanned",
              "Detection re-ran against the table as it stands now.",
            ])
          }
        >
          <ScanEye className="size-3.5" />
          Re-scan
        </Button>
        <Button size="sm" onClick={() => void api.downloadPrivacy(dataset.id)}>
          <Download className="size-3.5" />
          Export review
        </Button>
      </div>
      {!applied && planned.length > 0 && (
        <p className="text-[11.5px] leading-relaxed text-ink-3">
          Applying rewrites the one cleaned table that the preview, the sandbox, every export and
          every share link all read — so none of them has to remember to filter, and none of them
          can forget.
        </p>
      )}
    </div>
  );
}

function Columns({ dataset }: { dataset: Dataset }) {
  const [query, setQuery] = useState("");
  const columns = dataset.profile.columns.filter((c) => c.name.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 focus-within:border-accent/60">
        <Search className="size-3.5 text-ink-3" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter columns"
          className="flex-1 bg-transparent text-[13px] outline-none placeholder:text-ink-3"
        />
      </label>
      <ul className="divide-y divide-line rounded-lg border border-line">
        {columns.map((column) => (
          <ColumnRow key={column.name} column={column} rows={dataset.profile.n_rows} />
        ))}
        {columns.length === 0 && <li className="p-3 text-[13px] text-ink-3">No matching columns.</li>}
      </ul>
    </div>
  );
}

function ColumnRow({ column, rows }: { column: ColumnProfile; rows: number }) {
  const [open, setOpen] = useState(false);
  const Icon = columnIcon(column);
  const stats = column.stats ?? {};
  const numeric = column.dtype === "integer" || column.dtype === "decimal";

  return (
    <li>
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-muted/50">
        <Icon className="size-3.5 shrink-0 text-ink-3" />
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{column.name}</span>
        {column.sensitive && (
          <EyeOff
            className="size-3.5 shrink-0 text-warn"
            aria-label="Personal data: example values are withheld from every prompt"
          />
        )}
        <Badge tone={column.role === "measure" ? "accent" : "neutral"}>{column.role}</Badge>
        <ChevronDown className={cn("size-3.5 shrink-0 text-ink-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-3 px-3 pb-3 text-[12.5px]">
          <div className="flex items-center gap-3 text-ink-2">
            <span>{column.dtype}</span>
            <span>·</span>
            <span>{column.unique.toLocaleString()} unique</span>
            <span>·</span>
            <span className={column.missing ? "text-warn" : undefined}>{column.missing_pct}% missing</span>
          </div>
          {column.sensitive && (
            <p className="rounded-md bg-warn-soft px-2 py-1.5 leading-relaxed text-ink-2">
              Flagged as personal data. Example values are withheld from every prompt; the Privacy
              tab decides whether they are also removed from the table.
            </p>
          )}
          {numeric && stats.min !== undefined && (
            <div className="grid grid-cols-4 gap-2">
              {(["min", "median", "mean", "max"] as const).map((key) => (
                <div key={key} className="rounded-md bg-muted px-2 py-1.5">
                  <p className="text-[10.5px] text-ink-3 uppercase">{key}</p>
                  <p className="truncate font-medium text-ink tabular-nums">{formatValue(stats[key])}</p>
                </div>
              ))}
            </div>
          )}
          {column.dtype === "datetime" && stats.min && (
            <p className="text-ink-2">
              {String(stats.min).slice(0, 10)} → {String(stats.max).slice(0, 10)} · {String(stats.granularity)}
            </p>
          )}
          {column.top_values && column.top_values.length > 0 && (
            <div className="space-y-1.5">
              {column.top_values.map((top) => (
                <div key={String(top.value)} className="flex items-center gap-2">
                  <span className="w-28 shrink-0 truncate text-ink-2" title={String(top.value)}>
                    {String(top.value)}
                  </span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-subtle">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(2, (100 * top.count) / rows)}%` }} />
                  </div>
                  <span className="w-12 shrink-0 text-right text-ink-3 tabular-nums">{top.count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function Cleaning({ dataset }: { dataset: Dataset }) {
  const { cleaning } = dataset;
  return (
    <div className="space-y-6">
      <p className="text-[13px] leading-relaxed text-ink-2">
        Numera applied {cleaning.actions.length} deterministic cleaning steps before any AI touched your data.
        Missing values are reported, never invented.
      </p>

      <ol className="space-y-3">
        {cleaning.actions.map((action, i) => {
          const warning = action.step === "missing_values" || action.step === "skip_malformed_lines";
          return (
            <li key={i} className="flex gap-2.5">
              <span
                className={cn(
                  "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full",
                  warning ? "bg-warn-soft text-warn" : "bg-good-soft text-good",
                )}
              >
                {warning ? <TriangleAlert className="size-3" /> : <Check className="size-3" />}
              </span>
              <div className="min-w-0 text-[13px] leading-snug">
                {action.column && <p className="font-medium text-ink">{action.column}</p>}
                <p className="text-ink-2">{action.detail}</p>
              </div>
            </li>
          );
        })}
        {cleaning.actions.length === 0 && <li className="text-[13px] text-ink-3">The data was already clean.</li>}
      </ol>

      {cleaning.outliers.length > 0 && (
        <section>
          <SectionLabel>Potential outliers</SectionLabel>
          <ul className="space-y-1.5 text-[13px]">
            {cleaning.outliers.map((o) => (
              <li key={o.column} className="flex justify-between gap-3">
                <span className="truncate text-ink">{o.column}</span>
                <span className="shrink-0 text-ink-3">{o.count.toLocaleString()} values beyond 3×IQR</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[12px] text-ink-3">Flagged only — outliers are kept so totals stay accurate.</p>
        </section>
      )}
    </div>
  );
}

const EMPTY_SEMANTICS: Semantics = { metrics: [], rules: [], glossary: [] };
const METRIC_FORMATS: KpiFormat[] = ["auto", "number", "integer", "currency", "percent", "text"];

/**
 * The semantic layer. Definitions entered here are injected into every prompt as
 * binding instructions and are checked afterwards by the verifier, so "revenue"
 * means the same thing in every answer.
 */
function Metrics({ dataset, onSaved }: { dataset: Dataset; onSaved?: (s: Semantics) => void }) {
  const toast = useToast();
  const [draft, setDraft] = useState<Semantics | null>(null);
  const [saved, setSaved] = useState<Semantics | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getSemantics(dataset.id)
      .then((semantics) => {
        if (!active) return;
        setDraft(semantics);
        setSaved(semantics);
      })
      .catch((e) => active && setError(e instanceof ApiError ? e.message : "Could not load metrics."));
    return () => {
      active = false;
    };
  }, [dataset.id]);

  if (error) return <p className="text-[13px] text-bad">{error}</p>;
  if (!draft) return <div className="h-48 animate-pulse rounded-lg bg-muted" />;

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved ?? EMPTY_SEMANTICS);

  const patch = (next: Partial<Semantics>) => setDraft({ ...draft, ...next });
  const patchMetric = (index: number, next: Partial<MetricDefinition>) =>
    patch({ metrics: draft.metrics.map((m, i) => (i === index ? { ...m, ...next } : m)) });

  const save = async () => {
    setBusy(true);
    try {
      const cleaned: Semantics = {
        metrics: draft.metrics.filter((m) => m.name.trim() && m.definition.trim()),
        rules: draft.rules.map((r) => r.trim()).filter(Boolean),
        glossary: draft.glossary.filter((g) => g.term.trim() && g.definition.trim()),
      };
      const result = await api.saveSemantics(dataset.id, cleaned);
      setDraft(result);
      setSaved(result);
      onSaved?.(result);
      toast.success("Definitions saved", "Every new answer will use them.");
    } catch (e) {
      toast.error("Couldn't save definitions", e instanceof ApiError ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-[13px] leading-relaxed text-ink-2">
        Define your metrics once. The agent must compute them exactly as written, in every analysis —
        and flags the answer if the code does not appear to follow a definition.
      </p>

      <section>
        <SectionLabel>Metric definitions</SectionLabel>
        <div className="space-y-2.5">
          {draft.metrics.map((metric, index) => (
            <div key={index} className="rounded-lg border border-line p-2.5">
              <div className="flex items-center gap-1.5">
                <input
                  value={metric.name}
                  onChange={(e) => patchMetric(index, { name: e.target.value })}
                  placeholder="Net revenue"
                  maxLength={80}
                  aria-label="Metric name"
                  className="h-7 min-w-0 flex-1 rounded-md bg-muted px-2 text-[13px] font-medium text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
                />
                <select
                  value={metric.format ?? "auto"}
                  onChange={(e) => patchMetric(index, { format: e.target.value as KpiFormat })}
                  aria-label="Metric format"
                  className="h-7 shrink-0 rounded-md bg-muted px-1.5 text-[12px] text-ink-2 outline-none"
                >
                  {METRIC_FORMATS.map((format) => (
                    <option key={format} value={format}>
                      {format}
                    </option>
                  ))}
                </select>
                <IconButton
                  label="Remove metric"
                  size="sm" className="hover:bg-bad-soft hover:text-bad"
                  onClick={() => patch({ metrics: draft.metrics.filter((_, i) => i !== index) })}
                >
                  <Trash className="size-3.5" />
                </IconButton>
              </div>
              <textarea
                value={metric.definition}
                onChange={(e) => patchMetric(index, { definition: e.target.value })}
                placeholder="Gross sales minus refunds and cancellations, excluding internal test orders."
                rows={2}
                maxLength={600}
                aria-label="Metric definition"
                className="mt-1.5 w-full resize-y rounded-md bg-transparent px-0.5 text-[12.5px] leading-relaxed text-ink-2 outline-none placeholder:text-ink-3 focus-visible:outline-none"
              />
            </div>
          ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="mt-2"
          onClick={() =>
            patch({ metrics: [...draft.metrics, { name: "", definition: "", format: "auto" }] })
          }
        >
          <Plus className="size-3.5" />
          Add metric
        </Button>
      </section>

      <section>
        <SectionLabel>Analysis rules</SectionLabel>
        <textarea
          value={draft.rules.join("\n")}
          onChange={(e) => patch({ rules: e.target.value.split("\n") })}
          placeholder={"One rule per line, e.g.\nExclude orders with status = cancelled.\nThe fiscal year starts in April."}
          rows={4}
          className="w-full resize-y rounded-lg border border-line bg-transparent p-2.5 text-[12.5px] leading-relaxed text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
        />
      </section>

      <section>
        <SectionLabel>Glossary</SectionLabel>
        <div className="space-y-2">
          {draft.glossary.map((entry, index) => (
            <div key={index} className="flex items-start gap-1.5">
              <input
                value={entry.term}
                onChange={(e) =>
                  patch({
                    glossary: draft.glossary.map((g, i) =>
                      i === index ? { ...g, term: e.target.value } : g,
                    ),
                  })
                }
                placeholder="Term"
                maxLength={80}
                aria-label="Glossary term"
                className="h-7 w-24 shrink-0 rounded-md bg-muted px-2 text-[12.5px] text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
              />
              <input
                value={entry.definition}
                onChange={(e) =>
                  patch({
                    glossary: draft.glossary.map((g, i) =>
                      i === index ? { ...g, definition: e.target.value } : g,
                    ),
                  })
                }
                placeholder="What it means in your business"
                maxLength={600}
                aria-label="Glossary definition"
                className="h-7 min-w-0 flex-1 rounded-md bg-muted px-2 text-[12.5px] text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
              />
              <IconButton
                label="Remove term"
                size="sm" className="hover:bg-bad-soft hover:text-bad"
                onClick={() => patch({ glossary: draft.glossary.filter((_, i) => i !== index) })}
              >
                <Trash className="size-3.5" />
              </IconButton>
            </div>
          ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="mt-2"
          onClick={() => patch({ glossary: [...draft.glossary, { term: "", definition: "" }] })}
        >
          <Plus className="size-3.5" />
          Add term
        </Button>
      </section>

      <div className="sticky bottom-0 -mx-4 flex items-center gap-2 border-t border-line bg-panel px-4 py-3">
        <span className="flex-1 text-[12px] text-ink-3">
          {dirty ? "Unsaved changes" : "Saved — used by every new answer"}
        </span>
        {dirty && (
          <Button size="sm" variant="ghost" onClick={() => setDraft(saved ?? EMPTY_SEMANTICS)}>
            Reset
          </Button>
        )}
        <Button size="sm" variant="primary" onClick={() => void save()} disabled={!dirty} loading={busy}>
          Save
        </Button>
      </div>
    </div>
  );
}

/**
 * The promise this table makes to every later upload. Suggested from the profile, edited
 * here, inherited by each new version and checked the moment one arrives.
 */
function Contract({ dataset }: { dataset: Dataset }) {
  const toast = useToast();
  const [state, setState] = useState<ContractState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getContract(dataset.id)
      .then((next) => active && setState(next))
      .catch((e) => active && setError(e instanceof ApiError ? e.message : "Could not load the contract."));
    return () => {
      active = false;
    };
  }, [dataset.id]);

  const guard = async (key: string, action: () => Promise<ContractState>, failure: string) => {
    setBusy(key);
    try {
      setState(await action());
    } catch (e) {
      toast.error(failure, e instanceof ApiError ? e.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  const adopt = () =>
    void guard("adopt", async () => {
      const suggested = await api.suggestContract(dataset.id);
      const saved = await api.saveContract(dataset.id, suggested);
      toast.success("Contract saved", `${suggested.expectations.length} expectations now guard this dataset.`);
      return saved;
    }, "Couldn't build a contract");

  const save = (expectations: Expectation[]) =>
    void guard("save", () => api.saveContract(dataset.id, { expectations }), "Couldn't save the contract");

  if (error) return <p className="text-[13px] text-bad">{error}</p>;
  if (!state) return <div className="h-64 animate-pulse rounded-lg bg-muted" />;

  const { contract, result } = state;
  const byStatus = new Map((result?.results ?? []).map((r) => [r.id, r]));

  return (
    <div className="space-y-5">
      <p className="text-[13px] leading-relaxed text-ink-2">
        Expectations this table must keep meeting. Every new version is checked against them
        automatically, so a dropped column or an unknown category is caught on arrival — not three
        answers later.
      </p>

      {contract.expectations.length === 0 ? (
        <div className="rounded-xl border-2 border-dashed border-line-strong p-6 text-center">
          <ShieldCheck className="mx-auto size-6 text-ink-3" />
          <p className="mt-2 text-[13.5px] font-medium text-ink">No contract yet</p>
          <p className="mx-auto mt-1 max-w-xs text-[12.5px] leading-relaxed text-ink-2">
            Numera can propose one from this dataset&apos;s profile — types, completeness, category
            sets, ranges and freshness — for you to trim.
          </p>
          <Button size="sm" variant="primary" className="mt-3" loading={busy === "adopt"} onClick={adopt}>
            Suggest a contract
          </Button>
        </div>
      ) : (
        <>
          {result && (
            <div className="rounded-xl border border-line p-3.5">
              <div className="flex items-center gap-2">
                <Badge tone={CONTRACT_TONE[result.status] ?? "neutral"}>
                  {result.status === "pass" ? "All checks passed" : result.status === "fail" ? "Failing" : "Warnings"}
                </Badge>
                {result.score !== null && (
                  <span className="ml-auto text-sm font-semibold text-ink tabular-nums">
                    {result.score}/100
                  </span>
                )}
              </div>
              <p className="mt-2 text-[12.5px] leading-relaxed text-ink-2">{result.headline}</p>
              <p className="mt-1 text-[11.5px] text-ink-3">
                {result.counts.pass} passed · {result.counts.warn} warned · {result.counts.fail} failed ·
                checked {relativeTime(result.checked_at)}
              </p>
            </div>
          )}

          <div className="space-y-1.5">
            {contract.expectations.map((expectation) => {
              const check = byStatus.get(expectation.id);
              return (
                <div key={expectation.id} className="flex items-start gap-2 rounded-lg border border-line p-2.5">
                  <span
                    className={cn(
                      "mt-1.5 size-2 shrink-0 rounded-full",
                      expectation.enabled ? (CHECK_TONE[check?.status ?? ""] ?? "bg-line-strong") : "bg-line-strong",
                    )}
                    title={check?.status ?? "not checked"}
                  />
                  <div className="min-w-0 flex-1">
                    <p className={cn("text-[12.5px] leading-snug text-ink", !expectation.enabled && "opacity-50")}>
                      {expectation.description}
                    </p>
                    {check && (
                      <p
                        className={cn(
                          "mt-0.5 text-[11.5px] leading-snug",
                          check.status === "fail" ? "text-bad" : check.status === "pass" ? "text-ink-3" : "text-warn",
                        )}
                      >
                        {check.detail}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() =>
                      save(
                        contract.expectations.map((e) =>
                          e.id === expectation.id ? { ...e, enabled: !e.enabled } : e,
                        ),
                      )
                    }
                    disabled={busy !== null}
                    className="shrink-0 rounded-md px-1.5 py-0.5 text-[11px] text-ink-3 transition hover:bg-muted hover:text-ink disabled:opacity-50"
                    title={expectation.enabled ? "Stop checking this" : "Check this again"}
                  >
                    {expectation.enabled ? "Mute" : "Unmute"}
                  </button>
                  <IconButton
                    label="Remove this expectation"
                    size="sm"
                    className="hover:bg-bad-soft hover:text-bad"
                    disabled={busy !== null}
                    onClick={() => save(contract.expectations.filter((e) => e.id !== expectation.id))}
                  >
                    <Trash className="size-3.5" />
                  </IconButton>
                </div>
              );
            })}
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              loading={busy === "check"}
              onClick={() => void guard("check", () => api.checkContract(dataset.id), "Couldn't re-check")}
            >
              <Check className="size-3.5" />
              Re-check now
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void api.downloadContract(dataset.id)}>
              <Download className="size-3.5" />
              Export
            </Button>
            <Button size="sm" variant="ghost" loading={busy === "adopt"} onClick={adopt}>
              Re-suggest
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function Versions({
  dataset,
  onUploadVersion,
}: {
  dataset: Dataset;
  onUploadVersion?: (file: File) => void;
}) {
  const [versions, setVersions] = useState<DatasetVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    api
      .datasetVersions(dataset.id)
      .then((list) => active && setVersions(list))
      .catch((e) => active && setError(e instanceof ApiError ? e.message : "Could not load versions."));
    return () => {
      active = false;
    };
  }, [dataset.id]);

  return (
    <div className="space-y-5">
      <p className="text-[13px] leading-relaxed text-ink-2">
        Upload next period&apos;s export as a new version. Numera diffs it against the previous one,
        carries your metric definitions across and re-checks every monitor on this dataset.
      </p>

      {onUploadVersion && (
        <>
          <Button size="sm" onClick={() => input.current?.click()}>
            <CloudUpload className="size-3.5" />
            Upload new version
          </Button>
          <input
            ref={input}
            type="file"
            accept=".csv,.tsv,.txt,.xlsx,.xlsm"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUploadVersion(file);
              event.target.value = "";
            }}
          />
        </>
      )}

      {error && <p className="text-[13px] text-bad">{error}</p>}
      {!versions && !error && <div className="h-32 animate-pulse rounded-lg bg-muted" />}

      {versions && (
        <ol className="space-y-2.5">
          {[...versions].reverse().map((version) => (
            <li
              key={version.id}
              className={cn(
                "rounded-lg border p-3",
                version.id === dataset.id ? "border-accent/40 bg-accent-soft/25" : "border-line",
              )}
            >
              <div className="flex items-center gap-2">
                <Badge tone={version.id === dataset.id ? "accent" : "neutral"}>v{version.version}</Badge>
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2">
                  {version.original_filename}
                </span>
                <span className="shrink-0 text-[11.5px] text-ink-3">
                  {relativeTime(version.created_at)}
                </span>
              </div>
              <p className="mt-1.5 text-[12px] text-ink-3 tabular-nums">
                {version.n_rows.toLocaleString()} rows · {version.n_cols} columns ·{" "}
                {formatBytes(version.size_bytes)}
              </p>
              {version.version_diff && (
                <p className="mt-1.5 text-[12.5px] leading-snug text-ink-2">
                  {version.version_diff.headline}
                </p>
              )}
              {version.contract_result && version.contract_result.status !== "empty" && (
                <p
                  className={cn(
                    "mt-1.5 text-[12px] leading-snug",
                    version.contract_result.status === "pass"
                      ? "text-good"
                      : version.contract_result.status === "fail"
                        ? "text-bad"
                        : "text-warn",
                  )}
                >
                  {version.contract_result.headline}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function PreviewTab({ datasetId }: { datasetId: string }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .previewDataset(datasetId, 100)
      .then((p) => active && setPreview(p))
      .catch((e) => active && setError(e instanceof ApiError ? e.message : "Could not load preview."));
    return () => {
      active = false;
    };
  }, [datasetId]);

  const columns = useMemo(() => (preview?.columns ?? []).map((name) => ({ name })), [preview]);

  if (error) return <p className="text-[13px] text-bad">{error}</p>;
  if (!preview) return <div className="h-64 animate-pulse rounded-lg bg-muted" />;
  // Fill the panel rather than stopping at a fixed height and leaving dead space below.
  return (
    <DataTable
      columns={columns}
      rows={preview.rows}
      totalRows={preview.total_rows}
      maxHeight="calc(100dvh - 11rem)"
    />
  );
}
