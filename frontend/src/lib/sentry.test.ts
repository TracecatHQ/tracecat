import type { ErrorEvent, EventHint } from "@sentry/nextjs"
import { beforeSend } from "@/lib/sentry"

function asRecord(value: unknown): Record<string, unknown> {
  expect(value).toEqual(expect.any(Object))
  return value as Record<string, unknown>
}

describe("beforeSend", () => {
  it("redacts camelCase sensitive keys", () => {
    const event = {
      contexts: {
        request: {
          apiKey: "api-key-secret",
          privateKey: "private-key-secret",
          safe: "visible",
        },
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const contexts = asRecord(result?.contexts)
    const request = asRecord(contexts.request)

    expect(request.apiKey).toBe("[Filtered]")
    expect(request.privateKey).toBe("[Filtered]")
    expect(request.safe).toBe("visible")
  })

  it("redacts values beyond the scrub depth limit", () => {
    const event = {
      contexts: {
        payload: {
          d1: {
            d2: {
              d3: {
                d4: {
                  d5: {
                    d6: {
                      d7: {
                        d8: {
                          d9: {
                            safeLookingField: "deep-secret",
                          },
                        },
                      },
                    },
                  },
                },
              },
            },
          },
        },
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const serialized = JSON.stringify(result)

    expect(serialized).toContain("[Filtered]")
    expect(serialized).not.toContain("deep-secret")
  })

  it("redacts request query string secrets", () => {
    const event = {
      request: {
        query_string: "code=secret-code&state=secret-state&safe=visible",
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const request = asRecord(result?.request)
    const params = new URLSearchParams(String(request.query_string))

    expect(params.get("code")).toBe("[Filtered]")
    expect(params.get("state")).toBe("[Filtered]")
    expect(params.get("safe")).toBe("visible")
  })

  it("redacts object request query string secrets", () => {
    const event = {
      request: {
        query_string: {
          code: "secret-code",
          state: "secret-state",
          safe: "visible",
        },
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const request = asRecord(result?.request)
    const queryString = asRecord(request.query_string)

    expect(queryString.code).toBe("[Filtered]")
    expect(queryString.state).toBe("[Filtered]")
    expect(queryString.safe).toBe("visible")
  })

  it("redacts pair-array request query string secrets", () => {
    const event = {
      request: {
        query_string: [
          ["code", "secret-code"],
          ["state", "secret-state"],
          ["safe", "visible"],
        ],
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const request = asRecord(result?.request)
    const queryString = request.query_string as [string, string][]

    expect(queryString).toEqual([
      ["code", "[Filtered]"],
      ["state", "[Filtered]"],
      ["safe", "visible"],
    ])
  })

  it("redacts request URL query secrets", () => {
    const event = {
      request: {
        url: "https://example.com/auth/oauth/callback?code=secret-code&state=secret-state&safe=visible",
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const request = asRecord(result?.request)
    const url = new URL(String(request.url))

    expect(url.origin).toBe("https://example.com")
    expect(url.pathname).toBe("/auth/oauth/callback")
    expect(url.searchParams.get("code")).toBe("[Filtered]")
    expect(url.searchParams.get("state")).toBe("[Filtered]")
    expect(url.searchParams.get("safe")).toBe("visible")
  })

  it("removes request URL credentials and fragments", () => {
    const event = {
      request: {
        url: "https://user:password@example.com/auth/oauth/callback?code=secret-code&safe=visible#access_token=secret-token",
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const request = asRecord(result?.request)
    const url = new URL(String(request.url))

    expect(url.username).toBe("")
    expect(url.password).toBe("")
    expect(url.hash).toBe("")
    expect(url.searchParams.get("code")).toBe("[Filtered]")
    expect(url.searchParams.get("safe")).toBe("visible")
    expect(request.url).not.toContain("password")
    expect(request.url).not.toContain("secret-token")
  })

  it("removes relative request URL fragments", () => {
    const event = {
      request: {
        url: "/auth/oauth/callback#access_token=secret-token",
      },
    } as unknown as ErrorEvent

    const result = beforeSend(event, {} as EventHint)
    const request = asRecord(result?.request)

    expect(request.url).toBe("/auth/oauth/callback")
  })
})
