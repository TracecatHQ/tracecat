import { getPostAuthRedirectPath } from "@/lib/auth-redirect"

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
