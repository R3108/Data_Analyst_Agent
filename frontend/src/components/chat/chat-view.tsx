"use client";

import { Compass } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import { Button } from "@/components/ui/primitives";
import type { Dataset, RecallSummary, SessionDetail, Step } from "@/lib/types";

import { AgentTimeline } from "./agent-timeline";
import { AssistantAvatar, AssistantMessage } from "./assistant-message";
import { Composer, type ComposerHandle } from "./composer";
import { DatasetIntro } from "./dataset-intro";
import { RecallNotice } from "./recall-notice";

export interface PendingTurn {
  question: string;
  userMessageId?: string;
  steps: Step[];
  /** Arrives before the work starts, so a repeat question can be stopped early. */
  recall?: RecallSummary;
  investigating?: boolean;
}

export function ChatView({
  session,
  dataset,
  pending,
  onSend,
  onStop,
  onInvestigate,
  onOpenCleaning,
  onExplain,
  onOpenSession,
  budgetExhausted = false,
}: {
  session: SessionDetail;
  dataset: Dataset | null;
  pending: PendingTurn | null;
  onSend: (text: string) => void;
  onStop: () => void;
  onInvestigate?: (objective: string | null) => void;
  onOpenCleaning: () => void;
  onExplain?: (query: { measure: string; focus?: string }) => void;
  onOpenSession?: (sessionId: string) => void;
  budgetExhausted?: boolean;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const composer = useRef<ComposerHandle>(null);
  const messages = session.messages;
  const streaming = pending !== null;

  useEffect(() => {
    stickToBottom.current = true;
    composer.current?.focus();
  }, [session.id]);

  useEffect(() => {
    const el = scroller.current;
    if (el && stickToBottom.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages.length, pending?.steps]);

  const elapsed = useMemo(() => {
    const result: Record<string, number> = {};
    messages.forEach((message, i) => {
      const previous = messages[i - 1];
      if (message.role === "assistant" && previous?.role === "user") {
        result[message.id] = new Date(message.created_at).getTime() - new Date(previous.created_at).getTime();
      }
    });
    return result;
  }, [messages]);

  const lastAssistantIndex = messages.map((m) => m.role).lastIndexOf("assistant");
  const questionBefore = (index: number) =>
    [...messages.slice(0, index)].reverse().find((m) => m.role === "user")?.content;

  const send = (text: string) => {
    if (budgetExhausted) return;
    stickToBottom.current = true;
    onSend(text);
  };

  /** Jump to a sub-analysis from an investigation's step list. */
  const scrollToMessage = (messageId: string) => {
    stickToBottom.current = false;
    const element = document.getElementById(`message-${messageId}`);
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scroller}
        onScroll={(event) => {
          const el = event.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
        }}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto w-full max-w-3xl space-y-8 px-4 pt-6 pb-10 sm:px-6">
          {messages.length === 0 && !pending && dataset && (
            <DatasetIntro
              dataset={dataset}
              onAsk={send}
              onOpenCleaning={onOpenCleaning}
              onExplain={onExplain}
              disabled={budgetExhausted}
            />
          )}

          {messages.map((message, index) =>
            message.role === "user" ? (
              <UserBubble key={message.id} text={message.content} />
            ) : (
              <AssistantMessage
                key={message.id}
                message={message}
                isLast={index === lastAssistantIndex && !streaming}
                elapsedMs={elapsed[message.id]}
                onFollowUp={budgetExhausted ? undefined : send}
                onOpenSession={onOpenSession}
                onOpenMessage={scrollToMessage}
                onRetry={
                  !budgetExhausted && index === lastAssistantIndex && !streaming && message.status !== "complete"
                    ? () => {
                        const question = questionBefore(index);
                        if (question) send(question);
                      }
                    : undefined
                }
              />
            ),
          )}

          {pending && (
            <>
              {!pending.userMessageId && <UserBubble text={pending.question} />}
              <div className="animate-rise flex gap-3">
                <AssistantAvatar />
                <div className="min-w-0 flex-1 space-y-3">
                  {pending.recall && (
                    <RecallNotice recall={pending.recall} onOpen={onOpenSession} />
                  )}
                  <AgentTimeline
                    steps={
                      pending.steps.length
                        ? pending.steps
                        : [
                            {
                              id: "start",
                              label: pending.investigating
                                ? "Scoping the investigation"
                                : "Starting analysis",
                              status: "running",
                            },
                          ]
                    }
                    live
                  />
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="relative shrink-0 px-4 pt-2 pb-4 sm:px-6">
        <div className="pointer-events-none absolute inset-x-0 -top-8 h-8 bg-gradient-to-t from-canvas to-transparent" />
        {onInvestigate && !streaming && !budgetExhausted && (
          <div className="mx-auto mb-2 flex max-w-3xl justify-end">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onInvestigate(null)}
              title="Scope an objective into several analyses, run them all, and write one brief"
            >
              <Compass className="size-3.5" />
              Investigate
            </Button>
          </div>
        )}
        <Composer
          ref={composer}
          onSend={send}
          onStop={onStop}
          streaming={streaming}
          disabled={budgetExhausted}
          placeholder={
            budgetExhausted
              ? "Monthly AI budget reached"
              : dataset
                ? `Ask anything about ${dataset.name}…`
                : "Ask a question about your data…"
          }
        />
      </div>
    </div>
  );
}

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="animate-rise flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap text-white shadow-card">
        {text}
      </div>
    </div>
  );
}
