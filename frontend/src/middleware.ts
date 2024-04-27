import { NextResponse, type NextRequest } from "next/server"
import {
  clerkMiddleware,
  ClerkMiddlewareAuth,
  createRouteMatcher,
} from "@clerk/nextjs/server"
import { get } from "@vercel/edge-config"

import { authConfig } from "@/config/auth"

const isProtectedRoute = createRouteMatcher([
  "/workflows(.*)",
  "/settings(.*)",
  "/library(.*)",
  "/onboarding(.*)",
])
export default clerkMiddleware(
  async (auth: ClerkMiddlewareAuth, req: NextRequest) => {
    if (authConfig.disabled) {
      console.warn("Auth disabled, skipping authentication middleware")
      return NextResponse.next()
    }
    // ** Site down **
    if (
      process.env.NEXT_PUBLIC_APP_ENV === "production" &&
      (await get("isUnderMaintenance"))
    ) {
      req.nextUrl.pathname = "/status"
      console.log("Redirecting to status page")
      return NextResponse.rewrite(req.nextUrl)
    }

    const { userId, sessionClaims, redirectToSignIn } = auth()
    const isProtected = isProtectedRoute(req)

    // ** Unauthorized **
    // User isn't signed in and the route is private -- redirect to sign-in
    if (!userId && isProtected) {
      // NOTE: Using this API requires NEXT_PUBLIC_CLERK_SIGN_IN_URL
      // Otherwise, use `return NextResponse.redirect("/sign-in")`
      return redirectToSignIn({ returnBackUrl: req.url })
    }

    // ** Authorized **
    // // For user visiting /onboarding, don't try and redirect
    // if (userId && req.nextUrl.pathname === "/onboarding") {
    //   return NextResponse.next()
    // }
    // Catch users who doesn't have `onboardingComplete: true` in PublicMetata
    // Redirect them to the /onboading out to complete onboarding
    if (userId && !sessionClaims?.metadata?.onboardingComplete) {
      const onboardingUrl = new URL("/onboarding", req.url)
      console.log("Redirecting to onboarding", onboardingUrl.toString())
      return NextResponse.rewrite(onboardingUrl)
    }
    // Handle normally
    if (isProtectedRoute(req)) auth().protect()
  }
)
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
