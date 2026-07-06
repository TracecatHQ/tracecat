import { NextResponse } from "next/server"
import { buildContentSecurityPolicy } from "@/lib/content-security-policy"

export default function middleware() {
  const response = NextResponse.next()
  response.headers.set("Content-Security-Policy", buildContentSecurityPolicy())
  return response
}

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
