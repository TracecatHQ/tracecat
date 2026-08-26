import { buildContentSecurityPolicy, parseOrigins } from "@/lib/csp"

const BASE_POLICY =
  "connect-src 'self'; default-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline'; script-src-attr 'none'; style-src 'self' 'unsafe-inline'"

const POSTHOG_POLICY =
  "connect-src 'self' https://*.posthog.com; default-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline' https://*.posthog.com; script-src-attr 'none'; style-src 'self' 'unsafe-inline'"

describe("parseOrigins", () => {
  it.each([
    ["undefined", undefined],
    ["null", null],
    ["empty string", ""],
    ["whitespace only", "   "],
  ])("returns an empty list for %s", (_label, value) => {
    expect(parseOrigins(value)).toEqual([])
  })

  it("splits on spaces", () => {
    expect(parseOrigins("https://a.example.com https://b.example.com")).toEqual(
      ["https://a.example.com", "https://b.example.com"]
    )
  })

  it("splits on commas", () => {
    expect(parseOrigins("https://a.example.com,https://b.example.com")).toEqual(
      ["https://a.example.com", "https://b.example.com"]
    )
  })

  it("splits on newlines", () => {
    expect(
      parseOrigins("https://a.example.com\nhttps://b.example.com\n")
    ).toEqual(["https://a.example.com", "https://b.example.com"])
  })

  it("splits on mixed separators", () => {
    expect(
      parseOrigins(
        "  https://a.example.com, \n https://b.example.com\t,,https://c.example.com  "
      )
    ).toEqual([
      "https://a.example.com",
      "https://b.example.com",
      "https://c.example.com",
    ])
  })

  it("keeps only the origin of a URL with a path and query", () => {
    expect(
      parseOrigins("https://b.s3.us-west-2.amazonaws.com/path?x=1")
    ).toEqual(["https://b.s3.us-west-2.amazonaws.com"])
  })

  it("strips a trailing slash", () => {
    expect(parseOrigins("https://a.example.com/")).toEqual([
      "https://a.example.com",
    ])
  })

  it("keeps an explicit port", () => {
    expect(parseOrigins("https://minio.local:9000")).toEqual([
      "https://minio.local:9000",
    ])
  })

  it("keeps http origins", () => {
    expect(parseOrigins("http://localhost:9000")).toEqual([
      "http://localhost:9000",
    ])
  })

  it("dedupes while preserving order", () => {
    expect(
      parseOrigins(
        "https://b.example.com https://a.example.com https://b.example.com/x https://a.example.com/"
      )
    ).toEqual(["https://b.example.com", "https://a.example.com"])
  })

  it.each([
    "javascript:alert(1)",
    "data:x",
    "file:///etc",
    "ftp://x",
    "foo",
    "*",
    "'self'",
    "https://",
  ])("drops %s", (value) => {
    expect(parseOrigins(value)).toEqual([])
  })

  it("keeps valid origins alongside invalid tokens", () => {
    expect(parseOrigins("foo https://a.example.com 'self'")).toEqual([
      "https://a.example.com",
    ])
  })

  it("never returns a token containing a separator or a directive delimiter", () => {
    const origins = parseOrigins(
      "https://a.example.com/x;y https://b.example.com, foo https://c.example.com:9000"
    )
    expect(origins.length).toBeGreaterThan(0)
    for (const origin of origins) {
      expect(origin).not.toMatch(/[;,\s]/)
    }
  })
})

describe("buildContentSecurityPolicy", () => {
  it("returns the base policy with no arguments", () => {
    expect(buildContentSecurityPolicy()).toBe(BASE_POLICY)
  })

  it("returns the base policy for an empty options object", () => {
    expect(buildContentSecurityPolicy({})).toBe(BASE_POLICY)
  })

  it("returns the base policy when posthog is disabled", () => {
    expect(buildContentSecurityPolicy({ posthogEnabled: false })).toBe(
      BASE_POLICY
    )
  })

  it("adds posthog origins when posthog is enabled", () => {
    expect(buildContentSecurityPolicy({ posthogEnabled: true })).toBe(
      POSTHOG_POLICY
    )
  })

  it("returns the base policy for an empty extra list", () => {
    expect(buildContentSecurityPolicy({ extraConnectSrc: [] })).toBe(
      BASE_POLICY
    )
  })

  it("appends extra origins to connect-src after the existing sources", () => {
    expect(
      buildContentSecurityPolicy({
        extraConnectSrc: [
          "https://a.s3.us-west-2.amazonaws.com",
          "https://b.s3.us-west-2.amazonaws.com",
        ],
      })
    ).toBe(
      "connect-src 'self' https://a.s3.us-west-2.amazonaws.com https://b.s3.us-west-2.amazonaws.com; default-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline'; script-src-attr 'none'; style-src 'self' 'unsafe-inline'"
    )
  })

  it("appends extra origins after the posthog origin", () => {
    expect(
      buildContentSecurityPolicy({
        posthogEnabled: true,
        extraConnectSrc: ["https://a.s3.us-west-2.amazonaws.com"],
      })
    ).toBe(
      "connect-src 'self' https://*.posthog.com https://a.s3.us-west-2.amazonaws.com; default-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline' https://*.posthog.com; script-src-attr 'none'; style-src 'self' 'unsafe-inline'"
    )
  })

  it("puts extra origins in connect-src and in no other directive", () => {
    const extra = "https://a.s3.us-west-2.amazonaws.com"
    const policy = buildContentSecurityPolicy({
      posthogEnabled: true,
      extraConnectSrc: [extra],
    })
    const directives = policy.split("; ")
    const matching = directives.filter((directive) => directive.includes(extra))
    expect(matching).toEqual([
      `connect-src 'self' https://*.posthog.com ${extra}`,
    ])
  })

  it("keeps directive order stable", () => {
    const directiveNames = (policy: string) =>
      policy.split("; ").map((directive) => directive.split(" ")[0])
    const expected = [
      "connect-src",
      "default-src",
      "worker-src",
      "frame-ancestors",
      "img-src",
      "object-src",
      "base-uri",
      "script-src",
      "script-src-attr",
      "style-src",
    ]
    expect(directiveNames(buildContentSecurityPolicy())).toEqual(expected)
    expect(
      directiveNames(
        buildContentSecurityPolicy({
          posthogEnabled: true,
          extraConnectSrc: ["https://a.s3.us-west-2.amazonaws.com"],
        })
      )
    ).toEqual(expected)
  })
})
