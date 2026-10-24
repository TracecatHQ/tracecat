/** @jest-environment node */

import { NextRequest } from "next/server"
import { GET as oauthCallback } from "@/app/auth/oauth/callback/route"
import {
  POST_AUTH_RETURN_URL_COOKIE_NAME,
  serializePostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"

async function redirectAfterSignIn(cookie?: string): Promise<URL> {
  jest
    .spyOn(global, "fetch")
    .mockResolvedValueOnce(
      new Response(null, {
        status: 204,
        headers: { "set-cookie": "fastapiusersauth=synthetic; Path=/" },
      })
    )
    .mockResolvedValueOnce(
      Response.json({ public_app_url: "http://localhost" })
    )
  jest.spyOn(console, "log").mockImplementation(() => {})
  const request = new NextRequest(
    "http://localhost/auth/oauth/callback?code=synthetic&state=synthetic",
    cookie ? { headers: { cookie } } : undefined
  )
  const response = await oauthCallback(request)
  const location = response.headers.get("location")
  if (!location) {
    throw new Error("expected a redirect")
  }
  return new URL(location)
}

afterEach(() => {
  jest.restoreAllMocks()
})

describe("OAuth callback redirect", () => {
  // The sign-in page sends a session that still owes a security key check to
  // /mfa. The app root only knows fully signed-in users, so an MFA-pending
  // OIDC sign-in without a return URL used to stall there.
  it("sends a sign-in without a return URL through the sign-in page", async () => {
    const url = await redirectAfterSignIn()

    expect(url.pathname).toBe("/sign-in")
    expect(url.search).toBe("")
  })

  it("keeps a saved return URL on the way through the sign-in page", async () => {
    const saved = serializePostAuthReturnUrlCookie(
      "/workspaces/synthetic",
      false
    ).split(";")[0]
    expect(saved.startsWith(`${POST_AUTH_RETURN_URL_COOKIE_NAME}=`)).toBe(true)

    const url = await redirectAfterSignIn(saved)

    expect(url.pathname).toBe("/sign-in")
    expect(url.searchParams.get("returnUrl")).toBe("/workspaces/synthetic")
  })
})
