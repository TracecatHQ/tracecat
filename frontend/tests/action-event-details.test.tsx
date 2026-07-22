/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type React from "react"
import { ActionEventDetails } from "@/components/executions/action-event-details"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"

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
    action_result: { stream: 1 },
    action_error: null,
    stream_id: "scatter:0",
    ...overrides,
  } as WorkflowExecutionEventCompact
}

function renderSingle(events: WorkflowExecutionEventCompact[]) {
  return render(
    <ActionEventDetails
      executionId="exec-1"
      actionRef="reshape"
      status="COMPLETED"
      events={events}
      type="result"
      presentation="single"
    />
  )
}

function renderInput(events: WorkflowExecutionEventCompact[]) {
  return render(
    <ActionEventDetails
      executionId="exec-1"
      actionRef="reshape"
      status="RUNNING"
      events={events}
      type="input"
    />
  )
}

describe("ActionEventDetails input payloads", () => {
  it.each([
    ["STARTED", "Action is running..."],
    ["SCHEDULED", "Action is scheduled..."],
  ] as const)(
    "renders the input for a %s event instead of the result placeholder",
    (status, placeholder) => {
      renderInput([
        createEvent({
          status,
          action_input: { payload: status.toLowerCase() },
          action_result: null,
        }),
      ])

      expect(screen.getByTestId("json-view")).toHaveTextContent(
        status.toLowerCase()
      )
      expect(screen.queryByText(placeholder)).not.toBeInTheDocument()
    }
  )

  it("navigates distinct inputs in chronological stream order", () => {
    const first = createEvent({
      source_event_id: 1,
      close_time: "2026-03-13T17:16:35Z",
      action_input: { payload: "first" },
      stream_id: "scatter:0",
    })
    const latest = createEvent({
      source_event_id: 2,
      close_time: "2026-03-13T17:16:36Z",
      action_input: { payload: "latest" },
      stream_id: "scatter:1",
    })

    renderInput([latest, first])

    expect(screen.getByText("Stream 2 of 2")).toBeInTheDocument()
    expect(screen.getByTestId("json-view")).toHaveTextContent("latest")

    fireEvent.click(screen.getByRole("button", { name: "Previous stream" }))
    expect(screen.getByTestId("json-view")).toHaveTextContent("first")
  })

  it("renders identical inputs once with the shared-input placeholder", () => {
    const actionInput = { payload: "shared" }

    renderInput([
      createEvent({
        source_event_id: 1,
        action_input: actionInput,
        stream_id: undefined,
      }),
      createEvent({
        source_event_id: 2,
        action_input: actionInput,
        stream_id: undefined,
      }),
    ])

    expect(screen.queryByText("Stream 1 of 2")).not.toBeInTheDocument()
    expect(
      screen.getByText("Input is the same for all events")
    ).toBeInTheDocument()
    expect(screen.getAllByTestId("json-view")).toHaveLength(1)
  })
})

