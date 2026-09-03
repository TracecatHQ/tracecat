import { shouldRenderMermaidDiagram } from "@/components/tiptap-node/mermaid-code-block-node/mermaid-code-block-node"

describe("shouldRenderMermaidDiagram", () => {
  it("renders Mermaid diagrams in read-only views", () => {
    expect(
      shouldRenderMermaidDiagram({
        isEditable: false,
        isFocused: false,
        language: "mermaid",
        renderWhenBlurred: false,
      })
    ).toBe(true)
  })

  it("renders Mermaid diagrams for opted-in editable views when blurred", () => {
    expect(
      shouldRenderMermaidDiagram({
        isEditable: true,
        isFocused: false,
        language: "mermaid",
        renderWhenBlurred: true,
      })
    ).toBe(true)
  })

  it("keeps Mermaid source editable while focused", () => {
    expect(
      shouldRenderMermaidDiagram({
        isEditable: true,
        isFocused: true,
        language: "mermaid",
        renderWhenBlurred: true,
      })
    ).toBe(false)
  })

  it("does not render non-Mermaid code blocks as diagrams", () => {
    expect(
      shouldRenderMermaidDiagram({
        isEditable: false,
        isFocused: false,
        language: "python",
        renderWhenBlurred: true,
      })
    ).toBe(false)
  })
})
