import type { Metadata } from "next";

import { ReportView } from "@/components/report-view";

export const metadata: Metadata = {
  title: "Shared report · Numera",
  robots: { index: false, follow: false },
};

export default async function SharePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <ReportView source={{ token }} />;
}
