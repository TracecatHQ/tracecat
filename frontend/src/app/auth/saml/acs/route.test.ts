class MockHeaders {
  private readonly values = new Map<string, string[]>()

  constructor(init?: Record<string, string>) {
    for (const [key, value] of Object.entries(init ?? {})) {
      this.values.set(key.toLowerCase(), [value])
    }
  }

  append(key: string, value: string) {
    const normalizedKey = key.toLowerCase()
    const existing = this.values.get(normalizedKey) ?? []
    existing.push(value)
    this.values.set(normalizedKey, existing)
  }

  get(key: string) {
    return this.values.get(key.toLowerCase())?.[0] ?? null
  }
}

class MockNextResponse {
  readonly headers: MockHeaders
  readonly status: number
  private readonly body: string

  constructor(
    body?: string | null,
    init?: { headers?: Record<string, string>; status?: number }
  ) {
    this.body = body ?? ""
    this.status = init?.status ?? 200
    this.headers = new MockHeaders(init?.headers)
  }

  static redirect(url: string | URL, init?: number | { status?: number }) {
    const status = typeof init === "number" ? init : (init?.status ?? 307)
    return new MockNextResponse("", {
      status,
      headers: {
        location: String(url),
      },
    })
  }

  async text() {
    return this.body
  }
}

type MockFetchResponse = {
  headers: MockHeaders
  json: () => Promise<unknown>
  ok: boolean
  status: number
  text: () => Promise<string>
}

function makeMockFetchResponse(
  body: string,
  init?: { headers?: Record<string, string>; status?: number }
): MockFetchResponse {
  const status = init?.status ?? 200
  return {
    headers: new MockHeaders(init?.headers),
    json: async () => JSON.parse(body),
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  }
}

jest.mock("next/server", () => ({
  NextResponse: MockNextResponse,
}))

import { POST } from "@/app/auth/saml/acs/route"

describe("POST /auth/saml/acs", () => {
  const originalFetch = global.fetch

  afterEach(() => {
    jest.restoreAllMocks()
    global.fetch = originalFetch
  })

  it("passes through successful MCP completion HTML from the backend", async () => {
    const formData = new FormData()
    formData.append("SAMLResponse", "response")
    formData.append("RelayState", "relay")

    const fetchMock = jest.fn((input) => {
      const url = String(input)
      if (url.endsWith("/info")) {
        return Promise.resolve(
          makeMockFetchResponse(
            JSON.stringify({ public_app_url: "http://localhost" }),
            {
              headers: {
                "content-type": "application/json",
              },
            }
          ) as never
        )
      }
      if (url.endsWith("/auth/saml/acs")) {
        return Promise.resolve(
          makeMockFetchResponse(
            "<html><body>Continue to Claude</body></html>",
            {
              headers: {
                "content-type": "text/html; charset=utf-8",
              },
            }
          ) as never
        )
      }
      throw new Error(`Unexpected fetch URL: ${url}`)
    })
    global.fetch = fetchMock as never

    const request = {
      cookies: {
        get: () => undefined,
      },
      formData: async () => formData,
      nextUrl: new URL("http://localhost/auth/saml/acs"),
    }

    const response = await POST(request as never)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(response.status).toBe(200)
    expect(response.headers.get("content-type")).toContain("text/html")
    await expect(response.text()).resolves.toContain("Continue to Claude")
  })

  it("passes through MCP error HTML from the backend", async () => {
    const formData = new FormData()
    formData.append("SAMLResponse", "response")
    formData.append("RelayState", "relay")

    const fetchMock = jest.fn((input) => {
      const url = String(input)
      if (url.endsWith("/info")) {
        return Promise.resolve(
          makeMockFetchResponse(
            JSON.stringify({ public_app_url: "http://localhost" }),
            {
              headers: {
                "content-type": "application/json",
              },
            }
          ) as never
        )
      }
      if (url.endsWith("/auth/saml/acs")) {
        return Promise.resolve(
          makeMockFetchResponse(
            "<html><body>No Tracecat account exists for this email.</body></html>",
            {
              headers: {
                "content-type": "text/html; charset=utf-8",
              },
              status: 403,
            }
          ) as never
        )
      }
      throw new Error(`Unexpected fetch URL: ${url}`)
    })
    global.fetch = fetchMock as never

    const request = {
      cookies: {
        get: () => undefined,
      },
      formData: async () => formData,
      nextUrl: new URL("http://localhost/auth/saml/acs"),
    }

    const response = await POST(request as never)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(response.status).toBe(403)
    expect(response.headers.get("content-type")).toContain("text/html")
    await expect(response.text()).resolves.toContain(
      "No Tracecat account exists for this email."
    )
  })
})
