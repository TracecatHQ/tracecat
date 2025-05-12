import { ReactFlowInstance } from "@xyflow/react"

import { pruneReactFlowInstance } from "@/lib/workflow"

// Mock the canvas module to avoid circular dependencies
jest.mock("@/components/builder/canvas/canvas", () => ({
  isEphemeral: jest.fn((node: { type: string }) => node.type === "selector"),
}))

describe("pruneReactFlowInstance", () => {
  let mockReactFlowInstance: ReactFlowInstance

  beforeEach(() => {
    // Create a mock ReactFlowInstance for testing
    mockReactFlowInstance = {
      toObject: jest.fn(),
    } as unknown as ReactFlowInstance
  })

  it("preserves valid nodes and edges", () => {
    // Set up test data with valid nodes and edges
    mockReactFlowInstance.toObject = jest.fn().mockReturnValue({
      nodes: [
        { id: "node1", type: "trigger" },
        { id: "node2", type: "udf" },
      ],
      edges: [{ id: "edge1", source: "node1", target: "node2" }],
      viewport: { x: 0, y: 0, zoom: 1 },
    })

    const result = pruneReactFlowInstance(mockReactFlowInstance)

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toHaveLength(1)
  })

  it("removes ephemeral nodes and their connected edges", () => {
    // Set up test data with ephemeral nodes
    mockReactFlowInstance.toObject = jest.fn().mockReturnValue({
      nodes: [
        { id: "node1", type: "trigger" },
        { id: "node2", type: "udf" },
        { id: "ephemeral1", type: "selector" }, // ephemeral node
      ],
      edges: [
        { id: "edge1", source: "node1", target: "node2" },
        { id: "edge2", source: "node2", target: "ephemeral1" }, // connected to ephemeral
      ],
      viewport: { x: 0, y: 0, zoom: 1 },
    })

    const result = pruneReactFlowInstance(mockReactFlowInstance)

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toHaveLength(1)
    expect(result.edges.find((e) => e.id === "edge2")).toBeUndefined()
  })

  it("removes orphaned edges with missing source or target nodes", () => {
    // Set up test data with orphaned edges
    mockReactFlowInstance.toObject = jest.fn().mockReturnValue({
      nodes: [
        { id: "node1", type: "trigger" },
        { id: "node2", type: "udf" },
      ],
      edges: [
        { id: "edge1", source: "node1", target: "node2" }, // valid edge
        { id: "edge2", source: "node1", target: "missing_node" }, // orphaned edge
        { id: "edge3", source: "missing_node", target: "node2" }, // orphaned edge
      ],
      viewport: { x: 0, y: 0, zoom: 1 },
    })

    const result = pruneReactFlowInstance(mockReactFlowInstance)

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toHaveLength(1)
    expect(result.edges[0].id).toBe("edge1")
  })

  it("throws error when there's no trigger node", () => {
    // Set up test data without a trigger node
    mockReactFlowInstance.toObject = jest.fn().mockReturnValue({
      nodes: [
        { id: "node1", type: "udf" },
        { id: "node2", type: "udf" },
      ],
      edges: [{ id: "edge1", source: "node1", target: "node2" }],
      viewport: { x: 0, y: 0, zoom: 1 },
    })

    expect(() => pruneReactFlowInstance(mockReactFlowInstance)).toThrow(
      "Workflow cannot be saved without a trigger node"
    )
  })
})
