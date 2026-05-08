import { buildAppUrl } from "@/lib/url-utils"

describe("buildAppUrl", () => {
  it("preserves a sub-path on the base URL", () => {
    const url = buildAppUrl("/auth/error", "https://example.com/tracecat")
    expect(url.toString()).toBe("https://example.com/tracecat/auth/error")
  })

  it("works with a base URL that has no sub-path", () => {
    const url = buildAppUrl("/auth/error", "https://example.com")
    expect(url.toString()).toBe("https://example.com/auth/error")
  })

  it("normalises a trailing slash on the base", () => {
    const url = buildAppUrl("/auth/error", "https://example.com/tracecat/")
    expect(url.toString()).toBe("https://example.com/tracecat/auth/error")
  })

  it("accepts a path without a leading slash", () => {
    const url = buildAppUrl("auth/error", "https://example.com/tracecat")
    expect(url.toString()).toBe("https://example.com/tracecat/auth/error")
  })

  it("keeps query parameters and fragments on the base", () => {
    const url = buildAppUrl("/auth/error", "https://example.com/tracecat")
    url.searchParams.set("error", "invalid")
    expect(url.toString()).toBe(
      "https://example.com/tracecat/auth/error?error=invalid"
    )
  })

  it("supports a deeper sub-path on the base", () => {
    const url = buildAppUrl("/auth/error", "https://example.com/foo/bar/baz")
    expect(url.toString()).toBe("https://example.com/foo/bar/baz/auth/error")
  })
})
