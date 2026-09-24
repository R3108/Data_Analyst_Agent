"use client";

import { CornerDownLeft, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import { useFocusTrap } from "@/lib/focus-trap";

export interface PaletteCommand {
  id: string;
  label: string;
  group: string;
  icon: React.ReactNode;
  hint?: string;
  keywords?: string;
  run: () => void;
}

export function CommandPalette({
  open,
  onClose,
  commands,
}: {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useFocusTrap(panel, open);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    window.setTimeout(() => input.current?.focus(), 0);
  }, [open]);

  const filtered = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    const matches = terms.length
      ? commands.filter((c) => {
          const haystack = `${c.label} ${c.keywords ?? ""} ${c.group}`.toLowerCase();
          return terms.every((term) => haystack.includes(term));
        })
      : commands;
    return matches.slice(0, 60);
  }, [commands, query]);

  useEffect(() => setActive(0), [query]);

  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;

  const execute = (command: PaletteCommand | undefined) => {
    if (!command) return;
    onClose();
    command.run();
  };

  const groups: { name: string; items: { command: PaletteCommand; index: number }[] }[] = [];
  filtered.forEach((command, index) => {
    const group = groups.find((g) => g.name === command.group);
    if (group) group.items.push({ command, index });
    else groups.push({ name: command.group, items: [{ command, index }] });
  });

  return (
    <div
      className="animate-fade fixed inset-0 z-[80] flex items-start justify-center bg-black/40 p-4 pt-[12vh] backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="animate-pop w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-panel shadow-pop"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Search className="size-4 text-ink-3" />
          <input
            ref={input}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((i) => Math.min(i + 1, filtered.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((i) => Math.max(i - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                execute(filtered[active]);
              } else if (event.key === "Escape") {
                onClose();
              }
            }}
            placeholder="Search analyses, boards and datasets, or run a command…"
            aria-label="Search commands"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            className="h-12 flex-1 bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
          />
          <kbd className="rounded border border-line px-1.5 py-0.5 text-[10px] text-ink-3">Esc</kbd>
        </div>

        <div ref={list} id="palette-list" role="listbox" className="max-h-[52vh] overflow-y-auto p-2">
          {filtered.length === 0 && <p className="px-3 py-8 text-center text-[13px] text-ink-3">No matches.</p>}
          {groups.map((group) => (
            <div key={group.name} className="mb-1">
              <p className="px-2.5 pt-2 pb-1 text-[11px] font-medium tracking-wide text-ink-3 uppercase">{group.name}</p>
              {group.items.map(({ command, index }) => (
                <button
                  key={command.id}
                  data-index={index}
                  role="option"
                  aria-selected={index === active}
                  onMouseMove={() => setActive(index)}
                  onClick={() => execute(command)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-[13.5px]",
                    index === active ? "bg-muted text-ink" : "text-ink-2",
                  )}
                >
                  <span className="flex size-4 shrink-0 items-center justify-center text-ink-3">{command.icon}</span>
                  <span className="min-w-0 flex-1 truncate">{command.label}</span>
                  {command.hint && <span className="shrink-0 text-[11.5px] text-ink-3">{command.hint}</span>}
                  {index === active && <CornerDownLeft className="size-3.5 shrink-0 text-ink-3" />}
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
