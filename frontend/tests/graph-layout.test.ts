import type { Edge, Node } from "@xyflow/react"
import {
  mergeHydratedEdges,
  mergeHydratedNodes,
} from "@/components/builder/canvas/graph-layout"

function createNode(id: string, type: string, overrides?: Partial<Node>): Node {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: {},
    ...overrides,
  } as Node
}

function createEdge(source: string, target: string): Edge {
  return {
    id: `${source}-${target}`,
    source,
    target,
  }
}

describe("mergeHydratedNodes", () => {
  it("preserves measured dimensions, width, height, and selection for matching nodes", () => {
    const currentNodes = [
      createNode("trigger-1", "trigger", {
        selected: true,
        measured: { width: 256, height: 48 },
        width: 256,
        height: 48,
      }),
      createNode("action-1", "udf", {
        measured: { width: 320, height: 64 },
        width: 320,
        height: 64,
      }),
    ]
    const hydratedNodes = [
      createNode("trigger-1", "trigger", {
        selected: false,
        position: { x: 10, y: 20 },
      }),
      createNode("action-1", "udf", {
        position: { x: 30, y: 40 },
      }),
      createNode("action-2", "udf", {
        position: { x: 50, y: 60 },
      }),
    ]

    expect(mergeHydratedNodes(currentNodes, hydratedNodes)).toEqual([
      createNode("trigger-1", "trigger", {
        selected: true,
        position: { x: 10, y: 20 },
        measured: { width: 256, height: 48 },
        width: 256,
        height: 48,
      }),
      createNode("action-1", "udf", {
        position: { x: 30, y: 40 },
        measured: { width: 320, height: 64 },
        width: 320,
        height: 64,
      }),
      createNode("action-2", "udf", {
        position: { x: 50, y: 60 },
      }),
    ])
  })

  it("preserves local ephemeral nodes when requested", () => {
    const currentNodes = [
      createNode("trigger-1", "trigger"),
      createNode("selector-1", "selector", {
        position: { x: 100, y: 200 },
      }),
    ]
    const hydratedNodes = [createNode("trigger-1", "trigger")]

    expect(
      mergeHydratedNodes(currentNodes, hydratedNodes, {
        preserveEphemeral: true,
        isEphemeralNode: (node) => node.type === "selector",
      })
    ).toEqual([
      createNode("trigger-1", "trigger"),
      createNode("selector-1", "selector", {
        position: { x: 100, y: 200 },
      }),
    ])
  })
})

describe("mergeHydratedEdges", () => {
  it("preserves local edges connected to preserved ephemeral nodes", () => {
    const currentEdges = [
      createEdge("trigger-1", "selector-1"),
      createEdge("removed-action", "selector-1"),
    ]
    const hydratedEdges = [createEdge("trigger-1", "action-1")]
    const nodes = [
      createNode("trigger-1", "trigger"),
      createNode("action-1", "udf"),
      createNode("selector-1", "selector"),
    ]

    expect(
      mergeHydratedEdges(currentEdges, hydratedEdges, nodes, {
        preserveEphemeral: true,
        isEphemeralNode: (node) => node.type === "selector",
      })
    ).toEqual([
      createEdge("trigger-1", "action-1"),
      createEdge("trigger-1", "selector-1"),
    ])
  })

  it("uses the hydrated edge list when ephemeral preservation is disabled", () => {
    const currentEdges = [createEdge("trigger-1", "selector-1")]
    const hydratedEdges = [createEdge("trigger-1", "action-1")]
    const nodes = [
      createNode("trigger-1", "trigger"),
      createNode("action-1", "udf"),
      createNode("selector-1", "selector"),
    ]

    expect(mergeHydratedEdges(currentEdges, hydratedEdges, nodes)).toEqual(
      hydratedEdges
    )
  })
})
