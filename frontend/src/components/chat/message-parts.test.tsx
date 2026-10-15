import { useChat } from "@ai-sdk/react"
import { createChat } from "@shadcn/helpers/ai-sdk"
import { fireEvent, render, screen } from "@testing-library/react"
import type { UIMessage } from "ai"
import type { ReactNode } from "react"
import {
  AgentRunningLine,
  getRunningLineLabel,
  MessageParts,
} from "@/components/chat/message-part"
import {
  type ChatVerbosity,
  ChatVerbosityContext,
  getChatVerbosityPolicy,
} from "@/hooks/use-chat-verbosity"
import { transformMessages } from "@/lib/chat"

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

jest.mock("@/hooks/use-subagent-stream", () => ({
  useSubagentTranscript: () => ({
    messages: [],
    isLive: false,
    isLoading: false,
    isError: false,
    isRecoveryPaused: false,
    retryRecovery: jest.fn(),
  }),
}))

const QUESTION = "Why did the alert fire?"
const ANSWER = "The rule matched a burst of failed logins."

/** A turn that thinks, runs one tool, fails another, then answers. */
function scriptTurn({ toolDelayMs = 0 }: { toolDelayMs?: number } = {}) {
  return createChat()
    .user(QUESTION)
    .assistant(({ writer }) => {
      writer.reasoning("Check the detection rule before the logs.")
      writer
        .tool("core__cases__list_cases", { input: { limit: 5 } })
        .sleep(toolDelayMs)
        .output({ cases: [] })
      writer
        .tool("core__http_request", { input: { url: "https://example.com" } })
        .error("Connection refused")
      writer.text(ANSWER)
    })
}

function Thread({
  chat,
  verbosity,
}: {
  chat: ReturnType<typeof scriptTurn>
  verbosity: ChatVerbosity
}) {
  const { messages, sendMessage, status } = useChat({
    transport: chat.transport({ delayMs: undefined }),
  })
  return (
    <ChatVerbosityContext.Provider value={verbosity}>
      <button type="button" onClick={() => sendMessage({ text: QUESTION })}>
        Send
      </button>
      {messages.map((message) => (
        <MessageParts
          key={message.id}
          message={message}
          status={status}
          isLastMessage={message.id === messages.at(-1)?.id}
        />
      ))}
      <AgentRunningLine status={status} messages={messages} />
    </ChatVerbosityContext.Provider>
  )
}

async function streamTurn(
  verbosity: ChatVerbosity,
  chat: ReturnType<typeof scriptTurn> = scriptTurn()
) {
  render(<Thread chat={chat} verbosity={verbosity} />)
  fireEvent.click(screen.getByRole("button", { name: "Send" }))
  await screen.findByText(ANSWER)
}

