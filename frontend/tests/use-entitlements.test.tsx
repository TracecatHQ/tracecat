import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { organizationGetOrganizationEntitlements } from "@/client"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useOrganization } from "@/hooks/use-organization"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    organizationGetOrganizationEntitlements: jest.fn(),
  }
})

jest.mock("@/hooks/use-organization", () => ({
  useOrganization: jest.fn(),
}))

const mockGetEntitlements =
  organizationGetOrganizationEntitlements as jest.MockedFunction<
    typeof organizationGetOrganizationEntitlements
  >
const mockUseOrganization = useOrganization as jest.MockedFunction<
  typeof useOrganization
>

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

describe("useEntitlements", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    jest.clearAllMocks()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    mockUseOrganization.mockReturnValue({
      organization: { id: "org-1" },
      isLoading: false,
    } as unknown as ReturnType<typeof useOrganization>)
  })

  it("reports entitlements as unknown when a refetch fails", async () => {
    // React Query keeps the last good data through a failed refetch, so the
    // hook must not read "known" off stale data while `hasEntitlement` has
    // already switched to false -- that combination reads as "not entitled"
    // and shows a paying org an upsell.
    mockGetEntitlements.mockResolvedValueOnce({
      agent_addons: true,
    } as unknown as Awaited<ReturnType<typeof mockGetEntitlements>>)

    const { result } = renderHook(() => useEntitlements(), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => expect(result.current.hasEntitlementData).toBe(true))
    expect(result.current.hasEntitlement("agent_addons")).toBe(true)

    mockGetEntitlements.mockRejectedValueOnce(new Error("network down"))
    await queryClient.refetchQueries({
      queryKey: ["organization-entitlements", "org-1"],
    })

    await waitFor(() => expect(result.current.hasEntitlementData).toBe(false))
    // Both halves now agree the answer is unknown rather than "not entitled".
    expect(result.current.hasEntitlement("agent_addons")).toBe(false)
  })
})
