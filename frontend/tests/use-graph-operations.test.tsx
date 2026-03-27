import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import {
  type GraphOperation,
  graphApplyGraphOperations,
  graphGetGraph,
} from "@/client"
import { useGraphOperations } from "@/lib/hooks"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    graphApplyGraphOperations: jest.fn(),
    graphGetGraph: jest.fn(),
  }
})

const mockGraphApplyGraphOperations =
  graphApplyGraphOperations as jest.MockedFunction<
    typeof graphApplyGraphOperations
  >

const mockGraphGetGraph = graphGetGraph as jest.MockedFunction<
  typeof graphGetGraph
>

function createGraphResponse(version: number, nodeX = 0) {
  return {
    version,
    nodes: [
      {
        id: "trigger-workflow-1",
        type: "trigger",
        position: { x: nodeX, y: 0 },
        data: {},
      },
    ],
    edges: [],
    viewport: { x: 0, y: 0, zoom: 1 },
  }
}

describe("useGraphOperations", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    jest.clearAllMocks()
    mockGraphGetGraph.mockResolvedValue(
      createGraphResponse(1) as Awaited<ReturnType<typeof graphGetGraph>>
    )
  })

  it("does not rewrite the graph cache for non-structural graph operations", async () => {
    const cachedGraph = createGraphResponse(1)
    const returnedGraph = createGraphResponse(1, 400)
    queryClient.setQueryData(
      ["graph", "workspace-1", "workflow-1"],
      cachedGraph
    )
    const invalidateQueriesSpy = jest.spyOn(queryClient, "invalidateQueries")

    mockGraphApplyGraphOperations.mockResolvedValue(
      returnedGraph as Awaited<ReturnType<typeof graphApplyGraphOperations>>
    )

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(
      () => useGraphOperations("workspace-1", "workflow-1"),
      { wrapper }
    )

    const operations: GraphOperation[] = [
      {
        type: "move_nodes",
        payload: {
          positions: [{ action_id: "action-1", x: 400, y: 200 }],
        },
      },
    ]

    await result.current.applyGraphOperations({
      baseVersion: 1,
      operations,
    })

    await waitFor(() => {
      expect(mockGraphApplyGraphOperations).toHaveBeenCalledTimes(1)
    })
    expect(
      queryClient.getQueryData(["graph", "workspace-1", "workflow-1"])
    ).toBe(cachedGraph)
    expect(invalidateQueriesSpy).not.toHaveBeenCalled()
  })

  it("updates the graph cache for structural graph operations", async () => {
    const cachedGraph = createGraphResponse(1)
    const returnedGraph = createGraphResponse(2, 240)
    queryClient.setQueryData(
      ["graph", "workspace-1", "workflow-1"],
      cachedGraph
    )
    const invalidateQueriesSpy = jest.spyOn(queryClient, "invalidateQueries")

    mockGraphApplyGraphOperations.mockResolvedValue(
      returnedGraph as Awaited<ReturnType<typeof graphApplyGraphOperations>>
    )

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(
      () => useGraphOperations("workspace-1", "workflow-1"),
      { wrapper }
    )

    const operations: GraphOperation[] = [
      {
        type: "add_node",
        payload: {
          type: "core.test",
          title: "Test node",
          position_x: 240,
          position_y: 120,
        },
      },
    ]

    await result.current.applyGraphOperations({
      baseVersion: 1,
      operations,
    })

    await waitFor(() => {
      expect(
        queryClient.getQueryData(["graph", "workspace-1", "workflow-1"])
      ).toStrictEqual(returnedGraph)
    })
    expect(invalidateQueriesSpy).toHaveBeenCalledWith({
      queryKey: ["workflow", "workflow-1"],
    })
  })
})
