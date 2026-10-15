import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { ActionSessionStream } from "@/components/executions/action-session-stream"

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

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const ANSWER = "The workspace has no tables."

function at(seconds: number) {
  return {
    created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, seconds)).toISOString(),
  }
}

describe("ActionSessionStream", () => {
  it("folds a finished run's steps into one worked line", () => {
    render(
      <ActionSessionStream
        session={{
          id: "session-1",
          events: [
            {
              id: "prompt",
              role: "user",
              metadata: at(0),
              parts: [{ type: "text", text: "List the tables" }],
            },
            {
              id: "tool",
              role: "assistant",
              metadata: at(4),
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
            },
            {
              id: "answer",
              role: "assistant",
              metadata: at(12),
              parts: [{ type: "text", text: ANSWER }],
            },
          ],
        }}
      />
    )

    expect(screen.getByText(ANSWER)).toBeInTheDocument()
    expect(
      screen.queryByText("Ran core.table.list_tables")
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("Worked for 12s"))

    expect(screen.getByText("Ran core.table.list_tables")).toBeInTheDocument()
  })
})
