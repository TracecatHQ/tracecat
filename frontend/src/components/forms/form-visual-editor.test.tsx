import { fireEvent, render, screen } from "@testing-library/react"
import { FormVisualEditor } from "@/components/forms/form-visual-editor"

const TEXT = `# Kept
title: Laptop request
layout:
  - field: model
  - heading: Details
`

function renderEditor(text = TEXT, readOnly = false) {
  const onChange = jest.fn()
  const onEditYaml = jest.fn()
  render(
    <FormVisualEditor
      text={text}
      onChange={onChange}
      fields={["model", "reason"]}
      defaultTitle="Laptop workflow"
      readOnly={readOnly}
      onEditYaml={onEditYaml}
    />
  )
  return { onChange, onEditYaml }
}

describe("FormVisualEditor", () => {
  it("edits the title in the YAML and keeps its comments", () => {
    const { onChange } = renderEditor()
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Order" },
    })
    expect(onChange).toHaveBeenCalledWith(
      "# Kept\ntitle: Order\nlayout:\n  - field: model\n  - heading: Details\n"
    )
  })

  it("opens a block to edit it and removes blocks", () => {
    const { onChange } = renderEditor()
    fireEvent.click(screen.getByRole("button", { name: /Details/ }))
    fireEvent.change(screen.getByLabelText("Heading"), {
      target: { value: "" },
    })
    expect(onChange).toHaveBeenLastCalledWith(
      '# Kept\ntitle: Laptop request\nlayout:\n  - field: model\n  - heading: ""\n'
    )
    fireEvent.click(screen.getAllByRole("button", { name: "Remove block" })[0])
    expect(onChange).toHaveBeenLastCalledWith(
      "# Kept\ntitle: Laptop request\nlayout:\n  - heading: Details\n"
    )
  })

  it("sends YAML it can't show to the YAML editor", () => {
    const { onEditYaml } = renderEditor("title: [broken\n")
    fireEvent.click(screen.getByRole("button", { name: "Edit YAML" }))
    expect(onEditYaml).toHaveBeenCalled()
  })

  it("is read only without form:update", () => {
    renderEditor(TEXT, true)
    expect(screen.getByLabelText("Title")).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Add block" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Remove block" })).toBeNull()
  })
})
