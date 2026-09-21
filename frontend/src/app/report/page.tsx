import type { Metadata } from "next";

import { ReportPage } from "@/components/report-page";
import { RequireSession } from "@/lib/session";

export const metadata: Metadata = {
  title: "Report · Numera",
  robots: { index: false, follow: false },
};

export default function Page() {
  // The print view renders one of your own analyses, so it needs a session. The public,
  // read-only equivalent is `/share/[token]`, which carries its own secret instead.
  return (
    <RequireSession>
      <ReportPage />
    </RequireSession>
  );
}
