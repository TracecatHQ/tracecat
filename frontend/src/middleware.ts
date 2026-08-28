import { NextResponse } from "next/server"
import { buildContentSecurityPolicyFromEnv } from "@/lib/csp"

// `TRACECAT__CSP_CONNECT_SRC_ORIGINS` is read from the runtime environment once
// at cold start, so the server must be restarted for a change to take effect.
// `NEXT_PUBLIC_POSTHOG_KEY` is inlined at build time when it is set during
// `next build`, which is also the only configuration in which the PostHog
// provider (`src/providers/posthog.tsx`) initializes, so the two stay
// consistent. Keep both as direct `process.env.X` member expressions: that is
// what Next's build-time inlining matches.
const CONTENT_SECURITY_POLICY = buildContentSecurityPolicyFromEnv({
  NEXT_PUBLIC_POSTHOG_KEY: process.env.NEXT_PUBLIC_POSTHOG_KEY,
  TRACECAT__CSP_CONNECT_SRC_ORIGINS:
    process.env.TRACECAT__CSP_CONNECT_SRC_ORIGINS,
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
