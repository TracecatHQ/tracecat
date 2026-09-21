import type { Edge, Node } from "@xyflow/react"
import {
  getLayoutedElements,
  getNodeLayoutDimensions,
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

function createEdge(
  source: string,
  target: string,
  sourceHandle?: "success" | "error"
): Edge {
  return {
    id: `${source}-${target}`,
    source,
    target,
    sourceHandle,
  }
}

function positionOf(nodes: Node[], id: string) {
  const node = nodes.find((n) => n.id === id)
  if (!node) {
    throw new Error(`missing node ${id}`)
  }
  return node.position
}

describe("getNodeLayoutDimensions", () => {
  it("prefers measured dimensions over width and fallback values", () => {
    const node = createNode("trigger-1", "trigger", {
      measured: { width: 256, height: 48 },
      width: 172,
      height: 36,
    })

    expect(getNodeLayoutDimensions(node)).toEqual({
      width: 256,
      height: 48,
    })
  })
})

describe("getLayoutedElements", () => {
  it("uses measured widths when converting dagre positions to top-left coordinates", () => {
    const { nodes } = getLayoutedElements(
      [
        createNode("trigger-1", "trigger", {
          measured: { width: 256, height: 36 },
        }),
        createNode("action-1", "udf", {
          measured: { width: 256, height: 36 },
        }),
      ],
      [createEdge("trigger-1", "action-1")]
    )

    expect(nodes.map((node) => node.position)).toEqual([
      { x: 0, y: 0 },
      { x: 0, y: 250 },
    ])
  })

  it("does not leak dagre state between successive layouts", () => {
    getLayoutedElements(
      [
        createNode("trigger", "trigger"),
        createNode("action-a", "udf"),
        createNode("action-b", "udf"),
      ],
      [createEdge("trigger", "action-a"), createEdge("trigger", "action-b")]
    )

    const { nodes } = getLayoutedElements(
      [createNode("trigger", "trigger"), createNode("action-a", "udf")],
      [createEdge("trigger", "action-a")]
    )

    expect(nodes.map((node) => node.position)).toEqual([
      { x: 0, y: 0 },
      { x: 0, y: 250 },
    ])
  })

  it("keeps the trigger auto-layout gap for vertical layouts", () => {
    const { nodes } = getLayoutedElements(
      [createNode("trigger-1", "trigger"), createNode("action-1", "udf")],
      [createEdge("trigger-1", "action-1")]
    )

    const trigger = nodes.find((node) => node.type === "trigger")
    const action = nodes.find((node) => node.type === "udf")

    expect(trigger?.position.y).toBe(0)
    expect(action?.position.y).toBe(250)
  })

  it.each([
    ["success first", ["success", "error"] as const],
    ["error first", ["error", "success"] as const],
  ])(
    "places success branches left of error branches (%s edge order)",
    (_label, order) => {
      const nodes = [
        createNode("trigger", "trigger"),
        createNode("require", "udf"),
        createNode("on-success", "udf"),
        createNode("on-error", "udf"),
        createNode("after-success", "udf"),
        createNode("after-error", "udf"),
      ]
      const branchEdges = order.map((handle) =>
        createEdge("require", `on-${handle}`, handle)
      )
      const edges = [
        createEdge("trigger", "require"),
        ...branchEdges,
        createEdge("on-success", "after-success", "success"),
        createEdge("on-error", "after-error", "success"),
      ]

      const { nodes: layouted } = getLayoutedElements(nodes, edges)

      expect(positionOf(layouted, "on-success").x).toBeLessThan(
        positionOf(layouted, "on-error").x
      )
      expect(positionOf(layouted, "after-success").x).toBe(
        positionOf(layouted, "on-success").x
      )
      expect(positionOf(layouted, "after-error").x).toBe(
        positionOf(layouted, "on-error").x
      )
    }
  )

  it("keeps every success child left of every error child with multiple children per handle", () => {
    const nodes = [
      createNode("trigger", "trigger"),
      createNode("require", "udf"),
      createNode("ok-1", "udf"),
      createNode("ok-2", "udf"),
      createNode("err-1", "udf"),
      createNode("err-2", "udf"),
    ]
    const edges = [
      createEdge("trigger", "require"),
      createEdge("require", "err-1", "error"),
      createEdge("require", "ok-1", "success"),
      createEdge("require", "err-2", "error"),
      createEdge("require", "ok-2", "success"),
    ]

    const { nodes: layouted } = getLayoutedElements(nodes, edges)
    const xs = (ids: string[]) => ids.map((id) => positionOf(layouted, id).x)
    const maxSuccessX = Math.max(...xs(["ok-1", "ok-2"]))
    const minErrorX = Math.min(...xs(["err-1", "err-2"]))

    expect(maxSuccessX).toBeLessThan(minErrorX)
    expect(new Set(xs(["ok-1", "ok-2", "err-1", "err-2"])).size).toBe(4)
    expect(
      new Set(
        ["ok-1", "ok-2", "err-1", "err-2"].map(
          (id) => positionOf(layouted, id).y
        )
      ).size
    ).toBe(1)
  })

  it("does not shift a downstream node shared by both branches", () => {
    const nodes = [
      createNode("trigger", "trigger"),
      createNode("require", "udf"),
      createNode("on-success", "udf"),
      createNode("on-error", "udf"),
      createNode("join", "udf"),
    ]
    const edges = [
      createEdge("trigger", "require"),
      createEdge("require", "on-error", "error"),
      createEdge("require", "on-success", "success"),
      createEdge("on-success", "join", "success"),
      createEdge("on-error", "join", "success"),
    ]

    const { nodes: layouted } = getLayoutedElements(nodes, edges)
    const successX = positionOf(layouted, "on-success").x
    const errorX = positionOf(layouted, "on-error").x
    const joinX = positionOf(layouted, "join").x

    expect(successX).toBeLessThan(errorX)
    expect(joinX).toBeGreaterThanOrEqual(successX)
    expect(joinX).toBeLessThanOrEqual(errorX)
    expect(joinX).toBe(positionOf(layouted, "require").x)
  })

  it("leaves nodes untouched when only one handle type is used", () => {
    const nodes = [
      createNode("trigger", "trigger"),
      createNode("require", "udf"),
      createNode("a", "udf"),
      createNode("b", "udf"),
    ]
    const edges = [
      createEdge("trigger", "require"),
      createEdge("require", "a", "success"),
      createEdge("require", "b", "success"),
    ]

    const { nodes: layouted } = getLayoutedElements(nodes, edges)
    const aX = positionOf(layouted, "a").x
    const bX = positionOf(layouted, "b").x

    expect(aX).not.toBe(bX)
    expect(positionOf(layouted, "a").y).toBe(positionOf(layouted, "b").y)
  })

  it("does not reorder branches whose children land on different ranks", () => {
    const nodes = [
      createNode("trigger", "trigger"),
      createNode("require", "udf"),
      createNode("on-success", "udf"),
      createNode("on-error", "udf"),
    ]
    const edges = [
      createEdge("trigger", "require"),
      createEdge("require", "on-error", "error"),
      createEdge("require", "on-success", "success"),
      createEdge("on-error", "on-success", "success"),
    ]

    const { nodes: layouted } = getLayoutedElements(nodes, edges)
    const { nodes: baseline } = getLayoutedElements(
      nodes,
      edges.map((edge) => ({ ...edge, sourceHandle: undefined }))
    )

    expect(positionOf(layouted, "on-error").y).toBeLessThan(
      positionOf(layouted, "on-success").y
    )
    expect(layouted.map((node) => node.position)).toEqual(
      baseline.map((node) => node.position)
    )
  })

  it("does not apply branch ordering to horizontal layouts", () => {
    const nodes = [
      createNode("trigger", "trigger"),
      createNode("require", "udf"),
      createNode("on-success", "udf"),
      createNode("on-error", "udf"),
    ]
    const edges = [
      createEdge("trigger", "require"),
      createEdge("require", "on-error", "error"),
      createEdge("require", "on-success", "success"),
    ]

    const { nodes: layouted } = getLayoutedElements(nodes, edges, "LR")

    expect(positionOf(layouted, "on-success").x).toBe(
      positionOf(layouted, "on-error").x
    )
    expect(positionOf(layouted, "on-success").y).not.toBe(
      positionOf(layouted, "on-error").y
    )
  })
})

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
