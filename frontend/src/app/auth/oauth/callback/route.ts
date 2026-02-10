import { type NextRequest, NextResponse } from "next/server"

import {
  decodeAndSanitizeReturnUrl,
  POST_AUTH_RETURN_URL_COOKIE_NAME,
  serializeClearPostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"
import { buildUrl } from "@/lib/ss-utils"

/**
 * @param request
 * @returns
 */
export const GET = async (request: NextRequest) => {
  console.log("GET /auth/oauth/callback")
  const url = new URL(buildUrl("/auth/oauth/callback"))
  url.search = request.nextUrl.search
  const returnUrl = decodeAndSanitizeReturnUrl(
    request.cookies.get(POST_AUTH_RETURN_URL_COOKIE_NAME)?.value
  )

  const incomingCookie = request.headers.get("cookie")
  const response = await fetch(url.toString(), {
    headers: incomingCookie ? { cookie: incomingCookie } : undefined,
    cache: "no-store",
  })
  const setCookieHeader = response.headers.get("set-cookie")

  if (!response.ok) {
    console.error(
      `OAuth callback failed with status ${response.status}: ${await response.text()}`
    )
    const resp = await fetch(buildUrl("/info"))
    const { public_app_url } = await resp.json()
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  // Get redirect
  const resp = await fetch(buildUrl("/info"))
  const { public_app_url } = await resp.json()
  console.log("Public app URL", public_app_url)

  if (!setCookieHeader) {
    console.error("No set-cookie header found in response")
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  const targetPath = returnUrl ?? "/"
  console.log(`Redirecting to ${targetPath}`)
  const redirectResponse = NextResponse.redirect(
    new URL(targetPath, public_app_url)
  )
  redirectResponse.headers.append("set-cookie", setCookieHeader)
  redirectResponse.headers.append(
    "set-cookie",
    serializeClearPostAuthReturnUrlCookie(request.nextUrl.protocol === "https:")
  )
  return redirectResponse
}
