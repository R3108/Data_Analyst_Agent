import type { Metadata } from "next";

import { AdminDashboard } from "@/components/admin/admin-dashboard";
import { RequireSession } from "@/lib/session";

export const metadata: Metadata = {
  title: "Administration — Numera",
  robots: { index: false, follow: false },
};

export default function AdminPage() {
  // `adminOnly` decides which screen to render. It is not the permission: every call
  // this page makes is authorised again on the server, which answers 403 to a member
  // however the page got rendered.
  return (
    <RequireSession adminOnly>
      <AdminDashboard />
    </RequireSession>
  );
}
