import type {
  ExprValidationResult,
  SecretValidationResult,
  ValidationResult,
} from "@/client"
import type { ReactFlowInstance, ReactFlowJsonObject } from "@xyflow/react"

import { isEphemeral } from "@/components/builder/canvas/canvas"

export const CHILD_WORKFLOW_ACTION_TYPE = "core.workflow.execute" as const

export type ValidationError =
  | ValidationResult
  | SecretValidationResult
  | ExprValidationResult

export function isSecretValidationError(
  error: ValidationError
): error is SecretValidationResult {
  return Boolean(
    error.status === "error" &&
      error.detail &&
      "environment" in error.detail &&
      "secret_name" in error.detail
  )
}
export function isExprValidationError(
  error: ValidationError
): error is ExprValidationResult {
  return Boolean(error.status === "error" && "expression_type" in error)
}

export function isGeneralValidationError(
  error: ValidationError
): error is ValidationResult {
  return !isSecretValidationError(error) && !isExprValidationError(error)
}

/**
 * Prune the React Flow instance to remove ephemeral nodes and edges.
 * @param reactFlowInstance - The React Flow instance.
 * @returns The pruned React Flow instance.
 */
export function pruneReactFlowInstance(reactFlowInstance: ReactFlowInstance) {
  return pruneGraphObject(reactFlowInstance.toObject())
}

/**
 * Prune the graph object to remove ephemeral nodes and edges.
 * @param reactFlowInstance - The React Flow instance.
 * @returns The pruned graph object.
 */
export function pruneGraphObject(
  object: Omit<ReactFlowJsonObject, "viewport">
) {
  // Keep nodes that are NOT ephemeral
  object.nodes = object.nodes.filter((node) => !isEphemeral(node))

  // Create a Set of all valid node IDs for quick lookups
  const validNodeIds = new Set(object.nodes.map((node) => node.id))

  // Keep edges that have both source and target nodes that exist in the graph
  // This is true for all well-formed workflow graphs
  object.edges = object.edges.filter(
    (edge) => validNodeIds.has(edge.source) && validNodeIds.has(edge.target)
  )

  // Check that the object at least contains the trigger node
  if (!object.nodes.some((node) => node.type === "trigger")) {
    throw new Error("Workflow cannot be saved without a trigger node")
  }
  return object
}
