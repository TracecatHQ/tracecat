import { act, renderHook } from "@testing-library/react"
import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { Editor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import { TracecatTable } from "@/components/tiptap-node/table-node/table-node-extension"
import type { TableButtonGroups } from "@/components/tiptap-ui/table-controls/use-table-controls"
import {
  useInsertTable,
  useTableControls,
} from "@/components/tiptap-ui/table-controls/use-table-controls"

/** Three columns, so the middle one has a neighbour on either side. */
const THREE_COLUMN_TABLE = `
<table>
  <tbody>
    <tr><th>H1</th><th>H2</th><th>H3</th></tr>
    <tr><td>a</td><td>b</td><td>c</td></tr>
  </tbody>
</table>
`

function createEditor(
  content: string,
  options: { editable?: boolean; withTable?: boolean } = {}
): { editor: Editor; element: HTMLElement } {
  const { editable = true, withTable = true } = options
  const element = document.createElement("div")
  document.body.appendChild(element)
  const editor = new Editor({
    element,
    editable,
    extensions: withTable
      ? [
          StarterKit,
          TracecatTable.configure({ resizable: true, cellMinWidth: 48 }),
          TableRow,
          TableHeader,
          TableCell,
        ]
      : [StarterKit],
    content,
  })
  return { editor, element }
}

function withEditor(
  content: string,
  run: (editor: Editor) => void,
  options?: { editable?: boolean; withTable?: boolean }
): void {
  const { editor, element } = createEditor(content, options)
  try {
    run(editor)
  } finally {
    editor.destroy()
    element.remove()
  }
}

/** Document positions of every cell, indexed by row and then by column. */
function cellPositions(editor: Editor): number[][] {
  const rows: number[][] = []
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name !== "tableRow") {
      return true
    }
    const positions: number[] = []
    node.forEach((_cell, offset) => {
      positions.push(pos + 1 + offset)
    })
    rows.push(positions)
    return false
  })
  return rows
}

/** Put a plain text cursor inside the given cell, as clicking into it would. */
function placeCursorInCell(editor: Editor, row: number, column: number): void {
  const cellPos = cellPositions(editor)[row][column]
  // +1 enters the cell, +1 again enters its first paragraph.
  act(() => {
    editor.commands.setTextSelection(cellPos + 2)
  })
}

/** Every button's disabled flag, flattened across the three groups. */
function disabledByKey(groups: TableButtonGroups): Record<string, boolean> {
  const buttons = [
    ...groups.insertButtons,
    ...groups.moveButtons,
    ...groups.deleteButtons,
  ]
  return Object.fromEntries(
    buttons.map((button) => [button.key, button.disabled])
  )
}

describe("useTableControls", () => {
  it("follows the caret between columns of the same table", () => {
    withEditor(THREE_COLUMN_TABLE, (editor) => {
      // Start in the middle column, where both moves are available. Nothing a
      // memo could key on changes from here on — the editor instance is stable
      // and the cursor never leaves the table — so a memoized result would
      // stay stuck on these two flags for the rest of the test.
      placeCursorInCell(editor, 1, 1)
      const { result } = renderHook(() => useTableControls(editor))

      expect(disabledByKey(result.current)).toMatchObject({
        "move-column-left": false,
        "move-column-right": false,
      })

      placeCursorInCell(editor, 1, 0)
      expect(disabledByKey(result.current)).toMatchObject({
        "move-column-left": true,
        "move-column-right": false,
      })

      placeCursorInCell(editor, 1, 2)
      expect(disabledByKey(result.current)).toMatchObject({
        "move-column-left": false,
        "move-column-right": true,
      })
    })
  })

  it("has no buttons while the cursor is outside a table", () => {
    withEditor(`<p>outside</p>${THREE_COLUMN_TABLE}`, (editor) => {
      const { result } = renderHook(() => useTableControls(editor))

      act(() => {
        editor.commands.setTextSelection(2)
      })
      expect(result.current).toEqual({
        insertButtons: [],
        moveButtons: [],
        deleteButtons: [],
      })

      placeCursorInCell(editor, 1, 1)
      expect(result.current.moveButtons.map((button) => button.key)).toEqual([
        "move-column-left",
        "move-column-right",
      ])
    })
  })

  it("has no buttons on a read-only editor", () => {
    withEditor(
      THREE_COLUMN_TABLE,
      (editor) => {
        placeCursorInCell(editor, 1, 1)
        const { result } = renderHook(() => useTableControls(editor))

        expect(result.current).toEqual({
          insertButtons: [],
          moveButtons: [],
          deleteButtons: [],
        })
      },
      { editable: false }
    )
  })

  it("has no buttons without an editor", () => {
    const { result } = renderHook(() => useTableControls(null))

    expect(result.current).toEqual({
      insertButtons: [],
      moveButtons: [],
      deleteButtons: [],
    })
  })
})

describe("useInsertTable", () => {
  it("reports the command as available inside a plain paragraph", () => {
    withEditor("<p>plain paragraph</p>", (editor) => {
      const { result } = renderHook(() => useInsertTable(editor))

      expect(result.current.canInsertTable).toBe(true)
    })
  })

  it("reports the command as unavailable when it is not registered", () => {
    withEditor(
      "<p>plain paragraph</p>",
      (editor) => {
        expect(editor.can().insertTable).toBeUndefined()

        const { result } = renderHook(() => useInsertTable(editor))

        expect(result.current.canInsertTable).toBe(false)
      },
      { withTable: false }
    )
  })

  it("reports the command as unavailable on a read-only editor", () => {
    withEditor(
      "<p>plain paragraph</p>",
      (editor) => {
        const { result } = renderHook(() => useInsertTable(editor))

        expect(result.current.canInsertTable).toBe(false)
      },
      { editable: false }
    )
  })
})
