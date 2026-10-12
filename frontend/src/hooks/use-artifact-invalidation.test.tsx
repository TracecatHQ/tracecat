import { renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { useArtifactInvalidation } from "@/hooks/use-artifact-invalidation"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import {
  ARTIFACT_DATA_PART_TYPE,
  parseWorkspaceChatArtifactStreamPart,
} from "@/types/workspace-chat-artifacts"

const WORKSPACE_ID = "workspace-1"
const FORM_ID = "11111111-1111-4111-8111-111111111111"

function formPart(artifact: Record<string, unknown> = {}) {
  return {
    type: ARTIFACT_DATA_PART_TYPE,
    data: {
      op: "upsert",
      artifact: {
        type: "form",
        id: FORM_ID,
        title: "Laptop request",
        workflowId: "22222222-2222-4222-8222-222222222222",
        status: "online",
        ...artifact,
      },
    },
  }
}

function setup() {
  const queryClient = new QueryClient()
  const invalidate = jest.spyOn(queryClient, "invalidateQueries")
  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
  const { result } = renderHook(() => useArtifactInvalidation(WORKSPACE_ID), {
    wrapper,
  })
  return { invalidate, result }
}

describe("form artifact parsing", () => {
  it("parses a form artifact part", () => {
    const part = parseWorkspaceChatArtifactStreamPart(formPart())

    expect(part?.data.artifact).toMatchObject({
      type: "form",
      id: FORM_ID,
      title: "Laptop request",
      status: "online",
    })
  })

  it("accepts a form artifact without a status", () => {
    expect(
      parseWorkspaceChatArtifactStreamPart(formPart({ status: undefined }))
    ).toBeDefined()
    expect(
      parseWorkspaceChatArtifactStreamPart(formPart({ status: null }))
    ).toBeDefined()
  })

  it.each([
    ["an unknown status", { status: "draft" }],
    ["a missing workflow", { workflowId: undefined }],
  ])("rejects a form artifact with %s", (_name, artifact) => {
    expect(
      parseWorkspaceChatArtifactStreamPart(formPart(artifact))
    ).toBeUndefined()
  })
})

describe("useArtifactInvalidation", () => {
  it("refreshes exactly the form's detail, the list and its submissions", () => {
    const { invalidate, result } = setup()

    result.current.handleStreamData(formPart() as never)

    // The detail key is a prefix of the rendered form's key. Without `exact`
    // an open requester page would refetch its form and record a view.
    expect(invalidate.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: ["form", WORKSPACE_ID, FORM_ID], exact: true },
      { queryKey: ["forms", WORKSPACE_ID] },
      { queryKey: ["form-submissions", WORKSPACE_ID, FORM_ID] },
    ])
  })

  it("leaves a rendered form alone", async () => {
    const queryClient = new QueryClient()
    const renderedKey = ["form", WORKSPACE_ID, FORM_ID, "render"]
    queryClient.setQueryData(renderedKey, { id: FORM_ID })
    queryClient.setQueryData(["form", WORKSPACE_ID, FORM_ID], { id: FORM_ID })
    function wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      )
    }
    const { result } = renderHook(() => useArtifactInvalidation(WORKSPACE_ID), {
      wrapper,
    })

    result.current.handleStreamData(formPart() as never)

    expect(queryClient.getQueryState(renderedKey)?.isInvalidated).toBe(false)
    expect(
      queryClient.getQueryState(["form", WORKSPACE_ID, FORM_ID])?.isInvalidated
    ).toBe(true)
  })

  it("refreshes a table the chat changed", () => {
    const { invalidate, result } = setup()

    result.current.handleStreamData({
      type: ARTIFACT_DATA_PART_TYPE,
      data: {
        op: "upsert",
        artifact: { type: "table", id: "table-1", title: "Assets" },
      },
    } as never)

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["table", WORKSPACE_ID, "table-1"],
    })
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["tables", WORKSPACE_ID],
    })
  })

  it("ignores stream parts that are not artifacts", () => {
    const { invalidate, result } = setup()

    result.current.handleStreamData({
      type: "data-other",
      data: { anything: true },
    } as never)
    result.current.handleStreamData({
      type: ARTIFACT_DATA_PART_TYPE,
      data: { op: "upsert", artifact: { type: "form", id: FORM_ID } },
    } as never)

    expect(invalidate).not.toHaveBeenCalled()
  })
})
