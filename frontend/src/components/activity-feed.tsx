"use client";

import {
  Bell,
  CloudUpload,
  Compass,
  Database,
  Dot,
  LayoutDashboard,
  MessageSquare,
  Clock,
  MessageCircle,
  Pin,
  Share2,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { SectionLabel } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { ActivityEntry } from "@/lib/types";

const ICONS: Record<string, React.ReactNode> = {
  upload: <CloudUpload className="size-3.5" />,
  database: <Database className="size-3.5" />,
  message: <MessageSquare className="size-3.5" />,
  compass: <Compass className="size-3.5" />,
  clock: <Clock className="size-3.5" />,
  pin: <Pin className="size-3.5" />,
  share: <Share2 className="size-3.5" />,
  bell: <Bell className="size-3.5" />,
  shield: <ShieldCheck className="size-3.5" />,
  comment: <MessageCircle className="size-3.5" />,
  trash: <Trash2 className="size-3.5" />,
  dot: <Dot className="size-3.5" />,
};

/**
 * What has happened in this workspace, newest first.
 *
 * Two people sharing a workspace need to know that the dataset under a board was replaced
 * this morning — none of which is visible from a list of analyses sorted by date.
 */
export function ActivityFeed({ limit = 40, onOpen }: { limit?: number; onOpen?: (entry: ActivityEntry) => void }) {
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null);

  const refresh = useCallback(async () => {
    try {
      setEntries(await api.activity(limit));
    } catch {
      setEntries([]); // the feed is context, never a blocker
    }
  }, [limit]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!entries) {
    return <div className="h-24 animate-pulse rounded-xl bg-muted" />;
  }

  if (entries.length === 0) {
    return (
      <p className="rounded-xl border border-line bg-subtle p-4 text-[12.5px] leading-relaxed text-ink-2">
        Nothing has happened here yet. Uploads, analyses, pins, syncs and breached monitors all
        show up in this feed.
      </p>
    );
  }

  return (
    <div>
      <SectionLabel>Recent activity</SectionLabel>
      <ul className="space-y-0.5">
        {entries.map((entry) => {
          const clickable = Boolean(onOpen && entry.subject_id);
          const Row = clickable ? "button" : "div";
          return (
            <li key={entry.id}>
              <Row
                {...(clickable ? { onClick: () => onOpen?.(entry), type: "button" as const } : {})}
                className={[
                  "flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left",
                  clickable ? "transition hover:bg-muted" : "",
                ].join(" ")}
              >
                <span className="mt-0.5 shrink-0 text-ink-3">{ICONS[entry.icon] ?? ICONS.dot}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-ink">{entry.summary}</span>
                  {entry.detail && (
                    <span className="block truncate text-[11.5px] text-ink-3">{entry.detail}</span>
                  )}
                </span>
                <span className="shrink-0 pt-0.5 text-[11px] whitespace-nowrap text-ink-3">
                  {relativeTime(entry.created_at)}
                </span>
              </Row>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
