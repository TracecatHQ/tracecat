import {
  getMermaidThemeVariables,
  MermaidCodeBlock,
  shouldRenderMermaidDiagram,
} from "@/components/tiptap-node/mermaid-code-block-node/mermaid-code-block-node"

it("preserves the existing code-block arrow-up behavior", () => {
  expect(MermaidCodeBlock.options.exitOnArrowUp).toBe(false)
})

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

describe("getMermaidThemeVariables", () => {
  it("uses a light surface palette in light mode", () => {
    const variables = getMermaidThemeVariables("light")
    expect(variables.darkMode).toBe(false)
    expect(variables.background).toBe("#ffffff")
    expect(variables.primaryTextColor).toBe("#18181b")
  })

  it("uses a dark surface palette with light text in dark mode", () => {
    const variables = getMermaidThemeVariables("dark")
    expect(variables.darkMode).toBe(true)
    expect(variables.background).toBe("#101010")
    expect(variables.primaryColor).toBe("#262626")
    expect(variables.primaryTextColor).toBe("#fafafa")
    expect(variables.edgeLabelBackground).toBe("#101010")
  })

  it("defines all twelve categorical slots for both themes", () => {
    for (const theme of ["light", "dark"] as const) {
      const variables: Record<string, unknown> = getMermaidThemeVariables(theme)
      for (let index = 0; index < 12; index += 1) {
        expect(variables[`cScale${index}`]).toMatch(/^#[0-9a-f]{6}$/)
        expect(variables[`pie${index + 1}`]).toMatch(/^#[0-9a-f]{6}$/)
      }
    }
  })
})
