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
})
