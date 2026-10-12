import {
  canSubmitForms,
  type FormsAccessInput,
  resolveFormsAccess,
} from "@/lib/forms/access"

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
