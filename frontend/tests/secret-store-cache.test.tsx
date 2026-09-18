import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { secretsListAuthorizedSecretStores } from "@/client"
import {
  useAuthorizedSecretStores,
  useOrgSecretStores,
} from "@/hooks/use-secret-stores"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  organizationSecretStoresListSecretStores: jest.fn().mockResolvedValue({
    items: [],
    next_cursor: null,
  }),
  organizationSecretStoresAuthorizeSecretStoreWorkspace: jest
    .fn()
    .mockResolvedValue({}),
  organizationSecretStoresRevokeSecretStoreWorkspace: jest
    .fn()
    .mockResolvedValue(undefined),
  secretsListAuthorizedSecretStores: jest.fn(),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

test.each(["authorizeWorkspace", "revokeWorkspace"] as const)(
  "%s refreshes the cached selector when the workspace form reopens",
  async (mutation) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const store = { id: "synthetic-store" }
    const before = mutation === "authorizeWorkspace" ? [] : [store]
    const after = mutation === "authorizeWorkspace" ? [store] : []
    const workspaceKey = ["workspace-secret-stores", "synthetic-workspace"]
    const otherKey = ["workspace-secret-stores", "other-workspace"]
    client.setQueryData(workspaceKey, before)
    client.setQueryData(otherKey, [])
    jest.mocked(secretsListAuthorizedSecretStores).mockResolvedValue({
      items: after,
      next_cursor: null,
    } as Awaited<ReturnType<typeof secretsListAuthorizedSecretStores>>)

    function wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      )
    }
    const management = renderHook(() => useOrgSecretStores(), { wrapper })
    await act(async () => {
      await management.result.current[mutation]({
        storeId: "synthetic-store",
        workspaceId: "synthetic-workspace",
      })
    })
    expect(client.getQueryState(otherKey)?.isInvalidated).toBe(false)

    const selector = renderHook(
      () => useAuthorizedSecretStores("synthetic-workspace"),
      { wrapper }
    )
    await waitFor(() => expect(selector.result.current.stores).toEqual(after))
    selector.unmount()
    management.unmount()
    client.clear()
  }
)
