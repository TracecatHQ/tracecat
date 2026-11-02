import { fireEvent, render, screen } from "@testing-library/react"
import { ReadOnlyFieldset } from "@/components/managed/read-only-fieldset"

function renderEditable(readOnly: boolean, onKeyDown: jest.Mock) {
  render(
    <ReadOnlyFieldset readOnly={readOnly}>
      <div
        role="textbox"
        tabIndex={0}
        contentEditable
        suppressContentEditableWarning
        onKeyDown={onKeyDown}
      >
        text
      </div>
    </ReadOnlyFieldset>
  )
  const editor = screen.getByRole("textbox")
  // jsdom does not implement isContentEditable.
  Object.defineProperty(editor, "isContentEditable", { value: true })
  return editor
}

describe("ReadOnlyFieldset", () => {
  it("blocks edit keys aimed at a contenteditable editor when read-only", () => {
    const onKeyDown = jest.fn()
    const editor = renderEditable(true, onKeyDown)

    fireEvent.keyDown(editor, { key: "x" })
    fireEvent.keyDown(editor, { key: "Backspace" })
    expect(onKeyDown).not.toHaveBeenCalled()

    // Navigation and copy still reach the editor.
    fireEvent.keyDown(editor, { key: "ArrowDown" })
    fireEvent.keyDown(editor, { key: "c", metaKey: true })
    expect(onKeyDown).toHaveBeenCalledTimes(2)
  })

  it("leaves editors alone when editable", () => {
    const onKeyDown = jest.fn()
    const editor = renderEditable(false, onKeyDown)

    fireEvent.keyDown(editor, { key: "x" })
    expect(onKeyDown).toHaveBeenCalledTimes(1)
  })
})
