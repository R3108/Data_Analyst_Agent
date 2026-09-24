"use client";

import { TriangleAlert } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { useFocusTrap } from "@/lib/focus-trap";

export interface ConfirmOptions {
  title: string;
  body?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Destructive by default: most confirmations in the app guard a delete. */
  tone?: "danger" | "default";
  /** The user must type this (case-insensitive) before the confirm button unlocks. */
  requireText?: string;
}

type Confirm = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<Confirm | null>(null);

/**
 * An in-app replacement for `window.confirm`: themed, accessible, and awaitable.
 *
 *   const confirm = useConfirm();
 *   if (!(await confirm({ title: "Delete this board?" }))) return;
 */
export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [request, setRequest] = useState<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null);

  const confirm = useCallback<Confirm>(
    (options) =>
      new Promise<boolean>((resolve) => {
        setRequest((current) => {
          // A second request supersedes an unanswered first one, which counts as cancelled.
          current?.resolve(false);
          return { ...options, resolve };
        });
      }),
    [],
  );

  const settle = (ok: boolean) => {
    request?.resolve(ok);
    setRequest(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {request && <ConfirmDialog key={request.title} options={request} onSettle={settle} />}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): Confirm {
  const context = useContext(ConfirmContext);
  if (!context) throw new Error("useConfirm must be used inside ConfirmProvider");
  return context;
}

function ConfirmDialog({ options, onSettle }: { options: ConfirmOptions; onSettle: (ok: boolean) => void }) {
  const { title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", tone = "danger", requireText } = options;
  const [typed, setTyped] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const bodyId = useId();
  useFocusTrap(panel);

  const locked = Boolean(requireText) && typed.trim().toLowerCase() !== requireText!.trim().toLowerCase();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onSettle(false);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onSettle]);

  return (
    <div
      className="animate-fade fixed inset-0 z-[90] flex items-end justify-center bg-black/40 p-4 backdrop-blur-sm sm:items-center"
      onMouseDown={() => onSettle(false)}
    >
      <div
        ref={panel}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={body ? bodyId : undefined}
        className="animate-pop w-full max-w-md rounded-2xl border border-line bg-panel p-5 shadow-pop"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!locked) onSettle(true);
          }}
        >
          <div className="flex items-start gap-3">
            <span
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-xl",
                tone === "danger" ? "bg-bad-soft text-bad" : "bg-accent-soft text-accent",
              )}
            >
              <TriangleAlert className="size-4" />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <h2 id={titleId} className="text-base font-semibold text-balance text-ink">
                {title}
              </h2>
              {body && (
                <div id={bodyId} className="mt-1.5 text-[13px] leading-relaxed text-ink-2">
                  {body}
                </div>
              )}
            </div>
          </div>

          {requireText && (
            <label className="mt-4 block">
              <span className="text-[12px] text-ink-2">
                Type <span className="font-mono font-medium text-ink">{requireText}</span> to confirm
              </span>
              <input
                data-autofocus
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                className="mt-1.5 h-9 w-full rounded-lg border border-line bg-panel px-2.5 font-mono text-[13px] text-ink transition focus:border-accent focus:outline-none"
              />
            </label>
          )}

          <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button type="button" onClick={() => onSettle(false)} data-autofocus={requireText ? undefined : true}>
              {cancelLabel}
            </Button>
            <Button type="submit" variant={tone === "danger" ? "destructive" : "primary"} disabled={locked}>
              {confirmLabel}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
