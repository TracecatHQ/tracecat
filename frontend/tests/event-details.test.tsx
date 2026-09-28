/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type React from "react"
import type { ActionEventDetailsProps } from "@/components/executions/action-event-details"
import { WorkflowExecutionEventDetailView } from "@/components/executions/event-details"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"

// Most tests stub ActionEventDetails to assert prop wiring. The cross-tab
// regression test flips this flag to render the real component so it can
// exercise the hoisted stream selection across tab switches.
let mockRenderRealActionEventDetails = false

jest.mock("@/components/executions/action-event-details", () => {
  const actual = jest.requireActual(
    "@/components/executions/action-event-details"
  ) as typeof import("@/components/executions/action-event-details")
  return {
    ...actual,
    ActionEventDetails: (props: ActionEventDetailsProps) => {
      if (mockRenderRealActionEventDetails) {
        return <actual.ActionEventDetails {...props} />
      }
      const { actionRef, events, executionId, status, type, presentation } =
        props
      return (
        <div
          data-testid={`action-${type}`}
          data-action-ref={actionRef}
          data-event-count={events.length}
          data-execution-id={executionId}
          data-status={status}
          data-presentation={presentation}
        />
      )
    },
  }
})

// Leaf mocks used only when the real ActionEventDetails renders.
jest.mock("@/components/code-block", () => ({
  CodeBlock: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="code-block">{children}</div>
  ),
}))

jest.mock("@/components/events/workflow-event-status", () => ({
  getWorkflowEventIcon: () => <span data-testid="status-icon" />,
}))

jest.mock("@/components/executions/action-session-stream", () => ({
  ActionSessionStream: () => <div data-testid="session-stream" />,
}))

jest.mock("@/components/executions/collection-object-result", () => ({
  CollectionObjectResult: ({ eventId }: { eventId: number }) => (
    <div data-testid="collection-result" data-event-id={eventId} />
  ),
}))

jest.mock("@/components/executions/external-object-result", () => ({
  ExternalObjectResult: ({ eventId }: { eventId: number }) => (
    <div data-testid="external-result" data-event-id={eventId} />
  ),
}))

jest.mock("@/components/json-viewer", () => ({
  JsonViewWithControls: ({ src }: { src: unknown }) => (
    <pre data-testid="json-view">{JSON.stringify(src)}</pre>
  ),
}))

jest.mock("@/components/separator", () => ({
  InlineDotSeparator: () => <span>·</span>,
}))

jest.mock("@/components/ui/carousel", () => ({
  Carousel: ({ children }: { children: React.ReactNode }) => children,
  CarouselContent: ({ children }: { children: React.ReactNode }) => children,
  CarouselItem: ({ children }: { children: React.ReactNode }) => children,
}))

jest.mock("@/lib/stored-object", () => ({
  isCollectionStoredObject: (value: unknown) =>
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    value.type === "collection",
  isExternalStoredObject: (value: unknown) =>
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    value.type === "external",
}))

afterEach(() => {
  mockRenderRealActionEventDetails = false
})

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

  it("keeps the pinned stream when switching between Input and Result tabs", async () => {
    // Render the real ActionEventDetails so the hoisted, shared stream
    // selection is exercised end to end across tab switches.
    mockRenderRealActionEventDetails = true
    const user = userEvent.setup()
    const events = [
      createEvent({
        source_event_id: 1,
        close_time: "2026-03-13T17:16:35Z",
        action_input: { input: "one" },
        action_result: { result: "one" },
        stream_id: "scatter:0",
      }),
      createEvent({
        source_event_id: 2,
        close_time: "2026-03-13T17:16:36Z",
        action_input: { input: "two" },
        action_result: { result: "two" },
        stream_id: "scatter:1",
      }),
      createEvent({
        source_event_id: 3,
        close_time: "2026-03-13T17:16:37Z",
        action_input: { input: "three" },
        action_result: { result: "three" },
        stream_id: "scatter:2",
      }),
    ]

    render(
      <WorkflowExecutionEventDetailView
        actionRef="reshape"
        events={events}
        executionId="exec-1"
        executionStatus="COMPLETED"
      />
    )

    // (a) Result tab is default and starts on the latest stream.
    expect(screen.getByText("Stream 3 of 3")).toBeInTheDocument()
    expect(screen.getByTestId("json-view")).toHaveTextContent(
      '{"result":"three"}'
    )

    // Navigate back to stream 2 on the Result tab.
    await user.click(screen.getByRole("button", { name: "Previous stream" }))
    expect(screen.getByText("Stream 2 of 3")).toBeInTheDocument()
    expect(screen.getByTestId("json-view")).toHaveTextContent(
      '{"result":"two"}'
    )

    // (b) Switching to the Input tab shows stream 2's input, not the latest.
    await user.click(screen.getByRole("tab", { name: "Input" }))
    expect(screen.getByText("Stream 2 of 3")).toBeInTheDocument()
    expect(screen.getByTestId("json-view")).toHaveTextContent('{"input":"two"}')

    // (c) Switching back to Result still shows stream 2's result.
    await user.click(screen.getByRole("tab", { name: "Result" }))
    expect(screen.getByText("Stream 2 of 3")).toBeInTheDocument()
    expect(screen.getByTestId("json-view")).toHaveTextContent(
      '{"result":"two"}'
    )
  })
})
