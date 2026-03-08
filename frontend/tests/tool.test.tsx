import { render, screen, waitFor } from "@testing-library/react"

jest.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({
    children,
    code,
  }: {
    children?: React.ReactNode
    code: string
  }) => (
    <div data-testid="mock-code-block">
      {children}
      <pre>{code}</pre>
    </div>
  ),
  CodeBlockCopyButton: () => null,
}))

import { ToolInput } from "@/components/ai-elements/tool"

describe("Tool payload downloads", () => {
  beforeEach(() => {
    URL.createObjectURL = jest.fn(() => "blob:tool-payload")
    URL.revokeObjectURL = jest.fn()
  })

  it("keeps the json extension for large JSON string payloads", async () => {
    render(<ToolInput input={`{"items":[${'"value",'.repeat(3000)}"done"]}`} />)

    await waitFor(() => {
      expect(
        screen.getByRole("link", { name: /download file/i })
      ).toHaveAttribute("download", "tool-parameters.json")
    })
  })
})
