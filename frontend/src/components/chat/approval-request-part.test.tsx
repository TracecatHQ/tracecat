import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ApprovalRequestPart } from "@/components/chat/approval-request-part"
import type { ApprovalCard } from "@/lib/chat"

jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: () => null,
}))

jest.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { code: string }) => <pre>{code}</pre>,
  CodeBlockCopyButton: () => null,
}))

jest.mock("@/components/icons", () => ({
  getIcon: () => <span data-testid="tool-icon" />,
}))

const approval = (id: string, n: number): ApprovalCard => ({
  tool_call_id: id,
  tool_name: "core__transform__reshape",
  args: { value: { n } },
})

describe("ApprovalRequestPart in the prompt box", () => {
  it("approves a lone call on Enter and denies it on Escape", async () => {
    const onSubmit = jest.fn().mockResolvedValue(["call-1"])
    const { rerender } = render(
      <ApprovalRequestPart
        variant="composer"
        approvals={[approval("call-1", 1)]}
        onSubmit={onSubmit}
      />
    )
    const box = screen.getByRole("region", { name: "Approval required" })

    fireEvent.keyDown(box, { key: "Enter" })

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith([
        { tool_call_id: "call-1", action: "approve" },
      ])
    )

    onSubmit.mockClear()
    onSubmit.mockResolvedValue(["call-2"])
    rerender(
      <ApprovalRequestPart
        variant="composer"
        approvals={[approval("call-2", 2)]}
        onSubmit={onSubmit}
      />
    )
    fireEvent.keyDown(
      screen.getByRole("region", { name: "Approval required" }),
      { key: "Escape" }
    )

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith([
        expect.objectContaining({ tool_call_id: "call-2", action: "deny" }),
      ])
    )
  })

  it("sends a batch's decided calls on Enter, without an approve-all", async () => {
    const onSubmit = jest.fn().mockResolvedValue(["call-1"])
    render(
      <ApprovalRequestPart
        variant="composer"
        approvals={[approval("call-1", 1), approval("call-2", 2)]}
        onSubmit={onSubmit}
      />
    )
    const box = screen.getByRole("region", { name: "Approvals required" })
    expect(screen.queryByText("Approve rest")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled()

    fireEvent.keyDown(box, { key: "Enter" })
    expect(onSubmit).not.toHaveBeenCalled()

    fireEvent.click(screen.getAllByRole("button", { name: "Approve" })[0])
    fireEvent.keyDown(box, { key: "Enter" })

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith([
        { tool_call_id: "call-1", action: "approve" },
      ])
    )
  })

  it("denies every call in a batch on Escape", async () => {
    const onSubmit = jest.fn().mockResolvedValue(["call-1", "call-2"])
    render(
      <ApprovalRequestPart
        variant="composer"
        approvals={[approval("call-1", 1), approval("call-2", 2)]}
        onSubmit={onSubmit}
      />
    )

    fireEvent.keyDown(
      screen.getByRole("region", { name: "Approvals required" }),
      { key: "Escape" }
    )

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith([
        expect.objectContaining({ tool_call_id: "call-1", action: "deny" }),
        expect.objectContaining({ tool_call_id: "call-2", action: "deny" }),
      ])
    )
  })
})
