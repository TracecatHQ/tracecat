import Dagre from "@dagrejs/dagre"
import { type Edge, type Node, Position } from "@xyflow/react"

const defaultNodeWidth = 172
const defaultNodeHeight = 36
const builderNodeWidth = 256
const triggerNodeAutoLayoutGap = 64

function getDefaultNodeWidth(node: Node): number {
  if (node.type === "trigger" || node.type === "udf") {
    return builderNodeWidth
  }
  return defaultNodeWidth
}

export function getNodeLayoutDimensions(node: Node): {
  width: number
  height: number
} {
  return {
    width: node.measured?.width ?? node.width ?? getDefaultNodeWidth(node),
    height: node.measured?.height ?? node.height ?? defaultNodeHeight,
  }
}

/**
 * Taken from https://reactflow.dev/examples/layout/dagre
 */
export function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
  direction = "TB"
): {
  nodes: Node[]
  edges: Edge[]
} {
  const dagreGraph = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  const isHorizontal = direction === "LR"
  dagreGraph.setGraph({ rankdir: direction, nodesep: 250, ranksep: 300 })

  nodes.forEach((node) => {
    const { width, height } = getNodeLayoutDimensions(node)
    dagreGraph.setNode(node.id, { width, height })
  })

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target)
  })

  Dagre.layout(dagreGraph)

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id)
    const { width, height } = getNodeLayoutDimensions(node)

    return {
      ...node,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      // Dagre uses a center anchor while React Flow uses top-left.
      position: {
        x: nodeWithPosition.x - width / 2,
        y: nodeWithPosition.y - height / 2,
      },
    }
  })

  if (isHorizontal) {
    return { nodes: newNodes, edges }
  }

  const triggerNode = newNodes.find((node) => node.type === "trigger")
  if (!triggerNode) {
    return { nodes: newNodes, edges }
  }

  const triggerY = triggerNode.position.y
  return {
    nodes: newNodes.map((node) => {
      if (node.id === triggerNode.id || node.position.y <= triggerY) {
        return node
      }
      return {
        ...node,
        position: {
          ...node.position,
          y: node.position.y + triggerNodeAutoLayoutGap,
        },
      }
    }),
    edges,
  }
}

export function mergeHydratedNodes(
  currentNodes: Node[],
  hydratedNodes: Node[]
): Node[] {
  const currentNodesById = new Map(currentNodes.map((node) => [node.id, node]))

  return hydratedNodes.map((node) => {
    const currentNode = currentNodesById.get(node.id)
    if (!currentNode) {
      return node
    }

    return {
      ...node,
      selected: currentNode.selected ?? node.selected,
      measured: currentNode.measured ?? node.measured,
      width: currentNode.width ?? node.width,
      height: currentNode.height ?? node.height,
    }
  })
}
