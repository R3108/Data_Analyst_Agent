"use client";

import {
  ArrowLeft,
  KeyRound,
  LaptopMinimal,
  Link2,
  LogOut,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { FormMessage, GoogleMark, PasswordField, StrengthMeter } from "@/components/auth/auth-shell";
import { Badge, Button, TextInput } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, auth, googleErrorMessage } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AuthSession, PasswordStrength, SignInMethods } from "@/lib/types";

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

export function AccountView() {
  const params = useSearchParams();
  const { user, refresh, signOut, config } = useSession();
  const mustChange = Boolean(user?.must_change_password) || params.get("change") === "1";

  return (
    <div className="min-h-dvh bg-canvas">
      <header className="flex h-14 items-center gap-2 border-b border-line bg-panel/70 px-4 backdrop-blur">
        <Link
          href="/"
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] text-ink-2 transition hover:bg-muted hover:text-ink"
        >
          <ArrowLeft className="size-3.5" />
          Workspace
        </Link>
        <h1 className="ml-1 text-sm font-semibold text-ink">Your account</h1>
      </header>

      <div className="mx-auto w-full max-w-2xl space-y-5 px-6 py-8">
        {mustChange && (
          <div className="rounded-xl border border-warn/30 bg-warn-soft p-4">
            <p className="text-[13.5px] font-medium text-ink">Choose your own password</p>
            <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
              This account is using a password an administrator set. Until you replace it,
              the rest of the workspace stays locked.
            </p>
          </div>
        )}

        <ProfileCard onSaved={refresh} />
        <SignInMethodsCard />
        <PasswordCard
          minLength={config?.min_password_length ?? 10}
          hasPassword={user?.has_password !== false}
        />
        <DevicesCard onSignOutEverywhere={signOut} />
      </div>
    </div>
  );
}

