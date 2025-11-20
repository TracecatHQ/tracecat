import { type NextRequest, NextResponse } from "next/server"

import { buildUrl } from "@/lib/ss-utils"
import { isIntegrationOAuthCallback } from "@/lib/utils"

export const GET = async (request: NextRequest) => {
  console.log("RECEIVED GET /integrations/callback", request)

  let cachedPublicAppUrl: string | null = null
  const resolvePublicAppUrl = async () => {
    if (cachedPublicAppUrl) {
      return cachedPublicAppUrl
    }

    try {
      const response = await fetch(buildUrl("/info"))
      if (response.ok) {
        const { public_app_url } = await response.json()
        if (typeof public_app_url === "string" && public_app_url.length > 0) {
          cachedPublicAppUrl = public_app_url
          return cachedPublicAppUrl
        }
        console.error(
          "public_app_url missing from /info response",
          public_app_url
        )
      } else {
        console.error(
          "Failed to fetch /info for public app url",
          response.status,
          response.statusText
        )
      }
    } catch (error_) {
      console.error("Failed to resolve public app url", error_)
    }

    cachedPublicAppUrl = request.nextUrl.origin
    return cachedPublicAppUrl
  }

  // Check for OAuth error response
  const error = request.nextUrl.searchParams.get("error")
  const errorDescription = request.nextUrl.searchParams.get("error_description")

  if (error) {
    console.error("OAuth error received:", error, errorDescription)
    // Redirect to OAuth error page with error details
    const errorUrl = new URL("/integrations/error", await resolvePublicAppUrl())
    errorUrl.searchParams.set("error", error)
    if (errorDescription) {
      errorUrl.searchParams.set("error_description", errorDescription)
    }
    return NextResponse.redirect(errorUrl)
  }

  const state = request.nextUrl.searchParams.get("state")
  if (!request.nextUrl.searchParams.get("code") || !state) {
    console.error("Missing code or state in request")
    return NextResponse.redirect(
      new URL("/auth/error", await resolvePublicAppUrl())
    )
  }

  const url = new URL(buildUrl(`/integrations/callback`))
  url.search = request.nextUrl.search

  const cookie = request.headers.get("cookie")
  if (!cookie) {
    console.error("Missing cookie in request")
    return NextResponse.redirect(
      new URL("/auth/error", await resolvePublicAppUrl())
    )
  }

  const response = await fetch(url.toString(), {
    headers: {
      Cookie: cookie,
    },
  })

  // Redirect to the public app URL
  const cb = await response.json()
  if (!isIntegrationOAuthCallback(cb)) {
    console.error("Invalid integration callback", cb)
    return NextResponse.redirect(
      new URL("/auth/error", await resolvePublicAppUrl())
    )
  }
  const { redirect_url } = cb

  console.log("Redirecting to", redirect_url)
  return NextResponse.redirect(redirect_url)
}
