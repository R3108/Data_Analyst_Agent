"use client";

import { ChevronDown, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";

export interface ExportOption {
  id: string;
  label: string;
  hint?: string;
  icon: React.ReactNode;
  run: () => Promise<void> | void;
}

/** One button, every deliverable: Markdown, notebook, PDF, PowerPoint, print. */
export function ExportMenu({ options, label = "Export" }: { options: ExportOption[]; label?: string }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = async (option: ExportOption) => {
    setBusy(option.id);
    try {
      await option.run();
      setOpen(false);
    } catch (error) {
      toast.error(
        `Couldn't export ${option.label}`,
        error instanceof ApiError ? error.message : "Please try again.",
      );
    } finally {
      setBusy(null);
    }
  };

  if (options.length === 0) return null;

  return (
    <div ref={root} className="relative no-print">
      <Button variant="ghost" size="sm" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        {busy ? <LoaderCircle className="size-3.5 animate-spin" /> : null}
        <span className="hidden sm:inline">{label}</span>
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
      </Button>

      {open && (
        <div className="animate-rise absolute top-full right-0 z-40 mt-1 w-64 rounded-xl border border-line bg-panel p-1.5 shadow-pop">
          {options.map((option) => (
            <button
              key={option.id}
              onClick={() => void choose(option)}
              disabled={busy !== null}
              className="flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left transition hover:bg-muted disabled:opacity-50"
            >
              <span className="mt-0.5 shrink-0 text-ink-3">
                {busy === option.id ? <LoaderCircle className="size-4 animate-spin" /> : option.icon}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium text-ink">{option.label}</span>
                {option.hint && <span className="block text-[11.5px] leading-snug text-ink-3">{option.hint}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
