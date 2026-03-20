import type { Edge, Node } from "@xyflow/react"
import {
  getLayoutedElements,
  getNodeLayoutDimensions,
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
      { x: 0, y: 400 },
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
      { x: 0, y: 400 },
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
    expect(action?.position.y).toBe(400)
  })
})
