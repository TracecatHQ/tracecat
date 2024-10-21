import { NextRequest, NextResponse } from "next/server"

import { buildUrl, getDomain } from "@/lib/ss-utils"

/**
 * Wrapper around the FastAPI endpoint /auth/oauth/callback,
 * which adds back a redirect to the main app.
 * @param request
 * @returns
 */
export const GET = async (request: NextRequest) => {
  console.log("GET /auth/saml/callback", request.nextUrl.toString())
  const url = new URL(buildUrl("/auth/saml/callback"))
  url.search = request.nextUrl.search

  // Forward this request to the FastAPI backend
  const response = await fetch(url.toString())
  const setCookieHeader = response.headers.get("set-cookie")

  if (!setCookieHeader) {
    console.error("No set-cookie header found in response")
    return NextResponse.redirect(new URL("/auth/error", getDomain(request)))
  }

  console.log("Redirecting to /")
  const redirectResponse = NextResponse.redirect(
    new URL("/", getDomain(request))
  )
  redirectResponse.headers.set("set-cookie", setCookieHeader)
  return redirectResponse
}
