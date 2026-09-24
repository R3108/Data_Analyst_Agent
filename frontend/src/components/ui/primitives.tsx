"use client";

import { Check, Copy, LoaderCircle } from "lucide-react";
import { forwardRef, useState } from "react";

import { cn } from "@/lib/cn";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "destructive";
type ButtonSize = "sm" | "md" | "lg";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover shadow-card",
  secondary: "bg-panel text-ink border border-line hover:bg-muted shadow-card",
  ghost: "text-ink-2 hover:bg-muted hover:text-ink",
  danger: "text-bad hover:bg-bad-soft",
  // The filled counterpart of `danger`, for the one button that commits a destructive act.
  destructive: "bg-bad text-white hover:brightness-110 shadow-card",
};
const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-2.5 text-[13px] gap-1.5 rounded-lg",
  md: "h-9 px-3.5 text-sm gap-2 rounded-lg",
  lg: "h-11 px-5 text-[15px] gap-2 rounded-xl",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, className, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        // A slight press on click makes every button feel physical; disabled ones stay put.
        "inline-flex shrink-0 items-center justify-center font-medium whitespace-nowrap transition-[color,background-color,border-color,box-shadow,scale,filter] duration-150 enabled:active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {loading && <LoaderCircle className="size-4 animate-spin" />}
      {children}
    </button>
  );
});

/**
 * `cn` is clsx, not tailwind-merge, so a `size-*` passed through `className` would sit
 * alongside the base one and lose to whichever Tailwind emits later. Sizes are a prop.
 */
const ICON_SIZES = { xs: "size-6", sm: "size-7", md: "size-8" } as const;

export function IconButton({
  label,
  size = "md",
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string; size?: keyof typeof ICON_SIZES }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-lg text-ink-2 transition-[color,background-color,scale] duration-150 hover:bg-muted hover:text-ink enabled:active:scale-90 disabled:opacity-40",
        ICON_SIZES[size],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode;
  tone?: "neutral" | "accent" | "good" | "bad" | "warn";
  className?: string;
}) {
  const tones = {
    neutral: "bg-muted text-ink-2",
    accent: "bg-accent-soft text-accent-ink",
    good: "bg-good-soft text-good",
    bad: "bg-bad-soft text-bad",
    warn: "bg-warn-soft text-warn",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1600);
        } catch {
          /* clipboard unavailable */
        }
      }}
      className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-ink-2 transition hover:bg-muted hover:text-ink"
    >
      {copied ? <Check className="size-3.5 text-good" /> : <Copy className="size-3.5" />}
      {copied ? "Copied" : label}
    </button>
  );
}

export function SectionLabel({ children, icon }: { children: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="mb-2.5 flex items-center gap-1.5 text-xs font-medium tracking-wide text-ink-3 uppercase">
      {icon}
      {children}
    </div>
  );
}

export interface SelectOption {
  value: string;
  label: string;
  title?: string;
}

export function Select({
  label,
  value,
  options,
  onChange,
  disabled,
  className,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">{label}</span>
      <select
        value={value}
        disabled={disabled || options.length === 0}
        onChange={(event) => onChange(event.target.value)}
        className={cn(
          "h-9 max-w-56 min-w-0 rounded-lg border border-line bg-panel px-2 text-[13px] text-ink transition hover:bg-muted disabled:opacity-50",
          className,
        )}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} title={option.title}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function TextInput({
  label,
  hint,
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label?: string; hint?: string }) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      {label && (
        <span className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">{label}</span>
      )}
      <input
        className={cn(
          "h-9 w-full min-w-0 rounded-lg border border-line bg-panel px-2.5 text-[13px] text-ink transition placeholder:text-ink-3 focus:border-accent focus:outline-none disabled:opacity-50",
          className,
        )}
        {...props}
      />
      {hint && <span className="text-[11.5px] leading-snug text-ink-3">{hint}</span>}
    </label>
  );
}

/** The empty state every view shows before there is anything to work with. */
export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-2xl px-6 pt-16">
      <div className="rounded-2xl border-2 border-dashed border-line-strong p-10 text-center">
        <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-accent-soft text-accent">
          {icon}
        </div>
        <p className="mt-4 text-[15px] font-medium text-ink">{title}</p>
        <p className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-ink-2">{body}</p>
        {action && <div className="mt-5 flex justify-center gap-2">{action}</div>}
      </div>
    </div>
  );
}

/** Page header shared by the full-width analysis views. */
export function ViewHeader({
  eyebrow,
  icon,
  title,
  body,
  aside,
}: {
  eyebrow: string;
  icon: React.ReactNode;
  title: string;
  body: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <p className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-ink-3 uppercase">
          {icon}
          {eyebrow}
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">{title}</h1>
        <p className="mt-1.5 max-w-2xl text-[14px] leading-relaxed text-ink-2">{body}</p>
      </div>
      {aside}
    </div>
  );
}
