import Dagre from "@dagrejs/dagre"
import { Position, type Edge, type Node } from "@xyflow/react"

export const dagreGraph = new Dagre.graphlib.Graph().setDefaultEdgeLabel(
  () => ({})
)
export const defaultNodeWidth = 172
export const defaultNodeHeight = 36

/**
 * Taken from https://reactflow.dev/examples/layout/dagre
 * @param nodes
 * @param edges
 * @param direction
 * @returns
 */

export function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
  direction = "TB"
): {
  nodes: Node[]
  edges: Edge[]
} {
  const isHorizontal = direction === "LR"
  dagreGraph.setGraph({ rankdir: direction, nodesep: 200, ranksep: 200 })

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, {
      width: node.width ?? defaultNodeWidth,
      height: node.height ?? defaultNodeHeight,
    })
  })

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target)
  })

  Dagre.layout(dagreGraph)

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id)
    const height = node.height ?? defaultNodeHeight
    const width = node.width ?? defaultNodeWidth
    const newNode = {
      ...node,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      // We are shifting the dagre node position (anchor=center center) to the top left
      // so it matches the React Flow node anchor point (top left).
      position: {
        x: nodeWithPosition.x - width / 2,
        y: nodeWithPosition.y - height / 2,
      },
      selected: false,
    }

    return newNode
  })

  return { nodes: newNodes, edges }
}
