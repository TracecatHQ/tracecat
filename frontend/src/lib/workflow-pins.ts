import type { ActionRead } from "@/client"
import { slugifyActionRef } from "@/lib/utils"

export type WorkflowDraftPins = {
  source_execution_id: string
  action_refs: string[]
}

type WorkflowWithDraftPins = {
  draft_pins?: WorkflowDraftPins | null
}

type ActionReadWithDepends = ActionRead & {
  depends_on?: string[] | null
}

export type PinDomains = {
  pinnedRefs: Set<string>
  forceSkipRefs: Set<string>
}

function parseDependencySourceRef(depRef: string): string {
  const [sourceRef] = depRef.split(".", 1)
  return sourceRef
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

  const actionList = Object.values(actions) as ActionReadWithDepends[]
  const allActionRefs = new Set<string>(
    actionList
      .map((action) => slugifyActionRef(action.title))
      .filter((actionRef) => actionRef.length > 0)
  )

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
    const targetRef = slugifyActionRef(action.title)
    if (!allActionRefs.has(targetRef)) {
      continue
    }

    for (const depRef of action.depends_on ?? []) {
      const sourceRef = parseDependencySourceRef(depRef)
      if (!allActionRefs.has(sourceRef)) {
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
