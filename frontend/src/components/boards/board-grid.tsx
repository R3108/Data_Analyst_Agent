"use client";

import { MessageSquare } from "lucide-react";

import { ChartCard } from "@/components/chat/chart-card";
import { InsightCard } from "@/components/chat/insight-card";
import { KpiTile } from "@/components/chat/kpi-grid";
import { TableCard } from "@/components/chat/table-card";
import { Markdown } from "@/components/markdown";
import { cn } from "@/lib/cn";
import type { BoardItem } from "@/lib/types";

function span(item: BoardItem): string {
  if (item.kind === "kpi") return item.wide ? "col-span-12 sm:col-span-6" : "col-span-6 lg:col-span-3";
  return item.wide ? "col-span-12" : "col-span-12 xl:col-span-6";
}

export function BoardGrid({
  items,
  renderToolbar,
  renderNote,
  onOpenSource,
}: {
  items: BoardItem[];
  renderToolbar?: (item: BoardItem, index: number) => React.ReactNode;
  renderNote?: (item: Extract<BoardItem, { kind: "note" }>) => React.ReactNode;
  onOpenSource?: (sessionId: string) => void;
}) {
  return (
    <div className="grid grid-flow-row-dense grid-cols-12 gap-x-4 gap-y-6">
      {items.map((item, index) => (
        <div key={item.id} className={cn("group relative flex min-w-0 flex-col", span(item))}>
          {renderToolbar && (
            <div className="no-print absolute -top-3 right-3 z-20 flex items-center gap-0.5 rounded-lg border border-line bg-panel p-0.5 shadow-card transition sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
              {renderToolbar(item, index)}
            </div>
          )}
          {/* Children stretch to the row height here so cards in one row line up; in the chat
              flow they size to their own content instead. */}
          <div className="min-h-0 min-w-0 flex-1 [&>*]:h-full">
            <ItemBody item={item} renderNote={renderNote} />
          </div>
          {item.source_question && (
            <button
              type="button"
              disabled={!onOpenSource || !item.source_session_id}
              onClick={() => item.source_session_id && onOpenSource?.(item.source_session_id)}
              className="mt-1.5 flex w-full min-w-0 items-center gap-1.5 text-left text-[11.5px] text-ink-3 enabled:hover:text-ink disabled:cursor-default"
              title={item.source_question}
            >
              <MessageSquare className="size-3 shrink-0" />
              <span className="truncate">
                {item.source_question}
                {item.dataset_name ? ` · ${item.dataset_name}` : ""}
              </span>
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function ItemBody({
  item,
  renderNote,
}: {
  item: BoardItem;
  renderNote?: (item: Extract<BoardItem, { kind: "note" }>) => React.ReactNode;
}) {
  switch (item.kind) {
    case "kpi":
      return <KpiTile kpi={{ ...item.content, label: item.title }} />;
    case "chart":
      return <ChartCard chart={{ ...item.content, title: item.title }} />;
    case "table":
      return <TableCard table={{ ...item.content, title: item.title }} />;
    case "insight":
      return <InsightCard insight={item.content} />;
    case "note":
      return renderNote ? (
        renderNote(item)
      ) : (
        <div className="print-avoid-break h-full rounded-xl border border-line bg-panel p-4 shadow-card">
          <Markdown>{item.content.text}</Markdown>
        </div>
      );
  }
}
