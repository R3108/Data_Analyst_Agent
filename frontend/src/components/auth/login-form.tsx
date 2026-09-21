"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  AuthShell,
  Field,
  FormMessage,
  GoogleButton,
  OrDivider,
  PasswordField,
  SubmitButton,
} from "@/components/auth/auth-shell";
import { ApiError, auth, googleErrorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * Where the `next` parameter is allowed to send someone after they sign in.
 *
 * Only a path on this origin. Accepting `next=https://evil.example` would turn the
 * login page into an open redirect — a phishing primitive that borrows our domain's
 * credibility — and `//evil.example` is a protocol-relative URL that looks like a path
 * until a browser parses it.
 */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { adopt, config, status } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const next = safeNext(params.get("next"));
  const justReset = params.get("reset") === "1";
  // Set by the server when a Google sign-in comes back unsuccessful. Only a code travels
  // in the URL; the wording lives here.
  const googleError = params.get("error");

  // Somebody who is already signed in has no business on this page.
  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { user } = await auth.login({ email: email.trim(), password });
      adopt(user);
      // A hard navigation, so nothing from a previous account survives in memory.
      window.location.assign(user.must_change_password ? "/account?change=1" : next);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "Something went wrong signing you in. Please try again.",
      );
      setPassword("");
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title="Sign in to Numera"
      subtitle="Your datasets, analyses and boards are private to your account."
      footer={
        config?.registration_enabled ? (
          <>
            New here?{" "}
            <Link href="/register" className="font-medium text-accent hover:underline">
              Create an account
            </Link>
          </>
        ) : (
          <span className="text-ink-3">Accounts on this workspace are created by an administrator.</span>
        )
      }
    >
      {config?.google_enabled && (
        <>
          <GoogleButton href={auth.googleStartUrl({ next })}>Continue with Google</GoogleButton>
          <OrDivider />
        </>
      )}
      {googleError && !error && (
        <div className="-mt-3 mb-4">
          <FormMessage tone="bad">{googleErrorMessage(googleError)}</FormMessage>
        </div>
      )}
      <form onSubmit={submit} noValidate>
        <div className="space-y-3.5">
          <Field
            label="Email"
            name="email"
            type="email"
            autoComplete="username"
            autoFocus={!config?.google_enabled}
            required
            value={email}
            placeholder="you@company.com"
            onChange={(event) => setEmail(event.target.value)}
          />
          <PasswordField
            label="Password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
          />
        </div>

        {justReset && !error && (
          <FormMessage tone="good">
            Your password has been changed. Sign in with the new one.
          </FormMessage>
        )}
        {error && <FormMessage tone="bad">{error}</FormMessage>}

        <SubmitButton busy={busy} disabled={!email.trim() || !password}>
          Sign in
        </SubmitButton>

        <p className="mt-3 text-center text-[12.5px]">
          <Link href="/forgot-password" className="text-ink-2 hover:text-ink hover:underline">
            Forgot your password?
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
