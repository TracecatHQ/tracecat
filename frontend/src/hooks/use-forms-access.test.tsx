import { renderHook } from "@testing-library/react"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useFormsAccess } from "@/hooks/use-forms-access"

// `null` is a failed request: settled, but with no data.
let flag: boolean | null | undefined
let entitled: boolean | null | undefined

jest.mock("@/hooks/use-feature-flags", () => ({
  useFeatureFlag: () => ({
    isFeatureEnabled: () => flag === true,
    isLoading: flag === undefined,
    hasFeatureData: typeof flag === "boolean",
  }),
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: jest.fn(() => ({
    hasEntitlement: () => entitled === true,
    isLoading: entitled === undefined,
    hasEntitlementData: typeof entitled === "boolean",
  })),
}))

it.each([
  ["loading", undefined, true],
  ["loading", null, true],
  ["hidden", false, true],
  ["loading", true, undefined],
  ["loading", true, null],
  ["locked", true, false],
  ["enabled", true, true],
])("is %s with flag %p and entitlement %p", (access, flagOn, entitlement) => {
  flag = flagOn
  entitled = entitlement

  expect(renderHook(() => useFormsAccess()).result.current).toBe(access)
  // The entitlement is asked for only while the flag is on.
  expect(useEntitlements).toHaveBeenLastCalledWith({ enabled: flagOn === true })
})
