import { WorkflowRead } from "@/client"
import { ReactFlowInstance } from "reactflow"

import { slugify } from "@/lib/utils"
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
  // Keep edges that are NOT connected to ephemeral nodes
  object.edges = object.edges.filter(
    (edge) => !ephemeralNodeIds.has(edge.target)
  )

  // Check that the object at least contains the trigger node
  if (!object.nodes.some((node) => node.type === "trigger")) {
    throw new Error("Workflow cannot be saved without a trigger node")
  }
  return object
}
export function ref2id(
  ref: string,
  workflow: WorkflowRead | null
): string | undefined {
  const action = Object.values(workflow?.actions || {}).find(
    (act) => slugify(act.title) === ref
  )
  return action?.id
}
