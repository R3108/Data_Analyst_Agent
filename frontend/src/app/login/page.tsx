import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";

export const metadata: Metadata = {
  title: "Sign in — Numera",
  // Sign-in screens have nothing to offer a search engine and everything to lose by
  // being indexed alongside a password field.
  robots: { index: false, follow: false },
};

export default function LoginPage() {
  // `useSearchParams` reads the `next` and `reset` hints, so the form renders inside a
  // Suspense boundary rather than opting the whole route out of static rendering.
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
