import type { WorkflowRead } from "@/client"
import type { Edge, Node } from "@xyflow/react"

import { expandSubflowGraph, NodeTypename } from "@/lib/workbench"

const workflowCommon: Omit<WorkflowRead, "object"> = {
  id: "child-workflow",
  title: "Child Workflow",
  description: "Child Workflow Description",
  status: "active",
  actions: {},
  owner_id: "owner-id",
  webhook: {
    id: "webhook-id",
    secret: "webhook-secret",
    status: "online",
    owner_id: "owner-id",
    url: "https://example.com/webhook",
    created_at: "2021-01-01",
    updated_at: "2021-01-01",
    filters: {},
    method: "GET",
    workflow_id: "child-workflow",
  },
  schedules: [],
  entrypoint: null,
  static_inputs: {},
  returns: {},
  config: null,
}
describe("expandSubflowNode", () => {
  // Test data setup
  const baseNodes: Node[] = [
    {
      id: "subflow-1",
      type: NodeTypename.Subflow,
      position: { x: 0, y: 0 },
      data: {},
    },
    {
      id: "regular-node",
      type: "default",
      position: { x: 100, y: 0 },
      data: {},
    },
  ]

  const baseEdges: Edge[] = []

  const mockChildWorkflow: WorkflowRead = {
    ...workflowCommon,
    object: {
      nodes: [
        {
          id: "child-1",
          type: "default",
          position: { x: 0, y: 0 },
          data: {},
        },
        {
          id: "child-2",
          type: "default",
          position: { x: 100, y: 0 },
          data: {},
        },
      ],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    },
  }

  it("should return original nodes when subflow node is not found", async () => {
    const result = await expandSubflowGraph({
      nodes: baseNodes,
      edges: baseEdges,
      workflow: mockChildWorkflow,
      subflowNodeId: "non-existent",
    })

    expect(result.nodes).toEqual(baseNodes)
  })

  it("should return original nodes when node type is not Subflow", async () => {
    const nodesWithoutSubflow: Node[] = [
      {
        id: "regular-1",
        type: "default",
        position: { x: 0, y: 0 },
        data: {},
      },
    ]

    const result = await expandSubflowGraph({
      nodes: nodesWithoutSubflow,
      edges: baseEdges,
      workflow: mockChildWorkflow,
      subflowNodeId: "regular-1",
    })

    expect(result.nodes).toEqual(nodesWithoutSubflow)
  })

  it("should return original nodes when workflow has no child nodes", async () => {
    const emptyWorkflow: WorkflowRead = {
      ...workflowCommon,
      object: {
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    }

    const result = await expandSubflowGraph({
      nodes: baseNodes,
      edges: baseEdges,
      workflow: emptyWorkflow,
      subflowNodeId: "subflow-1",
    })

    expect(result.nodes).toEqual(baseNodes)
  })

  it("should throw error when workflow nodes are not an array", async () => {
    const invalidWorkflow = {
      ...workflowCommon,
      object: {
        nodes: "not an array",
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    }

    await expect(
      expandSubflowGraph({
        nodes: baseNodes,
        edges: baseEdges,
        workflow: invalidWorkflow,
        subflowNodeId: "subflow-1",
      })
    ).rejects.toThrow("Child workflow elements are not arrays")
  })

  it("should successfully expand subflow node with child nodes", async () => {
    const result = await expandSubflowGraph({
      nodes: baseNodes,
      edges: baseEdges,
      workflow: mockChildWorkflow,
      subflowNodeId: "subflow-1",
    })

    // Expected: original nodes + child nodes with parentId and extent
    const expectedNodes = [
      ...baseNodes,
      {
        id: "child-1",
        type: "default",
        position: { x: 0, y: 0 },
        data: {},
        parentId: "subflow-1",
        extent: "parent",
      },
      {
        id: "child-2",
        type: "default",
        position: { x: 100, y: 0 },
        data: {},
        parentId: "subflow-1",
        extent: "parent",
      },
    ]

    expect(result.nodes).toEqual(expectedNodes)
    expect(result.nodes.length).toBe(4) // 2 original + 2 child nodes
    expect(
      result.nodes.filter((n: Node) => n.parentId === "subflow-1").length
    ).toBe(2)
  })
})
