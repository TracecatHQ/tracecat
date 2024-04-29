import { NextResponse } from "next/server"
import clerkMiddleware from "@/middleware/clerk"

import { authConfig } from "@/config/auth"

const middleware = authConfig.disabled
  ? () => {
      console.warn("Auth is disabled, skipping middleware")
      return NextResponse.next()
    }
  : clerkMiddleware

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
