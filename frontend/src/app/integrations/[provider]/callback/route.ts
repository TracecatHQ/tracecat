import { NextRequest, NextResponse } from "next/server"

import { buildUrl } from "@/lib/ss-utils"
import { isIntegrationOauthCallback } from "@/lib/utils"

export const GET = async (
  request: NextRequest,
  { params }: { params: Promise<{ provider: string }> }
) => {
  console.log("RECEIVED GET /integrations/[provider]/callback", request)
  const { provider } = await params
  console.log("Got provider", { provider })
  const state = request.nextUrl.searchParams.get("state")
  if (!request.nextUrl.searchParams.get("code") || !state) {
    console.error("Missing code or state in request")
    return NextResponse.redirect(new URL("/auth/error"))
  }

  const workspaceId = state.split(":")[0]
  const url = new URL(buildUrl(`/integrations/${provider}/callback`))
  url.search = request.nextUrl.search
  // Add workspaceId as a search param if not already present
  if (!url.searchParams.has("workspace_id")) {
    url.searchParams.append("workspace_id", workspaceId)
  }

  const cookie = request.headers.get("cookie")
  if (!cookie) {
    console.error("Missing cookie in request")
    return NextResponse.redirect(new URL("/auth/error"))
  }

  const response = await fetch(url.toString(), {
    headers: {
      Cookie: cookie,
    },
  })

  // Redirect to the public app URL
  const cb = await response.json()
  if (!isIntegrationOauthCallback(cb)) {
    console.error("Invalid integration callback", cb)
    return NextResponse.redirect(new URL("/auth/error"))
  }
  const { status, provider_id: providerId, redirect_url } = cb
  if (providerId !== provider) {
    console.error("Invalid integration provider", providerId, provider)
    return NextResponse.redirect(new URL("/auth/error"))
  }

  console.log("Redirecing to", redirect_url)
  return NextResponse.redirect(redirect_url)
}
