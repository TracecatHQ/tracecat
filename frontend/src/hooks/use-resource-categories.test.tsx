import { renderHook } from "@testing-library/react"
import { useResourceCategories } from "@/hooks/use-resource-categories"
import type { FormsAccess } from "@/lib/forms/access"
import { RESOURCE_CATEGORIES } from "@/lib/rbac"

let mockFormsAccess: FormsAccess = "enabled"

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => mockFormsAccess,
}))

describe("useResourceCategories", () => {
  it("leaves out Forms with the feature flag off and nothing else", () => {
    mockFormsAccess = "hidden"

    const { result } = renderHook(() => useResourceCategories())

    expect(Object.keys(result.current)).toEqual(
      Object.keys(RESOURCE_CATEGORIES).filter((key) => key !== "forms")
    )
  })

  it.each<FormsAccess>(["loading", "locked", "enabled"])(
    "returns every category when Forms is %s",
    (access) => {
      mockFormsAccess = access

      const { result } = renderHook(() => useResourceCategories())

      expect(result.current).toBe(RESOURCE_CATEGORIES)
      expect(result.current.forms.label).toBe("Forms")
    }
  )
})
