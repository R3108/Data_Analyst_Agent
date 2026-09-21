import { NextResponse, type NextRequest } from "next/server";

/**
 * An optimistic redirect for signed-out visitors.
 *
 * Next 16 renamed Middleware to Proxy; the contract is the same, and so is the
 * warning in its documentation: this is *not* session management. All it can see is
 * whether a cookie exists — not whether it is valid, unexpired, unrevoked, or attached
 * to an account that still has the role a page needs. A forged cookie called
 * `numera_session=x` walks straight past this file.
 *
 * That is fine, because nothing is protected here. The backend re-authenticates every
 * request and refuses the data, and `RequireSession` decides what to render. This only
 * spares a signed-out visitor the flash of an app shell that is about to redirect
 * itself, which is a latency win rather than a security control.
 *
 * It can only see the cookie at all when the API is reached through this app's own
 * origin — the default. Pointing `NEXT_PUBLIC_API_URL` at the backend directly makes
 * the session cookie belong to that host instead, so the check is skipped and the
 * client-side gate does the whole job.
 */

const SESSION_COOKIE = "numera_session";
// Routes that render the app shell and are useless without an account.
const PRIVATE_PREFIXES = ["/admin", "/account"];
// Sign-in screens, which a signed-in visitor should not be sitting on.
const AUTH_ROUTES = ["/login", "/register"];

const SAME_ORIGIN_API = !process.env.NEXT_PUBLIC_API_URL;

function hasSessionCookie(request: NextRequest): boolean {
  return Boolean(
    request.cookies.get(SESSION_COOKIE) ?? request.cookies.get(`__Host-${SESSION_COOKIE}`),
  );
}

export function proxy(request: NextRequest) {
  if (!SAME_ORIGIN_API) return NextResponse.next();

  const { pathname, search } = request.nextUrl;
  const signedIn = hasSessionCookie(request);

  if (!signedIn && PRIVATE_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }

  if (signedIn && AUTH_ROUTES.includes(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Everything except the API rewrite, Next's own assets and static files. `/reset-password`
  // is intentionally included in the matcher but not in either list above: it must stay
  // reachable while signed in, because that is how a stale session recovers its account.
  matcher: ["/((?!api/|_next/|favicon.ico|.*\\.[^/]+$).*)"],
};