function Card({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-panel p-5 shadow-card">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
          {icon}
        </span>
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
          <p className="mt-0.5 text-[13px] leading-relaxed text-ink-2">{description}</p>
        </div>
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function ProfileCard({ onSaved }: { onSaved: () => Promise<unknown> }) {
  const toast = useToast();
  const { user } = useSession();
  const [name, setName] = useState(user?.name ?? "");
  const [busy, setBusy] = useState(false);

  useEffect(() => setName(user?.name ?? ""), [user?.name]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await auth.updateProfile(name.trim());
      await onSaved();
      toast.success("Profile updated", "Your new name appears on comments and activity.");
    } catch (error) {
      toast.error("Couldn't update your profile", describe(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      icon={<UserRound className="size-4" />}
      title="Profile"
      description="How you appear on comments and in the workspace activity feed."
    >
      <form onSubmit={save} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1">
          <TextInput
            label="Display name"
            value={name}
            maxLength={80}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <Button type="submit" variant="primary" loading={busy} disabled={!name.trim() || name === user?.name}>
          Save
        </Button>
      </form>
      <dl className="mt-4 grid gap-3 border-t border-line pt-4 text-[13px] sm:grid-cols-2">
        <div>
          <dt className="text-ink-3">Email</dt>
          <dd className="mt-0.5 truncate text-ink">{user?.email}</dd>
        </div>
        <div>
          <dt className="text-ink-3">Role</dt>
          <dd className="mt-0.5">
            <Badge tone={user?.role === "admin" ? "accent" : "neutral"}>
              {user?.role === "admin" ? "Administrator" : "Member"}
            </Badge>
          </dd>
        </div>
      </dl>
    </Card>
  );
}

function SignInMethodsCard() {
  const toast = useToast();
  const params = useSearchParams();
  const [methods, setMethods] = useState<SignInMethods | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    auth.methods().then(setMethods).catch(() => undefined);
  }, []);

  // The Google round trip lands back here with its outcome in the query string. Report
  // it once, then tidy the URL so a reload does not report it again.
  const reported = useRef(false);
  useEffect(() => {
    const outcome = params.get("google");
    const failure = params.get("google_error");
    if (reported.current || (!outcome && !failure)) return;
    reported.current = true;
    if (outcome === "connected") toast.success("Google connected", "You can now sign in with Google.");
    if (failure) toast.error("Couldn't connect Google", googleErrorMessage(failure));
    window.history.replaceState(null, "", window.location.pathname);
  }, [params, toast]);

  if (!methods || (!methods.google_available && !methods.google)) return null;

  const disconnect = async () => {
    if (!window.confirm("Disconnect Google? You will sign in with your password instead.")) return;
    setBusy(true);
    try {
      setMethods(await auth.disconnectGoogle());
      toast.success("Google disconnected");
    } catch (error) {
      toast.error("Couldn't disconnect Google", describe(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      icon={<Link2 className="size-4" />}
      title="Sign-in methods"
      description="Ways you can get into this account. Keep at least one."
    >
      <div className="flex items-center gap-3 rounded-lg border border-line p-3">
        <GoogleMark className="size-5 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] font-medium text-ink">Google</p>
          <p className="truncate text-[12.5px] text-ink-3">
            {methods.google
              ? `${methods.google.email ?? "Connected"} · connected ${relativeTime(methods.google.connected_at)}`
              : "Not connected"}
          </p>
        </div>
        {methods.google ? (
          <Button
            variant="secondary"
            size="sm"
            loading={busy}
            disabled={!methods.password}
            title={methods.password ? undefined : "Set a password first, so you can still sign in"}
            onClick={() => void disconnect()}
          >
            Disconnect
          </Button>
        ) : (
          <a
            href={auth.googleStartUrl({ intent: "link" })}
            className="inline-flex h-8 shrink-0 items-center rounded-lg border border-line bg-panel px-2.5 text-[13px] font-medium text-ink shadow-card transition hover:bg-muted"
          >
            Connect
          </a>
        )}
      </div>
      {methods.google && !methods.password && (
        <p className="mt-2 text-[12px] leading-relaxed text-ink-3">
          Google is your only way in. Set a password below before disconnecting it.
        </p>
      )}
    </Card>
  );
}

function PasswordCard({ minLength, hasPassword }: { minLength: number; hasPassword: boolean }) {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [strength, setStrength] = useState<PasswordStrength | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!next) {
      setStrength(null);
      return;
    }
    const timer = window.setTimeout(() => {
      auth.strength(next).then(setStrength).catch(() => undefined);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [next]);

  const mismatch = confirmation.length > 0 && confirmation !== next;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirmation("");
      setStrength(null);
      toast.success(hasPassword ? "Password changed" : "Password set", "Every other device has been signed out.");
      // The change rotated this browser's session; a reload picks up the new cookie and
      // clears the "must change password" gate if one was in force.
      window.setTimeout(() => window.location.assign("/"), 900);
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      icon={<KeyRound className="size-4" />}
      title="Password"
      description={
        hasPassword
          ? "Changing it signs out every other device on this account."
          : "You sign in with Google. Add a password to also sign in with your email address."
      }
    >
      <form onSubmit={submit} noValidate className="space-y-3.5">
        {hasPassword && (
          <PasswordField
            label="Current password"
            autoComplete="current-password"
            value={current}
            onChange={setCurrent}
          />
        )}
        <div>
          <PasswordField
            label="New password"
            autoComplete="new-password"
            value={next}
            onChange={setNext}
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
          autoComplete="new-password"
          value={confirmation}
          onChange={setConfirmation}
          error={mismatch ? "The two passwords do not match." : undefined}
        />
        {error && <FormMessage tone="bad">{error}</FormMessage>}
        <Button
          type="submit"
          variant="primary"
          loading={busy}
          disabled={(hasPassword && !current) || next.length < minLength || mismatch || !confirmation}
        >
          {hasPassword ? "Change password" : "Set password"}
        </Button>
      </form>
    </Card>
  );
}

function DevicesCard({ onSignOutEverywhere }: { onSignOutEverywhere: () => Promise<void> }) {
  const toast = useToast();
  const [sessions, setSessions] = useState<AuthSession[] | null>(null);

  const load = useCallback(() => {
    auth.listSessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  useEffect(load, [load]);

  const revoke = async (session: AuthSession) => {
    try {
      await auth.revokeSession(session.id);
      if (session.current) {
        window.location.assign("/login");
        return;
      }
      toast.success("Signed that device out", "It will need to sign in again.");
      load();
    } catch (error) {
      toast.error("Couldn't sign that device out", describe(error));
    }
  };

  return (
    <Card
      icon={<LaptopMinimal className="size-4" />}
      title="Signed-in devices"
      description="Every browser holding a live session for this account."
    >
      <ul className="divide-y divide-line rounded-lg border border-line">
        {sessions === null && <li className="p-3 text-[13px] text-ink-3">Loading…</li>}
        {sessions?.length === 0 && (
          <li className="p-3 text-[13px] text-ink-3">No active sessions.</li>
        )}
        {sessions?.map((session) => (
          <li key={session.id} className="flex items-center gap-3 p-3">
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-1.5 truncate text-[13px] text-ink">
                {describeAgent(session.user_agent)}
                {session.current && <Badge tone="good">This device</Badge>}
              </p>
              <p className="mt-0.5 truncate text-[11.5px] text-ink-3">
                {session.ip ?? "unknown address"} · active {relativeTime(session.last_seen_at)}
              </p>
            </div>
            <Button size="sm" variant="ghost" onClick={() => void revoke(session)}>
              <LogOut className="size-3.5" />
              Sign out
            </Button>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex items-center gap-2">
        <Button
          size="sm"
          variant="danger"
          onClick={() => {
            if (window.confirm("Sign out of every device, including this one?")) {
              void auth.revokeAllSessions().finally(() => void onSignOutEverywhere());
            }
          }}
        >
          <ShieldCheck className="size-3.5" />
          Sign out everywhere
        </Button>
        <p className="text-[11.5px] leading-snug text-ink-3">
          Use this if you think somebody else has your password.
        </p>
      </div>
    </Card>
  );
}

/** A readable device name from a user-agent string, without pretending to be precise. */
function describeAgent(agent: string | null): string {
  if (!agent) return "Unknown device";
  const browser =
    /Edg\//.test(agent) ? "Edge"
    : /OPR\//.test(agent) ? "Opera"
    : /Firefox\//.test(agent) ? "Firefox"
    : /Chrome\//.test(agent) ? "Chrome"
    : /Safari\//.test(agent) ? "Safari"
    : "Browser";
  const platform =
    /Windows/.test(agent) ? "Windows"
    : /Macintosh|Mac OS/.test(agent) ? "macOS"
    : /Android/.test(agent) ? "Android"
    : /iPhone|iPad/.test(agent) ? "iOS"
    : /Linux/.test(agent) ? "Linux"
    : "";
  return platform ? `${browser} on ${platform}` : browser;
}
