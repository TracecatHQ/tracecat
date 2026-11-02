/** @jest-environment node */

import { NextRequest } from "next/server"
import { GET as integrationCallback } from "@/app/integrations/callback/route"

function infoResponse(): Response {
  return Response.json({ public_app_url: "http://localhost" })
}

async function redirectFor(backendResponse: Response): Promise<URL> {
  jest
    .spyOn(global, "fetch")
    .mockResolvedValueOnce(backendResponse)
    .mockResolvedValueOnce(infoResponse())
  jest.spyOn(console, "log").mockImplementation(() => {})
  jest.spyOn(console, "error").mockImplementation(() => {})
  const request = new NextRequest(
    "http://localhost/integrations/callback?code=synthetic&state=synthetic",
    { headers: { cookie: "session=synthetic" } }
  )
  const response = await integrationCallback(request)
  const location = response.headers.get("location")
  if (!location) {
    throw new Error("expected a redirect")
  }
  return new URL(location)
}

afterEach(() => {
  jest.restoreAllMocks()
})

describe("integration OAuth callback failures", () => {
  it("explains missing integration scopes", async () => {
    const url = await redirectFor(
      Response.json(
        {
          error: {
            code: "insufficient_scope",
            message: "synthetic",
            required_scopes: ["integration:create", "integration:update"],
            missing_scopes: ["integration:create", "integration:update"],
          },
        },
        { status: 403 }
      )
    )

    expect(url.pathname).toBe("/integrations/error")
    expect(url.searchParams.get("error")).toBe("insufficient_scope")
    expect(url.searchParams.get("error_description")).toContain(
      "permission to connect integrations"
    )
  })

  it("forwards the backend detail", async () => {
    const url = await redirectFor(
      Response.json(
        { detail: "Invalid or expired state parameter" },
        { status: 400 }
      )
    )

    expect(url.pathname).toBe("/integrations/error")
    expect(url.searchParams.get("error")).toBe("callback_failed")
    expect(url.searchParams.get("error_description")).toBe(
      "Invalid or expired state parameter"
    )
  })

  it("falls back to a generic message for unreadable bodies", async () => {
    const url = await redirectFor(new Response("oops", { status: 502 }))

    expect(url.pathname).toBe("/integrations/error")
    expect(url.searchParams.get("error_description")).toBe(
      "Failed to complete the OAuth connection. Please try again."
    )
  })

  it("redirects to the callback target on success", async () => {
    const url = await redirectFor(
      Response.json({
        status: "connected",
        provider_id: "google_drive",
        redirect_url: "http://localhost/workspaces/ws/integrations",
      })
    )

    expect(url.toString()).toBe("http://localhost/workspaces/ws/integrations")
  })
})
