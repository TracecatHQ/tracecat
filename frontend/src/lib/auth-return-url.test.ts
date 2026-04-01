import {
  decodeAndSanitizeReturnUrl,
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

  it.each(["/mcp-auth/continue?txn=abc123", "/mcp-auth/select-org?txn=abc123"])(
    "allows MCP auth resume paths for %s",
    (value) => {
      expect(sanitizeReturnUrl(value)).toBe(value)
    }
  )
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
})
