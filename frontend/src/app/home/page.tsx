import type { Metadata } from "next";

import { LandingPage } from "@/components/landing/landing-page";

const TITLE = "Numera — The AI data analyst that shows its work";
const DESCRIPTION =
  "Upload a spreadsheet or connect a database and ask questions in plain English. Numera cleans your data, runs Python in a secure sandbox, and verifies every figure in its answer.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  openGraph: { type: "website", siteName: "Numera", title: TITLE, description: DESCRIPTION },
  twitter: { card: "summary", title: TITLE, description: DESCRIPTION },
};

export default function HomePage() {
  return <LandingPage />;
}
