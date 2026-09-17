"use client";

import { ChevronDown, ChevronUp, LayoutDashboard, Maximize2, Minimize2, Pencil, Pin, StickyNote, Trash } from "lucide-react";
import { useEffect, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Button, IconButton } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { BoardDetail, BoardItem } from "@/lib/types";

import { BoardGrid } from "./board-grid";

type NoteItem = Extract<BoardItem, { kind: "note" }>;

export function BoardView({
  boardId,
  onChange,
  onOpenSession,
}: {
  boardId: string;
  onChange: (board: BoardDetail) => void;
  onOpenSession: (sessionId: string) => void;
}) {
  const toast = useToast();
  const [board, setBoard] = useState<BoardDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setBoard(null);
    setError(null);
    api
      .getBoard(boardId)
      .then((detail) => {
        if (!active) return;
        setBoard(detail);
        onChange(detail);
      })
      .catch((e) => active && setError(e instanceof ApiError ? e.message : "Could not load this board."));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boardId]);

  const commit = (next: BoardDetail) => {
    setBoard(next);
    onChange(next);
  };

  const guard = async (action: () => Promise<void>, failure: string) => {
    try {
      await action();
    } catch (e) {
      toast.error(failure, e instanceof ApiError ? e.message : undefined);
      api.getBoard(boardId).then(commit).catch(() => undefined);
    }
  };

  if (error) return <p className="mx-auto max-w-5xl px-6 pt-10 text-sm text-bad">{error}</p>;
  if (!board) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-4 px-6 pt-10">
        <div className="h-8 w-1/3 animate-pulse rounded bg-muted" />
        <div className="grid grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-muted" />
          ))}
        </div>
        <div className="h-72 animate-pulse rounded-xl bg-muted" />
      </div>
    );
  }

  const setItems = (items: BoardItem[]) => commit({ ...board, items, item_count: items.length });

  const move = (index: number, delta: number) =>
    guard(async () => {
      const target = index + delta;
      if (target < 0 || target >= board.items.length) return;
      const items = board.items.slice();
      [items[index], items[target]] = [items[target], items[index]];
      setItems(items);
      commit(await api.reorderBoard(board.id, items.map((i) => i.id)));
    }, "Couldn't reorder");

  const toggleWide = (item: BoardItem) =>
    guard(async () => {
      const updated = await api.updateBoardItem(board.id, item.id, { wide: !item.wide });
      setItems(board.items.map((i) => (i.id === item.id ? updated : i)));
    }, "Couldn't resize");

  const remove = (item: BoardItem) =>
    guard(async () => {
      setItems(board.items.filter((i) => i.id !== item.id));
      await api.removeBoardItem(board.id, item.id);
    }, "Couldn't remove item");

  const saveMeta = (patch: { title?: string; description?: string }) =>
    guard(async () => {
      if (patch.title !== undefined && (!patch.title.trim() || patch.title === board.title)) return;
      if (patch.description !== undefined && patch.description === board.description) return;
      commit(await api.updateBoard(board.id, patch));
    }, "Couldn't save");

  const saveNote = (text: string, item?: NoteItem) =>
    guard(async () => {
      if (!text.trim()) return;
      if (item) {
        const updated = await api.updateBoardItem(board.id, item.id, { text });
        setItems(board.items.map((i) => (i.id === item.id ? updated : i)));
        setEditingNote(null);
      } else {
        const created = await api.addNote(board.id, text);
        setItems([...board.items, created]);
        setDraft(null);
      }
    }, "Couldn't save note");

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-4 pt-8 pb-16 sm:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-ink-3 uppercase">
              <LayoutDashboard className="size-3.5" />
              Board · {board.items.length} item{board.items.length === 1 ? "" : "s"} · updated{" "}
              {relativeTime(board.updated_at)}
            </p>
            <input
              key={`title-${board.id}-${board.title}`}
              defaultValue={board.title}
              aria-label="Board title"
              maxLength={120}
              onBlur={(e) => saveMeta({ title: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
              className="-ml-2 mt-1 w-full rounded-lg bg-transparent px-2 py-1 text-2xl font-semibold tracking-tight text-ink outline-none hover:bg-muted/60 focus:bg-muted/60 focus-visible:outline-none sm:text-[28px]"
            />
            <textarea
              key={`desc-${board.id}-${board.description}`}
              defaultValue={board.description}
              aria-label="Board description"
              placeholder="Add a description for this board…"
              rows={1}
              maxLength={1000}
              onBlur={(e) => saveMeta({ description: e.target.value })}
              className="-ml-2 w-full resize-none rounded-lg bg-transparent px-2 py-1 text-[15px] text-ink-2 outline-none placeholder:text-ink-3 hover:bg-muted/60 focus:bg-muted/60 focus-visible:outline-none"
            />
          </div>
          <Button onClick={() => setDraft("")} className="no-print">
            <StickyNote className="size-4" />
            Add note
          </Button>
        </div>

        {draft !== null && (
          <NoteEditor initial="" onCancel={() => setDraft(null)} onSave={(text) => saveNote(text)} />
        )}

        {board.items.length === 0 && draft === null ? (
          <div className="mt-10 rounded-2xl border-2 border-dashed border-line-strong p-10 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-accent-soft text-accent">
              <Pin className="size-5" />
            </div>
            <p className="mt-4 text-[15px] font-medium text-ink">This board is empty</p>
            <p className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-ink-2">
              Use the pin icon on any KPI, chart, table or insight in an analysis to add it here. Boards keep a
              snapshot, so they stay intact even if the analysis is deleted.
            </p>
          </div>
        ) : (
          <div className="mt-8">
            <BoardGrid
              items={board.items}
              onOpenSource={onOpenSession}
              renderNote={(item) =>
                editingNote === item.id ? (
                  <NoteEditor
                    initial={item.content.text}
                    onCancel={() => setEditingNote(null)}
                    onSave={(text) => saveNote(text, item)}
                  />
                ) : (
                  <div className="h-full rounded-xl border border-line bg-panel p-4 shadow-card">
                    <Markdown>{item.content.text}</Markdown>
                  </div>
                )
              }
              renderToolbar={(item, index) => (
                <>
                  <IconButton label="Move earlier" size="sm" disabled={index === 0} onClick={() => move(index, -1)}>
                    <ChevronUp className="size-3.5" />
                  </IconButton>
                  <IconButton
                    label="Move later"
                    size="sm"
                    disabled={index === board.items.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    <ChevronDown className="size-3.5" />
                  </IconButton>
                  {item.kind === "note" && (
                    <IconButton label="Edit note" size="sm" onClick={() => setEditingNote(item.id)}>
                      <Pencil className="size-3.5" />
                    </IconButton>
                  )}
                  <IconButton label={item.wide ? "Make narrow" : "Make wide"} size="sm" onClick={() => toggleWide(item)}>
                    {item.wide ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
                  </IconButton>
                  <IconButton label="Remove from board" size="sm" className="hover:bg-bad-soft hover:text-bad" onClick={() => remove(item)}>
                    <Trash className="size-3.5" />
                  </IconButton>
                </>
              )}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function NoteEditor({ initial, onSave, onCancel }: { initial: string; onSave: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(initial);
  return (
    <div className="animate-rise mt-6 rounded-xl border border-accent/40 bg-panel p-3 shadow-card ring-4 ring-accent-soft">
      <textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSave(text);
          if (e.key === "Escape") onCancel();
        }}
        rows={4}
        maxLength={10000}
        placeholder="Write a note — Markdown supported (**bold**, lists, links)…"
        className="w-full resize-y bg-transparent text-[14px] leading-relaxed text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
      />
      <div className="mt-2 flex items-center justify-end gap-2">
        <span className="mr-auto text-[11px] text-ink-3">⌘/Ctrl + Enter to save</span>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" onClick={() => onSave(text)} disabled={!text.trim()}>
          Save note
        </Button>
      </div>
    </div>
  );
}
