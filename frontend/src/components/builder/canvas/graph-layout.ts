import Dagre from "@dagrejs/dagre"
import { type Edge, type Node, Position } from "@xyflow/react"

const defaultNodeWidth = 172
const defaultNodeHeight = 36
const builderNodeWidth = 256
const triggerNodeAutoLayoutGap = 64

interface HydratedGraphMergeOptions {
  preserveEphemeral?: boolean
  isEphemeralNode?: (node: Node) => boolean
}

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

function getHandleRank(edge: Edge): number {
  return edge.sourceHandle === "error" ? 1 : 0
}

/**
 * Order edges so success branches are added to dagre before error branches.
 * Dagre seeds its crossing minimization from insertion order, so this biases
 * success children to the left.
 */
function sortEdgesByHandle(edges: Edge[]): Edge[] {
  return [...edges].sort((a, b) => getHandleRank(a) - getHandleRank(b))
}

/**
 * Collect nodes reachable from `start` that are not reachable from any of
 * `exclude` without passing through `start`.
 */
function collectExclusiveDescendants(
  start: string,
  exclude: string[],
  childrenById: Map<string, string[]>
): Set<string> {
  const reachableFromOthers = new Set<string>()
  const stack = [...exclude]
  while (stack.length > 0) {
    const id = stack.pop()
    if (id === undefined || id === start || reachableFromOthers.has(id)) {
      continue
    }
    reachableFromOthers.add(id)
    stack.push(...(childrenById.get(id) ?? []))
  }

  const result = new Set<string>()
  const queue = [start]
  while (queue.length > 0) {
    const id = queue.pop()
    if (id === undefined || result.has(id) || reachableFromOthers.has(id)) {
      continue
    }
    result.add(id)
    queue.push(...(childrenById.get(id) ?? []))
  }
  return result
}

/**
 * Post-process a top-to-bottom dagre layout so that, for every node with both
 * success and error branches, the success children sit to the left of the
 * error children. Subtrees are shifted with their root so edges stay
 * untangled.
 */
function orderBranchesByHandle(
  dagreGraph: Dagre.graphlib.Graph,
  nodes: Node[],
  edges: Edge[]
): void {
  const childrenById = new Map<string, string[]>()
  const edgesBySource = new Map<string, Edge[]>()
  for (const edge of edges) {
    childrenById.set(edge.source, [
      ...(childrenById.get(edge.source) ?? []),
      edge.target,
    ])
    edgesBySource.set(edge.source, [
      ...(edgesBySource.get(edge.source) ?? []),
      edge,
    ])
  }

  for (const node of nodes) {
    const outgoing = edgesBySource.get(node.id) ?? []
    const ranks = new Set(outgoing.map(getHandleRank))
    if (ranks.size < 2) {
      continue
    }

    const children = outgoing
      .filter((edge) => dagreGraph.node(edge.target) !== undefined)
      .map((edge) => ({
        id: edge.target,
        rank: getHandleRank(edge),
        x: dagreGraph.node(edge.target).x,
        y: dagreGraph.node(edge.target).y,
      }))
    const firstY = children[0]?.y
    if (firstY === undefined || children.some((c) => c.y !== firstY)) {
      continue
    }

    const slots = children.map((c) => c.x).sort((a, b) => a - b)
    const desired = [...children].sort((a, b) => a.rank - b.rank || a.x - b.x)
    const childIds = children.map((c) => c.id)

    desired.forEach((child, index) => {
      const delta = slots[index] - child.x
      if (delta === 0) {
        return
      }
      const subtree = collectExclusiveDescendants(
        child.id,
        childIds.filter((id) => id !== child.id),
        childrenById
      )
      for (const id of subtree) {
        const dagreNode = dagreGraph.node(id)
        if (dagreNode) {
          dagreNode.x += delta
        }
      }
    })
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

  sortEdgesByHandle(edges).forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target)
  })

  Dagre.layout(dagreGraph)

  if (!isHorizontal) {
    orderBranchesByHandle(dagreGraph, nodes, edges)
  }

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

/**
 * Merge backend-hydrated nodes while preserving local node metadata and, when
 * requested, local-only ephemeral nodes.
 */
export function mergeHydratedNodes(
  currentNodes: Node[],
  hydratedNodes: Node[],
  options: HydratedGraphMergeOptions = {}
): Node[] {
  const currentNodesById = new Map(currentNodes.map((node) => [node.id, node]))

  const mergedNodes = hydratedNodes.map((node) => {
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

  const isEphemeralNode = options.isEphemeralNode
  if (!options.preserveEphemeral || !isEphemeralNode) {
    return mergedNodes
  }

  const hydratedNodeIds = new Set(mergedNodes.map((node) => node.id))
  const ephemeralNodes = currentNodes.filter(
    (node) => isEphemeralNode(node) && !hydratedNodeIds.has(node.id)
  )

  return [...mergedNodes, ...ephemeralNodes]
}

/**
 * Merge backend-hydrated edges while preserving local-only edges connected to
 * preserved ephemeral nodes.
 */
export function mergeHydratedEdges(
  currentEdges: Edge[],
  hydratedEdges: Edge[],
  nodes: Node[],
  options: HydratedGraphMergeOptions = {}
): Edge[] {
  const isEphemeralNode = options.isEphemeralNode
  if (!options.preserveEphemeral || !isEphemeralNode) {
    return hydratedEdges
  }

  const nodeIds = new Set(nodes.map((node) => node.id))
  const ephemeralNodeIds = new Set(
    nodes.filter((node) => isEphemeralNode(node)).map((node) => node.id)
  )
  const hydratedEdgeIds = new Set(hydratedEdges.map((edge) => edge.id))

  const ephemeralEdges = currentEdges.filter((edge) => {
    if (hydratedEdgeIds.has(edge.id)) {
      return false
    }
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      return false
    }
    return (
      ephemeralNodeIds.has(edge.source) || ephemeralNodeIds.has(edge.target)
    )
  })

  return [...hydratedEdges, ...ephemeralEdges]
}
