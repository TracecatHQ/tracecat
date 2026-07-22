/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { WorkflowExecutionEventDetailView } from "@/components/executions/event-details"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"

jest.mock("@/components/executions/action-event-details", () => ({
  ActionEventDetails: ({
    actionRef,
    events,
    executionId,
    status,
    type,
    presentation,
  }: {
    actionRef: string
    events: WorkflowExecutionEventCompact[]
    executionId: string
    status: string
    type: string
    presentation: string
  }) => (
    <div
      data-testid={`action-${type}`}
      data-action-ref={actionRef}
      data-event-count={events.length}
      data-execution-id={executionId}
      data-status={status}
      data-presentation={presentation}
    />
  ),
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
    action_input: { some: "trigger" },
    action_result: { transformed: true },
    action_error: null,
    ...overrides,
  } as WorkflowExecutionEventCompact
}

describe("WorkflowExecutionEventDetailView", () => {
  it("keeps workflow trigger payloads under Input", () => {
    const trigger = createEvent({
      action_ref: "__workflow_trigger__",
      action_name: "Trigger",
      action_result: null,
    })

    render(
      <WorkflowExecutionEventDetailView
        actionRef="__workflow_trigger__"
        events={[trigger]}
        executionId="exec-1"
        executionStatus="COMPLETED"
      />
    )

    expect(screen.getByRole("tab", { name: "Input" })).toBeInTheDocument()
    expect(
      screen.queryByRole("tab", { name: "Result" })
    ).not.toBeInTheDocument()
    expect(screen.getByTestId("action-input")).toHaveAttribute(
      "data-presentation",
      "single"
    )
  })

  it("shows an explicit Result surface for null non-trigger results", () => {
    render(
      <WorkflowExecutionEventDetailView
        actionRef="reshape"
        events={[createEvent({ action_result: null })]}
        executionId="exec-1"
        executionStatus="COMPLETED"
      />
    )

    expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
      "data-state",
      "active"
    )
    expect(screen.getByTestId("action-result")).toHaveAttribute(
      "data-action-ref",
      "reshape"
    )
  })

  it("passes the whole execution to both grouped payload tabs", async () => {
    const user = userEvent.setup()
    const events = [
      createEvent({ source_event_id: 1, stream_id: "scatter:0" }),
      createEvent({ source_event_id: 2, stream_id: "scatter:1" }),
    ]

    render(
      <WorkflowExecutionEventDetailView
        actionRef="reshape"
        events={events}
        executionId="exec-1"
        executionStatus="RUNNING"
      />
    )

    expect(screen.getByTestId("action-result")).toHaveAttribute(
      "data-event-count",
      "2"
    )
    await user.click(screen.getByRole("tab", { name: "Input" }))
    expect(screen.getByTestId("action-input")).toHaveAttribute(
      "data-status",
      "RUNNING"
    )
  })
})
