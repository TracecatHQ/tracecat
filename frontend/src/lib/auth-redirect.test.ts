import {
  getPostAuthDecisionPath,
  getPostAuthRedirectPath,
} from "@/lib/auth-redirect"

describe("getPostAuthRedirectPath", () => {
  it("forces multi-tenant superusers into admin", () => {
    expect(
      getPostAuthRedirectPath({
        isSuperuser: true,
        eeMultiTenant: true,
        returnUrl: "/workspaces/tenant-path",
      })
    ).toBe("/admin")
  })

  it("honors MCP OAuth continuation paths for multi-tenant superusers", () => {
    expect(
      getPostAuthRedirectPath({
        isSuperuser: true,
        eeMultiTenant: true,
        returnUrl: "/oauth/mcp/continue?txn=abc123",
      })
    ).toBe("/oauth/mcp/continue?txn=abc123")
  })

  it("rewrites legacy MCP OAuth org-selection paths", () => {
    expect(
      getPostAuthRedirectPath({
        isSuperuser: true,
        eeMultiTenant: true,
        returnUrl: "/oauth/mcp/select-org?txn=abc123",
      })
    ).toBe("/oauth/mcp/continue?txn=abc123")
  })

  it("does not treat similar MCP paths as continuation paths", () => {
    expect(
      getPostAuthRedirectPath({
        isSuperuser: true,
        eeMultiTenant: true,
        returnUrl: "/oauth/mcp/continue-later?txn=abc123",
      })
    ).toBe("/admin")
  })

  it("keeps single-tenant superusers on normal app routes", () => {
    expect(
      getPostAuthRedirectPath({
        isSuperuser: true,
        eeMultiTenant: false,
        returnUrl: "/workspaces/default",
      })
    ).toBe("/workspaces/default")
  })

  it("uses workspaces as the default app route", () => {
    expect(
      getPostAuthRedirectPath({
        isSuperuser: false,
        eeMultiTenant: true,
      })
    ).toBe("/workspaces")
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
