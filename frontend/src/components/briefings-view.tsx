"use client";

import {
  CalendarClock,
  CircleCheck,
  Info,
  Pause,
  Play,
  Send,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useConfirm } from "@/components/ui/confirm";
import {
  Badge,
  Button,
  EmptyState,
  IconButton,
  SectionLabel,
  Select,
  TextInput,
  ViewHeader,
} from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { Briefing, DatasetSummary } from "@/lib/types";

const CADENCES = [
  { value: "6", label: "Every 6 hours" },
  { value: "24", label: "Daily" },
  { value: "168", label: "Weekly" },
  { value: "720", label: "Monthly" },
];

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

function cadenceLabel(hours: number): string {
  return CADENCES.find((c) => c.value === String(hours))?.label ?? `Every ${hours}h`;
}

/**
 * Saved questions that re-ask themselves on a cadence and deliver the answer.
 *
 * The deliberate distinction from monitors is stated in the UI, not just the docs: a
 * monitor re-runs snapshotted code and costs nothing; a briefing re-runs the whole agent
 * and costs tokens. Watch a number with one, ask a question with the other.
 */
export function BriefingsView({
  datasets,
  intervalMinutes,
  onOpenSession,
}: {
  datasets: DatasetSummary[];
  intervalMinutes?: number;
  onOpenSession: (sessionId: string) => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setBriefings(await api.listBriefings());
    } catch (error) {
      toast.error("Couldn't load briefings", describe(error));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (briefing: Briefing) => {
    setRunning(briefing.id);
    try {
      const result = await api.runBriefing(briefing.id);
      toast.success(
        result.briefing.last_status === "ok" ? "Briefing ready" : "Briefing failed",
        result.briefing.last_headline ?? result.briefing.last_error ?? "",
        { label: "Open the analysis", onClick: () => onOpenSession(result.session_id) },
      );
      await refresh();
    } catch (error) {
      toast.error("Couldn't run the briefing", describe(error));
      await refresh();
    } finally {
      setRunning(null);
    }
  };

  const toggle = async (briefing: Briefing) => {
    try {
      await api.updateBriefing(briefing.id, { enabled: !briefing.enabled });
      await refresh();
    } catch (error) {
      toast.error("Couldn't update the briefing", describe(error));
    }
  };

  const remove = async (briefing: Briefing) => {
    const ok = await confirm({
      title: `Delete the briefing “${briefing.title}”?`,
      body: "It stops running on its schedule. Analyses it already produced are kept.",
      confirmLabel: "Delete briefing",
    });
    if (!ok) return;
    try {
      await api.deleteBriefing(briefing.id);
      await refresh();
    } catch (error) {
      toast.error("Couldn't delete the briefing", describe(error));
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 pt-8 pb-16 sm:px-6">
        <ViewHeader
          eyebrow="Briefings"
          icon={<CalendarClock className="size-3.5" />}
          title="Questions that answer themselves"
          body="Save a question and Numera re-asks it on a cadence against whatever data has arrived since, then delivers the written answer to Slack or your inbox — with a link to the full analysis, so nobody has to take the number on faith."
        />

        <p className="mt-4 flex items-start gap-2 rounded-lg border border-line bg-subtle p-3 text-[12.5px] leading-relaxed text-ink-2">
          <Info className="mt-0.5 size-3.5 shrink-0 text-ink-3" />
          <span>
            A <strong className="font-medium text-ink">monitor</strong> re-runs snapshotted code
            to watch one number and costs no model tokens. A{" "}
            <strong className="font-medium text-ink">briefing</strong> re-runs the whole agent to
            answer a question in English, so each run costs roughly one analysis.
            {intervalMinutes
              ? ` The scheduler sweeps every ${intervalMinutes} minutes.`
              : " The background scheduler is off (BRIEFING_INTERVAL_MINUTES=0), so briefings run when you ask them to."}
          </span>
        </p>

        <NewBriefing datasets={datasets} onCreated={refresh} />

        {loading ? (
          <div className="mt-6 h-28 animate-pulse rounded-xl bg-muted" />
        ) : briefings.length === 0 ? (
          <div className="mt-6">
            <EmptyState
              icon={<CalendarClock className="size-5" />}
              title="No scheduled briefings"
              body="Save the question you find yourself asking every Monday and let it answer itself."
            />
          </div>
        ) : (
          <div className="mt-6 space-y-3">
            {briefings.map((briefing) => (
              <article
                key={briefing.id}
                className="rounded-xl border border-line bg-panel p-4 shadow-card"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-[15px] font-semibold text-ink">
                        {briefing.title}
                      </h3>
                      <Badge tone="neutral">{cadenceLabel(briefing.schedule_hours)}</Badge>
                      {!briefing.enabled && <Badge tone="warn">Paused</Badge>}
                      {briefing.deliver && <Badge tone="accent">Delivered</Badge>}
                      {briefing.last_status === "ok" && (
                        <Badge tone="good">
                          <CircleCheck className="size-3" />
                          Ran
                        </Badge>
                      )}
                      {briefing.last_status === "error" && (
                        <Badge tone="bad">
                          <TriangleAlert className="size-3" />
                          Failed
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
                      “{briefing.question}”
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button size="sm" onClick={() => void run(briefing)} loading={running === briefing.id}>
                      <Send className="size-3.5" />
                      Run now
                    </Button>
                    <IconButton
                      label={briefing.enabled ? "Pause this briefing" : "Resume this briefing"}
                      size="sm"
                      onClick={() => void toggle(briefing)}
                    >
                      {briefing.enabled ? (
                        <Pause className="size-3.5" />
                      ) : (
                        <Play className="size-3.5" />
                      )}
                    </IconButton>
                    <IconButton
                      label="Delete this briefing"
                      size="sm"
                      className="hover:bg-bad-soft hover:text-bad"
                      onClick={() => void remove(briefing)}
                    >
                      <Trash2 className="size-3.5" />
                    </IconButton>
                  </div>
                </div>

                {briefing.last_headline && (
                  <p className="mt-2.5 rounded-lg bg-subtle p-2.5 text-[13px] leading-relaxed text-ink">
                    {briefing.last_headline}
                  </p>
                )}
                {briefing.last_error && (
                  <p className="mt-2.5 rounded-lg border border-bad/30 bg-bad-soft p-2.5 text-[12.5px] leading-relaxed text-ink">
                    {briefing.last_error}
                  </p>
                )}

                <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-ink-3">
                  <span>{briefing.dataset_name ?? "Dataset"}</span>
                  {briefing.last_run_at && <span>Last run {relativeTime(briefing.last_run_at)}</span>}
                  {briefing.enabled && briefing.next_run_at && !briefing.due && (
                    <span>
                      Next {new Date(briefing.next_run_at).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "numeric",
                        minute: "2-digit",
                      })}
                    </span>
                  )}
                  {briefing.due && briefing.enabled && <span className="text-warn">Due now</span>}
                  {briefing.run_count > 0 && (
                    <span className="tabular-nums">{briefing.run_count} runs</span>
                  )}
                  {briefing.last_session_id && (
                    <button
                      onClick={() => onOpenSession(briefing.last_session_id!)}
                      className="text-accent underline-offset-2 hover:underline"
                    >
                      Open the full analysis
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function NewBriefing({
  datasets,
  onCreated,
}: {
  datasets: DatasetSummary[];
  onCreated: () => Promise<void>;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [datasetId, setDatasetId] = useState(datasets[0]?.id ?? "");
  const [question, setQuestion] = useState("");
  const [title, setTitle] = useState("");
  const [hours, setHours] = useState("24");
  const [deliver, setDeliver] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!datasetId && datasets.length) setDatasetId(datasets[0].id);
  }, [datasets, datasetId]);

  if (!datasets.length) return null;

  if (!open) {
    return (
      <Button variant="primary" className="mt-6" onClick={() => setOpen(true)}>
        <CalendarClock className="size-4" />
        Schedule a question
      </Button>
    );
  }

  const save = async () => {
    setBusy(true);
    try {
      await api.createBriefing({
        dataset_id: datasetId,
        question,
        title: title || undefined,
        schedule_hours: Number(hours),
        deliver,
      });
      toast.success("Briefing scheduled", "Run it now to see the first answer.");
      await onCreated();
      setQuestion("");
      setTitle("");
      setOpen(false);
    } catch (error) {
      toast.error("Couldn't schedule the briefing", describe(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6 rounded-xl border border-line bg-panel p-4 shadow-card">
      <SectionLabel icon={<CalendarClock className="size-3.5" />}>New briefing</SectionLabel>
      <label className="flex flex-col gap-1">
        <span className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">Question</span>
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          maxLength={1000}
          placeholder="How is revenue tracking against last month, and which region moved most?"
          className="w-full rounded-lg border border-line bg-panel p-2.5 text-[13.5px] leading-relaxed text-ink transition placeholder:text-ink-3 focus:border-accent focus:outline-none"
        />
      </label>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <TextInput
          label="Title"
          value={title}
          placeholder="Defaults to the question"
          onChange={(event) => setTitle(event.target.value)}
        />
        <Select
          label="Dataset"
          value={datasetId}
          onChange={setDatasetId}
          options={datasets.map((d) => ({ value: d.id, label: d.name }))}
        />
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Select label="Cadence" value={hours} onChange={setHours} options={CADENCES} />
        <label className="mb-2 flex items-center gap-2 text-[13px] text-ink">
          <input
            type="checkbox"
            checked={deliver}
            onChange={(event) => setDeliver(event.target.checked)}
            className="size-4 accent-accent"
          />
          Deliver to alert channels
        </label>
        <Button variant="primary" onClick={() => void save()} loading={busy} disabled={!question.trim()}>
          Schedule
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
