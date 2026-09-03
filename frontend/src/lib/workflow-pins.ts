import type { ActionRead } from "@/client"
import { slugifyActionRef } from "@/lib/utils"

export type WorkflowDraftPins = {
  source_execution_id: string
  action_refs: string[]
}

type WorkflowWithDraftPins = {
  draft_pins?: WorkflowDraftPins | null
}

export type PinDomains = {
  pinnedRefs: Set<string>
  forceSkipRefs: Set<string>
}

export function getWorkflowDraftPins(
  workflow: unknown
): WorkflowDraftPins | null {
  const pins = (workflow as WorkflowWithDraftPins | null)?.draft_pins
  if (
    !pins ||
    typeof pins.source_execution_id !== "string" ||
    !Array.isArray(pins.action_refs)
  ) {
    return null
  }

  const actionRefs = pins.action_refs.filter(
    (actionRef): actionRef is string => typeof actionRef === "string"
  )
  return {
    source_execution_id: pins.source_execution_id,
    action_refs: actionRefs,
  }
}

export function computePinDomains(
  actions: Record<string, ActionRead> | null | undefined,
  pins: WorkflowDraftPins | null
): PinDomains {
  if (!actions || !pins || pins.action_refs.length === 0) {
    return { pinnedRefs: new Set(), forceSkipRefs: new Set() }
  }

  const actionList = Object.values(actions)
  // Workflow reads describe the graph via `upstream_edges` keyed by action ID,
  // so resolve edges through an ID -> ref map rather than `depends_on`.
  const refByActionId = new Map<string, string>()
  for (const action of actionList) {
    const actionRef = slugifyActionRef(action.title)
    if (actionRef.length > 0) {
      refByActionId.set(action.id, actionRef)
    }
  }
  const allActionRefs = new Set<string>(refByActionId.values())

  const pinnedRefs = new Set(
    pins.action_refs.filter((actionRef) => allActionRefs.has(actionRef))
  )
  if (pinnedRefs.size === 0) {
    return { pinnedRefs, forceSkipRefs: new Set() }
  }

  const adjacency = new Map<string, Set<string>>()
  for (const actionRef of allActionRefs) {
    adjacency.set(actionRef, new Set())
  }

  for (const action of actionList) {
    const targetRef = refByActionId.get(action.id)
    if (!targetRef) {
      continue
    }

    for (const edge of action.upstream_edges ?? []) {
      if (edge.source_type !== "udf") {
        continue
      }
      const sourceRef = refByActionId.get(edge.source_id)
      if (!sourceRef || sourceRef === targetRef) {
        continue
      }
      adjacency.get(sourceRef)?.add(targetRef)
    }
  }

  const skipDomain = new Set(pinnedRefs)
  let changed = true
  while (changed) {
    changed = false
    for (const [actionRef, nextRefs] of adjacency.entries()) {
      if (skipDomain.has(actionRef) || nextRefs.size === 0) {
        continue
      }
      const allDownstreamSkipped = Array.from(nextRefs).every((nextRef) =>
        skipDomain.has(nextRef)
      )
      if (allDownstreamSkipped) {
        skipDomain.add(actionRef)
        changed = true
      }
    }
  }

  const forceSkipRefs = new Set(
    Array.from(skipDomain).filter((actionRef) => !pinnedRefs.has(actionRef))
  )
  return { pinnedRefs, forceSkipRefs }
}
