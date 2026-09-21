"use client";

import { ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  AuthShell,
  Field,
  FormMessage,
  GoogleButton,
  OrDivider,
  PasswordField,
  StrengthMeter,
  SubmitButton,
} from "@/components/auth/auth-shell";
import { ApiError, auth } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { PasswordStrength } from "@/lib/types";

export function RegisterForm() {
  const router = useRouter();
  const { adopt, config, status } = useSession();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [strength, setStrength] = useState<PasswordStrength | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status === "authenticated") router.replace("/");
  }, [status, router]);

  // Scored by the same function that will accept or reject it, and debounced so a
  // fast typist does not generate a request per keystroke.
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

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { user } = await auth.register({ email: email.trim(), password, name: name.trim() });
      adopt(user);
      window.location.assign("/");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create your account. Please try again.");
      setBusy(false);
    }
  };

  const domains = config?.allowed_domains ?? [];
  const minLength = config?.min_password_length ?? 10;

  return (
    <AuthShell
      title={config?.first_run ? "Create the first account" : "Create your account"}
      subtitle={
        config?.first_run
          ? "This deployment has no accounts yet, so this one becomes the administrator."
          : "You get a private workspace — nobody else can see your data."
      }
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-accent hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      {config?.google_enabled && (
        <>
          <GoogleButton href={auth.googleStartUrl()}>Sign up with Google</GoogleButton>
          <OrDivider />
        </>
      )}
      <form onSubmit={submit} noValidate>
        <div className="space-y-3.5">
          <Field
            label="Name"
            name="name"
            autoComplete="name"
            autoFocus={!config?.google_enabled}
            required
            value={name}
            placeholder="Ada Lovelace"
            hint="Shown on the comments and activity you leave."
            onChange={(event) => setName(event.target.value)}
          />
          <Field
            label="Email"
            name="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            placeholder="you@company.com"
            hint={domains.length ? `Limited to ${domains.map((d) => `@${d}`).join(", ")}` : undefined}
            onChange={(event) => setEmail(event.target.value)}
          />
          <div>
            <PasswordField
              label="Password"
              name="password"
              autoComplete="new-password"
              value={password}
              onChange={setPassword}
              hint={strength ? undefined : `At least ${minLength} characters. A short phrase works well.`}
            />
            {strength && (
              <StrengthMeter
                score={strength.score}
                label={strength.label}
                suggestions={strength.suggestions}
              />
            )}
          </div>
        </div>

        {error && <FormMessage tone="bad">{error}</FormMessage>}

        <SubmitButton busy={busy} disabled={!email.trim() || password.length < minLength}>
          Create account
        </SubmitButton>

        <p className="mt-4 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-ink-3">
          <ShieldCheck className="mt-px size-3.5 shrink-0 text-good" />
          Your password is hashed with Argon2id and never stored, logged or sent anywhere
          in a readable form.
        </p>
      </form>
    </AuthShell>
  );
}