describe("ActionEventDetails single presentation", () => {
  it("starts at the latest stream and navigates in chronological order", () => {
    const first = createEvent({
      source_event_id: 10,
      close_time: "2026-03-13T17:16:35Z",
      action_result: { stream: "first" },
      stream_id: "scatter:0",
    })
    const failed = createEvent({
      source_event_id: 20,
      close_time: "2026-03-13T17:16:36Z",
      status: "FAILED",
      curr_event_type: "ACTIVITY_TASK_FAILED",
      action_result: null,
      action_error: { message: "branch failed" } as never,
      stream_id: "scatter:1",
    })
    const latest = createEvent({
      source_event_id: 30,
      close_time: "2026-03-13T17:16:37Z",
      action_result: { stream: "latest" },
      stream_id: "scatter:2",
    })

    renderSingle([latest, first, failed])

    expect(screen.getByText("Stream 3 of 3")).toBeInTheDocument()
    expect(screen.getByTestId("json-view")).toHaveTextContent("latest")

    fireEvent.click(screen.getByRole("button", { name: "Previous stream" }))
    expect(screen.getByTestId("code-block")).toHaveTextContent("branch failed")
    expect(screen.getByText("scatter")).toBeInTheDocument()
    expect(screen.getByText("1")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Previous stream" }))
    expect(screen.getByTestId("json-view")).toHaveTextContent("first")
  })

  it("follows the latest event until navigation pins a stream", () => {
    const first = createEvent({
      source_event_id: 1,
      close_time: "2026-03-13T17:16:35Z",
      action_result: { stream: "first" },
    })
    const second = createEvent({
      source_event_id: 2,
      close_time: "2026-03-13T17:16:36Z",
      action_result: { stream: "second" },
    })
    const third = createEvent({
      source_event_id: 3,
      close_time: "2026-03-13T17:16:37Z",
      action_result: { stream: "third" },
    })
    const fourth = createEvent({
      source_event_id: 4,
      close_time: "2026-03-13T17:16:38Z",
      action_result: { stream: "fourth" },
    })
    const view = renderSingle([first, second])

    view.rerender(
      <ActionEventDetails
        executionId="exec-1"
        actionRef="reshape"
        status="RUNNING"
        events={[first, second, third]}
        type="result"
        presentation="single"
      />
    )
    expect(screen.getByTestId("json-view")).toHaveTextContent("third")

    fireEvent.click(screen.getByRole("button", { name: "Previous stream" }))
    expect(screen.getByTestId("json-view")).toHaveTextContent("second")

    view.rerender(
      <ActionEventDetails
        executionId="exec-1"
        actionRef="reshape"
        status="RUNNING"
        events={[first, second, third, fourth]}
        type="result"
        presentation="single"
      />
    )
    expect(screen.getByTestId("json-view")).toHaveTextContent("second")
  })

  it("mounts only the selected collection result", () => {
    const first = createEvent({
      source_event_id: 1,
      close_time: "2026-03-13T17:16:35Z",
      action_result: { type: "collection", count: 10 },
    })
    const second = createEvent({
      source_event_id: 2,
      close_time: "2026-03-13T17:16:36Z",
      action_result: { type: "collection", count: 20 },
    })

    renderSingle([first, second])

    expect(screen.getAllByTestId("collection-result")).toHaveLength(1)
    expect(screen.getByTestId("collection-result")).toHaveAttribute(
      "data-event-id",
      "2"
    )

    fireEvent.click(screen.getByRole("button", { name: "Previous stream" }))
    expect(screen.getAllByTestId("collection-result")).toHaveLength(1)
    expect(screen.getByTestId("collection-result")).toHaveAttribute(
      "data-event-id",
      "1"
    )
  })

  it("renders explicit null, scheduled, and running result states", () => {
    const nullView = renderSingle([
      createEvent({ action_result: null, stream_id: "outer:0/inner:2" }),
    ])
    expect(
      screen.getByText("This action returned no result (null).")
    ).toBeInTheDocument()
    expect(screen.getByText("outer")).toBeInTheDocument()
    expect(screen.getByText("inner")).toBeInTheDocument()
    nullView.unmount()

    const scheduledView = renderSingle([
      createEvent({ status: "SCHEDULED", action_result: null }),
    ])
    expect(screen.getByText("Action is scheduled...")).toBeInTheDocument()
    scheduledView.unmount()

    renderSingle([createEvent({ status: "STARTED", action_result: null })])
    expect(screen.getByText("Action is running...")).toBeInTheDocument()
  })

  it("passes the selected source event id to external results", () => {
    renderSingle([
      createEvent({
        source_event_id: 42,
        action_result: { type: "external" },
      }),
    ])

    expect(screen.getByTestId("external-result")).toHaveAttribute(
      "data-event-id",
      "42"
    )
  })

  it("loads agent sessions conditionally and preserves the raw result tab", async () => {
    const user = userEvent.setup()
    renderSingle([
      createEvent({
        session: { id: "session-1", events: [] } as never,
        action_result: { final: true },
      }),
    ])

    expect(screen.getByRole("tab", { name: "Session" })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId("session-stream")).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: "Result" }))
    expect(screen.getByTestId("json-view")).toHaveTextContent("final")
  })
})
