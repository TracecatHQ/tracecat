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
  organizationSecretStoresUpdateSecretStore: jest.fn().mockResolvedValue({}),
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

test.each([
  { all_workspaces: true },
  { all_workspaces: false },
  { enabled: false },
])(
  "store access change %j refreshes every workspace selector",
  async (params) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const workspaceKeys = ["first-workspace", "second-workspace"].map((id) => [
      "workspace-secret-stores",
      id,
    ])
    for (const key of workspaceKeys) client.setQueryData(key, [])
    function wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      )
    }
    const management = renderHook(() => useOrgSecretStores(), { wrapper })
    await act(async () => {
      await management.result.current.updateStore({
        storeId: "synthetic-store",
        params,
      })
    })
    for (const key of workspaceKeys) {
      expect(client.getQueryState(key)?.isInvalidated).toBe(true)
    }
    management.unmount()
    client.clear()
  }
)
