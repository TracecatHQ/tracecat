import { renderHook } from "@testing-library/react"
import type { FormsAccess } from "@/hooks/use-forms-access"
import { useResourceCategories } from "@/hooks/use-resource-categories"
import { RESOURCE_CATEGORIES } from "@/lib/rbac"

let access: FormsAccess = "enabled"

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => access,
}))

it.each<[FormsAccess, boolean]>([
  ["hidden", false],
  ["loading", true],
  ["locked", true],
  ["enabled", true],
])("offers the Forms role category when Forms is %s: %p", (value, offered) => {
  access = value

  const { result } = renderHook(() => useResourceCategories())

  expect(Object.keys(result.current)).toEqual(
    Object.keys(RESOURCE_CATEGORIES).filter((key) => offered || key !== "forms")
  )
})