describe("MessageParts", () => {
  it("draws each step as a collapsed line at default", async () => {
    await streamTurn("default")

    expect(screen.getByText(/^Thought/)).toBeInTheDocument()
    expect(screen.getByText("Ran core.cases.list_cases")).toBeInTheDocument()
    expect(
      screen.getByText("Failed to run core.http_request")
    ).toBeInTheDocument()
    expect(screen.queryByText("limit")).not.toBeInTheDocument()
    expect(
      screen.queryByText("Check the detection rule before the logs.")
    ).not.toBeInTheDocument()
  })

  it("reopens or folds steps when the level changes", () => {
    const message: UIMessage = {
      id: "a1",
      role: "assistant",
      parts: [
        {
          type: "dynamic-tool",
          toolName: "core__cases__list_cases",
          toolCallId: "call-1",
          state: "output-available",
          input: { limit: 5 },
          output: [],
        },
      ],
    }
    function renderAt(verbosity: ChatVerbosity) {
      return (
        <ChatVerbosityContext.Provider value={verbosity}>
          <MessageParts message={message} status="ready" isLastMessage />
        </ChatVerbosityContext.Provider>
      )
    }
    const { rerender } = render(renderAt("default"))
    expect(screen.queryByText("limit")).not.toBeInTheDocument()

    rerender(renderAt("verbose"))
    expect(screen.getByText("limit")).toBeInTheDocument()

    rerender(renderAt("default"))
    expect(screen.queryByText("limit")).not.toBeInTheDocument()
  })

  it("expands a line when the person opens it", async () => {
    await streamTurn("default")

    fireEvent.click(screen.getByText("Ran core.cases.list_cases"))

    expect(screen.getByText("limit")).toBeInTheDocument()
    expect(screen.getByText("5")).toBeInTheDocument()
  })

  it("keeps only the answer and failures at minimal", async () => {
    await streamTurn("minimal")

    expect(screen.queryByText(/^Thought/)).not.toBeInTheDocument()
    expect(
      screen.queryByText("Ran core.cases.list_cases")
    ).not.toBeInTheDocument()
    expect(
      screen.getByText("Failed to run core.http_request")
    ).toBeInTheDocument()
    expect(screen.queryByText("Working...")).not.toBeInTheDocument()
  })

  it("shows one working line at minimal while steps run", async () => {
    render(
      <Thread chat={scriptTurn({ toolDelayMs: 300 })} verbosity="minimal" />
    )
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    const working = await screen.findByText("Working...")
    expect(working.closest("[data-slot=trace-line]")).toHaveAttribute(
      "data-status",
      "running"
    )
    expect(
      screen.queryByText(/core\.cases\.list_cases/)
    ).not.toBeInTheDocument()

    await screen.findByText(ANSWER)
    expect(screen.queryByText("Working...")).not.toBeInTheDocument()
  })

  it("marks a running tool line while it waits for output", async () => {
    render(
      <Thread chat={scriptTurn({ toolDelayMs: 300 })} verbosity="default" />
    )
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    const running = await screen.findByText("Running core.cases.list_cases")
    expect(running.closest("[data-slot=trace-line]")).toHaveAttribute(
      "data-status",
      "running"
    )

    expect(
      await screen.findByText("Ran core.cases.list_cases")
    ).toBeInTheDocument()
  })

  it("expands every step at verbose", async () => {
    await streamTurn("verbose")

    expect(screen.getByText("limit")).toBeInTheDocument()
    expect(screen.getByText("https://example.com")).toBeInTheDocument()
    expect(
      screen.getByText("Check the detection rule before the logs.")
    ).toBeInTheDocument()
    expect(screen.getByText("Connection refused")).toBeInTheDocument()
  })

  it("names the command a shell tool ran and shows only its output", async () => {
    const chat = createChat()
      .user(QUESTION)
      .assistant(({ writer }) => {
        writer
          .tool("Bash", {
            dynamic: true,
            input: { command: "rg -l ThemeProvider app", description: "Find" },
          })
          .output("app/layout.tsx")
        writer
          .tool("Read", { dynamic: true, input: { file_path: "app/page.tsx" } })
          .output("export default function Page() {}")
        writer.text(ANSWER)
      })
    await streamTurn("default", chat)

    expect(screen.getByText("Ran rg -l ThemeProvider app")).toBeInTheDocument()
    expect(screen.getByText("Read app/page.tsx")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Ran rg -l ThemeProvider app"))

    expect(screen.getByText("app/layout.tsx")).toBeInTheDocument()
    expect(screen.queryByText("description")).not.toBeInTheDocument()
  })

  it("shows a multi-line command whole once opened", async () => {
    const command = "cat <<'EOF' > run.sh\necho hi\nEOF"
    const chat = createChat()
      .user(QUESTION)
      .assistant(({ writer }) => {
        writer.tool("Bash", { dynamic: true, input: { command } }).output("")
        writer.text(ANSWER)
      })
    await streamTurn("default", chat)

    fireEvent.click(screen.getByText("Ran cat <<'EOF' > run.sh …"))

    expect(
      screen.getByText((_, node) => node?.textContent === command)
    ).toBeInTheDocument()
  })

  it.each([
    ["default", false],
    ["verbose", true],
  ] as const)("draws tool search at %s: %s", async (verbosity, shown) => {
    const chat = createChat()
      .user(QUESTION)
      .assistant(({ writer }) => {
        writer
          .tool("ToolSearch", { dynamic: true, input: { query: "tables" } })
          .output([])
        writer.text(ANSWER)
      })
    await streamTurn(verbosity, chat)

    expect(Boolean(screen.queryByText(/ToolSearch/))).toBe(shown)
  })
})

describe("getRunningLineLabel", () => {
  const minimal = getChatVerbosityPolicy("minimal")
  const defaults = getChatVerbosityPolicy("default")
  function running(
    toolName: string,
    state: "input-streaming" | "input-available" = "input-available"
  ): UIMessage {
    return {
      id: "a0",
      role: "assistant",
      parts: [
        {
          type: "dynamic-tool",
          toolName,
          toolCallId: "call-0",
          state,
          input: {},
        },
      ],
    }
  }
  const prompt: UIMessage = {
    id: "u1",
    role: "user",
    parts: [{ type: "text", text: QUESTION }],
  }
  const opened: UIMessage = {
    id: "a1",
    role: "assistant",
    parts: [
      {
        type: "dynamic-tool",
        toolName: "core__cases__list_cases",
        toolCallId: "call-1",
        state: "input-available",
        input: {},
      },
      {
        type: "data-approval-request",
        data: [{ tool_call_id: "call-1", tool_name: "core.cases.list_cases" }],
      },
    ],
  }
  // The continuation after approval streams only the call's output.
  const continued: UIMessage = {
    id: "a2",
    role: "assistant",
    parts: [
      {
        type: "dynamic-tool",
        toolName: "core__cases__list_cases",
        toolCallId: "call-1",
        state: "output-available",
        input: {},
        output: [],
      },
    ],
  }

  it("keeps a working line at minimal once a continuation merges away", () => {
    const thread = transformMessages([prompt, opened, continued])
    expect(thread.map(({ id }) => id)).toEqual(["u1", "a1"])
    expect(getRunningLineLabel("streaming", thread, minimal)).toBe("Working...")
  })

  it("leaves a running tool's own line to show progress at default", () => {
    const thread = [prompt, running("core__cases__list_cases")]
    expect(getRunningLineLabel("streaming", thread, defaults)).toBe(null)
  })

  it("thinks while a hidden tool runs at default", () => {
    const thread = [prompt, running("ToolSearch")]
    expect(getRunningLineLabel("streaming", thread, defaults)).toBe(
      "Thinking..."
    )
  })

  it("thinks while a hidden tool streams its input at default", () => {
    const thread = [prompt, running("ToolSearch", "input-streaming")]
    expect(getRunningLineLabel("streaming", thread, defaults)).toBe(
      "Thinking..."
    )
  })

  it("keeps a working line at minimal after an in-chat approval", () => {
    const submitted: UIMessage = {
      id: "c1",
      role: "user",
      parts: [
        {
          type: "data-continue",
          data: { kind: "continue", source: "chat", decisions: [] },
        },
      ],
    }
    const thread = transformMessages([prompt, opened, submitted, continued])
    expect(getRunningLineLabel("streaming", thread, minimal)).toBe("Working...")
  })

  it("draws nothing while an approval waits", () => {
    expect(getRunningLineLabel("streaming", [prompt, opened], minimal)).toBe(
      null
    )
  })

  it("thinks until the first step at minimal", () => {
    expect(getRunningLineLabel("submitted", [prompt], minimal)).toBe(
      "Thinking..."
    )
    const answering: UIMessage = {
      id: "a3",
      role: "assistant",
      parts: [{ type: "text", text: ANSWER }],
    }
    expect(getRunningLineLabel("streaming", [prompt, answering], minimal)).toBe(
      null
    )
  })
})

describe("approval submit", () => {
  const opened: UIMessage = {
    id: "a1",
    role: "assistant",
    parts: [
      {
        type: "data-approval-request",
        data: [{ tool_call_id: "call-1", tool_name: "core.cases.list_cases" }],
      },
    ],
  }

  function renderCard(status: "streaming" | "ready") {
    return render(
      <MessageParts
        message={opened}
        status={status}
        isLastMessage
        onSubmitApprovals={jest.fn()}
      />
    )
  }

  it("waits for the paused stream to close before submitting", () => {
    const { rerender } = renderCard("streaming")
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled()

    rerender(
      <MessageParts
        message={opened}
        status="ready"
        isLastMessage
        onSubmitApprovals={jest.fn()}
      />
    )
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled()
  })
})
