"use client";

import {
  Bell,
  Download,
  Eye,
  LoaderCircle,
  MessageSquare,
  Pause,
  Play,
  RefreshCw,
  Split,
  Trash,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AlertsPanel } from "@/components/alerts-panel";
import { Sparkline } from "@/components/chat/sparkline";
import { useConfirm } from "@/components/ui/confirm";
import { Badge, Button, EmptyState, IconButton, SectionLabel } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDelta, formatValue, relativeTime } from "@/lib/format";
import type { DriverQuery, Monitor, MonitorDigest, RootCause } from "@/lib/types";

const STATUS: Record<string, { label: string; tone: "good" | "bad" | "warn" | "neutral" }> = {
  ok: { label: "Within range", tone: "good" },
  breached: { label: "Breached", tone: "bad" },
  error: { label: "Check failed", tone: "warn" },
  pending: { label: "Not yet checked", tone: "neutral" },
};

/** Standing KPI watches: what changed since the conversation ended. */
export function MonitorsView({
  onOpenSession,
  onChanged,
  onExplain,
  emailConfigured,
}: {
  onOpenSession: (sessionId: string) => void;
  onChanged?: (digest: MonitorDigest) => void;
  /** Open the full drill-down, pre-aimed at whatever the breach was about. */
  onExplain?: (datasetId: string, query: DriverQuery) => void;
  emailConfigured?: boolean;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [digest, setDigest] = useState<MonitorDigest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.monitorDigest();
      setDigest(next);
      onChanged?.(next);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load monitors.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const guard = async (key: string, action: () => Promise<void>, failure: string) => {
    setBusy(key);
    try {
      await action();
      await load();
    } catch (e) {
      toast.error(failure, e instanceof ApiError ? e.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  const runAll = () =>
    guard("all", async () => {
      const { results } = await api.runAllMonitors();
      const breached = results.filter((r) => r.run.status === "breached").length;
      toast.success(
        `Checked ${results.length} monitor${results.length === 1 ? "" : "s"}`,
        breached ? `${breached} outside the set range.` : "Everything is within range.",
      );
    }, "Couldn't run the monitors");

  const runOne = (monitor: Monitor) =>
    guard(monitor.id, async () => {
      const { run } = await api.runMonitor(monitor.id);
      if (run.status === "error") toast.error(`${monitor.title} could not be checked`, run.detail ?? undefined);
      else toast.success(`${monitor.title} checked`, run.detail ?? undefined);
    }, "Couldn't run this monitor");

  const toggle = (monitor: Monitor) =>
    guard(monitor.id, async () => {
      await api.updateMonitor(monitor.id, { enabled: !monitor.enabled });
    }, "Couldn't update this monitor");

  const remove = async (monitor: Monitor) => {
    const ok = await confirm({
      title: `Stop watching “${monitor.title}”?`,
      body: "Its check history is deleted and no further alerts are sent for it.",
      confirmLabel: "Remove monitor",
    });
    if (!ok) return;
    void guard(monitor.id, async () => {
      await api.deleteMonitor(monitor.id);
    }, "Couldn't remove this monitor");
  };

  if (error) {
    return (
      <EmptyState
        icon={<TriangleAlert className="size-5" />}
        title="Monitors couldn't be loaded"
        body={error}
        action={
          <Button
            onClick={() => {
              setError(null);
              void load();
            }}
          >
            <RefreshCw className="size-3.5" />
            Try again
          </Button>
        }
      />
    );
  }
  if (!digest) {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-4 px-6 pt-10">
        <div className="h-8 w-1/3 animate-pulse rounded bg-muted" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  const { counts } = digest;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 pt-8 pb-16 sm:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-ink-3 uppercase">
              <Bell className="size-3.5" />
              Monitors
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">
              Metric watches
            </h1>
            <p className="mt-1.5 max-w-2xl text-[14px] leading-relaxed text-ink-2">
              Each monitor re-runs the exact analysis code that produced a KPI, so re-checking costs a
              sandbox run and no model tokens.{" "}
              {digest.interval_minutes > 0
                ? `Checked automatically every ${digest.interval_minutes} minutes and whenever a new dataset version is uploaded.`
                : "Checked on demand and whenever a new dataset version is uploaded."}
            </p>
          </div>
          {digest.total > 0 && (
            <div className="flex shrink-0 gap-2">
              <Button size="sm" onClick={() => void runAll()} disabled={busy !== null}>
                {busy === "all" ? (
                  <LoaderCircle className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="size-3.5" />
                )}
                Run all
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void api.downloadMonitorBriefing()}
                title="Download a Markdown briefing of every monitor"
              >
                <Download className="size-3.5" />
                Briefing
              </Button>
            </div>
          )}
        </div>

        {digest.total === 0 ? (
          <div className="mt-10 rounded-2xl border-2 border-dashed border-line-strong p-10 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-accent-soft text-accent">
              <Eye className="size-5" />
            </div>
            <p className="mt-4 text-[15px] font-medium text-ink">Nothing is being watched yet</p>
            <p className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-ink-2">
              Use the eye icon on any KPI in an analysis to watch it. Numera re-checks the number when
              you upload new data and tells you when it moves outside the range you set.
            </p>
          </div>
        ) : (
          <>
            <div className="mt-7 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
              <Tally label="Breached" value={counts.breached} tone="bad" />
              <Tally label="Within range" value={counts.ok} tone="good" />
              <Tally label="Failing" value={counts.error} tone="warn" />
              <Tally label="Unchecked" value={counts.pending} tone="neutral" />
            </div>

            <div className="mt-8">
              <SectionLabel>
                {digest.total} monitor{digest.total === 1 ? "" : "s"}
              </SectionLabel>
              <div className="space-y-3">
                {digest.monitors.map((monitor) => (
                  <MonitorCard
                    key={monitor.id}
                    monitor={monitor}
                    busy={busy === monitor.id}
                    disabled={busy !== null}
                    onRun={() => void runOne(monitor)}
                    onToggle={() => void toggle(monitor)}
                    onRemove={() => void remove(monitor)}
                    onOpenSession={onOpenSession}
                    onExplain={onExplain}
                  />
                ))}
              </div>
            </div>
          </>
        )}

        <AlertsPanel emailConfigured={emailConfigured} />
      </div>
    </div>
  );
}

function Tally({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "good" | "bad" | "warn" | "neutral";
}) {
  const colors = {
    good: "text-good",
    bad: "text-bad",
    warn: "text-warn",
    neutral: "text-ink-2",
  };
  return (
    <div className="rounded-xl border border-line bg-panel p-3 shadow-card">
      <p className="text-[12px] text-ink-2">{label}</p>
      <p className={cn("mt-0.5 text-2xl font-semibold tracking-tight tabular-nums", colors[tone])}>
        {value}
      </p>
    </div>
  );
}

function MonitorCard({
  monitor,
  busy,
  disabled,
  onRun,
  onToggle,
  onRemove,
  onOpenSession,
  onExplain,
}: {
  monitor: Monitor;
  busy: boolean;
  disabled: boolean;
  onRun: () => void;
  onToggle: () => void;
  onRemove: () => void;
  onOpenSession: (sessionId: string) => void;
  onExplain?: (datasetId: string, query: DriverQuery) => void;
}) {
  const status = STATUS[monitor.last_status ?? "pending"] ?? STATUS.pending;
  const change = monitor.last_change_pct;

  return (
    <div
      className={cn(
        "rounded-xl border bg-panel p-4 shadow-card transition",
        monitor.last_status === "breached" ? "border-bad/30" : "border-line",
        !monitor.enabled && "opacity-60",
      )}
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[14px] font-semibold text-ink">{monitor.title}</p>
            <Badge tone={status.tone}>
              {monitor.last_status === "breached" && <TriangleAlert className="size-3" />}
              {status.label}
            </Badge>
            {!monitor.enabled && <Badge>Paused</Badge>}
          </div>
          <p className="mt-1 text-[12.5px] text-ink-3">
            {monitor.dataset_name ?? "dataset"} · {monitor.rule}
            {monitor.last_run_at ? ` · checked ${relativeTime(monitor.last_run_at)}` : " · never checked"}
          </p>
        </div>

        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold tracking-tight text-ink tabular-nums">
            {monitor.formatted_value}
          </span>
          {typeof change === "number" && Number.isFinite(change) && change !== 0 && (
            <span
              className={cn(
                "rounded-md px-1.5 py-0.5 text-[11px] font-medium",
                change > 0 ? "bg-good-soft text-good" : "bg-bad-soft text-bad",
              )}
            >
              {formatDelta(change)}
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton label="Check now" size="sm" onClick={onRun} disabled={disabled}>
            {busy ? <LoaderCircle className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          </IconButton>
          <IconButton
            label={monitor.enabled ? "Pause this monitor" : "Resume this monitor"}
            size="sm"
            onClick={onToggle}
            disabled={disabled}
          >
            {monitor.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          </IconButton>
          <IconButton
            label="Stop watching"
            size="sm" className="hover:bg-bad-soft hover:text-bad"
            onClick={onRemove}
            disabled={disabled}
          >
            <Trash className="size-3.5" />
          </IconButton>
        </div>
      </div>

      {monitor.history.length > 1 && <Sparkline values={monitor.history} className="mt-3" />}

      {monitor.last_detail && (
        <p
          className={cn(
            "mt-2.5 text-[12.5px] leading-relaxed",
            monitor.last_status === "breached" ? "text-bad" : "text-ink-2",
          )}
        >
          {monitor.last_detail}
        </p>
      )}

      <WhyPanel monitor={monitor} onExplain={onExplain} />

      {monitor.question && monitor.source_session_id && (
        <button
          type="button"
          onClick={() => onOpenSession(monitor.source_session_id as string)}
          className="mt-2 flex min-w-0 items-center gap-1.5 text-left text-[11.5px] text-ink-3 hover:text-ink"
          title={monitor.question}
        >
          <MessageSquare className="size-3 shrink-0" />
          <span className="truncate">{monitor.question}</span>
        </button>
      )}
    </div>
  );
}

/**
 * Why the number moved, computed the moment it breached.
 *
 * A monitor that says "revenue is below target" and stops has handed the reader the
 * easy half. The drill-down that names the segment costs one more pass over the same
 * cleaned table and no model tokens, so it rides along with the breach — and can be
 * asked for on demand when the monitor is fine but the question is still interesting.
 */
function WhyPanel({
  monitor,
  onExplain,
}: {
  monitor: Monitor;
  onExplain?: (datasetId: string, query: DriverQuery) => void;
}) {
  const [cause, setCause] = useState<RootCause | null>(monitor.root_cause ?? null);
  const [loading, setLoading] = useState(false);

  useEffect(() => setCause(monitor.root_cause ?? null), [monitor.root_cause]);

  const diagnose = async () => {
    setLoading(true);
    try {
      setCause(await api.diagnoseMonitor(monitor.id));
    } catch {
      setCause({ status: "unavailable", reason: "The drill-down could not be computed.", contributors: [], summary: null });
    } finally {
      setLoading(false);
    }
  };

  if (!cause) {
    return (
      <button
        type="button"
        onClick={() => void diagnose()}
        disabled={loading}
        className="mt-2.5 flex items-center gap-1.5 text-[12px] text-accent transition hover:underline disabled:opacity-50"
      >
        {loading ? <LoaderCircle className="size-3 animate-spin" /> : <Split className="size-3" />}
        Explain what moved this number
      </button>
    );
  }

  if (cause.status !== "ok") {
    return (
      <p className="mt-2.5 text-[11.5px] leading-relaxed text-ink-3">
        No drill-down available: {cause.reason}
      </p>
    );
  }

  const movers = cause.contributors.slice(0, 3);
  return (
    <div className="mt-3 rounded-lg border border-line bg-subtle p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Split className="size-3.5 shrink-0 text-accent" />
        <span className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">
          Why it moved
        </span>
        <span className="text-[11px] text-ink-3">
          {cause.measure} by {cause.dimension} · matched on {cause.matched_on}
        </span>
        <span className="ml-auto text-[11px] text-ink-3">no model call</span>
      </div>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink">{cause.summary}</p>
      {movers.length > 0 && (
        <ul className="mt-2 space-y-1">
          {movers.map((mover) => (
            <li key={mover.label} className="flex items-center gap-2 text-[12px]">
              <span className="min-w-0 flex-1 truncate text-ink-2">{mover.label}</span>
              <span
                className={cn(
                  "shrink-0 font-medium tabular-nums",
                  mover.change > 0 ? "text-good" : "text-bad",
                )}
              >
                {mover.change > 0 ? "+" : ""}
                {formatValue(mover.change)}
              </span>
              {mover.contribution_pct !== null && (
                <span className="w-16 shrink-0 text-right text-[11px] text-ink-3 tabular-nums">
                  {(mover.contribution_pct * 100).toFixed(0)}% of it
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {cause.largest_term && (
        <p className="mt-2 text-[11.5px] leading-relaxed text-ink-3">
          Largest term: <span className="font-medium text-ink-2">{cause.largest_term.label}</span>{" "}
          ({formatValue(cause.largest_term.value)}) — {cause.largest_term.detail}.
        </p>
      )}
      {onExplain && cause.params && (
        <button
          type="button"
          onClick={() => onExplain(monitor.dataset_id, cause.params as DriverQuery)}
          className="mt-2 text-[12px] text-accent transition hover:underline"
        >
          Open the full drill-down →
        </button>
      )}
    </div>
  );
}
