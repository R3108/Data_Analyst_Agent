"use client";

import { Check, CornerDownRight, MessageCircle, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, IconButton } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import type { CommentListing, CommentSubject, CommentThread } from "@/lib/types";

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Couldn't save that. Please try again.";
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

/**
 * A discussion anchored to the artifact it is about.
 *
 * The decision an analysis feeds into is made in a conversation, and that conversation
 * otherwise happens somewhere the numbers are not — where nobody can see which version of
 * the data the figure came from.
 */
export function CommentsPanel({
  subjectKind,
  subjectId,
  author,
  title,
  className,
}: {
  subjectKind: CommentSubject;
  subjectId: string;
  author: string;
  title?: string;
  className?: string;
}) {
  const toast = useToast();
  const [listing, setListing] = useState<CommentListing | null>(null);
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [replyDraft, setReplyDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setListing(await api.listComments(subjectKind, subjectId));
    } catch {
      /* comments are supplementary; a failure here must not break the page */
    }
  }, [subjectKind, subjectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const post = async (body: string, parentId?: string) => {
    if (!body.trim()) return;
    setBusy(true);
    try {
      await api.addComment({
        subject_kind: subjectKind,
        subject_id: subjectId,
        body,
        parent_id: parentId,
        author,
      });
      setDraft("");
      setReplyDraft("");
      setReplyTo(null);
      await refresh();
    } catch (error) {
      toast.error("Couldn't post the comment", describe(error));
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (thread: CommentThread) => {
    try {
      await api.updateComment(thread.id, { resolved: !thread.resolved });
      await refresh();
    } catch (error) {
      toast.error("Couldn't update the thread", describe(error));
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deleteComment(id);
      await refresh();
    } catch (error) {
      toast.error("Couldn't delete the comment", describe(error));
    }
  };

  const threads = listing?.threads ?? [];

  return (
    <section className={cn("rounded-xl border border-line bg-panel p-4 shadow-card", className)}>
      <div className="mb-3 flex items-center gap-2">
        <MessageCircle className="size-3.5 text-ink-3" />
        <h3 className="text-xs font-medium tracking-wide text-ink-3 uppercase">
          {title ?? "Discussion"}
        </h3>
        {listing && listing.open_count > 0 && (
          <Badge tone="accent">{listing.open_count} open</Badge>
        )}
      </div>

      {threads.length > 0 && (
        <ul className="mb-3 space-y-3">
          {threads.map((thread) => (
            <li
              key={thread.id}
              className={cn(
                "rounded-lg border p-3",
                thread.resolved ? "border-line bg-subtle opacity-70" : "border-line bg-canvas",
              )}
            >
              <CommentBody
                author={thread.author}
                body={thread.body}
                createdAt={thread.created_at}
                resolved={thread.resolved}
                onDelete={() => void remove(thread.id)}
                onResolve={() => void resolve(thread)}
              />

              {thread.replies.length > 0 && (
                <ul className="mt-2.5 space-y-2.5 border-l-2 border-line pl-3">
                  {thread.replies.map((reply) => (
                    <li key={reply.id}>
                      <CommentBody
                        author={reply.author}
                        body={reply.body}
                        createdAt={reply.created_at}
                        onDelete={() => void remove(reply.id)}
                      />
                    </li>
                  ))}
                </ul>
              )}

              {!thread.resolved &&
                (replyTo === thread.id ? (
                  <form
                    className="mt-2.5 flex gap-1.5"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void post(replyDraft, thread.id);
                    }}
                  >
                    <input
                      autoFocus
                      value={replyDraft}
                      onChange={(event) => setReplyDraft(event.target.value)}
                      placeholder="Reply…"
                      maxLength={4000}
                      className="h-8 min-w-0 flex-1 rounded-md bg-muted px-2 text-[13px] text-ink outline-none placeholder:text-ink-3"
                    />
                    <IconButton label="Post reply" size="sm" type="submit" disabled={busy}>
                      <Send className="size-3.5" />
                    </IconButton>
                  </form>
                ) : (
                  <button
                    onClick={() => {
                      setReplyTo(thread.id);
                      setReplyDraft("");
                    }}
                    className="mt-2 inline-flex items-center gap-1 text-[12px] text-ink-3 transition hover:text-ink"
                  >
                    <CornerDownRight className="size-3" />
                    Reply
                  </button>
                ))}
            </li>
          ))}
        </ul>
      )}

      <form
        className="flex gap-1.5"
        onSubmit={(event) => {
          event.preventDefault();
          void post(draft);
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={threads.length ? "Add a comment…" : "Start the discussion…"}
          maxLength={4000}
          className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-panel px-2.5 text-[13px] text-ink transition outline-none placeholder:text-ink-3 focus:border-accent"
        />
        <IconButton label="Post comment" type="submit" disabled={busy || !draft.trim()}>
          <Send className="size-4" />
        </IconButton>
      </form>
    </section>
  );
}

function CommentBody({
  author,
  body,
  createdAt,
  resolved,
  onDelete,
  onResolve,
}: {
  author: string;
  body: string;
  createdAt: string;
  resolved?: boolean;
  onDelete: () => void;
  onResolve?: () => void;
}) {
  return (
    <div className="group/comment flex gap-2.5">
      <span
        className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[10px] font-semibold text-accent-ink"
        aria-hidden
      >
        {initials(author)}
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-[13px] font-medium text-ink">{author}</span>
          <span className="text-[11px] text-ink-3">{relativeTime(createdAt)}</span>
          {resolved && <Badge tone="good">Resolved</Badge>}
        </p>
        <p className="mt-0.5 text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">{body}</p>
      </div>
      <div className="flex shrink-0 items-start gap-0.5 opacity-0 transition group-hover/comment:opacity-100 focus-within:opacity-100">
        {onResolve && (
          <IconButton
            label={resolved ? "Reopen this thread" : "Resolve this thread"}
            size="sm"
            onClick={onResolve}
          >
            <Check className={cn("size-3.5", resolved && "text-good")} />
          </IconButton>
        )}
        <IconButton
          label="Delete"
          size="sm"
          className="hover:bg-bad-soft hover:text-bad"
          onClick={onDelete}
        >
          <Trash2 className="size-3.5" />
        </IconButton>
      </div>
    </div>
  );
}
