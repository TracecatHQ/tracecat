import { type NextRequest, NextResponse } from "next/server"

import { buildUrl } from "@/lib/ss-utils"

export const GET = async (request: NextRequest) => {
  console.log("GET /organization/vcs/github/install")

  const url = new URL(buildUrl("/organization/vcs/github/install"))
  url.search = request.nextUrl.search

  const cookie = request.headers.get("cookie")
  if (!cookie) {
    console.error("Missing cookie in request")
    return NextResponse.redirect(new URL(buildUrl("/auth/error")))
  }

  const response = await fetch(url.toString(), {
    headers: {
      Cookie: cookie,
    },
    redirect: "manual", // We want to handle the redirect manually
  })

  // We are expecting a redirect URL in the response
  // const cb = await response.json()
  const location = response.headers.get("location")
  if (location) {
    console.log("Redirecting to", location)
    return NextResponse.redirect(location)
  }
  console.warn("No redirect location found in response")

  // Redirect to the app
  const resp = await fetch(buildUrl("/info"))
  const { public_app_url } = await resp.json()
  console.log("Public app URL", public_app_url)
  const redirect_url = new URL("/organization/vcs", public_app_url)
  return NextResponse.redirect(redirect_url)
}
