import { pruneGraphObject } from "@/lib/workflow"

// Mock the canvas module to avoid circular dependencies
jest.mock("@/components/builder/canvas/canvas", () => ({
  isEphemeral: jest.fn((node: { type: string }) => node.type === "selector"),
}))

// Helper to create mock nodes with required React Flow properties
function createMockNode(id: string, type: string) {
  return { id, type, position: { x: 0, y: 0 }, data: {} }
}

describe("pruneGraphObject", () => {
  it("preserves valid nodes and edges", () => {
    const result = pruneGraphObject({
      nodes: [
        createMockNode("node1", "trigger"),
        createMockNode("node2", "udf"),
      ],
      edges: [{ id: "edge1", source: "node1", target: "node2" }],
    })

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toHaveLength(1)
  })

  it("removes ephemeral nodes and their connected edges", () => {
    const result = pruneGraphObject({
      nodes: [
        createMockNode("node1", "trigger"),
        createMockNode("node2", "udf"),
        createMockNode("ephemeral1", "selector"), // ephemeral node
      ],
      edges: [
        { id: "edge1", source: "node1", target: "node2" },
        { id: "edge2", source: "node2", target: "ephemeral1" }, // connected to ephemeral
      ],
    })

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toHaveLength(1)
    expect(result.edges.find((e) => e.id === "edge2")).toBeUndefined()
  })

  it("removes orphaned edges with missing source or target nodes", () => {
    const result = pruneGraphObject({
      nodes: [
        createMockNode("node1", "trigger"),
        createMockNode("node2", "udf"),
      ],
      edges: [
        { id: "edge1", source: "node1", target: "node2" }, // valid edge
        { id: "edge2", source: "node1", target: "missing_node" }, // orphaned edge
        { id: "edge3", source: "missing_node", target: "node2" }, // orphaned edge
      ],
    })

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toHaveLength(1)
    expect(result.edges[0].id).toBe("edge1")
  })

  it("throws error when there's no trigger node", () => {
    expect(() =>
      pruneGraphObject({
        nodes: [createMockNode("node1", "udf"), createMockNode("node2", "udf")],
        edges: [{ id: "edge1", source: "node1", target: "node2" }],
      })
    ).toThrow("Workflow cannot be saved without a trigger node")
  })
})
