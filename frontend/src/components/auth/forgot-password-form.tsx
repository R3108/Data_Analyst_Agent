"use client";

import Link from "next/link";
import { useState } from "react";

import { AuthShell, Field, FormMessage, SubmitButton } from "@/components/auth/auth-shell";
import { ApiError, auth } from "@/lib/api";
import { useSession } from "@/lib/session";

export function ForgotPasswordForm() {
  const { config } = useSession();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<{ message: string; link?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await auth.forgotPassword(email.trim());
      setSent({ message: result.message, link: result.reset_link });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not send the reset link. Try again shortly.");
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <AuthShell
        title="Check your email"
        subtitle={sent.message}
        footer={
          <Link href="/login" className="font-medium text-accent hover:underline">
            Back to sign in
          </Link>
        }
      >
        {sent.link ? (
          // Only ever present on a development server with no mail configured and
          // EXPOSE_RESET_LINK turned on. Production never returns this.
          <div className="rounded-lg border border-warn/30 bg-warn-soft p-3">
            <p className="text-[12px] font-medium text-ink">Development mode</p>
            <p className="mt-1 text-[12px] leading-relaxed text-ink-2">
              No SMTP server is configured, so the link is shown here instead of being
              emailed. Configure <code className="font-mono">SMTP_HOST</code> before going live.
            </p>
            <Link
              href={sent.link.replace(/^https?:\/\/[^/]+/, "")}
              className="mt-2 block truncate text-[12px] font-medium text-accent hover:underline"
            >
              Open the reset link
            </Link>
          </div>
        ) : (
          <p className="text-[13px] leading-relaxed text-ink-2">
            The link expires shortly and can only be used once. If nothing arrives, check
            your spam folder{config?.email_delivery === false ? " or ask an administrator" : ""}.
          </p>
        )}
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Reset your password"
      subtitle="Enter the email address on your account and we'll send a link to choose a new password."
      footer={
        <Link href="/login" className="font-medium text-accent hover:underline">
          Back to sign in
        </Link>
      }
    >
      <form onSubmit={submit} noValidate>
        <Field
          label="Email"
          name="email"
          type="email"
          autoComplete="username"
          autoFocus
          required
          value={email}
          placeholder="you@company.com"
          onChange={(event) => setEmail(event.target.value)}
        />
        {error && <FormMessage tone="bad">{error}</FormMessage>}
        <SubmitButton busy={busy} disabled={!email.trim()}>
          Send reset link
        </SubmitButton>
      </form>
    </AuthShell>
  );
}
