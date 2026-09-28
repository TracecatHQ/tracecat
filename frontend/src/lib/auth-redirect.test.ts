import {
  getPostAuthDecisionPath,
  getPostAuthRedirectPath,
} from "@/lib/auth-redirect"

describe("getPostAuthRedirectPath", () => {
  it("passes through a normal returnUrl", () => {
    expect(
      getPostAuthRedirectPath({ returnUrl: "/workspaces/tenant-path" })
    ).toBe("/workspaces/tenant-path")
  })

  it("honors MCP OAuth continuation paths", () => {
    expect(
      getPostAuthRedirectPath({ returnUrl: "/oauth/mcp/continue?txn=abc123" })
    ).toBe("/oauth/mcp/continue?txn=abc123")
  })

  it("rewrites legacy MCP OAuth org-selection paths", () => {
    expect(
      getPostAuthRedirectPath({ returnUrl: "/oauth/mcp/select-org?txn=abc123" })
    ).toBe("/oauth/mcp/continue?txn=abc123")
  })

  it("does not treat similar MCP paths as continuation paths", () => {
    expect(
      getPostAuthRedirectPath({
        returnUrl: "/oauth/mcp/continue-later?txn=abc123",
      })
    ).toBe("/oauth/mcp/continue-later?txn=abc123")
  })

  it("passes through a workspace returnUrl", () => {
    expect(getPostAuthRedirectPath({ returnUrl: "/workspaces/default" })).toBe(
      "/workspaces/default"
    )
  })

  it("defaults to /workspaces when no returnUrl is given", () => {
    expect(getPostAuthRedirectPath({})).toBe("/workspaces")
  })
})

describe("getPostAuthDecisionPath", () => {
  it("routes saved return URLs through the sign-in post-auth logic", () => {
    expect(getPostAuthDecisionPath("/workspaces/tenant-path")).toBe(
      "/sign-in?returnUrl=%2Fworkspaces%2Ftenant-path"
    )
  })

  it("normalizes MCP continuation paths before routing through sign-in", () => {
    expect(getPostAuthDecisionPath("/oauth/mcp/select-org?txn=abc123")).toBe(
      "/sign-in?returnUrl=%2Foauth%2Fmcp%2Fcontinue%3Ftxn%3Dabc123"
    )
  })

  it.each([undefined, null, "https://example.com/workspaces", "/auth/error"])(
    "falls back to the app root for unsafe return URLs: %s",
    (returnUrl) => {
      expect(getPostAuthDecisionPath(returnUrl)).toBe("/")
    }
  )
})
