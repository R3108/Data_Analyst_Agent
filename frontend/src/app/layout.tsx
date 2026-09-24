import type { Metadata, Viewport } from "next";
import { Suspense } from "react";

import { SessionProvider } from "@/lib/session";
import { ThemeProvider, themeBootScript } from "@/lib/theme";
import { ConfirmProvider } from "@/components/ui/confirm";
import { ToastProvider } from "@/components/ui/toast";

import "./globals.css";

export const metadata: Metadata = {
  title: "Numera — AI Data Analyst",
  description:
    "Upload a CSV or Excel file and ask questions in plain English. Numera cleans your data, writes and runs Python in a secure sandbox, and explains the business insights.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f6f3" },
    { media: "(prefers-color-scheme: dark)", color: "#0f0f0e" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <ThemeProvider>
          <ToastProvider>
            <ConfirmProvider>
              {/* The provider reads the current route so it can send an expired session
                  back to the page it was on. */}
              <Suspense>
                <SessionProvider>{children}</SessionProvider>
              </Suspense>
            </ConfirmProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
