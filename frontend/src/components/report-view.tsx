"use client";

import { CircleAlert, Printer } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { BoardGrid } from "@/components/boards/board-grid";
import { LogoMark } from "@/components/brand";
import { AssistantMessage } from "@/components/chat/assistant-message";
import { Markdown } from "@/components/markdown";
import { Badge, Button } from "@/components/ui/primitives";
import { ApiError, api } from "@/lib/api";
import { useTheme } from "@/lib/theme";
import type { SharedDocument } from "@/lib/types";

export type ReportSource = { token: string } | { sessionId: string } | { boardId: string };

async function load(source: ReportSource): Promise<SharedDocument> {
  if ("token" in source) return api.getShared(source.token);
  if ("sessionId" in source) {
    const session = await api.getSession(source.sessionId);
    return { type: "session", title: session.title, dataset_name: session.dataset_name,
             updated_at: session.updated_at, messages: session.messages };
  }
  const board = await api.getBoard(source.boardId);
  return { type: "board", title: board.title, description: board.description, updated_at: board.updated_at,
           items: board.items };
}

/** Clean, print-optimised document view used for share links and PDF export. */
export function ReportView({ source, autoPrint = false, readOnlyBadge = true }: {
  source: ReportSource;
  autoPrint?: boolean;
  readOnlyBadge?: boolean;
}) {
  const { mode, resolved, setMode } = useTheme();
  const [doc, setDoc] = useState<SharedDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const printed = useRef(false);

  useEffect(() => {
    load(source)
      .then(setDoc)
      .catch((e) => setError(e instanceof ApiError ? e.message : "This report could not be loaded."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(source)]);

  const print = useCallback(() => {
    // Charts print best on a light surface; switch temporarily, then restore the viewer's choice.
    const previous = mode;
    const needsLight = resolved === "dark";
    if (needsLight) setMode("light");
    window.setTimeout(() => {
      window.print();
      if (needsLight) setMode(previous);
    }, needsLight ? 900 : 50);
  }, [mode, resolved, setMode]);

  useEffect(() => {
    if (!doc || !autoPrint || printed.current) return;
    printed.current = true;
    const timer = window.setTimeout(print, 1800);
    return () => window.clearTimeout(timer);
  }, [doc, autoPrint, print]);

  const updated = doc ? new Date(doc.updated_at).toLocaleDateString(undefined, { dateStyle: "long" }) : "";

  return (
    <div className="min-h-dvh bg-canvas">
      <header className="no-print sticky top-0 z-20 border-b border-line bg-panel/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-2.5 px-4 sm:px-6">
          <LogoMark className="size-7" />
          <span className="text-[15px] font-semibold tracking-tight text-ink">Numera</span>
          {readOnlyBadge && <Badge className="ml-1">Read-only</Badge>}
          <Button className="ml-auto" size="sm" onClick={print} disabled={!doc}>
            <Printer className="size-3.5" />
            Print / Save PDF
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        {error && (
          <div className="mx-auto mt-10 max-w-md rounded-2xl border border-line bg-panel p-8 text-center shadow-card">
            <CircleAlert className="mx-auto size-8 text-ink-3" />
            <p className="mt-3 text-[15px] font-medium text-ink">Report unavailable</p>
            <p className="mt-1 text-[13px] text-ink-2">{error}</p>
          </div>
        )}
        {!doc && !error && (
          <div className="space-y-4">
            <div className="h-8 w-1/2 animate-pulse rounded bg-muted" />
            <div className="h-64 animate-pulse rounded-xl bg-muted" />
          </div>
        )}

        {/* A board needs the full grid width; a written report reads as a centred column. */}
        {doc && (
          <div className={doc.type === "board" ? undefined : "mx-auto max-w-3xl"}>
            <div className="print-avoid-break">
              <p className="text-xs font-medium tracking-wide text-ink-3 uppercase">
                {doc.type === "board" ? "Dashboard" : "Analysis report"}
              </p>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight text-balance text-ink sm:text-4xl">{doc.title}</h1>
              <p className="mt-2 text-[14px] text-ink-2">
                {doc.type === "session" ? `${doc.dataset_name} · ` : ""}Updated {updated}
              </p>
              {doc.type === "board" && doc.description && (
                <div className="mt-3 max-w-3xl text-ink-2">
                  <Markdown>{doc.description}</Markdown>
                </div>
              )}
            </div>

            {doc.type === "board" ? (
              <div className="mt-10">
                {doc.items.length ? (
                  <BoardGrid items={doc.items} />
                ) : (
                  <p className="text-[14px] text-ink-3">This board has no items yet.</p>
                )}
              </div>
            ) : (
              <div className="mt-6">
                {doc.messages.map((message) =>
                  message.role === "user" ? (
                    <h2
                      key={message.id}
                      className="print-avoid-break mt-12 border-t border-line pt-8 text-xl leading-snug font-semibold tracking-tight text-ink first:mt-6"
                    >
                      {message.content}
                    </h2>
                  ) : (
                    <div key={message.id} className="mt-6">
                      <AssistantMessage message={message} isLast={false} />
                    </div>
                  ),
                )}
              </div>
            )}
          </div>
        )}
      </main>

      <footer className="mx-auto max-w-6xl border-t border-line px-4 py-6 text-[12px] text-ink-3 sm:px-6">
        Generated with Numera — every figure is computed by analysis code executed against the source data.
      </footer>
    </div>
  );
}
