import type { WorkflowRead } from "@/client"
import type { Edge, Node, ReactFlowJsonObject } from "@xyflow/react"

import {
  ActionNodeData,
  ephemeralNodeTypes,
  invincibleNodeTypes,
  NodeData,
  NodeTypename,
  SubflowNodeData,
} from "@/lib/workbench"
import {
  defaultNodeHeight,
  defaultNodeWidth,
  getLayoutedElements,
} from "@/lib/workbench/utils/layout"

/**
 * Get the dimensions of a subflow node
 * @param initialWidth Width when collapsed
 * @param initialHeight Height when collapsed
 * @param expandedWidth Width when expanded
 * @param expandedHeight Height when expanded
 * @returns Dimensions for calculating paddings
 */
export function getSubflowNodeDimensions(
  initialWidth: number,
  initialHeight: number,
  expandedWidth: number,
  expandedHeight: number
): {
  left: number
  right: number
  top: number
  bottom: number
} {
  // Calculate the difference in width and height
  const dw = expandedWidth - initialWidth
  const dh = expandedHeight - initialHeight

  // Fixed top padding for header space
  const topPadding = 100

  return {
    left: dw / 2,
    right: dw / 2,
    top: topPadding,
    bottom: dh - initialHeight - topPadding,
  }
}

/**
 * Expand a subflow node.
 * Take a subflow + nodes and return the next state of the nodes array with the subflow expanded.
 * Logic:
 * - If a node is expanded, we need to make it a parent of its children
 * - We should also set the children's parentId to the subflow node's id
 * - If a node is collapsed, we don't do anything
 *
 * @param nodes - Array of all nodes in the workflow
 * @param edges - Array of all edges in the workflow
 * @param workflow - The child workflow object
 * @param subflowNodeId - ID of the subflow node to expand
 * @returns The updated nodes array with parent-child relationships set, or null if collapsed
 */
export function expandSubflowGraph({
  nodes,
  edges,
  workflow,
  subflowNodeId,
}: {
  nodes: Node[]
  edges: Edge[]
  workflow: WorkflowRead
  subflowNodeId: string
}): ReactFlowJsonObject<Node> {
  const subflowNode = nodes.find((node) => node.id === subflowNodeId)

  // If subflow node doesn't exist or is not a subflow type, return original elements
  const graph = workflow?.object as ReactFlowJsonObject<Node> | undefined
  if (!subflowNode || subflowNode.type !== NodeTypename.Subflow || !graph) {
    return { nodes, edges, viewport: { x: 0, y: 0, zoom: 1 } }
  }

  // Pull nodes and edges out of the child workflow
  const subflowNodes = graph.nodes
  const subflowEdges = graph.edges

  if (!Array.isArray(subflowNodes) || !Array.isArray(subflowEdges)) {
    throw new Error("Child workflow elements are not arrays")
  }

  if (!subflowNodes.length) {
    console.log("No child workflow nodes found")
    return { nodes, edges, viewport: { x: 0, y: 0, zoom: 1 } }
  }

  // Mark each child node's parentId to the subflow node's id
  const subflowNodeTypes = [NodeTypename.Subflow, NodeTypename.Action] as const
  const triggerNode = subflowNodes.find((node) => node.id.startsWith("trigger"))
  if (!triggerNode) {
    throw new Error("Trigger node not found in subflow")
  }

  const boundSubflowNodes = subflowNodes
    .filter((node) =>
      subflowNodeTypes.includes(node.type as (typeof subflowNodeTypes)[number])
    )
    .map((node) => ({
      ...node,
      parentId: subflowNodeId,
      extent: "parent",
      selected: false,
      data: {
        ...node.data,
        subflowId: workflow.id,
        subflowAlias: workflow.alias,
      } as ActionNodeData | SubflowNodeData,
    })) as Node[]

  // Mark each edge as belonging to this subflow
  // For all edges that have a source of the trigger node, set the target to the subflow node
  const boundSubflowEdges = subflowEdges
    .filter((edge) => edge.source !== subflowNodeId)
    .map((edge) => ({
      ...edge,
      source: edge.source === triggerNode.id ? subflowNodeId : edge.source,
      sourceHandle:
        edge.source === triggerNode.id ? "subflow-trigger" : edge.sourceHandle,
      data: {
        ...edge.data,
        subflowId: workflow.id,
      },
    })) as Edge[]

  // Layout the subflow nodes
  const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
    boundSubflowNodes,
    boundSubflowEdges
  )

  // Calculate the bounding box of all nodes to normalize positions
  const positions = layoutedNodes.map((node) => ({
    x: node.position.x,
    y: node.position.y,
    width: node.width ?? defaultNodeWidth,
    height: node.height ?? defaultNodeHeight,
  }))

  // Find the minimum x and y values to use as the origin point
  const minX = Math.min(...positions.map((p) => p.x))
  const minY = Math.min(...positions.map((p) => p.y))

  // Apply fixed padding for top header area and sides
  const HEADER_PADDING = 120 // Space for the subflow header
  const SIDE_PADDING = 50 // Padding on all sides

  // Transform node positions to be relative to the parent's top-left corner (with padding)
  const adjustedNodes = layoutedNodes.map((node) => ({
    ...node,
    position: {
      x: node.position.x - minX + SIDE_PADDING,
      y: node.position.y - minY + HEADER_PADDING,
    },
  }))

  return {
    nodes: [...nodes, ...adjustedNodes],
    edges: [...edges, ...layoutedEdges],
    viewport: graph.viewport,
  }
}

export function isInvincible(node: Node | Node<NodeData>): boolean {
  return invincibleNodeTypes.includes(node?.type as string)
}

export function isEphemeral(node: Node | Node<NodeData>): boolean {
  return ephemeralNodeTypes.includes(node?.type as string)
}
