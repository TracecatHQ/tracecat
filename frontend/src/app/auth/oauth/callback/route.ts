import { NextRequest, NextResponse } from "next/server"

import { buildUrl } from "@/lib/ss-utils"

/**
 * @param request
 * @returns
 */
export const GET = async (request: NextRequest) => {
  console.log("GET /auth/oauth/callback")
  const url = new URL(buildUrl("/auth/oauth/callback"))
  url.search = request.nextUrl.search

  const response = await fetch(url.toString())
  const setCookieHeader = response.headers.get("set-cookie")

  // Get  redirect
  const resp = await fetch(buildUrl("/info"))
  const { public_app_url } = await resp.json()
  console.log("Public app URL", public_app_url)

  if (!setCookieHeader) {
    console.error("No set-cookie header found in response")
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  console.log("Redirecting to /")
  const redirectResponse = NextResponse.redirect(new URL("/", public_app_url))
  redirectResponse.headers.set("set-cookie", setCookieHeader)
  return redirectResponse
}
