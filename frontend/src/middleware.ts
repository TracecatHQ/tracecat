import { NextResponse, type NextRequest } from "next/server"

export function middleware(request: NextRequest) {
  return NextResponse.redirect(new URL(`/workflows`, request.url))
}

export const config = {
  matcher: "/:path", // Match only on root path
}
