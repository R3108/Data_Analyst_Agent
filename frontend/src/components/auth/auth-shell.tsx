"use client";

import { CircleAlert, CircleCheck, Eye, EyeOff, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { forwardRef, useEffect, useId, useState } from "react";

import { LogoMark } from "@/components/brand";
import { cn } from "@/lib/cn";

/** The card every sign-in, sign-up and recovery screen is rendered inside. */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6 py-10">
      <div className="w-full max-w-sm">
        <div className="rounded-2xl border border-line bg-panel p-6 shadow-pop">
          <Link href="/home" aria-label="Numera home" className="inline-block rounded-lg">
            <LogoMark className="size-9" />
          </Link>
          <h1 className="mt-4 text-[19px] font-semibold tracking-tight text-ink">{title}</h1>
          {subtitle && (
            <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{subtitle}</p>
          )}
          <div className="mt-5">{children}</div>
        </div>
        {footer && <div className="mt-4 text-center text-[13px] text-ink-2">{footer}</div>}
      </div>
    </main>
  );
}

/** A form-level message. Errors are `alert`s so a screen reader announces them. */
export function FormMessage({
  tone,
  children,
}: {
  tone: "bad" | "good" | "warn";
  children: React.ReactNode;
}) {
  const tones = {
    bad: "border-bad/30 bg-bad-soft text-bad",
    good: "border-good/30 bg-good-soft text-good",
    warn: "border-warn/30 bg-warn-soft text-warn",
  };
  return (
    <p
      role={tone === "bad" ? "alert" : "status"}
      className={cn(
        "mt-3 flex items-start gap-2 rounded-lg border p-2.5 text-[12.5px] leading-relaxed",
        tones[tone],
      )}
    >
      <span className="mt-0.5 shrink-0">
        {tone === "good" ? <CircleCheck className="size-3.5" /> : <CircleAlert className="size-3.5" />}
      </span>
      <span className="text-ink">{children}</span>
    </p>
  );
}

/** A labelled field with an error slot wired up for assistive technology. */
export const Field = forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; error?: string }
>(function Field({ label, hint, error, className, id, ...props }, ref) {
  const generated = useId();
  const fieldId = id ?? generated;
  const describedBy = error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={fieldId} className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">
        {label}
      </label>
      <input
        ref={ref}
        id={fieldId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cn(
          "h-10 w-full min-w-0 rounded-lg border bg-panel px-3 text-[14px] text-ink transition placeholder:text-ink-3 focus:outline-none disabled:opacity-50",
          error ? "border-bad focus:border-bad" : "border-line focus:border-accent",
          className,
        )}
        {...props}
      />
      {error ? (
        <span id={`${fieldId}-error`} className="text-[11.5px] leading-snug text-bad">
          {error}
        </span>
      ) : (
        hint && (
          <span id={`${fieldId}-hint`} className="text-[11.5px] leading-snug text-ink-3">
            {hint}
          </span>
        )
      )}
    </div>
  );
});

/**
 * A password field with a reveal toggle.
 *
 * `autoComplete` is always passed explicitly by the caller — `current-password` on a
 * sign-in, `new-password` on a sign-up or a reset — because getting it wrong is how a
 * password manager ends up saving a new password over the old one.
 */
export function PasswordField({
  label,
  error,
  hint,
  value,
  onChange,
  autoComplete,
  autoFocus,
  id,
  name,
  required = true,
  disabled,
}: {
  label: string;
  error?: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  autoFocus?: boolean;
  id?: string;
  name?: string;
  required?: boolean;
  disabled?: boolean;
}) {
  const [revealed, setRevealed] = useState(false);
  return (
    <div className="relative">
      <Field
        id={id}
        name={name}
        label={label}
        error={error}
        hint={hint}
        type={revealed ? "text" : "password"}
        value={value}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        required={required}
        disabled={disabled}
        className="pr-10"
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        // Off the tab order: a sighted mouse user wants it, a keyboard user tabbing
        // from the password to the submit button does not.
        tabIndex={-1}
        aria-label={revealed ? "Hide password" : "Show password"}
        onClick={() => setRevealed((open) => !open)}
        className="absolute top-[26px] right-1.5 flex size-8 items-center justify-center rounded-md text-ink-3 transition hover:bg-muted hover:text-ink"
      >
        {revealed ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

/**
 * "Continue with Google", following Google's sign-in branding: the full-colour G on a
 * neutral surface, and the provider named in the label.
 *
 * A plain link rather than a script: the server redirects to Google and handles the
 * reply itself, so nothing from the exchange ever passes through this page.
 */
export function GoogleButton({ href, children }: { href: string; children: React.ReactNode }) {
  const [busy, setBusy] = useState(false);
  // Coming Back from Google restores this page from the back-forward cache, spinner and
  // all; without this the button would stay disabled.
  useEffect(() => {
    const reset = () => setBusy(false);
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);
  return (
    <a
      href={href}
      onClick={() => setBusy(true)}
      aria-busy={busy}
      className={cn(
        "inline-flex h-10 w-full items-center justify-center gap-2.5 rounded-lg border border-line-strong bg-panel text-sm font-medium text-ink shadow-card transition hover:bg-muted",
        busy && "pointer-events-none opacity-70",
      )}
    >
      {busy ? <LoaderCircle className="size-4 animate-spin" /> : <GoogleMark className="size-[18px]" />}
      {children}
    </a>
  );
}

export function GoogleMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

/** The "or" rule between a single-click provider and the email form. */
export function OrDivider() {
  return (
    <div className="my-5 flex items-center gap-3 text-[11px] font-medium tracking-wide text-ink-3 uppercase">
      <span className="h-px flex-1 bg-line" />
      or
      <span className="h-px flex-1 bg-line" />
    </div>
  );
}

/** Full-width submit button with a busy state. */
export function SubmitButton({
  children,
  busy,
  disabled,
}: {
  children: React.ReactNode;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="submit"
      disabled={busy || disabled}
      className="mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-accent text-sm font-medium text-white shadow-card transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
    >
      {busy && <LoaderCircle className="size-4 animate-spin" />}
      {children}
    </button>
  );
}

/**
 * A live strength meter.
 *
 * Advisory only — the server decides what it will accept, and says why. Showing a bar
 * that disagrees with the server's verdict would be worse than showing none, so the
 * score comes from the same scoring function the API exposes.
 */
export function StrengthMeter({ score, label, suggestions }: {
  score: number;
  label: string;
  suggestions: string[];
}) {
  const tones = ["bg-bad", "bg-bad", "bg-warn", "bg-accent", "bg-good"];
  return (
    <div className="mt-2">
      <div className="flex gap-1" aria-hidden="true">
        {[0, 1, 2, 3].map((step) => (
          <span
            key={step}
            className={cn(
              "h-1 flex-1 rounded-full transition-colors",
              step < score ? tones[score] : "bg-muted",
            )}
          />
        ))}
      </div>
      <p className="mt-1 text-[11.5px] leading-snug text-ink-3" aria-live="polite">
        <span className="font-medium text-ink-2">{label}</span>
        {suggestions.length > 0 && <> — {suggestions[0]}</>}
      </p>
    </div>
  );
}
