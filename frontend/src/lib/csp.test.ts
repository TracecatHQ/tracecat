import {
  buildContentSecurityPolicy,
  buildContentSecurityPolicyFromEnv,
  parseOrigins,
} from "@/lib/csp"

const BASE_POLICY =
  "connect-src 'self'; default-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline'; script-src-attr 'none'; style-src 'self' 'unsafe-inline'"

const POSTHOG_POLICY =
  "connect-src 'self' https://*.posthog.com; default-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline' https://*.posthog.com; script-src-attr 'none'; style-src 'self' 'unsafe-inline'"

const directiveNames = (policy: string) =>
  policy.split("; ").map((directive) => directive.split(" ")[0])

const connectSrc = (policy: string) =>
  policy.split("; ").find((directive) => directive.startsWith("connect-src "))

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

  it("strips userinfo", () => {
    expect(parseOrigins("https://user:pass@a.example.com")).toEqual([
      "https://a.example.com",
    ])
  })

  it("passes a wildcard host through unchanged", () => {
    expect(parseOrigins("https://*.s3.us-west-2.amazonaws.com")).toEqual([
      "https://*.s3.us-west-2.amazonaws.com",
    ])
  })

  it("keeps a wildcard over a public suffix, which is not guarded against", () => {
    expect(parseOrigins("https://*.com")).toEqual(["https://*.com"])
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

  it("reports exactly the dropped tokens to onReject", () => {
    const rejected: string[] = []
    const origins = parseOrigins(
      "foo https://a.example.com ftp://x, 'self' https://a.example.com/x",
      { onReject: (token) => rejected.push(token) }
    )
    expect(origins).toEqual(["https://a.example.com"])
    expect(rejected).toEqual(["foo", "ftp://x", "'self'"])
  })

  it("does not call onReject when every token parses", () => {
    const onReject = jest.fn()
    expect(
      parseOrigins("https://a.example.com https://b.example.com", { onReject })
    ).toEqual(["https://a.example.com", "https://b.example.com"])
    expect(onReject).not.toHaveBeenCalled()
  })

  it("drops a hostname carrying a directive delimiter and reports it", () => {
    const rejected: string[] = []
    expect(
      parseOrigins("https://example.com;script-src", {
        onReject: (token) => rejected.push(token),
      })
    ).toEqual([])
    expect(rejected).toEqual(["https://example.com;script-src"])
  })

  it("drops an IPv6 literal", () => {
    expect(parseOrigins("https://[::1]:9000")).toEqual([])
  })

  it("lowercases an uppercase hostname", () => {
    expect(parseOrigins("https://EXAMPLE.com")).toEqual(["https://example.com"])
  })

  it("never returns a token containing a separator or a directive delimiter", () => {
    const origins = parseOrigins(
      "https://a.example.com/x;y https://example.com;script-src https://b.example.com, foo https://c.example.com:9000"
    )
    expect(origins).toEqual([
      "https://a.example.com",
      "https://b.example.com",
      "https://c.example.com:9000",
    ])
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
      BASE_POLICY.replace(
        "connect-src 'self'",
        "connect-src 'self' https://a.s3.us-west-2.amazonaws.com https://b.s3.us-west-2.amazonaws.com"
      )
    )
  })

  it("appends extra origins after the posthog origin", () => {
    expect(
      buildContentSecurityPolicy({
        posthogEnabled: true,
        extraConnectSrc: ["https://a.s3.us-west-2.amazonaws.com"],
      })
    ).toBe(
      POSTHOG_POLICY.replace(
        "connect-src 'self' https://*.posthog.com",
        "connect-src 'self' https://*.posthog.com https://a.s3.us-west-2.amazonaws.com"
      )
    )
  })

  it("puts extra origins in connect-src and in no other directive", () => {
    const extra = "https://a.s3.us-west-2.amazonaws.com"
    const policy = buildContentSecurityPolicy({
      posthogEnabled: true,
      extraConnectSrc: [extra],
    })
    const directives = new Map(
      policy.split("; ").map((directive) => {
        const [name, ...sources] = directive.split(" ")
        return [name, sources] as const
      })
    )
    expect(directives.get("connect-src")).toEqual([
      "'self'",
      "https://*.posthog.com",
      extra,
    ])
    for (const [name, sources] of directives) {
      if (name !== "connect-src") {
        expect(sources.filter((source) => source === extra)).toEqual([])
      }
    }
  })

  it("keeps directive order stable", () => {
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

describe("buildContentSecurityPolicyFromEnv", () => {
  let warnSpy: jest.SpyInstance

  beforeEach(() => {
    warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {})
  })

  afterEach(() => {
    warnSpy.mockRestore()
  })

  it("returns the base policy for an empty environment", () => {
    expect(buildContentSecurityPolicyFromEnv({})).toBe(BASE_POLICY)
  })

  it("returns the posthog policy when a posthog key is set", () => {
    expect(
      buildContentSecurityPolicyFromEnv({ NEXT_PUBLIC_POSTHOG_KEY: "phc_x" })
    ).toBe(POSTHOG_POLICY)
  })

  it("adds configured origins to connect-src", () => {
    const policy = buildContentSecurityPolicyFromEnv({
      TRACECAT__CSP_CONNECT_SRC_ORIGINS: "https://a.example.com",
    })
    expect(policy).toBe(
      BASE_POLICY.replace(
        "connect-src 'self'",
        "connect-src 'self' https://a.example.com"
      )
    )
  })

  it("does not warn when every configured origin parses", () => {
    buildContentSecurityPolicyFromEnv({
      TRACECAT__CSP_CONNECT_SRC_ORIGINS: "https://a.example.com",
    })
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it("keeps valid origins and warns once about invalid ones", () => {
    const policy = buildContentSecurityPolicyFromEnv({
      TRACECAT__CSP_CONNECT_SRC_ORIGINS: "https://a.example.com, junk",
    })
    expect(connectSrc(policy)).toBe("connect-src 'self' https://a.example.com")
    expect(warnSpy).toHaveBeenCalledTimes(1)
    expect(warnSpy.mock.calls[0][0]).toContain("junk")
  })
})
