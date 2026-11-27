import { type NextRequest, NextResponse } from "next/server"
import {
  buildContentSecurityPolicy,
  contentSecurityPolicyOptionsFromEnv,
  generateNonce,
  NONCE_HEADER,
} from "@/lib/csp"

// `TRACECAT__CSP_CONNECT_SRC_ORIGINS` is read from the runtime environment once
// at cold start, so the server must be restarted for a change to take effect.
// `NEXT_PUBLIC_POSTHOG_KEY` is inlined at build time when it is set during
// `next build`, which is also the only configuration in which the PostHog
// provider (`src/providers/posthog.tsx`) initializes, so the two stay
// consistent. Keep both as direct `process.env.X` member expressions: that is
// what Next's build-time inlining matches.
const CSP_OPTIONS = contentSecurityPolicyOptionsFromEnv({
  NEXT_PUBLIC_POSTHOG_KEY: process.env.NEXT_PUBLIC_POSTHOG_KEY,
  TRACECAT__CSP_CONNECT_SRC_ORIGINS:
    process.env.TRACECAT__CSP_CONNECT_SRC_ORIGINS,
})

// Next.js reads the nonce from the request's CSP header to tag its own inline
// scripts, and the root layout reads `x-nonce` for the rest. Every page is
// already dynamically rendered (`PublicEnvScript` opts out of static caching),
// so a per-request nonce costs no prerendering.
function middleware(request: NextRequest) {
  const nonce = generateNonce()
  const policy = buildContentSecurityPolicy({ ...CSP_OPTIONS, nonce })
  const requestHeaders = new Headers(request.headers)
  requestHeaders.set(NONCE_HEADER, nonce)
  requestHeaders.set("content-security-policy", policy)
  const response = NextResponse.next({ request: { headers: requestHeaders } })
  response.headers.set("content-security-policy", policy)
  return response
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
