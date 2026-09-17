"use client";

import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";

type Tone = "error" | "success" | "info";

interface ToastAction {
  label: string;
  onClick: () => void;
}

interface Toast {
  id: number;
  title: string;
  description?: string;
  tone: Tone;
  action?: ToastAction;
}

interface ToastApi {
  show: (toast: Omit<Toast, "id">) => void;
  error: (title: string, description?: string) => void;
  success: (title: string, description?: string, action?: ToastAction) => void;
  info: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const ICONS = { error: CircleAlert, success: CircleCheck, info: Info };
const TONE_CLASS: Record<Tone, string> = {
  error: "text-bad",
  success: "text-good",
  info: "text-accent",
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((current) => current.filter((t) => t.id !== id)), []);

  const show = useCallback(
    (toast: Omit<Toast, "id">) => {
      const id = nextId.current++;
      setToasts((current) => [...current.slice(-3), { ...toast, id }]);
      window.setTimeout(() => dismiss(id), toast.tone === "error" || toast.action ? 8000 : 4500);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      show,
      error: (title, description) => show({ title, description, tone: "error" }),
      success: (title, description, action) => show({ title, description, tone: "success", action }),
      info: (title, description) => show({ title, description, tone: "info" }),
    }),
    [show],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        className="no-print pointer-events-none fixed inset-x-4 bottom-4 z-[100] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-4"
      >
        {toasts.map((toast) => {
          const Icon = ICONS[toast.tone];
          return (
            <div
              key={toast.id}
              role={toast.tone === "error" ? "alert" : "status"}
              className="animate-rise pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border border-line bg-panel p-3.5 shadow-pop"
            >
              <Icon className={cn("mt-0.5 size-4 shrink-0", TONE_CLASS[toast.tone])} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{toast.title}</p>
                {toast.description && (
                  <p className="mt-0.5 truncate text-[13px] leading-snug text-ink-2">{toast.description}</p>
                )}
                {toast.action && (
                  <button
                    onClick={() => {
                      toast.action?.onClick();
                      dismiss(toast.id);
                    }}
                    className="mt-1.5 text-[13px] font-medium text-accent-ink hover:underline"
                  >
                    {toast.action.label}
                  </button>
                )}
              </div>
              <button
                onClick={() => dismiss(toast.id)}
                className="rounded-md p-0.5 text-ink-3 transition hover:bg-muted hover:text-ink"
                aria-label="Dismiss notification"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
