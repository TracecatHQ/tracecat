import { NextResponse, type NextRequest } from "next/server"
import { updateSession } from "@/utils/supabase/middleware"
import { get } from "@vercel/edge-config"

export async function middleware(request: NextRequest) {
  if (
    process.env.NEXT_PUBLIC_APP_ENV === "production" &&
    (await get("isUnderMaintenance"))
  ) {
    request.nextUrl.pathname = `/status`
    console.log("Redirecting to status page")
    return NextResponse.rewrite(request.nextUrl)
  }
  return await updateSession(request)
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
