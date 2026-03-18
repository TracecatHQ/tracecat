import { getApiErrorDetail } from "@/lib/errors"

describe("getApiErrorDetail", () => {
  it("returns string detail when present", () => {
    const error = Object.assign(new Error("Bad Request"), {
      body: { detail: "Registry sync validation failed", message: null },
    })

    expect(getApiErrorDetail(error)).toBe("Registry sync validation failed")
  })

  it("prefers the API body message when detail is null", () => {
    const error = Object.assign(new Error("Bad Request"), {
      body: {
        detail: null,
        message: "Registry sync validation failed with 2 error(s).",
      },
    })

    expect(getApiErrorDetail(error)).toBe(
      "Registry sync validation failed with 2 error(s)."
    )
  })

  it("serializes structured detail before falling back to the Error text", () => {
    const error = Object.assign(new Error("Bad Request"), {
      body: {
        detail: {
          action: "tracecat.examples.broken",
          reason: "Action not found",
        },
        message: "Internal Server Error",
      },
    })

    expect(getApiErrorDetail(error)).toBe(
      JSON.stringify({
        action: "tracecat.examples.broken",
        reason: "Action not found",
      })
    )
  })
})
