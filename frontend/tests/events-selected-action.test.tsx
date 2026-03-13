/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type React from "react"
import {
  ActionEventPane,
  SuccessEvent,
} from "@/components/builder/events/events-selected-action"
import {
  WF_TRIGGER_EVENT_REF,
  type WorkflowExecutionEventCompact,
} from "@/lib/event-history"
import { isCollectionStoredObject } from "@/lib/stored-object"
import { useWorkflowBuilder } from "@/providers/builder"

jest.mock("@ai-sdk/react", () => ({
  useChat: jest.fn(() => ({
    messages: [],
    status: "ready",
  })),
}))

jest.mock("ai", () => ({
  DefaultChatTransport: jest.fn(),
}))

jest.mock("@/components/ai-elements/conversation", () => ({
  Conversation: ({ children }: { children: React.ReactNode }) => children,
  ConversationContent: ({ children }: { children: React.ReactNode }) =>
    children,
  ConversationScrollButton: () => null,
}))

jest.mock("@/components/chat/chat-session-pane", () => ({
  MessagePart: () => null,
}))

jest.mock("@/components/code-block", () => ({
  CodeBlock: ({ children }: { children: React.ReactNode }) => children,
}))

jest.mock("@/components/events/workflow-event-status", () => ({
  getWorkflowEventIcon: () => null,
}))

jest.mock("@/components/executions/collection-object-result", () => ({
  CollectionObjectResult: ({
    copyMode,
    copyPrefix,
  }: {
    copyMode?: string
    copyPrefix?: string
  }) => (
    <div
      data-testid="collection-result"
      data-copy-mode={copyMode}
      data-copy-prefix={copyPrefix}
    />
  ),
}))

jest.mock("@/components/executions/external-object-result", () => ({
  ExternalObjectResult: () => <div data-testid="external-result" />,
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
    <pre
      data-testid="json-view"
      data-copy-mode={copyMode}
      data-copy-prefix={copyPrefix}
    >
      {JSON.stringify(src)}
    </pre>
  ),
}))

jest.mock("@/components/loading/spinner", () => ({
  Spinner: () => null,
}))

jest.mock("@/components/notifications", () => ({
  AlertNotification: () => null,
}))

jest.mock("@/components/separator", () => ({
  InlineDotSeparator: () => null,
}))

