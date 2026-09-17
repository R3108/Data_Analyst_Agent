"use client";

import { ExternalLink, Globe, Link2, LockOpen, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, CopyButton, IconButton } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";

export interface ShareTarget {
  kind: "session" | "board";
  id: string;
  title: string;
  token: string | null;
}

export function ShareDialog({
  target,
  onClose,
  onTokenChange,
}: {
  target: ShareTarget;
  onClose: () => void;
  onTokenChange: (token: string | null) => void;
}) {
  const toast = useToast();
  const [token, setToken] = useState(target.token);
  const [busy, setBusy] = useState(false);
  const url = token ? `${window.location.origin}/share/${token}` : null;
  const noun = target.kind === "session" ? "analysis" : "board";

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const update = async (enable: boolean) => {
    setBusy(true);
    try {
      let next: string | null = null;
      if (enable) {
        next = (target.kind === "session" ? await api.shareSession(target.id) : await api.shareBoard(target.id)).token;
      } else if (target.kind === "session") {
        await api.unshareSession(target.id);
      } else {
        await api.unshareBoard(target.id);
      }
      setToken(next);
      onTokenChange(next);
      if (!enable) toast.info("Link disabled", "Anyone with the old link can no longer view it.");
    } catch (error) {
      toast.error("Couldn't update sharing", error instanceof ApiError ? error.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Share ${noun}`}
        className="animate-rise w-full max-w-md rounded-2xl border border-line bg-panel p-5 shadow-pop"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <Globe className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-base font-semibold text-ink">Share {noun}</p>
            <p className="truncate text-[13px] text-ink-2">{target.title}</p>
          </div>
          <IconButton label="Close" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>

        {url ? (
          <div className="mt-5 space-y-3">
            <div className="flex items-center gap-2 rounded-lg border border-line bg-muted/50 py-1 pr-1 pl-3">
              <Link2 className="size-3.5 shrink-0 text-ink-3" />
              <input
                readOnly
                value={url}
                onFocus={(e) => e.currentTarget.select()}
                aria-label="Share link"
                className="min-w-0 flex-1 bg-transparent font-mono text-[12px] text-ink outline-none focus-visible:outline-none"
              />
              <CopyButton text={url} label="Copy link" />
            </div>
            <p className="flex items-start gap-2 text-[12.5px] leading-relaxed text-ink-2">
              <LockOpen className="mt-0.5 size-3.5 shrink-0 text-warn" />
              Anyone with this link can view a read-only version, including charts, tables and the analysis code. It
              always shows the latest content.
            </p>
            <div className="flex items-center justify-between gap-2 pt-1">
              <Button variant="danger" size="sm" onClick={() => update(false)} loading={busy}>
                Disable link
              </Button>
              <Button size="sm" onClick={() => window.open(url, "_blank", "noopener")}>
                <ExternalLink className="size-3.5" />
                Open
              </Button>
            </div>
          </div>
        ) : (
          <div className="mt-5 space-y-4">
            <p className="text-[13px] leading-relaxed text-ink-2">
              Create a read-only link to this {noun} for stakeholders. They don&apos;t need an account, and you can
              disable the link at any time.
            </p>
            <Button variant="primary" className="w-full" onClick={() => update(true)} loading={busy}>
              <Link2 className="size-4" />
              Create share link
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
