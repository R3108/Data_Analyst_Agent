"use client";

import {
  ArrowLeft,
  CircleAlert,
  Database,
  HardDrive,
  KeyRound,
  LogOut,
  Plus,
  RefreshCw,
  ScrollText,
  Search,
  ShieldCheck,
  Trash,
  UserCheck,
  UserRound,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Button, EmptyState, TextInput } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, admin } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";
import type { AdminOverview, AdminUser, AuditEvent, Role } from "@/lib/types";

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

function bytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const scaled = value / 1024 ** index;
  return `${scaled >= 10 || index === 0 ? Math.round(scaled) : scaled.toFixed(1)} ${units[index]}`;
}

type Tab = "accounts" | "audit" | "security";

export function AdminDashboard() {
  const toast = useToast();
  const { user } = useSession();
  const [tab, setTab] = useState<Tab>("accounts");
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextOverview, nextUsers] = await Promise.all([admin.overview(), admin.listUsers()]);
      setOverview(nextOverview);
      setUsers(nextUsers);
    } catch (error) {
      toast.error("Couldn't load the dashboard", describe(error));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!users) return null;
    const needle = query.trim().toLowerCase();
    if (!needle) return users;
    return users.filter(
      (u) => u.email.toLowerCase().includes(needle) || u.name.toLowerCase().includes(needle),
    );
  }, [users, query]);

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
        <h1 className="ml-1 flex items-center gap-1.5 text-sm font-semibold text-ink">
          <ShieldCheck className="size-4 text-accent" />
          Administration
        </h1>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void load()}>
          <RefreshCw className="size-3.5" />
          <span className="hidden sm:inline">Refresh</span>
        </Button>
      </header>

      <div className="mx-auto w-full max-w-6xl px-6 py-7">
        <StatRow overview={overview} />

        <nav className="mt-7 flex gap-1 border-b border-line" aria-label="Sections">
          {(
            [
              ["accounts", "Accounts", <Users key="u" className="size-3.5" />],
              ["audit", "Audit log", <ScrollText key="a" className="size-3.5" />],
              ["security", "Security", <ShieldCheck key="s" className="size-3.5" />],
            ] as const
          ).map(([id, label, icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              aria-current={tab === id}
              className={cn(
                "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] transition",
                tab === id
                  ? "border-accent font-medium text-ink"
                  : "border-transparent text-ink-3 hover:text-ink",
              )}
            >
              {icon}
              {label}
            </button>
          ))}
        </nav>

        <div className="pt-5">
          {tab === "accounts" && (
            <>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-ink-3" />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search by name or email"
                    aria-label="Search accounts"
                    className="h-9 w-full rounded-lg border border-line bg-panel pr-2.5 pl-8 text-[13px] text-ink transition placeholder:text-ink-3 focus:border-accent focus:outline-none"
                  />
                </div>
                <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
                  <Plus className="size-3.5" />
                  New account
                </Button>
              </div>

              <UserTable
                users={filtered}
                currentUserId={user?.id ?? ""}
                onChanged={load}
              />
            </>
          )}
          {tab === "audit" && <AuditLog />}
          {tab === "security" && <SecurityPanel overview={overview} onChanged={load} />}
        </div>
      </div>

      {creating && (
        <CreateUserDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            void load();
          }}
        />
      )}
    </div>
  );
}

function StatRow({ overview }: { overview: AdminOverview | null }) {
  const stats = [
    {
      label: "Accounts",
      value: overview ? String(overview.users.total) : "—",
      hint: overview ? `${overview.users.admins} admin · ${overview.users.suspended} suspended` : "",
      icon: <Users className="size-4" />,
    },
    {
      label: "Signed in now",
      value: overview ? String(overview.sessions.signed_in_users) : "—",
      hint: overview ? `${overview.sessions.active} active sessions` : "",
      icon: <UserCheck className="size-4" />,
    },
    {
      label: "Datasets",
      value: overview ? String(overview.storage.datasets) : "—",
      hint: "across every workspace",
      icon: <Database className="size-4" />,
    },
    {
      label: "Storage",
      value: overview ? bytes(overview.storage.total_bytes) : "—",
      hint: "databases and cleaned tables",
      icon: <HardDrive className="size-4" />,
    },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {stats.map((stat) => (
        <div key={stat.label} className="rounded-xl border border-line bg-panel p-4 shadow-card">
          <div className="flex items-center gap-1.5 text-[11.5px] font-medium tracking-wide text-ink-3 uppercase">
            <span className="text-accent">{stat.icon}</span>
            {stat.label}
          </div>
          <p className="mt-2 text-2xl font-semibold tracking-tight text-ink tabular-nums">
            {stat.value}
          </p>
          <p className="mt-0.5 text-[11.5px] text-ink-3">{stat.hint}</p>
        </div>
      ))}
    </div>
  );
}

