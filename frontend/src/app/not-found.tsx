import type { Metadata } from "next";
import Link from "next/link";

import { StatusPage, primaryLinkClass } from "@/components/status-page";

export const metadata: Metadata = {
  title: "Page not found — Numera",
  robots: { index: false, follow: false },
};

export default function NotFound() {
  return (
    <StatusPage
      code="404"
      title="We couldn't find that page"
      body="The link may be mistyped, or the analysis, board or report it pointed to has been deleted."
      actions={
        <Link href="/" className={primaryLinkClass}>
          Back to the workspace
        </Link>
      }
    />
  );
}
