import type { ReactFlowJsonObject } from "@xyflow/react"
import { isEphemeral } from "@/components/builder/canvas/canvas"
import { client } from "@/lib/api"

export const CHILD_WORKFLOW_ACTION_TYPE = "core.workflow.execute" as const

type WorkflowDefinitionExport = {
  workspace_id?: string | null
  workflow_id?: string | null
  version?: number
  definition: {
    title: string
    [key: string]: unknown
  }
  layout?: unknown
  case_trigger?: unknown
}

export async function exportWorkflowDefinition(params: {
  workspaceId: string
  workflowId: string
  draft?: boolean
}): Promise<WorkflowDefinitionExport> {
  const response = await client.get<WorkflowDefinitionExport>(
    `/workflows/${params.workflowId}/export`,
    {
      params: {
        workspace_id: params.workspaceId,
        format: "json",
        draft: params.draft ?? false,
      },
    }
  )

  return response.data
}

export function buildDuplicatedWorkflowDefinition(
  definition: WorkflowDefinitionExport,
  title: string
): WorkflowDefinitionExport {
  return {
    ...definition,
    workflow_id: null,
    definition: {
      ...definition.definition,
      title: `Copy of ${title.trim() || definition.definition.title.trim() || "workflow"}`,
    },
  }
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
