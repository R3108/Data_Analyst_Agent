"use client";

import { useEffect } from "react";

import { StatusPage, primaryLinkClass, secondaryLinkClass } from "@/components/status-page";
import { themeBootScript } from "@/lib/theme";

import "./globals.css";

/**
 * The last line of defence: a crash in the root layout itself. It replaces the whole
 * document, so it brings its own <html>, styles and theme rather than inheriting them.
 */
export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <title>Something went wrong — Numera</title>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <StatusPage
          code="Something went wrong"
          title="Numera couldn't load"
          body="An unexpected error stopped the app from starting. Your data is safe. Try again in a moment."
          actions={
            <>
              <button onClick={() => retry()} className={primaryLinkClass}>
                Try again
              </button>
              <a href="/" className={secondaryLinkClass}>
                Reload
              </a>
            </>
          }
          detail={error.digest ? <>Reference: <code className="font-mono">{error.digest}</code></> : undefined}
        />
      </body>
    </html>
  );
}
