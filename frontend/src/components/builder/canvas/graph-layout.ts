import type { Edge, Node } from "@xyflow/react"

interface HydratedGraphMergeOptions {
  preserveEphemeral?: boolean
  isEphemeralNode?: (node: Node) => boolean
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
