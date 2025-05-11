import { ReactFlowInstance } from "@xyflow/react"

import { isEphemeral } from "@/components/workbench/canvas/canvas"

export const CHILD_WORKFLOW_ACTION_TYPE = "core.workflow.execute" as const

/**
 * Prune the graph object to remove ephemeral nodes and edges.
 * @param reactFlowInstance - The React Flow instance.
 * @returns The pruned graph object.
 */
export function pruneGraphObject(reactFlowInstance: ReactFlowInstance) {
  const object = reactFlowInstance.toObject()

  // Filter out non-ephemeral nodes and their associated edges
  const ephemeralNodeIds = new Set(
    object.nodes.filter(isEphemeral).map((node) => node.id)
  )

  // Keep nodes that are NOT ephemeral
  object.nodes = object.nodes.filter((node) => !ephemeralNodeIds.has(node.id))

  // Create a Set of all valid node IDs for quick lookups
  const validNodeIds = new Set(object.nodes.map((node) => node.id))

  // Keep edges that:
  // 1. Are NOT connected to ephemeral nodes
  // 2. Have both source and target nodes that exist in the graph
  object.edges = object.edges.filter(
    (edge) =>
      !ephemeralNodeIds.has(edge.target) &&
      validNodeIds.has(edge.source) &&
      validNodeIds.has(edge.target)
  )

  // Check that the object at least contains the trigger node
  if (!object.nodes.some((node) => node.type === "trigger")) {
    throw new Error("Workflow cannot be saved without a trigger node")
  }
  return object
}
