import type {
  ActionRead,
  WorkflowDraftPins as WorkflowDraftPinsRead,
  WorkflowRead,
} from "@/client"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"
import { slugifyActionRef } from "@/lib/utils"

export type WorkflowDraftPins = Required<WorkflowDraftPinsRead>

/** Stream id of root-level (non scatter/loop) events, mirroring the backend `ROOT_STREAM`. */
const ROOT_STREAM_ID = "<root>:0"

/** Action types whose orchestration results cannot be reused as draft pins. */
export const UNPINNABLE_ACTION_TYPES = new Set([
  "core.transform.scatter",
  "core.transform.gather",
  "core.loop.start",
  "core.loop.end",
])

export type PinDomains = {
  pinnedRefs: Set<string>
  forceSkipRefs: Set<string>
}

export function getWorkflowDraftPins(
  workflow: Pick<WorkflowRead, "draft_pins"> | null | undefined
): WorkflowDraftPins | null {
  const pins = workflow?.draft_pins
  if (!pins || typeof pins.source_execution_id !== "string") {
    return null
  }

  const actionRefs = (pins.action_refs ?? []).filter(
    (actionRef): actionRef is string => typeof actionRef === "string"
  )
  return {
    source_execution_id: pins.source_execution_id,
    action_refs: actionRefs,
  }
}

/** Return whether a selected execution event is eligible to become a draft pin. */
export function isPinnableActionEvent(
  actionRef: string | undefined,
  groupedEvents: Record<string, WorkflowExecutionEventCompact[]>,
  actions: Record<string, ActionRead> | null | undefined
): boolean {
  if (!actionRef || !groupedEvents[actionRef] || !actions) {
    return false
  }

  const action = Object.values(actions).find(
    (candidate) => slugifyActionRef(candidate.title) === actionRef
  )
  if (
    !action ||
    UNPINNABLE_ACTION_TYPES.has(action.type) ||
    action.control_flow?.mask_output === true
  ) {
    return false
  }

  return groupedEvents[actionRef].some(
    (event) =>
      event.status === "COMPLETED" &&
      !event.action_error &&
      event.stream_id === ROOT_STREAM_ID
  )
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