function UserTable({
  users,
  currentUserId,
  onChanged,
}: {
  users: AdminUser[] | null;
  currentUserId: string;
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [resetting, setResetting] = useState<AdminUser | null>(null);

  const act = async (id: string, run: () => Promise<unknown>, failure: string) => {
    setBusyId(id);
    try {
      await run();
      await onChanged();
    } catch (error) {
      toast.error(failure, describe(error));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (account: AdminUser) => {
    const typed = window.prompt(
      `This permanently deletes ${account.email} and every dataset, analysis and board in their workspace.\n\nType the email address to confirm:`,
    );
    if (typed === null) return;
    if (typed.trim().toLowerCase() !== account.email) {
      toast.error("Not deleted", "The address you typed did not match.");
      return;
    }
    await act(
      account.id,
      () => admin.deleteUser(account.id, account.email),
      "Couldn't delete the account",
    );
    toast.success("Account deleted", `${account.email} and their workspace have been erased.`);
  };

  if (users === null) {
    return (
      <div className="mt-4 space-y-2">
        {[0, 1, 2].map((row) => (
          <div key={row} className="h-16 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  if (users.length === 0) {
    return (
      <EmptyState
        icon={<Users className="size-5" />}
        title="No accounts match"
        body="Try a different search, or create an account for somebody."
      />
    );
  }

  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-line bg-panel shadow-card">
      <table className="w-full text-left text-[13px]">
        <thead className="border-b border-line text-[11px] tracking-wide text-ink-3 uppercase">
          <tr>
            <th scope="col" className="px-4 py-2.5 font-medium">Account</th>
            <th scope="col" className="hidden px-4 py-2.5 font-medium sm:table-cell">Workspace</th>
            <th scope="col" className="hidden px-4 py-2.5 font-medium md:table-cell">Last seen</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Role</th>
            <th scope="col" className="px-4 py-2.5 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {users.map((account) => {
            const self = account.id === currentUserId;
            const busy = busyId === account.id;
            return (
              <tr key={account.id} className={cn("align-middle", busy && "opacity-50")}>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2.5">
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[12px] font-semibold text-accent-ink">
                      {account.name.slice(0, 1).toUpperCase()}
                    </span>
                    <div className="min-w-0">
                      <p className="flex items-center gap-1.5 truncate font-medium text-ink">
                        {account.name}
                        {self && <Badge tone="neutral">You</Badge>}
                        {account.status !== "active" && <Badge tone="bad">Suspended</Badge>}
                      </p>
                      <p className="truncate text-[11.5px] text-ink-3">{account.email}</p>
                    </div>
                  </div>
                </td>
                <td className="hidden px-4 py-3 text-ink-2 sm:table-cell">
                  <span className="tabular-nums">{account.usage.datasets}</span> datasets ·{" "}
                  <span className="tabular-nums">{bytes(account.usage.storage_bytes)}</span>
                  <p className="text-[11.5px] text-ink-3">
                    {account.usage.sessions} analyses · {account.usage.boards} boards
                  </p>
                </td>
                <td className="hidden px-4 py-3 text-ink-2 md:table-cell">
                  {account.last_login_at ? relativeTime(account.last_login_at) : "never"}
                  <p className="text-[11.5px] text-ink-3">
                    {account.active_sessions} active session{account.active_sessions === 1 ? "" : "s"}
                  </p>
                </td>
                <td className="px-4 py-3">
                  <select
                    aria-label={`Role for ${account.email}`}
                    value={account.role}
                    disabled={busy}
                    onChange={(event) =>
                      void act(
                        account.id,
                        () => admin.updateUser(account.id, { role: event.target.value as Role }),
                        "Couldn't change the role",
                      )
                    }
                    className="h-8 rounded-lg border border-line bg-panel px-2 text-[12.5px] text-ink transition hover:bg-muted disabled:opacity-50"
                  >
                    <option value="user">Member</option>
                    <option value="admin">Administrator</option>
                  </select>
                </td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-0.5">
                    <IconAction
                      label="Set a temporary password"
                      onClick={() => setResetting(account)}
                      disabled={busy}
                    >
                      <KeyRound className="size-3.5" />
                    </IconAction>
                    <IconAction
                      label="Sign out every device"
                      disabled={busy || account.active_sessions === 0}
                      onClick={() =>
                        void act(
                          account.id,
                          () => admin.revokeSessions(account.id),
                          "Couldn't sign the account out",
                        )
                      }
                    >
                      <LogOut className="size-3.5" />
                    </IconAction>
                    <IconAction
                      label={account.status === "active" ? "Suspend account" : "Reactivate account"}
                      disabled={busy || self}
                      onClick={() =>
                        void act(
                          account.id,
                          () =>
                            admin.updateUser(account.id, {
                              status: account.status === "active" ? "suspended" : "active",
                            }),
                          "Couldn't change the status",
                        )
                      }
                    >
                      <CircleAlert
                        className={cn("size-3.5", account.status !== "active" && "text-warn")}
                      />
                    </IconAction>
                    <IconAction
                      label="Delete account and its data"
                      danger
                      disabled={busy || self}
                      onClick={() => void remove(account)}
                    >
                      <Trash className="size-3.5" />
                    </IconAction>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {resetting && (
        <SetPasswordDialog
          account={resetting}
          onClose={() => setResetting(null)}
          onDone={() => {
            setResetting(null);
            void onChanged();
          }}
        />
      )}
    </div>
  );
}

function IconAction({
  label,
  danger,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string; danger?: boolean }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-lg text-ink-3 transition disabled:opacity-30",
        danger ? "hover:bg-bad-soft hover:text-bad" : "hover:bg-muted hover:text-ink",
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function Dialog({
  title,
  description,
  onClose,
  children,
}: {
  title: string;
  description: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-sm rounded-2xl border border-line bg-panel p-5 shadow-pop"
      >
        <h2 className="text-[16px] font-semibold tracking-tight text-ink">{title}</h2>
        <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{description}</p>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

function CreateUserDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await admin.createUser({ email: email.trim(), name: name.trim(), password, role });
      toast.success("Account created", `Send ${email.trim()} their temporary password securely.`);
      onCreated();
    } catch (e) {
      setError(describe(e));
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Create an account"
      description="They will be required to choose their own password the first time they sign in."
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-3">
        <TextInput
          label="Email"
          type="email"
          required
          autoFocus
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="newhire@company.com"
        />
        <TextInput
          label="Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="New Hire"
        />
        <TextInput
          label="Temporary password"
          type="text"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          hint="Share it over a channel you trust. It is single-use in practice — they must replace it."
        />
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">Role</span>
          <select
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
            className="h-9 rounded-lg border border-line bg-panel px-2 text-[13px] text-ink"
          >
            <option value="user">Member</option>
            <option value="admin">Administrator</option>
          </select>
        </label>
        {error && (
          <p role="alert" className="rounded-lg border border-bad/30 bg-bad-soft p-2.5 text-[12.5px] text-ink">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={busy} disabled={!email.trim() || !password}>
            Create
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function SetPasswordDialog({
  account,
  onClose,
  onDone,
}: {
  account: AdminUser;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await admin.setPassword(account.id, password);
      toast.success("Temporary password set", `${account.email} has been signed out everywhere.`);
      onDone();
    } catch (e) {
      setError(describe(e));
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Set a temporary password"
      description={`${account.email} will be signed out of every device and must choose a new password before using the workspace again.`}
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-3">
        <TextInput
          label="Temporary password"
          type="text"
          required
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          hint="Deliver it over a channel you trust — not the email address you are resetting."
        />
        {error && (
          <p role="alert" className="rounded-lg border border-bad/30 bg-bad-soft p-2.5 text-[12.5px] text-ink">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={busy} disabled={!password}>
            Set password
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const EVENT_LABELS: Record<string, string> = {
  login: "Signed in",
  logout: "Signed out",
  "logout.all": "Signed out everywhere",
  register: "Account created",
  "session.revoke": "Device signed out",
  "password.change": "Password changed",
  "password.reset.request": "Reset link requested",
  "password.reset.confirm": "Password reset",
  "admin.user.create": "Admin created an account",
  "admin.user.role": "Admin changed a role",
  "admin.user.status": "Admin changed a status",
  "admin.user.password": "Admin set a password",
  "admin.user.delete": "Admin deleted an account",
};

function AuditLog() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);

  useEffect(() => {
    admin.audit({ limit: 200 }).then(setEvents).catch(() => setEvents([]));
  }, []);

  if (events === null) {
    return <div className="h-64 animate-pulse rounded-xl bg-muted" />;
  }
  if (events.length === 0) {
    return (
      <EmptyState
        icon={<ScrollText className="size-5" />}
        title="Nothing recorded yet"
        body="Sign-ins, password changes and administrative actions appear here as they happen."
      />
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-card">
      <ul className="divide-y divide-line">
        {events.map((event) => (
          <li key={event.id} className="flex items-start gap-3 px-4 py-2.5 text-[13px]">
            <span
              className={cn(
                "mt-1.5 size-1.5 shrink-0 rounded-full",
                event.outcome === "success" ? "bg-good" : "bg-warn",
              )}
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <p className="truncate text-ink">
                {EVENT_LABELS[event.event] ?? event.event}
                {event.outcome !== "success" && (
                  <span className="ml-1.5 text-warn">· {event.outcome.replace(/_/g, " ")}</span>
                )}
              </p>
              <p className="truncate text-[11.5px] text-ink-3">
                {event.user_email ?? event.email ?? "unknown account"}
                {event.ip ? ` · ${event.ip}` : ""}
                {event.detail ? ` · ${event.detail}` : ""}
              </p>
            </div>
            <span className="shrink-0 text-[11.5px] text-ink-3">
              {relativeTime(event.created_at)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SecurityPanel({
  overview,
  onChanged,
}: {
  overview: AdminOverview | null;
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  if (!overview) return <div className="h-64 animate-pulse rounded-xl bg-muted" />;
  const security = overview.security;

  /**
   * Each row is a posture check, not a settings form. These come from the environment
   * the server was started with, so the dashboard reports them and names the variable
   * to change — it does not offer to rewrite a running process's configuration.
   */
  const checks: { label: string; value: string; ok: boolean; hint: string }[] = [
    {
      label: "Password hashing",
      value: security.password_algorithm,
      ok: security.argon2_available,
      hint: security.argon2_available
        ? "Argon2id, the OWASP first choice."
        : "argon2-cffi is not installed; PBKDF2-SHA256 is in use. Install it and restart.",
    },
    {
      label: "Session cookies",
      value: security.secure_cookies ? `Secure · SameSite=${security.same_site}` : "Not marked Secure",
      ok: security.secure_cookies,
      hint: security.secure_cookies
        ? "Sent over HTTPS only, and unreadable by page scripts."
        : "Set COOKIE_SECURE=true and serve over HTTPS before going live.",
    },
    {
      label: "Session signing key",
      value: security.auth_secret_configured ? "Configured" : "Missing",
      ok: security.auth_secret_configured,
      hint: security.auth_secret_configured
        ? "Rotating AUTH_SECRET signs every account out, which is the intended effect."
        : "Without AUTH_SECRET the key is regenerated on each restart, signing everybody out.",
    },
    {
      label: "Environment",
      value: security.environment,
      ok: security.environment === "production",
      hint:
        security.environment === "production"
          ? "The interactive API docs are disabled."
          : "A development server exposes /docs and relaxes cookie rules.",
    },
    {
      label: "Sign-up",
      value: security.registration_enabled ? "Open" : "Invite only",
      ok: true,
      hint: security.allowed_domains.length
        ? `Limited to ${security.allowed_domains.map((d) => `@${d}`).join(", ")}.`
        : "Set REGISTRATION_ALLOWED_DOMAINS to limit who may sign up.",
    },
    {
      label: "Session lifetime",
      value: `${security.session_idle_days}d idle · ${security.session_absolute_days}d maximum`,
      ok: true,
      hint: "An idle session expires; an active one still stops at the hard limit.",
    },
    {
      label: "Password reset email",
      value: security.email_delivery ? "SMTP configured" : "Not configured",
      ok: security.email_delivery,
      hint: security.email_delivery
        ? "Reset links are emailed to the account holder."
        : "Without SMTP_HOST, reset links are written to the server log instead.",
    },
    {
      label: "Proxy headers",
      value: security.trust_forwarded_for ? "X-Forwarded-For trusted" : "Direct connections",
      ok: true,
      hint: security.trust_forwarded_for
        ? "Only correct behind a proxy that sets the header itself."
        : "Turn on TRUST_FORWARDED_FOR only when a reverse proxy is in front.",
    },
  ];

  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-card">
        <ul className="divide-y divide-line">
          {checks.map((check) => (
            <li key={check.label} className="flex items-start gap-3 px-4 py-3">
              <span
                className={cn(
                  "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full",
                  check.ok ? "bg-good-soft text-good" : "bg-warn-soft text-warn",
                )}
              >
                {check.ok ? <ShieldCheck className="size-3" /> : <CircleAlert className="size-3" />}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-ink">
                  {check.label}
                  <span className="ml-2 font-normal text-ink-2">{check.value}</span>
                </p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-ink-3">{check.hint}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="flex items-center gap-3 rounded-xl border border-line bg-panel p-4 shadow-card">
        <UserRound className="size-4 shrink-0 text-ink-3" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium text-ink">Housekeeping</p>
          <p className="text-[12px] leading-relaxed text-ink-3">
            Drop expired sessions, spent reset links and stale rate-limit counters. This
            runs hourly on its own; nothing here can authenticate anything either way.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() =>
            void admin
              .purgeSessions()
              .then(async (result) => {
                toast.success(
                  "Housekeeping done",
                  `${result.removed_sessions} expired session${result.removed_sessions === 1 ? "" : "s"} removed.`,
                );
                await onChanged();
              })
              .catch((error) => toast.error("Couldn't run housekeeping", describe(error)))
          }
        >
          Run now
        </Button>
      </div>
    </div>
  );
}
