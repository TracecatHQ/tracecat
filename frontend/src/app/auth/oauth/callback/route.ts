import { NextRequest, NextResponse } from "next/server"

import { buildUrl, getDomain } from "@/lib/ss-utils"

/**
 * Wrapper around the FastAPI endpoint /auth/oauth/callback,
 * which adds back a redirect to the main app.
 * @param request
 * @returns
 */
export const GET = async (request: NextRequest) => {
  const url = new URL(buildUrl("/auth/oauth/callback"))
  url.search = request.nextUrl.search

  const response = await fetch(url.toString())
  const setCookieHeader = response.headers.get("set-cookie")

  if (!setCookieHeader) {
    return NextResponse.redirect(new URL("/auth/error", getDomain(request)))
  }

  const redirectResponse = NextResponse.redirect(
    new URL("/", getDomain(request))
  )
  redirectResponse.headers.set("set-cookie", setCookieHeader)
  return redirectResponse
}
