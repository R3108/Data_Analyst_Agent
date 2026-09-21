import type { Metadata } from "next";
import { Suspense } from "react";

import { AccountView } from "@/components/auth/account-view";
import { RequireSession } from "@/lib/session";

export const metadata: Metadata = {
  title: "Your account — Numera",
  robots: { index: false, follow: false },
};

export default function AccountPage() {
  return (
    <RequireSession>
      <Suspense>
        <AccountView />
      </Suspense>
    </RequireSession>
  );
}
