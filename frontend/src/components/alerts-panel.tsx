"use client";

import {
  Check,
  LoaderCircle,
  Mail,
  Pause,
  Play,
  Send,
  MessageSquareText,
  Trash,
  TriangleAlert,
  Webhook,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, IconButton, SectionLabel } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import type { AlertChannel, AlertEvent, AlertKind, AlertSettings } from "@/lib/types";

const KINDS: Record<AlertKind, { label: string; icon: typeof Webhook; placeholder: string }> = {
  slack: { label: "Slack", icon: MessageSquareText, placeholder: "https://hooks.slack.com/services/…" },
  webhook: { label: "Webhook", icon: Webhook, placeholder: "https://example.com/hooks/numera" },
  email: { label: "Email", icon: Mail, placeholder: "alerts@yourcompany.com" },
};

const EVENT_LABELS: Record<AlertEvent, string> = {
  breach: "Breach",
  recovery: "Recovery",
  failure: "Check failed",
  contract: "Contract broken",
  digest: "Monitor digest",
  briefing: "Scheduled briefing",
};

const EVENT_HINTS: Record<AlertEvent, string> = {
  breach: "a watched metric crosses its threshold",
  recovery: "it comes back inside the range",
  failure: "the snapshotted analysis stops running",
  contract: "an upload breaks the dataset's data contract",
  digest: "the periodic summary of every monitor",
  briefing: "a saved question re-runs on its cadence and has an answer",
};

const DEFAULT_EVENTS: AlertEvent[] = ["breach", "failure", "contract"];

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

