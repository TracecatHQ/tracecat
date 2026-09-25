import { render, screen, waitFor } from "@testing-library/react"
import type { UIMessage, UIMessagePart } from "ai"
import type { ReactNode } from "react"
import { MessagePart } from "@/components/chat/chat-session-pane"
import { SubagentStreamContext } from "@/hooks/use-subagent-stream"
import { SubagentStreamStore } from "@/lib/subagent-stream"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

const CHILD_ID = "0b5c6f7e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"

// jsdom lacks structuredClone, which readUIMessageStream uses per update.
if (typeof globalThis.structuredClone !== "function") {
  globalThis.structuredClone = ((value: unknown) =>
    JSON.parse(JSON.stringify(value))) as typeof structuredClone
}

const mockUseGetChatVercel = jest.fn()

jest.mock("@/hooks/use-chat", () => ({
  useGetChatVercel: (args: { chatId?: string; workspaceId: string }) =>
    mockUseGetChatVercel(args),
}))

// The real tool components pull in ESM-only syntax highlighting.
jest.mock("@/components/ai-elements/tool", () => ({
  getStatusBadge: () => null,
  Tool: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ToolContent: ({ children }: { children?: ReactNode }) => (
    <div>{children}</div>
  ),
  ToolHeader: ({ title, state }: { title?: string; state: string }) => (
    <div>
      <span>{title}</span>
      <span data-testid="tool-state">{state}</span>
    </div>
  ),
  ToolInput: () => null,
  ToolOutput: ({ errorText }: { errorText?: string }) =>
    errorText ? <div>{errorText}</div> : null,
}))

jest.mock("@/components/icons", () => ({
  getIcon: () => null,
  ProviderIcon: () => null,
}))

jest.mock("@/components/json-viewer", () => ({
  JsonViewWithControls: ({ src }: { src?: unknown }) => (
    <pre>{JSON.stringify(src)}</pre>
  ),
}))

jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: ({ value }: { value?: string }) => <pre>{value}</pre>,
}))

type Part = UIMessagePart<Record<string, unknown>, Record<string, never>>

function subagentPart(overrides: Record<string, unknown>): Part {
  return {
    type: "tool-subagent",
    toolCallId: "call_subagent",
    input: { alias: "Triage agent", task: "Summarize the open cases" },
    ...overrides,
  } as unknown as Part
}

function renderPart(part: Part, wrap: (node: ReactNode) => ReactNode) {
  return render(
    <>
      {wrap(
        <MessagePart
          part={part}
          partIdx={0}
          id="parent-message"
          role="assistant"
          isLastMessage
        />
      )}
    </>
  )
}

describe("subagent tool part", () => {
  beforeEach(() => {
    mockUseGetChatVercel.mockReset()
    mockUseGetChatVercel.mockReturnValue({
      chat: undefined,
      chatLoading: false,
      chatError: null,
    })
  })

  it("renders the live child transcript while the output is preliminary", async () => {
    const store = new SubagentStreamStore()
    for (const [index, chunk] of [
      { type: "start", messageId: CHILD_ID },
      { type: "text-start", id: "t1" },
      { type: "text-delta", id: "t1", delta: "Child is working" },
    ].entries()) {
      store.ingest({
        session_id: CHILD_ID,
        event_id: "e1",
        index,
        chunk: chunk as never,
      })
    }

    renderPart(
      subagentPart({
        state: "output-available",
        output: { session_id: CHILD_ID, status: "running" },
        preliminary: true,
      }),
      (node) => (
        <SubagentStreamContext.Provider
          value={{ store, workspaceId: "workspace-1" }}
        >
          {node}
        </SubagentStreamContext.Provider>
      )
    )

    expect(screen.getByText("Triage agent")).toBeInTheDocument()
    expect(screen.getByTestId("tool-state")).toHaveTextContent(
      "input-available"
    )
    expect(await screen.findByText("Child is working")).toBeInTheDocument()
    // While running, the persisted transcript is not requested.
    expect(mockUseGetChatVercel).toHaveBeenLastCalledWith({
      chatId: undefined,
      workspaceId: "workspace-1",
    })
  })

  it("loads the persisted child transcript once the call has finished", async () => {
    const persistedMessages: UIMessage[] = [
      {
        id: "child-user",
        role: "user",
        parts: [{ type: "text", text: "Summarize the open cases" }],
      },
      {
        id: "child-assistant",
        role: "assistant",
        parts: [{ type: "text", text: "Three cases are open" }],
      },
    ]
    mockUseGetChatVercel.mockImplementation(
      ({ chatId }: { chatId?: string }) => ({
        chat:
          chatId === CHILD_ID
            ? { id: CHILD_ID, messages: persistedMessages }
            : undefined,
        chatLoading: false,
        chatError: null,
      })
    )

    renderPart(
      subagentPart({
        state: "output-available",
        output: { session_id: CHILD_ID, summary: "done" },
      }),
      (node) => (
        <WorkspaceIdProvider workspaceId="workspace-1">
          {node}
        </WorkspaceIdProvider>
      )
    )

    expect(await screen.findByText("Three cases are open")).toBeInTheDocument()
    expect(screen.getByTestId("tool-state")).toHaveTextContent(
      "output-available"
    )
    // The task stays collapsed and the child prompt is not repeated.
    expect(
      screen.queryByText("Summarize the open cases")
    ).not.toBeInTheDocument()
    await waitFor(() =>
      expect(mockUseGetChatVercel).toHaveBeenCalledWith({
        chatId: CHILD_ID,
        workspaceId: "workspace-1",
      })
    )
  })
})
