"use client";

import { ArrowUp, Check, Copy, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

/**
 * Wraps a CSS-animated sequence with a button that plays it again. Remounting the
 * children restarts every animation in them, so the sequence itself needs no script.
 * Under reduced motion there is nothing to replay and the button is not shown.
 */
export function Replay({
  label,
  caption,
  children,
}: {
  label: string;
  caption: React.ReactNode;
  children: React.ReactNode;
}) {
  const [run, setRun] = useState(0);
  return (
    <>
      <div key={run}>{children}</div>
      <div className="mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
        <p>{caption}</p>
        <button
          onClick={() => setRun((value) => value + 1)}
          className="group inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium text-ink-2 transition hover:bg-muted hover:text-ink motion-reduce:hidden"
        >
          <RotateCcw className="size-3 transition-transform duration-500 group-hover:-rotate-180" />
          {label}
        </button>
      </div>
    </>
  );
}

/** A floating button back to the top, shown once the hero is well out of view. */
export function BackToTop() {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > window.innerHeight * 1.2);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <button
      onClick={() => window.scrollTo({ top: 0 })}
      aria-label="Back to top"
      // `inert` while hidden: an invisible button must not be reachable by keyboard.
      inert={!shown}
      className={cn(
        // Below `sm` it sits above the pinned call to action.
        "fixed right-5 bottom-20 z-40 flex size-10 items-center justify-center rounded-full border border-line-strong bg-panel text-ink-2 shadow-pop transition duration-300 hover:-translate-y-0.5 hover:text-ink sm:right-8 sm:bottom-8",
        shown ? "opacity-100" : "pointer-events-none translate-y-3 opacity-0",
      )}
    >
      <ArrowUp className="size-4" />
    </button>
  );
}

/**
 * A terminal card with the commands to run, and a button that copies them. The lines
 * appear in turn once the card scrolls into view (`reveal-rise`, see globals.css).
 */
export function CopyCommand({ lines }: { lines: { command: string; comment?: string }[] }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(0);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(lines.map((line) => line.command).join("\n"));
    } catch {
      // Clipboard access is refused on insecure origins; the text stays selectable.
      return;
    }
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="overflow-clip rounded-2xl border border-line-strong bg-[#121211] shadow-pop">
      <div className="flex h-10 items-center gap-3 border-b border-white/10 px-4">
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="size-2.5 rounded-full bg-white/15" />
          <span className="size-2.5 rounded-full bg-white/15" />
          <span className="size-2.5 rounded-full bg-white/15" />
        </div>
        <p className="flex-1 text-center text-[12px] text-white/45">Terminal</p>
        <button
          onClick={copy}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-medium text-white/70 transition hover:bg-white/10 hover:text-white focus-visible:outline-white"
        >
          {copied ? <Check className="animate-pop size-3.5 text-[#4cc24c]" /> : <Copy className="size-3.5" />}
          <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="overflow-x-auto p-5 font-mono text-[13px] leading-7 text-white/90">
        {lines.map(({ command, comment }, index) => (
          <code key={command} className="reveal-rise block" style={{ "--d": `${index * 350}ms` } as React.CSSProperties}>
            <span className="text-white/35 select-none">$ </span>
            {command}
            {comment && <span className="text-white/40 select-none">{`  # ${comment}`}</span>}
            {index === lines.length - 1 && <span aria-hidden="true" className="terminal-caret" />}
          </code>
        ))}
      </pre>
    </div>
  );
}
