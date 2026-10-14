import { renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { useArtifactInvalidation } from "@/hooks/use-artifact-invalidation"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { ARTIFACT_DATA_PART_TYPE } from "@/types/workspace-chat-artifacts"

it.each([
  [{ status: "online" }, true],
  [{ status: null }, true],
  [{ status: "draft" }, false],
])("refreshes the form a chat touched (%j): %p", (fields, refreshed) => {
  const queryClient = new QueryClient()
  const invalidate = jest.spyOn(queryClient, "invalidateQueries")
  const { result } = renderHook(() => useArtifactInvalidation("ws-1"), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  })

  result.current.handleStreamData({
    type: ARTIFACT_DATA_PART_TYPE,
    data: {
      op: "upsert",
      artifact: { type: "form", id: "form-1", title: "Laptop", ...fields },
    },
  } as never)

  expect(invalidate.mock.calls.map(([filters]) => filters)).toEqual(
    refreshed
      ? [
          { queryKey: ["form", "ws-1", "form-1"] },
          { queryKey: ["forms", "ws-1"] },
        ]
      : []
  )
})
