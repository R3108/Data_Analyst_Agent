"use client";

import { KeyRound, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { LogoMark } from "@/components/brand";
import { Button, TextInput } from "@/components/ui/primitives";
import { ApiError, api, auth } from "@/lib/api";

/**
 * Shown only when the backend is running with WORKSPACE_TOKENS set and the stored token
 * is missing or rejected. An open workspace never sees this screen at all — that is the
 * right default for a tool pointed at your own laptop.
 */
export function WorkspaceGate({ onAuthenticated }: { onAuthenticated: (name: string) => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    auth.set(token.trim());
    try {
      const identity = await api.me();
      onAuthenticated(identity.name);
    } catch (e) {
      auth.set(null);
      setError(
        e instanceof ApiError && e.status === 401
          ? "That token is not valid for this workspace."
          : "Couldn't reach the workspace. Is the backend running?",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-line bg-panel p-6 shadow-pop"
      >
        <LogoMark className="size-9" />
        <h1 className="mt-4 text-[19px] font-semibold tracking-tight text-ink">
          This workspace is protected
        </h1>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">
          Enter your access token to continue. Your name comes from the token, and it is what
          appears on the comments and activity you leave here.
        </p>

        <div className="mt-5">
          <TextInput
            label="Access token"
            type="password"
            autoFocus
            autoComplete="current-password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="••••••••••••"
          />
        </div>

        {error && (
          <p className="mt-3 flex items-start gap-2 rounded-lg border border-bad/30 bg-bad-soft p-2.5 text-[12.5px] leading-relaxed text-ink">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-bad" />
            {error}
          </p>
        )}

        <Button
          type="submit"
          variant="primary"
          className="mt-4 w-full"
          loading={busy}
          disabled={!token.trim()}
        >
          <KeyRound className="size-4" />
          Enter workspace
        </Button>

        <p className="mt-4 text-[11.5px] leading-relaxed text-ink-3">
          Tokens are configured on the server with{" "}
          <code className="font-mono">WORKSPACE_TOKENS=token:Your Name</code>. Leave it unset to
          run Numera open, as it does by default.
        </p>
      </form>
    </div>
  );
}
