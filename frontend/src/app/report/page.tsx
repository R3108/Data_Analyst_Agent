import type { Metadata } from "next";

import { ReportPage } from "@/components/report-page";

export const metadata: Metadata = {
  title: "Report · Numera",
  robots: { index: false, follow: false },
};

export default function Page() {
  return <ReportPage />;
}
