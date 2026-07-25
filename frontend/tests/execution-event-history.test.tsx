/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { WorkflowExecutionEventHistory } from "@/components/executions/event-history"
import type {
  WorkflowExecutionEventCompact,
  WorkflowExecutionReadCompact,
} from "@/lib/event-history"

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

function createEvent(
  overrides: Partial<WorkflowExecutionEventCompact> = {}
): WorkflowExecutionEventCompact {
  return {
    source_event_id: 1,
    schedule_time: "2026-03-13T17:16:35Z",
    start_time: "2026-03-13T17:16:35Z",
    close_time: "2026-03-13T17:16:35Z",
    curr_event_type: "ACTIVITY_TASK_COMPLETED",
    status: "COMPLETED",
    action_name: "Reshape",
    action_ref: "reshape",
    action_input: { source: "input" },
    action_result: { ok: true },
    stream_id: "scatter:0",
    ...overrides,
  } as WorkflowExecutionEventCompact
}

function createExecution(
  events: WorkflowExecutionEventCompact[]
): WorkflowExecutionReadCompact {
  return {
    id: "workflow-1/execution-1",
    run_id: "run-1",
    start_time: "2026-03-13T17:16:35Z",
    close_time: null,
    status: "RUNNING",
    workflow_type: "DSLWorkflow",
    task_queue: "default",
    history_length: events.length,
    trigger_type: "manual",
    events,
    interactions: [],
  } as WorkflowExecutionReadCompact
}

describe("WorkflowExecutionEventHistory", () => {
  it("keeps running scatter streams in the grouped count", () => {
    render(
      <WorkflowExecutionEventHistory
        execution={createExecution([
          createEvent({ source_event_id: 1, stream_id: "scatter:0" }),
          createEvent({
            source_event_id: 2,
            status: "STARTED",
            curr_event_type: "ACTIVITY_TASK_STARTED",
            close_time: null,
            stream_id: "scatter:1",
          }),
          createEvent({
            source_event_id: 3,
            status: "SCHEDULED",
            curr_event_type: "ACTIVITY_TASK_SCHEDULED",
            start_time: null,
            close_time: null,
            stream_id: "scatter:2",
          }),
        ])}
        setSelectedActionRef={jest.fn()}
      />
    )

    expect(screen.getByText("3x")).toBeInTheDocument()
    expect(screen.getByText("Reshape")).toBeInTheDocument()
  })

  it("selects the action group rather than one latest event", () => {
    const setSelectedActionRef = jest.fn()
    render(
      <WorkflowExecutionEventHistory
        execution={createExecution([
          createEvent({
            source_event_id: 1,
            status: "FAILED",
            curr_event_type: "ACTIVITY_TASK_FAILED",
            close_time: "2026-03-13T17:16:35Z",
            action_error: { message: "failed" } as never,
          }),
          createEvent({
            source_event_id: 2,
            close_time: "2026-03-13T17:16:36Z",
            stream_id: "scatter:1",
          }),
        ])}
        selectedActionRef="reshape"
        setSelectedActionRef={setSelectedActionRef}
      />
    )

    fireEvent.click(screen.getByRole("button"))
    expect(setSelectedActionRef).toHaveBeenCalledWith("reshape")
  })
})
