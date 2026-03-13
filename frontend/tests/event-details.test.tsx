/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type React from "react"
import { WorkflowExecutionEventDetailView } from "@/components/executions/event-details"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"
import {
  isCollectionStoredObject,
  isExternalStoredObject,
} from "@/lib/stored-object"

jest.mock("@/components/code-block", () => ({
  CodeBlock: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))

jest.mock("@/components/executions/collection-object-result", () => ({
  CollectionObjectResult: ({ copyMode }: { copyMode?: string }) => (
    <div data-testid="collection-result" data-copy-mode={copyMode} />
  ),
}))

jest.mock("@/components/executions/external-object-result", () => ({
  ExternalObjectResult: () => null,
}))

jest.mock("@/components/json-viewer", () => ({
  JsonViewWithControls: ({
    src,
    copyMode,
    copyPrefix,
  }: {
    src: unknown
    copyMode?: string
    copyPrefix?: string
  }) => (
    <div
      data-testid="json-view"
      data-copy-mode={copyMode}
      data-copy-prefix={copyPrefix}
    >
      {JSON.stringify(src)}
    </div>
  ),
}))

jest.mock("@/lib/stored-object", () => ({
  isCollectionStoredObject: jest.fn(() => false),
  isExternalStoredObject: jest.fn(() => false),
}))

const mockIsCollectionStoredObject =
  isCollectionStoredObject as jest.MockedFunction<
    typeof isCollectionStoredObject
  >
const mockIsExternalStoredObject =
  isExternalStoredObject as jest.MockedFunction<typeof isExternalStoredObject>

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
  beforeEach(() => {
    jest.clearAllMocks()
    mockIsCollectionStoredObject.mockReturnValue(false)
    mockIsExternalStoredObject.mockReturnValue(false)
  })

  it("passes dual copy mode to the input JSON viewer", () => {
    render(
      <WorkflowExecutionEventDetailView
        event={createEvent({ action_result: null })}
        executionId="exec-1"
      />
    )

    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-mode",
      "jsonpath-and-payload"
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "ACTIONS.reshape"
    )
  })

  it("passes result prefixes to the result JSON viewer", () => {
    render(
      <WorkflowExecutionEventDetailView
        event={createEvent({ action_input: null })}
        executionId="exec-1"
      />
    )

    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "ACTIONS.reshape.result"
    )
  })

  it("uses TRIGGER prefixes for trigger input", () => {
    render(
      <WorkflowExecutionEventDetailView
        event={createEvent({
          action_ref: "__workflow_trigger__",
          action_name: "Trigger",
          action_result: null,
        })}
        executionId="exec-1"
      />
    )

    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "TRIGGER"
    )
  })

  it("passes dual copy mode to collection results", () => {
    mockIsCollectionStoredObject.mockReturnValue(true)

    render(
      <WorkflowExecutionEventDetailView
        event={createEvent({
          action_input: null,
          action_result: { object_type: "collection" },
        })}
        executionId="exec-1"
      />
    )

    expect(screen.getByTestId("collection-result")).toHaveAttribute(
      "data-copy-mode",
      "jsonpath-and-payload"
    )
  })
})
