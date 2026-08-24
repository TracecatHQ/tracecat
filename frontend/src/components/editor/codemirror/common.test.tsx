import { createNodeTooltipContent } from "@/components/editor/codemirror/common"

const markupLiteral = "<b>markup</b>"

describe("expression tooltip rendering", () => {
  it.each([
    ["ACTIONS", `ACTIONS['${markupLiteral}']`],
    ["SECRETS", `SECRETS.example.${markupLiteral}`],
    ["VARS", `VARS.example.${markupLiteral}`],
    ["ENV", `ENV.${markupLiteral}`],
    ["TRIGGER", `TRIGGER.${markupLiteral}`],
  ])("renders %s token values as text", async (type, expression) => {
    const tooltip = await createNodeTooltipContent(
      { type, value: expression, start: 0, end: expression.length },
      expression,
      "workspace-id"
    )

    expect(tooltip).not.toBeNull()
    expect(tooltip?.querySelector("b")).toBeNull()
    expect(tooltip?.textContent).toContain(markupLiteral)
    expect(tooltip?.innerHTML).toContain("&lt;b&gt;")
  })
})
