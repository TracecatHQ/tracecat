import { fireEvent, render, screen, waitFor } from "@testing-library/react"

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

import { ToolInput, ToolOutput } from "@/components/ai-elements/tool"

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

  it("renders structured text and sources instead of raw JSON", async () => {
    render(
      <ToolOutput
        output={{
          response: { text: "Washington, DC: ⛅ +2°C" },
          sources: [
            {
              title: "wttr.in Washington, DC weather",
              url: "https://wttr.in/washington,dc?format=3",
            },
          ],
        }}
        errorText={undefined}
      />
    )

    expect(screen.getByText("Washington, DC: ⛅ +2°C")).toBeInTheDocument()
    expect(screen.queryByTestId("mock-code-block")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /used 1 sources/i }))

    await waitFor(() => {
      expect(
        screen.getByRole("link", { name: /wttr\.in washington, dc weather/i })
      ).toHaveAttribute("href", "https://wttr.in/washington,dc?format=3")
    })
  })
})
