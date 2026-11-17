import { renderHook } from "@testing-library/react"
import { useFormsAccess } from "@/hooks/use-forms-access"

// `null` is a failed request: settled, but with no data.
let entitled: boolean | null | undefined

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: () => entitled === true,
    isLoading: entitled === undefined,
    hasEntitlementData: typeof entitled === "boolean",
  }),
}))

it.each([
  ["loading", undefined],
  ["loading", null],
  ["locked", false],
  ["enabled", true],
])("is %s with entitlement %p", (access, entitlement) => {
  entitled = entitlement

  expect(renderHook(() => useFormsAccess()).result.current).toBe(access)
})
