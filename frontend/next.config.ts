import type { NextConfig } from "next";

/**
 * The backend, as reached from the Next.js *server* (not the browser).
 *
 * `BACKEND_URL` is a server-only variable on purpose — it is not prefixed with
 * `NEXT_PUBLIC_`, so it is never inlined into the JavaScript bundle. Inside Docker it
 * is a service name the browser could not resolve anyway.
 */
const BACKEND_URL = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,

  /**
   * Serve the API from this app's own origin.
   *
   * This is what makes the session a first-party cookie: the browser sets, sends and
   * expires it against one origin, so there are no cross-site cookie rules to satisfy,
   * no CORS preflight on every call, and nothing for a browser's third-party cookie
   * policy to drop. It also means `proxy.ts` can see the cookie at all.
   *
   * Setting `NEXT_PUBLIC_API_URL` makes the browser talk to the backend directly and
   * bypass this rewrite. That is supported, but the two hosts must then share a
   * registrable domain for a `SameSite=Lax` cookie to travel between them, and the
   * backend's `CORS_ORIGINS` must name this app.
   */
  async rewrites() {
    if (process.env.NEXT_PUBLIC_API_URL) return [];
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },

  async headers() {
    return [
      {
        // Applied to the app's own pages, not to the rewritten API responses, which
        // carry their own headers from the backend.
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          // The app renders nobody else's content in a frame, and nobody should render
          // it in one: clickjacking a "delete account" button is the classic use.
          { key: "X-Frame-Options", value: "DENY" },
          // A reset-password URL carries a single-use token in its query string. Never
          // let it travel in a `Referer` header to anything a page happens to load.
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-DNS-Prefetch-Control", value: "off" },
        ],
      },
    ];
  },
};

export default nextConfig;
