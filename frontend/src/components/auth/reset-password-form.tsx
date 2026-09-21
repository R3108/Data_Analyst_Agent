"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  AuthShell,
  FormMessage,
  PasswordField,
  StrengthMeter,
  SubmitButton,
} from "@/components/auth/auth-shell";
import { ApiError, auth } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { PasswordStrength } from "@/lib/types";

export function ResetPasswordForm() {
  const params = useSearchParams();
  const { adopt, config } = useSession();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [strength, setStrength] = useState<PasswordStrength | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!password) {
      setStrength(null);
      return;
    }
    const timer = window.setTimeout(() => {
      auth.strength(password).then(setStrength).catch(() => undefined);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [password]);

  const mismatch = confirmation.length > 0 && confirmation !== password;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError(null);
    try {
      const { user } = await auth.resetPassword(token, password);
      adopt(user);
      // The reset signs every other device out, and this one gets a fresh session, so
      // the user lands in the app rather than being asked to sign in again.
      window.location.assign("/");
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Could not reset your password. Request a new link.",
      );
      setBusy(false);
    }
  };

  if (!token) {
    return (
      <AuthShell
        title="That link is incomplete"
        subtitle="The reset link is missing its token. Links can be mangled by email clients — request a fresh one."
        footer={
          <Link href="/forgot-password" className="font-medium text-accent hover:underline">
            Request a new link
          </Link>
        }
      >
        <p className="text-[13px] leading-relaxed text-ink-2">
          Open the link from the email directly rather than copying part of it.
        </p>
      </AuthShell>
    );
  }

  const minLength = config?.min_password_length ?? 10;

  return (
    <AuthShell
      title="Choose a new password"
      subtitle="Setting it signs out every other device on this account."
      footer={
        <Link href="/login" className="text-ink-2 hover:text-ink hover:underline">
          Back to sign in
        </Link>
      }
    >
      <form onSubmit={submit} noValidate>
        <div className="space-y-3.5">
          <div>
            <PasswordField
              label="New password"
              name="password"
              autoComplete="new-password"
              autoFocus
              value={password}
              onChange={setPassword}
              hint={strength ? undefined : `At least ${minLength} characters.`}
            />
            {strength && (
              <StrengthMeter
                score={strength.score}
                label={strength.label}
                suggestions={strength.suggestions}
              />
            )}
          </div>
          <PasswordField
            label="Confirm new password"
            name="confirm-password"
            autoComplete="new-password"
            value={confirmation}
            onChange={setConfirmation}
            error={mismatch ? "The two passwords do not match." : undefined}
          />
        </div>

        {error && <FormMessage tone="bad">{error}</FormMessage>}

        <SubmitButton busy={busy} disabled={password.length < minLength || mismatch || !confirmation}>
          Set new password
        </SubmitButton>
      </form>
    </AuthShell>
  );
}
