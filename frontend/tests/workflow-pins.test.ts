import type { ActionRead } from "@/client"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"
import { computePinDomains, isPinnableActionEvent } from "@/lib/workflow-pins"

function action(
  id: string,
  title: string,
  upstreamIds: string[] = []
): ActionRead {
  return {
    id,
    type: "core.noop",
    title,
    description: "",
    status: "online",
    inputs: "",
    upstream_edges: upstreamIds.map((sourceId) => ({
      source_id: sourceId,
      source_type: "udf" as const,
      source_handle: "success" as const,
    })),
  } as ActionRead
}

// a -> b -> c, a -> d, and c + d -> e, wired through upstream_edges by ID.
const actions: Record<string, ActionRead> = {
  "id-a": action("id-a", "a", ["trigger-1"]),
  "id-b": action("id-b", "b", ["id-a"]),
  "id-c": action("id-c", "c", ["id-b"]),
  "id-d": action("id-d", "d", ["id-a"]),
  "id-e": action("id-e", "e", ["id-c", "id-d"]),
}

function completedEvent(streamId: string): WorkflowExecutionEventCompact {
  return {
    source_event_id: 1,
    schedule_time: "2026-09-04T00:00:00Z",
    curr_event_type: "ACTIVITY_TASK_COMPLETED",
    status: "COMPLETED",
    action_name: "core.noop",
    action_ref: "c",
    action_error: null,
    stream_id: streamId,
  }
}

describe("isPinnableActionEvent", () => {
  it("accepts completed root-stream events", () => {
    expect(
      isPinnableActionEvent("c", { c: [completedEvent("<root>:0")] }, actions)
    ).toBe(true)
  })

  it("rejects completed events from scoped streams", () => {
    expect(
      isPinnableActionEvent(
        "c",
        { c: [completedEvent("<root>:0/scatter:0")] },
        actions
      )
    ).toBe(false)
  })
})

describe("computePinDomains", () => {
  it("force-skips only the exclusive upstream of a pinned action", () => {
    const domains = computePinDomains(actions, {
      source_execution_id: "exec_1",
      action_refs: ["c"],
    })

    expect(Array.from(domains.pinnedRefs)).toEqual(["c"])
    // b feeds only c, but a also feeds d, which is still live.
    expect(Array.from(domains.forceSkipRefs).sort()).toEqual(["b"])
  })

  it("force-skips the whole upstream cone when every join parent is pinned", () => {
    const domains = computePinDomains(actions, {
      source_execution_id: "exec_1",
      action_refs: ["c", "d"],
    })

    expect(Array.from(domains.pinnedRefs).sort()).toEqual(["c", "d"])
    expect(Array.from(domains.forceSkipRefs).sort()).toEqual(["a", "b"])
  })

  it("ignores pins for refs that are not in the graph", () => {
    const domains = computePinDomains(actions, {
      source_execution_id: "exec_1",
      action_refs: ["missing"],
    })

    expect(domains.pinnedRefs.size).toBe(0)
    expect(domains.forceSkipRefs.size).toBe(0)
  })
})
