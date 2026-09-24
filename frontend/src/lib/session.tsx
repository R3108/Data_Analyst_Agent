"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, auth, setSessionLostHandler } from "@/lib/api";
import type { AuthConfig, User } from "@/lib/types";

/**
 * Who is signed in, for the whole client app.
 *
 * This is a *convenience*, not a security boundary. It decides which screen to render;
 * it never decides what data anyone may see. The server re-checks the session on every
 * single request and answers 401 or 403 on its own authority, so a user who edits this
 * state in a debugger gets an admin-shaped UI full of empty panels and error toasts.
 *
 * Treating the client as advisory is the whole point: a React state variable is not a
 * permission, and anything that reads like one is a bug waiting to be demonstrated.
 */

export type SessionStatus = "loading" | "authenticated" | "anonymous";

interface SessionValue {
  user: User | null;
  status: SessionStatus;
  config: AuthConfig | null;
  isAdmin: boolean;
  /** Re-read the session from the server, e.g. after a role or profile change. */
  refresh: () => Promise<User | null>;
  /** Adopt the account a sign-in or sign-up just returned. */
  adopt: (user: User) => void;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");
  // Guards against a burst of concurrent 401s each pushing its own navigation.
  const expiring = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const { user: current } = await auth.session();
      setUser(current);
      setStatus(current ? "authenticated" : "anonymous");
      expiring.current = false;
      return current;
    } catch (error) {
      // A network failure is not a sign-out: the backend may simply be restarting, and
      // throwing the user back to the login screen would lose whatever they were doing.
      if (error instanceof ApiError && error.status === 0) return user;
      setUser(null);
      setStatus("anonymous");
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void refresh();
    auth.config().then(setConfig).catch(() => undefined);
  }, [refresh]);

  // Any API call that comes back 401 means the session ended somewhere else — it
  // expired, an admin suspended the account, or another device signed out everywhere.
  useEffect(() => {
    setSessionLostHandler(() => {
      if (expiring.current) return;
      expiring.current = true;
      setUser(null);
      setStatus("anonymous");
      const here = window.location.pathname + window.location.search;
      const next = here === "/" ? "" : `?next=${encodeURIComponent(here)}`;
      router.replace(`/login${next}`);
    });
    return () => setSessionLostHandler(null);
  }, [router]);

  const adopt = useCallback((next: User) => {
    expiring.current = false;
    setUser(next);
    setStatus("authenticated");
  }, []);

  const signOut = useCallback(async () => {
    try {
      await auth.logout();
    } catch {
      /* the cookie is cleared server-side on any outcome worth acting on */
    }
    setUser(null);
    setStatus("anonymous");
    // A full navigation rather than a client push: it drops every cached dataset,
    // board and message the previous account had loaded into memory.
    window.location.assign("/login");
  }, []);

  const value = useMemo<SessionValue>(
    () => ({ user, status, config, isAdmin: user?.role === "admin", refresh, adopt, signOut }),
    [user, status, config, refresh, adopt, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/**
 * Renders `children` only for a signed-in account, and sends everyone else to sign in.
 *
 * The server has already refused the data; this only avoids rendering a shell that
 * cannot be filled.
 */
export function RequireSession({
  children,
  fallback,
  adminOnly = false,
}: {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  adminOnly?: boolean;
}) {
  const router = useRouter();
  const { status, isAdmin } = useSession();

  useEffect(() => {
    if (status !== "anonymous") return;
    const here = window.location.pathname + window.location.search;
    // The bare root is somebody arriving, not somebody returning to a deep link: show
    // them the landing page. `proxy.ts` normally does this before the page ever loads.
    router.replace(here === "/" ? "/home" : `/login?next=${encodeURIComponent(here)}`);
  }, [status, router]);

  if (status === "loading") return <>{fallback ?? <SessionSkeleton />}</>;
  if (status === "anonymous") return <>{fallback ?? <SessionSkeleton />}</>;
  if (adminOnly && !isAdmin) return <NotAllowed />;
  return <>{children}</>;
}

function SessionSkeleton() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas">
      <div className="w-full max-w-sm space-y-3 px-6">
        <div className="h-8 w-8 animate-pulse rounded-lg bg-muted" />
        <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}

function NotAllowed() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-6">
      <div className="max-w-sm text-center">
        <h1 className="text-[19px] font-semibold tracking-tight text-ink">
          Administrators only
        </h1>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">
          Your account does not have access to this area. If you think it should, ask an
          administrator to change your role.
        </p>
        <a
          href="/"
          className="mt-5 inline-flex h-9 items-center rounded-lg bg-accent px-3.5 text-sm font-medium text-white transition hover:bg-accent-hover"
        >
          Back to the workspace
        </a>
      </div>
    </div>
  );
}
