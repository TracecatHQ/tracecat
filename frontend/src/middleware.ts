import { NextResponse } from "next/server"
import { buildContentSecurityPolicy, parseOrigins } from "@/lib/csp"

// Captured once at cold start, so the server must be restarted for a change to
// `TRACECAT__CSP_CONNECT_SRC_ORIGINS` to take effect. PostHog is keyed on
// `NEXT_PUBLIC_POSTHOG_KEY` because that is the key `src/providers/posthog.tsx`
// initializes with.
const CONTENT_SECURITY_POLICY = buildContentSecurityPolicy({
  posthogEnabled: Boolean(process.env.NEXT_PUBLIC_POSTHOG_KEY),
  extraConnectSrc: parseOrigins(process.env.TRACECAT__CSP_CONNECT_SRC_ORIGINS),
})

function middleware() {
  return NextResponse.next({
    headers: { "content-security-policy": CONTENT_SECURITY_POLICY },
  })
}

export default middleware
export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * Feel free to modify this pattern to include more paths.
     */
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
    "/status",
  ],
}
