import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { approvalsSubmitApprovals } from "@/client"
import { AgentApprovalsDialog } from "@/components/agents/agent-approvals-dialog"
import type { AgentSessionWithStatus } from "@/lib/agents"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  approvalsSubmitApprovals: jest.fn().mockResolvedValue(undefined),
}))

jest.mock("@/hooks/use-chat", () => ({
  invalidateChatTurnQueries: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { code: string }) => <pre>{code}</pre>,
  CodeBlockCopyButton: () => null,
}))

jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: () => null,
}))

jest.mock("@/components/icons", () => ({
  getIcon: () => <span data-testid="tool-icon" />,
}))

const session = {
  id: "session-1",
  approvals: [
    {
      id: "approval-1",
      tool_call_id: "call-1",
      tool_name: "core__http_request",
      tool_call_args: { url: "https://example.com" },
      status: "pending",
    },
    {
      id: "approval-2",
      tool_call_id: "call-2",
      tool_name: "core__http_request",
      tool_call_args: '{"url": "https://example.org"}',
      status: "pending",
    },
  ],
} as unknown as AgentSessionWithStatus

describe("AgentApprovalsDialog", () => {
  it("decides a workflow agent's calls with the chat approval batch", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AgentApprovalsDialog session={session} open onOpenChange={jest.fn()} />
      </QueryClientProvider>
    )

    expect(screen.getByTestId("approval-batch")).toBeInTheDocument()
    const [, secondDeny] = screen.getAllByRole("button", { name: "Deny" })
    const [firstApprove] = screen.getAllByRole("button", { name: "Approve" })
    fireEvent.click(firstApprove)
    fireEvent.click(secondDeny)
    fireEvent.change(
      screen.getByRole("textbox", {
        name: "Deny reason for core.http_request",
      }),
      { target: { value: "Wrong host" } }
    )
    fireEvent.click(screen.getByRole("button", { name: /^Submit/ }))

    await waitFor(() =>
      expect(approvalsSubmitApprovals).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        sessionId: "session-1",
        requestBody: {
          approvals: {
            "call-1": true,
            "call-2": { kind: "tool-denied", message: "Wrong host" },
          },
        },
      })
    )
  })
})