/** Where breaches, recoveries and broken contracts are delivered. */
export function AlertsPanel({ emailConfigured }: { emailConfigured?: boolean }) {
  const toast = useToast();
  const [settings, setSettings] = useState<AlertSettings | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setSettings(await api.alertSettings());
    } catch {
      /* alerts are an optional surface; the monitors list stays usable without them */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const guard = async (key: string, action: () => Promise<void>, failure: string) => {
    setBusy(key);
    try {
      await action();
      await load();
    } catch (error) {
      toast.error(failure, describe(error));
    } finally {
      setBusy(null);
    }
  };

  if (!settings) return null;

  const emailReady = emailConfigured ?? settings.email_configured;

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <SectionLabel icon={<Send className="size-3.5" />}>Alert delivery</SectionLabel>
          <p className="-mt-1 max-w-2xl text-[13px] leading-relaxed text-ink-2">
            Numera only sends on a state change — a metric crossing its line, coming back, or an
            upload breaking its contract — so a persistent breach never becomes a repeating ping.
          </p>
        </div>
        <div className="flex gap-2">
          {settings.channels.some((c) => c.events.includes("digest") && c.enabled) && (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy !== null}
              onClick={() =>
                void guard("digest", async () => {
                  const { sent } = await api.sendDigest();
                  toast.success(`Briefing sent to ${sent} channel${sent === 1 ? "" : "s"}`);
                }, "Couldn't send the briefing")
              }
            >
              <Send className="size-3.5" />
              Send briefing
            </Button>
          )}
          <Button size="sm" onClick={() => setAdding((open) => !open)} aria-expanded={adding}>
            Add channel
          </Button>
        </div>
      </div>

      {!settings.enabled && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[12.5px] text-ink">
          <TriangleAlert className="mt-px size-3.5 shrink-0 text-warn" />
          Delivery is switched off on the server. Set <code className="font-mono">ALERTS_ENABLED=true</code>{" "}
          and restart the backend.
        </p>
      )}

      {adding && (
        <NewChannelForm
          emailReady={emailReady}
          busy={busy === "create"}
          onCancel={() => setAdding(false)}
          onCreate={(body) =>
            void guard("create", async () => {
              await api.createChannel(body);
              setAdding(false);
              toast.success("Channel added", "Send a test to make sure it arrives.");
            }, "Couldn't add the channel")
          }
        />
      )}

      <div className="mt-4 space-y-2.5">
        {settings.channels.length === 0 && !adding && (
          <p className="rounded-xl border border-dashed border-line-strong px-4 py-6 text-center text-[13px] text-ink-2">
            No channels yet. Add a Slack or generic webhook — or an email address once SMTP is
            configured — and breaches will reach you without anyone opening this tab.
          </p>
        )}
        {settings.channels.map((channel) => (
          <ChannelRow
            key={channel.id}
            channel={channel}
            busy={busy === channel.id}
            disabled={busy !== null}
            onToggle={() =>
              void guard(channel.id, async () => {
                await api.updateChannel(channel.id, { enabled: !channel.enabled });
              }, "Couldn't update the channel")
            }
            onEvents={(events) =>
              void guard(channel.id, async () => {
                await api.updateChannel(channel.id, { events });
              }, "Couldn't update the subscriptions")
            }
            onTest={() =>
              void guard(channel.id, async () => {
                await api.testChannel(channel.id);
                toast.success(`Test alert sent to ${channel.name}`);
              }, `Test alert to ${channel.name} failed`)
            }
            onRemove={() => {
              if (!window.confirm(`Remove “${channel.name}”?`)) return;
              void guard(channel.id, async () => {
                await api.deleteChannel(channel.id);
              }, "Couldn't remove the channel");
            }}
          />
        ))}
      </div>

      {settings.deliveries.length > 0 && (
        <div className="mt-5">
          <SectionLabel>Recent deliveries</SectionLabel>
          <div className="space-y-1">
            {settings.deliveries.slice(0, 6).map((delivery) => (
              <div key={delivery.id} className="flex items-center gap-2 text-[12.5px]">
                <span
                  className={cn(
                    "size-1.5 shrink-0 rounded-full",
                    delivery.status === "sent" ? "bg-good" : "bg-bad",
                  )}
                />
                <span className="min-w-0 flex-1 truncate text-ink-2" title={delivery.detail ?? ""}>
                  {delivery.title}
                  {delivery.channel_name ? ` → ${delivery.channel_name}` : ""}
                  {delivery.status === "failed" && delivery.detail ? ` — ${delivery.detail}` : ""}
                </span>
                <span className="shrink-0 text-ink-3">{relativeTime(delivery.created_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function ChannelRow({
  channel,
  busy,
  disabled,
  onToggle,
  onEvents,
  onTest,
  onRemove,
}: {
  channel: AlertChannel;
  busy: boolean;
  disabled: boolean;
  onToggle: () => void;
  onEvents: (events: AlertEvent[]) => void;
  onTest: () => void;
  onRemove: () => void;
}) {
  const kind = KINDS[channel.kind];
  const Icon = kind.icon;

  const toggleEvent = (event: AlertEvent) => {
    const next = channel.events.includes(event)
      ? channel.events.filter((e) => e !== event)
      : [...channel.events, event];
    if (next.length) onEvents(next);
  };

  return (
    <div
      className={cn(
        "rounded-xl border bg-panel p-3.5 shadow-card",
        channel.last_status === "failed" ? "border-bad/30" : "border-line",
        !channel.enabled && "opacity-60",
      )}
    >
      <div className="flex flex-wrap items-start gap-3">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted text-ink-2">
          <Icon className="size-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[13.5px] font-semibold text-ink">{channel.name}</p>
            {!channel.enabled && <Badge>Paused</Badge>}
            {channel.last_status === "failed" && <Badge tone="bad">Last send failed</Badge>}
            {channel.last_status === "sent" && <Badge tone="good">Delivering</Badge>}
          </div>
          <p className="truncate text-[11.5px] text-ink-3" title={channel.target}>
            {channel.target}
            {channel.last_sent_at && ` · last sent ${relativeTime(channel.last_sent_at)}`}
            {channel.sent_count > 0 && ` · ${channel.sent_count} total`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton label="Send a test alert" size="sm" onClick={onTest} disabled={disabled}>
            {busy ? <LoaderCircle className="size-3.5 animate-spin" /> : <Send className="size-3.5" />}
          </IconButton>
          <IconButton
            label={channel.enabled ? "Pause this channel" : "Resume this channel"}
            size="sm"
            onClick={onToggle}
            disabled={disabled}
          >
            {channel.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          </IconButton>
          <IconButton
            label="Remove this channel"
            size="sm"
            className="hover:bg-bad-soft hover:text-bad"
            onClick={onRemove}
            disabled={disabled}
          >
            <Trash className="size-3.5" />
          </IconButton>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {(Object.keys(EVENT_LABELS) as AlertEvent[]).map((event) => {
          const on = channel.events.includes(event);
          return (
            <button
              key={event}
              onClick={() => toggleEvent(event)}
              disabled={disabled}
              title={`Send when ${EVENT_HINTS[event]}`}
              aria-pressed={on}
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11.5px] font-medium transition disabled:opacity-50",
                on ? "bg-accent-soft text-accent-ink" : "bg-muted text-ink-3 hover:text-ink",
              )}
            >
              {on && <Check className="size-3" />}
              {EVENT_LABELS[event]}
            </button>
          );
        })}
      </div>

      {channel.last_status === "failed" && channel.last_error && (
        <p className="mt-2.5 text-[12px] leading-relaxed text-bad">{channel.last_error}</p>
      )}
    </div>
  );
}

function NewChannelForm({
  emailReady,
  busy,
  onCreate,
  onCancel,
}: {
  emailReady: boolean;
  busy: boolean;
  onCreate: (body: { kind: AlertKind; target: string; name?: string; events: AlertEvent[] }) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<AlertKind>("slack");
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (target.trim()) {
          onCreate({ kind, target: target.trim(), name: name.trim() || undefined, events: DEFAULT_EVENTS });
        }
      }}
      className="mt-4 rounded-xl border border-line bg-panel p-3.5 shadow-card"
    >
      <div className="flex flex-wrap gap-2">
        {(Object.keys(KINDS) as AlertKind[]).map((option) => {
          const Icon = KINDS[option].icon;
          const unavailable = option === "email" && !emailReady;
          return (
            <button
              key={option}
              type="button"
              onClick={() => setKind(option)}
              disabled={unavailable}
              title={unavailable ? "Set SMTP_HOST in the backend .env to enable email" : undefined}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[13px] transition disabled:opacity-40",
                kind === option ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-ink-2 hover:bg-muted",
              )}
            >
              <Icon className="size-3.5" />
              {KINDS[option].label}
            </button>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <input
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          placeholder={KINDS[kind].placeholder}
          required
          className="h-9 min-w-0 flex-[2] rounded-lg border border-line bg-canvas px-2.5 text-[13px] text-ink outline-none placeholder:text-ink-3 focus-visible:border-accent"
        />
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Name (optional)"
          maxLength={120}
          className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-canvas px-2.5 text-[13px] text-ink outline-none placeholder:text-ink-3 focus-visible:border-accent"
        />
        <Button type="submit" variant="primary" size="sm" loading={busy}>
          Add
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      <p className="mt-2 text-[12px] text-ink-3">
        New channels receive breaches, failed checks and broken contracts. Adjust that below once added.
      </p>
    </form>
  );
}
