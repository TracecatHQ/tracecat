import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  featureFlagsGetFeatureFlags,
  organizationGetOrganization,
  organizationGetOrganizationEntitlements,
} from "@/client"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  featureFlagsGetFeatureFlags: jest.fn(),
  organizationGetOrganization: jest.fn(),
  organizationGetOrganizationEntitlements: jest.fn(),
}))

const getFlags = jest.mocked(featureFlagsGetFeatureFlags)
const getOrganization = jest.mocked(organizationGetOrganization)
const getEntitlements = jest.mocked(organizationGetOrganizationEntitlements)

function renderAccess() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return renderHook(() => useFormsAccess(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  })
}

function mockFlags(enabled: string[]) {
  getFlags.mockResolvedValue({ enabled_features: enabled } as Awaited<
    ReturnType<typeof featureFlagsGetFeatureFlags>
  >)
}

function mockEntitlements(entitlements: Record<string, boolean>) {
  getEntitlements.mockResolvedValue(
    entitlements as Awaited<
      ReturnType<typeof organizationGetOrganizationEntitlements>
    >
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  getOrganization.mockResolvedValue({ id: "org-1" } as Awaited<
    ReturnType<typeof organizationGetOrganization>
  >)
})

describe("useFormsAccess", () => {
  it("is loading until the flag and the entitlement are known", async () => {
    let resolveEntitlements: (value: Record<string, boolean>) => void = () => {}
    mockFlags(["forms"])
    getEntitlements.mockReturnValue(
      new Promise((resolve) => {
        resolveEntitlements = resolve
      }) as ReturnType<typeof organizationGetOrganizationEntitlements>
    )

    const { result } = renderAccess()
    expect(result.current).toBe("loading")

    // Still loading once the flag is in but the entitlement is not.
    await waitFor(() => expect(getEntitlements).toHaveBeenCalled())
    expect(result.current).toBe("loading")

    resolveEntitlements({ forms: true })
    await waitFor(() => expect(result.current).toBe("enabled"))
  })

  it("is hidden when the flag is off and never asks for the entitlement", async () => {
    mockFlags(["some-other-flag"])
    mockEntitlements({ forms: true })

    const { result } = renderAccess()

    await waitFor(() => expect(result.current).toBe("hidden"))
    expect(getOrganization).not.toHaveBeenCalled()
    expect(getEntitlements).not.toHaveBeenCalled()
  })

  it("is locked when the flag is on without the entitlement", async () => {
    mockFlags(["forms"])
    mockEntitlements({ forms: false })

    const { result } = renderAccess()

    await waitFor(() => expect(result.current).toBe("locked"))
  })

  it("is enabled when the flag is on and the organization is entitled", async () => {
    mockFlags(["forms"])
    mockEntitlements({ forms: true })

    const { result } = renderAccess()

    await waitFor(() => expect(result.current).toBe("enabled"))
  })

  it("is locked when the entitlement cannot be read", async () => {
    mockFlags(["forms"])
    getEntitlements.mockRejectedValue(new Error("Network Error"))

    const { result } = renderAccess()

    await waitFor(() => expect(result.current).toBe("locked"))
  })
})
