"use client";

import { RotateCcw } from "lucide-react";
import { useEffect } from "react";

import { StatusPage, primaryLinkClass, secondaryLinkClass } from "@/components/status-page";

/** Catches a render crash anywhere below the root layout, so one bad view never blanks the app. */
export default function RouteError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <StatusPage
      code="Something went wrong"
      title="This page hit an unexpected error"
      body="Your data is safe — nothing was lost. Try again, and if it keeps happening, reload the workspace."
      actions={
        <>
          <button onClick={() => retry()} className={primaryLinkClass}>
            <RotateCcw className="size-4" />
            Try again
          </button>
          {/* A full navigation, not a client push: it discards whatever state crashed. */}
          <a href="/" className={secondaryLinkClass}>
            Reload the workspace
          </a>
        </>
      }
      detail={error.digest ? <>Reference: <code className="font-mono">{error.digest}</code></> : undefined}
    />
  );
}
