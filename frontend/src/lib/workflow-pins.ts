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

/** Map action IDs to their slugified refs, skipping actions with empty refs. */
function buildRefByActionId(actionList: ActionRead[]): Map<string, string> {
  const refByActionId = new Map<string, string>()
  for (const action of actionList) {
    const actionRef = slugifyActionRef(action.title)
    if (actionRef.length > 0) {
      refByActionId.set(action.id, actionRef)
    }
  }
  return refByActionId
}

const SCOPE_CLOSER_BY_OPENER = new Map([
  ["core.transform.scatter", "core.transform.gather"],
  ["core.loop.start", "core.loop.end"],
])

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

/** Return action refs that live between matching scatter or loop boundaries. */
export function computeScopedActionRefs(
  actions: Record<string, ActionRead> | null | undefined
): Set<string> {
  if (!actions) {
    return new Set()
  }

  const actionList = Object.values(actions)
  const refByActionId = new Map<string, string>()
  const typeByRef = new Map<string, string>()
  const adjacency = new Map<string, Set<string>>()
  for (const action of actionList) {
    const actionRef = slugifyActionRef(action.title)
    if (actionRef.length === 0) {
      continue
    }
    refByActionId.set(action.id, actionRef)
    typeByRef.set(actionRef, action.type)
    adjacency.set(action.id, new Set())
  }

  for (const action of actionList) {
    if (!refByActionId.has(action.id)) {
      continue
    }
    for (const edge of action.upstream_edges ?? []) {
      const sourceType = edge.source_type ?? "udf"
      if (sourceType !== "udf" || !refByActionId.has(edge.source_id)) {
        continue
      }
      adjacency.get(edge.source_id)?.add(action.id)
    }
  }

  const scopedRefs = new Set<string>()
  for (const action of actionList) {
    const openerRef = refByActionId.get(action.id)
    const closerType = SCOPE_CLOSER_BY_OPENER.get(action.type)
    if (!openerRef || !closerType) {
      continue
    }

    const queue = Array.from(adjacency.get(action.id) ?? []).map(
      (actionId) => ({ actionId, depth: 1 })
    )
    const visited = new Set<string>()
    while (queue.length > 0) {
      const state = queue.shift()
      if (!state) {
        break
      }
      const actionRef = refByActionId.get(state.actionId)
      if (!actionRef) {
        continue
      }
      const visitKey = `${actionRef}:${state.depth}`
      if (visited.has(visitKey)) {
        continue
      }
      visited.add(visitKey)

      const actionType = typeByRef.get(actionRef)
      let depth = state.depth
      if (actionType === closerType) {
        depth -= 1
        if (depth === 0) {
          continue
        }
      }

      scopedRefs.add(actionRef)
      const nextDepth = actionType === action.type ? depth + 1 : depth
      for (const childId of adjacency.get(state.actionId) ?? []) {
        queue.push({ actionId: childId, depth: nextDepth })
      }
    }
  }

  return scopedRefs
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

  if (computeScopedActionRefs(actions).has(actionRef)) {
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

/** Compute pinned and force-skipped refs without bypassing conditional edges. */
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
  const refByActionId = buildRefByActionId(actionList)
  const allActionRefs = new Set<string>(refByActionId.values())
  const scopedRefs = computeScopedActionRefs(actions)
  const eligibleRefs = new Set(
    actionList
      .filter(
        (action) =>
          !UNPINNABLE_ACTION_TYPES.has(action.type) &&
          action.control_flow?.mask_output !== true
      )
      .map((action) => slugifyActionRef(action.title))
  )

  const pinnedRefs = new Set(
    pins.action_refs.filter(
      (actionRef) => eligibleRefs.has(actionRef) && !scopedRefs.has(actionRef)
    )
  )
  if (pinnedRefs.size === 0) {
    return { pinnedRefs, forceSkipRefs: new Set() }
  }

  const adjacency = new Map<string, Set<string>>()
  const refsWithErrorEdges = new Set<string>()
  for (const actionRef of allActionRefs) {
    adjacency.set(actionRef, new Set())
  }

  for (const action of actionList) {
    const targetRef = refByActionId.get(action.id)
    if (!targetRef) {
      continue
    }

    for (const edge of action.upstream_edges ?? []) {
      const sourceType = edge.source_type ?? "udf"
      if (sourceType !== "udf") {
        continue
      }
      const sourceRef = refByActionId.get(edge.source_id)
      if (!sourceRef || sourceRef === targetRef) {
        continue
      }
      adjacency.get(sourceRef)?.add(targetRef)
      if (edge.source_handle === "error") {
        refsWithErrorEdges.add(sourceRef)
      }
    }
  }

  const skipDomain = new Set(pinnedRefs)
  const scatterRefs = new Set(
    actionList
      .filter((action) => action.type === "core.transform.scatter")
      .map((action) => slugifyActionRef(action.title))
  )
  let changed = true
  while (changed) {
    changed = false
    for (const [actionRef, nextRefs] of adjacency.entries()) {
      if (
        skipDomain.has(actionRef) ||
        nextRefs.size === 0 ||
        refsWithErrorEdges.has(actionRef) ||
        scatterRefs.has(actionRef)
      ) {
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

/** Why "Run from this action" is unavailable for `actionRef`, or null when it can run. */
export function getRunFromActionBlocker(
  actionRef: string | undefined,
  groupedEvents: Record<string, WorkflowExecutionEventCompact[]>,
  actions: Record<string, ActionRead> | null | undefined
): string | null {
  if (!actionRef || !actions) {
    return "Select a workflow action"
  }

  const actionList = Object.values(actions)
  const refByActionId = buildRefByActionId(actionList)
  const action = actionList.find(
    (candidate) => slugifyActionRef(candidate.title) === actionRef
  )
  if (!action) {
    return "Select a workflow action"
  }

  if (
    computeScopedActionRefs(actions).has(actionRef) ||
    UNPINNABLE_ACTION_TYPES.has(action.type)
  ) {
    return "Cannot run from inside a scatter or loop"
  }

  const typeByRef = new Map<string, string>()
  for (const candidate of actionList) {
    const candidateRef = refByActionId.get(candidate.id)
    if (candidateRef) {
      typeByRef.set(candidateRef, candidate.type)
    }
  }

  const parentRefs: string[] = []
  for (const edge of action.upstream_edges ?? []) {
    const sourceType = edge.source_type ?? "udf"
    if (sourceType !== "udf") {
      continue
    }
    const sourceRef = refByActionId.get(edge.source_id)
    if (!sourceRef || sourceRef === actionRef) {
      continue
    }
    if (edge.source_handle === "error") {
      return "Cannot run from an error branch"
    }
    if (!parentRefs.includes(sourceRef)) {
      parentRefs.push(sourceRef)
    }
  }

  if (
    parentRefs.some((parentRef) =>
      UNPINNABLE_ACTION_TYPES.has(typeByRef.get(parentRef) ?? "")
    )
  ) {
    return "Cannot run from directly after a gather or loop end"
  }

  if (
    parentRefs.some(
      (parentRef) => !isPinnableActionEvent(parentRef, groupedEvents, actions)
    )
  ) {
    return "Every upstream action needs a completed result in this run"
  }

  return null
}
