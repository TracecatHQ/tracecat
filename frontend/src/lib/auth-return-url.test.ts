import {
  decodeAndSanitizeReturnUrl,
  normalizeMcpAuthReturnUrl,
  sanitizeReturnUrl,
} from "@/lib/auth-return-url"

describe("sanitizeReturnUrl", () => {
  it.each([
    "/sign-in",
    "/sign-in/reset-password",
    "/sign-up",
    "/sign-up/invite",
    "/auth",
    "/auth/oauth/callback",
  ])("rejects auth routes for %s", (value) => {
    expect(sanitizeReturnUrl(value)).toBeNull()
  })

  it.each(["https://example.com/workspaces", "//example.com/workspaces"])(
    "rejects external return targets for %s",
    (value) => {
      expect(sanitizeReturnUrl(value)).toBeNull()
    }
  )

  it("keeps valid internal routes intact", () => {
    expect(sanitizeReturnUrl("/workspaces/123?tab=members#activity")).toBe(
      "/workspaces/123?tab=members#activity"
    )
  })

  it("does not over-match unrelated internal routes", () => {
    expect(sanitizeReturnUrl("/authentic/path")).toBe("/authentic/path")
  })

  it("allows MCP OAuth resume paths", () => {
    expect(sanitizeReturnUrl("/oauth/mcp/continue?txn=abc123")).toBe(
      "/oauth/mcp/continue?txn=abc123"
    )
  })

  it("rewrites legacy MCP OAuth return paths", () => {
    expect(sanitizeReturnUrl("/oauth/mcp/select-org?txn=abc123#resume")).toBe(
      "/oauth/mcp/continue?txn=abc123#resume"
    )
  })

  it("rejects legacy MCP OAuth return paths without transactions", () => {
    expect(sanitizeReturnUrl("/oauth/mcp/select-org")).toBeNull()
  })
})

describe("decodeAndSanitizeReturnUrl", () => {
  it("rejects encoded auth routes", () => {
    expect(
      decodeAndSanitizeReturnUrl("%2Fsign-in%3FreturnUrl%3D%252Fsign-in")
    ).toBeNull()
  })

  it("accepts encoded internal routes", () => {
    expect(
      decodeAndSanitizeReturnUrl("%2Fworkspaces%2Fabc%3Ftab%3Dmembers")
    ).toBe("/workspaces/abc?tab=members")
  })

  it("rewrites encoded legacy MCP OAuth return paths", () => {
    expect(
      decodeAndSanitizeReturnUrl("%2Foauth%2Fmcp%2Fselect-org%3Ftxn%3Dabc123")
    ).toBe("/oauth/mcp/continue?txn=abc123")
  })
})

describe("normalizeMcpAuthReturnUrl", () => {
  it("keeps current MCP OAuth continuation paths", () => {
    expect(normalizeMcpAuthReturnUrl("/oauth/mcp/continue?txn=abc123")).toBe(
      "/oauth/mcp/continue?txn=abc123"
    )
  })

  it("rewrites legacy MCP OAuth org-selection paths", () => {
    expect(normalizeMcpAuthReturnUrl("/oauth/mcp/select-org?txn=abc123")).toBe(
      "/oauth/mcp/continue?txn=abc123"
    )
  })

  it("ignores unrelated paths", () => {
    expect(normalizeMcpAuthReturnUrl("/workspaces/default")).toBeNull()
  })
})

describe("with NEXT_PUBLIC_BASE_PATH set", () => {
  const originalBasePath = process.env.NEXT_PUBLIC_BASE_PATH

  beforeAll(() => {
    process.env.NEXT_PUBLIC_BASE_PATH = "/tracecat"
  })

  afterAll(() => {
    if (originalBasePath === undefined) {
      delete process.env.NEXT_PUBLIC_BASE_PATH
    } else {
      process.env.NEXT_PUBLIC_BASE_PATH = originalBasePath
    }
  })

  it.each([
    "/tracecat/sign-in",
    "/tracecat/sign-in/reset-password",
    "/tracecat/sign-up",
    "/tracecat/auth",
    "/tracecat/auth/oauth/callback",
  ])(
    "rejects basePath-prefixed auth routes for %s (cannot bypass block via prefix)",
    (value) => {
      expect(sanitizeReturnUrl(value)).toBeNull()
    }
  )

  it("keeps basePath-prefixed internal routes intact", () => {
    expect(sanitizeReturnUrl("/tracecat/workspaces/123?tab=members")).toBe(
      "/tracecat/workspaces/123?tab=members"
    )
  })

  it("normalizes basePath-prefixed MCP continuation paths", () => {
    expect(
      normalizeMcpAuthReturnUrl("/tracecat/oauth/mcp/continue?txn=abc123")
    ).toBe("/tracecat/oauth/mcp/continue?txn=abc123")
  })

  it("rewrites basePath-prefixed legacy MCP paths to canonical (unprefixed) target", () => {
    // The canonical target is intentionally unprefixed: it is later passed
    // to <Link>/router.push which auto-apply the basePath.
    expect(
      normalizeMcpAuthReturnUrl("/tracecat/oauth/mcp/select-org?txn=abc123")
    ).toBe("/oauth/mcp/continue?txn=abc123")
  })

  it("still accepts unprefixed paths so legacy URLs keep working", () => {
    expect(sanitizeReturnUrl("/workspaces/123")).toBe("/workspaces/123")
  })
})
