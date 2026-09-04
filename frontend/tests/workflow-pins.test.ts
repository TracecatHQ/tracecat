import type { ActionRead } from "@/client"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"
import {
  computePinDomains,
  computeScopedActionRefs,
  isPinnableActionEvent,
} from "@/lib/workflow-pins"

type UpstreamEdge =
  | string
  | [sourceId: string, sourceHandle: "success" | "error"]

function action(
  id: string,
  title: string,
  upstreamEdges: UpstreamEdge[] = [],
  type = "core.noop"
): ActionRead {
  return {
    id,
    type,
    title,
    description: "",
    status: "online",
    inputs: "",
    upstream_edges: upstreamEdges.map((edge) => {
      const [sourceId, sourceHandle] =
        typeof edge === "string" ? [edge, "success" as const] : edge
      return {
        source_id: sourceId,
        source_type: "udf" as const,
        source_handle: sourceHandle,
      }
    }),
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
  it("keeps sources with error edges out of the force-skip domain", () => {
    const conditionalActions = {
      "id-a": action("id-a", "a"),
      "id-handler": action("id-handler", "handler", [["id-a", "error"]]),
    }
    const domains = computePinDomains(conditionalActions, {
      source_execution_id: "exec_1",
      action_refs: ["handler"],
    })

    expect(domains.forceSkipRefs.has("a")).toBe(false)
  })

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

describe("computeScopedActionRefs", () => {
  it("marks loop bodies as scoped and excludes their persisted pins", () => {
    const loopActions = {
      "id-loop-start": action(
        "id-loop-start",
        "loop_start",
        [],
        "core.loop.start"
      ),
      "id-body": action("id-body", "body", ["id-loop-start"]),
      "id-loop-end": action(
        "id-loop-end",
        "loop_end",
        ["id-body"],
        "core.loop.end"
      ),
      "id-after": action("id-after", "after", ["id-loop-end"]),
    }

    const scopedRefs = computeScopedActionRefs(loopActions)
    expect(scopedRefs.has("body")).toBe(true)
    expect(scopedRefs.has("after")).toBe(false)
    expect(
      isPinnableActionEvent(
        "body",
        { body: [completedEvent("<root>:0")] },
        loopActions
      )
    ).toBe(false)
    expect(
      computePinDomains(loopActions, {
        source_execution_id: "exec_1",
        action_refs: ["body"],
      }).pinnedRefs.size
    ).toBe(0)
  })

  it("keeps actions inside nested scatter scopes scoped", () => {
    const nestedScatterActions = {
      "id-scatter": action(
        "id-scatter",
        "scatter",
        [],
        "core.transform.scatter"
      ),
      "id-inner-scatter": action(
        "id-inner-scatter",
        "inner_scatter",
        ["id-scatter"],
        "core.transform.scatter"
      ),
      "id-x": action("id-x", "x", ["id-inner-scatter"]),
      "id-inner-gather": action(
        "id-inner-gather",
        "inner_gather",
        ["id-x"],
        "core.transform.gather"
      ),
      "id-y": action("id-y", "y", ["id-inner-gather"]),
      "id-gather": action(
        "id-gather",
        "gather",
        ["id-y"],
        "core.transform.gather"
      ),
    }

    const scopedRefs = computeScopedActionRefs(nestedScatterActions)
    expect(scopedRefs.has("x")).toBe(true)
    expect(scopedRefs.has("y")).toBe(true)
  })
})
