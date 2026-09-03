/** @jest-environment node */

import { NextRequest } from "next/server"
import { GET as oauthCallback } from "@/app/auth/oauth/callback/route"
import { POST as samlCallback } from "@/app/auth/saml/acs/route"

const ORIGINAL_SERVICE_KEY = process.env.TRACECAT__SERVICE_KEY
const ATTRIBUTION_HEADERS = {
  "user-agent": "SyntheticBrowser/1.0",
  "x-forwarded-for": "198.51.100.20",
}

function infoResponse(): Response {
  return Response.json({ public_app_url: "http://localhost" })
}

function authResponse(): Response {
  return new Response(null, {
    headers: { "set-cookie": "session=synthetic; Path=/" },
  })
}

afterEach(() => {
  jest.restoreAllMocks()
  if (ORIGINAL_SERVICE_KEY === undefined) {
    delete process.env.TRACECAT__SERVICE_KEY
  } else {
    process.env.TRACECAT__SERVICE_KEY = ORIGINAL_SERVICE_KEY
  }
})

describe("authentication callback attribution", () => {
  it("forwards OAuth attribution and cookies to the backend", async () => {
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(authResponse())
      .mockResolvedValueOnce(infoResponse())
    const request = new NextRequest(
      "http://localhost/auth/oauth/callback?code=synthetic",
      {
        headers: {
          ...ATTRIBUTION_HEADERS,
          cookie: "session=synthetic",
        },
      }
    )

    await oauthCallback(request)

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers)
    expect(headers.get("cookie")).toBe("session=synthetic")
    expect(headers.get("user-agent")).toBe("SyntheticBrowser/1.0")
    expect(headers.get("x-forwarded-for")).toBe("198.51.100.20")
  })

  it("forwards SAML attribution with service headers", async () => {
    process.env.TRACECAT__SERVICE_KEY = "synthetic-service-key"
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(infoResponse())
      .mockResolvedValueOnce(authResponse())
    const formData = new FormData()
    formData.set("SAMLResponse", "synthetic-response")
    const request = new NextRequest("http://localhost/auth/saml/acs", {
      method: "POST",
      body: formData,
      headers: ATTRIBUTION_HEADERS,
    })

    await samlCallback(request)

    const headers = new Headers(fetchMock.mock.calls[1][1]?.headers)
    expect(headers.get("user-agent")).toBe("SyntheticBrowser/1.0")
    expect(headers.get("x-forwarded-for")).toBe("198.51.100.20")
    expect(headers.get("x-tracecat-role-type")).toBe("service")
    expect(headers.get("x-tracecat-role-service-id")).toBe("tracecat-ui")
    expect(headers.get("x-tracecat-service-key")).toBe("synthetic-service-key")
  })
})
