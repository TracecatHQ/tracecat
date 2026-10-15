import { fireEvent, render, screen } from "@testing-library/react"
import type { UIMessage } from "ai"
import type { ReactNode } from "react"
import { AgentTurn, groupThread } from "@/components/chat/agent-turn"
import {
  type ChatVerbosity,
  ChatVerbosityContext,
} from "@/hooks/use-chat-verbosity"

jest.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { code: string }) => <pre>{code}</pre>,
  CodeBlockCopyButton: () => null,
}))

jest.mock("@/components/ai-elements/response", () => ({
  Response: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}))

jest.mock("@/components/icons", () => ({
  getIcon: () => <span data-testid="tool-icon" />,
}))

const ANSWER = "The workspace has no tables."

function at(seconds: number) {
  return {
    created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, seconds)).toISOString(),
  }
}

const PROMPT: UIMessage = {
  id: "prompt",
  role: "user",
  metadata: at(0),
  parts: [{ type: "text", text: "List the tables" }],
}
const THOUGHT: UIMessage = {
  id: "thought",
  role: "assistant",
  metadata: at(3),
  parts: [{ type: "reasoning", text: "Use the tables tool.", state: "done" }],
}
const TOOL_CALL: UIMessage = {
  id: "tool",
  role: "assistant",
  metadata: at(5),
  parts: [
    {
      type: "dynamic-tool",
      toolName: "core__table__list_tables",
      toolCallId: "call-1",
      state: "output-available",
      input: {},
      output: [],
    },
  ],
}
const ANSWER_MESSAGE: UIMessage = {
  id: "answer",
  role: "assistant",
  metadata: at(16),
  parts: [{ type: "text", text: ANSWER }],
}

function renderTurn(
  thread: UIMessage[],
  {
    verbosity = "default",
    isLive = false,
  }: { verbosity?: ChatVerbosity; isLive?: boolean } = {}
) {
  const [, turn] = groupThread(thread)
  if (turn?.kind !== "turn") {
    throw new Error("Expected an agent turn after the prompt")
  }
  render(
    <ChatVerbosityContext.Provider value={verbosity}>
      <AgentTurn
        messages={turn.messages}
        startedAt={turn.startedAt}
        isLive={isLive}
        status={isLive ? "streaming" : "ready"}
        lastMessageId={thread.at(-1)?.id}
      />
    </ChatVerbosityContext.Provider>
  )
}

describe("AgentTurn", () => {
  it("folds a finished turn's steps into one worked line", () => {
    renderTurn([PROMPT, THOUGHT, TOOL_CALL, ANSWER_MESSAGE])

    expect(screen.getByText("Worked for 16s")).toBeInTheDocument()
    expect(screen.getByText(ANSWER)).toBeInTheDocument()
    expect(
      screen.queryByText("Ran core.table.list_tables")
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("Worked for 16s"))

    expect(screen.getByText("Ran core.table.list_tables")).toBeInTheDocument()
    expect(screen.getByText("Thought for 3 seconds")).toBeInTheDocument()
  })

  it("shows a turn that only thought as its thought", () => {
    renderTurn([PROMPT, THOUGHT, ANSWER_MESSAGE])

    expect(screen.queryByText(/^Worked/)).not.toBeInTheDocument()
    expect(screen.getByText("Thought for 3 seconds")).toBeInTheDocument()
  })

  it("draws every step while the turn streams", () => {
    renderTurn([PROMPT, THOUGHT, TOOL_CALL], { isLive: true })

    expect(screen.queryByText(/^Worked/)).not.toBeInTheDocument()
    expect(screen.getByText("Ran core.table.list_tables")).toBeInTheDocument()
  })

  it("keeps the whole transcript inline at verbose", () => {
    renderTurn([PROMPT, THOUGHT, TOOL_CALL, ANSWER_MESSAGE], {
      verbosity: "verbose",
    })

    expect(screen.queryByText(/^Worked/)).not.toBeInTheDocument()
    expect(screen.getByText("Ran core.table.list_tables")).toBeInTheDocument()
    expect(screen.getByText("Use the tables tool.")).toBeInTheDocument()
    expect(screen.getByText(ANSWER)).toBeInTheDocument()
  })

  it("says worked without a time when history has none", () => {
    const strip = ({ metadata: _, ...message }: UIMessage): UIMessage => message
    renderTurn([PROMPT, THOUGHT, TOOL_CALL, ANSWER_MESSAGE].map(strip))

    expect(screen.getByText("Worked")).toBeInTheDocument()
  })
})