jest.mock("@/components/ui/badge", () => ({
  Badge: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

jest.mock("@/components/ui/button", () => ({
  Button: ({ children }: { children: React.ReactNode }) => (
    <button type="button">{children}</button>
  ),
}))

jest.mock("@/components/ui/carousel", () => ({
  Carousel: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  CarouselContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  CarouselItem: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))

jest.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectGroup: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectItem: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectValue: () => null,
}))

jest.mock("@/components/ui/tabs", () => ({
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  TabsList: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  TabsTrigger: ({ children }: { children: React.ReactNode }) => (
    <button type="button">{children}</button>
  ),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/hooks/use-chat", () => ({
  parseChatError: jest.fn(),
}))

jest.mock("@/lib/agents", () => ({
  isUIMessageArray: jest.fn(() => false),
}))

jest.mock("@/lib/api", () => ({
  getBaseUrl: jest.fn(() => "http://localhost:3000"),
}))

jest.mock("@/lib/stored-object", () => ({
  isCollectionStoredObject: jest.fn(() => false),
  isExternalStoredObject: jest.fn(() => false),
}))

jest.mock("@/providers/builder", () => ({
  useWorkflowBuilder: jest.fn(() => ({
    workspaceId: "workspace-1",
    workflowId: "workflow-1",
    selectedActionEventRef: "reshape",
    setSelectedActionEventRef: jest.fn(),
  })),
}))

const mockIsCollectionStoredObject =
  isCollectionStoredObject as jest.MockedFunction<
    typeof isCollectionStoredObject
  >
const mockUseWorkflowBuilder = useWorkflowBuilder as jest.MockedFunction<
  typeof useWorkflowBuilder
>

function createEvent(
  overrides: Partial<WorkflowExecutionEventCompact> = {}
): WorkflowExecutionEventCompact {
  return {
    source_event_id: 1,
    schedule_time: "2026-03-13T17:16:35Z",
    start_time: "2026-03-13T17:16:35Z",
    close_time: "2026-03-13T17:16:35Z",
    curr_event_type: "WORKFLOW_EXECUTION_STARTED",
    status: "COMPLETED",
    action_name: "Workflow",
    action_ref: WF_TRIGGER_EVENT_REF,
    action_input: { some: "trigger" },
    action_result: null,
    ...overrides,
  } as WorkflowExecutionEventCompact
}

describe("SuccessEvent", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockIsCollectionStoredObject.mockReturnValue(false)
    mockUseWorkflowBuilder.mockReturnValue({
      workspaceId: "workspace-1",
      workflowId: "workflow-1",
      selectedActionEventRef: "reshape",
      setSelectedActionEventRef: jest.fn(),
    } as never)
  })

  it("renders trigger input on the result tab", () => {
    render(
      <SuccessEvent
        event={createEvent()}
        type="result"
        eventRef={WF_TRIGGER_EVENT_REF}
        executionId="exec-1"
        eventId={1}
      />
    )

    expect(screen.getByTestId("json-view")).toHaveTextContent(
      '{"some":"trigger"}'
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-mode",
      "jsonpath-and-payload"
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "TRIGGER"
    )
    expect(
      screen.queryByText("This action returned no result (null).")
    ).not.toBeInTheDocument()
  })

  it("renders trigger input on the input tab unchanged", () => {
    render(
      <SuccessEvent
        event={createEvent()}
        type="input"
        eventRef={WF_TRIGGER_EVENT_REF}
        executionId="exec-1"
        eventId={1}
      />
    )

    expect(screen.getByTestId("json-view")).toHaveTextContent(
      '{"some":"trigger"}'
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-mode",
      "jsonpath-and-payload"
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "TRIGGER"
    )
  })

  it("renders non-trigger results unchanged", () => {
    render(
      <SuccessEvent
        event={createEvent({
          action_ref: "reshape",
          action_name: "Reshape",
          action_input: { some: "trigger" },
          action_result: { transformed: true },
          curr_event_type: "ACTIVITY_TASK_COMPLETED",
        })}
        type="result"
        eventRef="reshape"
        executionId="exec-1"
        eventId={2}
      />
    )

    expect(screen.getByTestId("json-view")).toHaveTextContent(
      '{"transformed":true}'
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-mode",
      "jsonpath-and-payload"
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "ACTIONS.reshape.result"
    )
    expect(screen.getByTestId("json-view")).not.toHaveTextContent(
      '{"some":"trigger"}'
    )
  })

  it("passes dual copy mode to collection results", () => {
    mockIsCollectionStoredObject.mockReturnValue(true)

    render(
      <SuccessEvent
        event={createEvent({
          action_ref: "reshape",
          action_name: "Reshape",
          action_result: { object_type: "collection" },
          curr_event_type: "ACTIVITY_TASK_COMPLETED",
        })}
        type="result"
        eventRef="reshape"
        executionId="exec-1"
        eventId={2}
      />
    )

    expect(screen.getByTestId("collection-result")).toHaveAttribute(
      "data-copy-mode",
      "jsonpath-and-payload"
    )
    expect(screen.getByTestId("collection-result")).toHaveAttribute(
      "data-copy-prefix",
      "ACTIONS.reshape.result"
    )
  })

  it("passes dual copy mode to interaction viewers", () => {
    render(
      <ActionEventPane
        execution={
          {
            id: "exec-1",
            status: "COMPLETED",
            events: [
              createEvent({
                action_ref: "reshape",
                action_name: "Reshape",
              }),
            ],
            interactions: [
              {
                action_ref: "reshape",
                response_payload: { approved: true },
              },
            ],
          } as never
        }
        type="interaction"
      />
    )

    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-mode",
      "jsonpath-and-payload"
    )
    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "ACTIONS.reshape.interaction"
    )
  })

  it("uses ACTIONS.<ref> prefixes for action input", () => {
    render(
      <SuccessEvent
        event={createEvent({
          action_ref: "reshape",
          action_name: "Reshape",
        })}
        type="input"
        eventRef="reshape"
        executionId="exec-1"
        eventId={1}
      />
    )

    expect(screen.getByTestId("json-view")).toHaveAttribute(
      "data-copy-prefix",
      "ACTIONS.reshape"
    )
  })
})
