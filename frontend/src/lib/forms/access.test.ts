import {
  canSubmitForms,
  type FormsAccessInput,
  getIntakeRedirect,
  type IntakeRedirectInput,
  resolveFormsAccess,
} from "@/lib/forms/access"

const intakeOnly: IntakeRedirectInput = {
  canReadWorkspace: false,
  canReadForms: true,
  formsAccess: "enabled",
  pathname: "/workspaces/workspace-1/workflows",
}

describe("getIntakeRedirect", () => {
  it("sends an intake-only user to the requester catalog", () => {
    expect(getIntakeRedirect(intakeOnly)).toBe("/forms")
    expect(
      getIntakeRedirect({ ...intakeOnly, pathname: "/workspaces/workspace-1" })
    ).toBe("/forms")
    expect(
      getIntakeRedirect({
        ...intakeOnly,
        pathname: "/workspaces/workspace-1/forms",
      })
    ).toBe("/forms")
  })

  it("maps a form's builder page to its requester page", () => {
    expect(
      getIntakeRedirect({
        ...intakeOnly,
        pathname: "/workspaces/workspace-1/forms/form-1",
      })
    ).toBe("/forms/form-1")
  })

  it("redirects without the entitlement, where /forms explains", () => {
    expect(getIntakeRedirect({ ...intakeOnly, formsAccess: "locked" })).toBe(
      "/forms"
    )
  })

  it.each([
    ["the flag is off", { formsAccess: "hidden" }],
    ["the flag is loading", { formsAccess: "loading" }],
    ["the user can read the workspace", { canReadWorkspace: true }],
    ["workspace:read is loading", { canReadWorkspace: undefined }],
    ["the user cannot read forms", { canReadForms: false }],
    ["form:read is loading", { canReadForms: undefined }],
  ] satisfies [string, Partial<IntakeRedirectInput>][])(
    "does nothing when %s",
    (_name, overrides) => {
      expect(getIntakeRedirect({ ...intakeOnly, ...overrides })).toBeNull()
    }
  )
})

const known: FormsAccessInput = {
  flagLoading: false,
  flagEnabled: true,
  entitlementLoading: false,
  entitled: true,
}

describe("resolveFormsAccess", () => {
  it("is loading until the flag is known", () => {
    expect(resolveFormsAccess({ ...known, flagLoading: true })).toBe("loading")
  })

  it("is hidden when the flag is off, whatever the entitlement says", () => {
    expect(resolveFormsAccess({ ...known, flagEnabled: false })).toBe("hidden")
    expect(
      resolveFormsAccess({
        ...known,
        flagEnabled: false,
        entitlementLoading: true,
      })
    ).toBe("hidden")
  })

  it("is loading while the entitlement of an enabled flag is fetched", () => {
    expect(resolveFormsAccess({ ...known, entitlementLoading: true })).toBe(
      "loading"
    )
  })

  it("is locked without the entitlement and enabled with it", () => {
    expect(resolveFormsAccess({ ...known, entitled: false })).toBe("locked")
    expect(resolveFormsAccess(known)).toBe("enabled")
  })
})

describe("canSubmitForms", () => {
  it("needs form:submit, directly or through a wildcard", () => {
    expect(canSubmitForms(["form:read", "form:submit"])).toBe(true)
    expect(canSubmitForms(["form:*"])).toBe(true)
    expect(canSubmitForms(["form:read"])).toBe(false)
    expect(canSubmitForms([])).toBe(false)
  })
})
